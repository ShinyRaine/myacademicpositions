"""Entry point: `python -m radar.run scan` / `sync` / `rescore`.

scan     sitemap diff -> fetch new ads -> classify -> score -> LLM -> issues
sync     read triage labels off the issues -> data/decisions.json
rescore  re-run scoring (and optionally the LLM) over everything already stored
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import classify, http, issues as gh, llm, parse, score

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SEEN = DATA / "seen.json"
JOBS = DATA / "jobs.json"
DECISIONS = DATA / "decisions.json"
RAW = DATA / "raw.json"
BODY_CAP = 12_000


def _load(path: Path, default):
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _save(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")


def load_profile() -> dict:
    with (ROOT / "profile.yaml").open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------
def cmd_scan(args) -> int:
    profile = load_profile()
    seen: dict = _load(SEEN, {})
    raw: dict = _load(RAW, {})

    if args.reconsider_skipped:
        dropped = [k for k, v in seen.items()
                   if str(v.get("skipped", "")).startswith("slug-type:")]
        for k in dropped:
            seen.pop(k)
        print(f"  reconsidering {len(dropped)} advert(s) skipped on slug type")

    print(f"[{now_iso()}] fetching sitemap")
    urls = parse.sitemap_urls(http.get(parse.SITEMAP))
    print(f"  {len(urls)} live adverts listed")
    if not urls:
        print("  ! empty sitemap, aborting without touching stored data")
        return 1

    live_ids = {parse.job_id(u): u for u in urls}
    new_ids = [i for i in live_ids if i not in seen]
    print(f"  {len(new_ids)} not seen before")

    # The URL slug is the advert title, so the position type can be read before
    # spending a request. Types that could never pass the hard filter are
    # recorded as skipped and never fetched: that is ~40% of the board.
    if not args.no_prefilter:
        wanted = set(profile.get("hard_filters", {}).get("position_types") or [])
        keep = []
        for jid in new_ids:
            slug_title = live_ids[jid].rsplit("/", 2)[-2].replace("-", " ")
            guess = classify.position_type(slug_title)
            if wanted and guess != "other" and guess not in wanted:
                seen[jid] = {"first_seen": now_iso(), "url": live_ids[jid],
                             "skipped": f"slug-type:{guess}"}
            else:
                keep.append(jid)
        print(f"  {len(new_ids) - len(keep)} skipped on slug type, {len(keep)} to fetch")
        new_ids = keep

    if args.limit:
        new_ids = new_ids[: args.limit]
        print(f"  limited to {len(new_ids)} this run")

    fetched = 0
    for n, jid in enumerate(new_ids, 1):
        url = live_ids[jid]
        try:
            rec = parse.parse_ad(url, http.get(url))
        except Exception as exc:  # noqa: BLE001
            print(f"  [{n}/{len(new_ids)}] FAIL {url}: {exc}")
            continue
        if not rec:
            print(f"  [{n}/{len(new_ids)}] no JobPosting on {url}")
            seen[jid] = {"first_seen": now_iso(), "url": url, "skipped": "no-jsonld"}
            continue
        # Cap the stored body: the LLM reads 9k chars and scoring needs less,
        # so keeping the whole advert would only bloat the committed history.
        rec["body"] = rec["body"][:BODY_CAP]
        rec["language"] = classify.language(rec["title"], rec["body"])
        rec["position_type"] = classify.position_type(rec["title"], rec["body"])
        rec["first_seen"] = now_iso()
        raw[jid] = rec
        seen[jid] = {"first_seen": rec["first_seen"], "url": url}
        fetched += 1
        if n % 20 == 0 or n == len(new_ids):
            # Checkpoint: the first run walks ~800 adverts over a quarter of an
            # hour, and a timeout or a runner eviction must not throw that away.
            _save(RAW, raw)
            _save(SEEN, seen)
            print(f"  [{n}/{len(new_ids)}] fetched")

    print(f"  parsed {fetched} new adverts")

    # Drop records whose advert has left the sitemap (filled or expired).
    gone = [jid for jid in raw if jid not in live_ids]
    for jid in gone:
        raw.pop(jid, None)
    if gone:
        print(f"  dropped {len(gone)} adverts no longer listed")

    _save(RAW, raw)
    _save(SEEN, seen)

    jobs, newly_shortlisted = build_jobs(raw, profile, run_llm=not args.no_llm)
    _save(JOBS, jobs)

    if not args.no_issues:
        open_issues(newly_shortlisted, profile)

    print(f"[{now_iso()}] scan done: {len(jobs['jobs'])} jobs on the board")
    return 0


# ---------------------------------------------------------------------------
# scoring + dedup + LLM, shared by scan and rescore
# ---------------------------------------------------------------------------
def build_jobs(raw: dict, profile: dict, *, run_llm: bool) -> tuple[dict, list[dict]]:
    now = datetime.now(timezone.utc)
    previous = {j["id"]: j for j in _load(JOBS, {}).get("jobs", [])}

    scored = []
    dropped = {"position_type": 0, "expired": 0}
    for rec in raw.values():
        job = score.score_job(rec, profile, now)
        ok, why = score.passes_hard_filters(job, profile)
        if not ok:
            key = "expired" if "deadline" in why else "position_type"
            dropped[key] += 1
            continue
        scored.append(job)

    # Collapse EN/SV (and other) translations of the same vacancy.
    scored, duplicates = classify.merge_translations(scored)
    print(f"  filtered out {dropped['position_type']} by type, {dropped['expired']} expired, "
          f"{duplicates} translation duplicates")

    # Carry over verdicts already paid for; only judge what is new.
    cfg = profile.get("llm", {})
    min_tier = cfg.get("min_tier", "B")
    order = {"A": 0, "B": 1, "C": 2, "D": 3}
    budget = int(cfg.get("max_jobs_per_run", 25))

    todo = []
    for job in scored:
        old = previous.get(job["id"])
        if old and old.get("llm"):
            job["llm"] = old["llm"]
        elif order.get(job["tier"], 9) <= order.get(min_tier, 1):
            todo.append(job)

    if run_llm and cfg.get("enabled", True) and todo:
        todo.sort(key=lambda j: (order.get(j["tier"], 9), -j["score"]))
        todo = todo[:budget]
        print(f"  asking {cfg.get('model')} about {len(todo)} job(s)")
        key = llm._key()
        if not key:
            print("  ! no GEMINI_API_KEY, skipping verdicts")
        else:
            for n, job in enumerate(todo, 1):
                verdict = llm.judge(job, profile, key)
                if verdict:
                    job["llm"] = verdict
                    print(f"    [{n}/{len(todo)}] {verdict['verdict']:5s} fit {verdict['fit']}/10  "
                          f"{job['title'][:60]}")
                else:
                    print(f"    [{n}/{len(todo)}] no verdict  {job['title'][:60]}")

    scored.sort(key=lambda j: (order.get(j["tier"], 9),
                               -(j.get("llm", {}).get("fit", 0)),
                               -j["score"]))

    # Newly shortlisted = tier A/B that has no issue-worthy predecessor.
    newly = [j for j in scored
             if order.get(j["tier"], 9) <= 1 and j["id"] not in previous]

    payload = {
        "generated_at": now_iso(),
        "source": parse.SITEMAP,
        # Lets the static dashboard link back to the issues without hardcoding
        # the repository name in the front-end.
        "repo": os.environ.get("GITHUB_REPOSITORY"),
        "counts": {
            "on_board": len(scored),
            "tier_A": sum(1 for j in scored if j["tier"] == "A"),
            "tier_B": sum(1 for j in scored if j["tier"] == "B"),
            "tier_C": sum(1 for j in scored if j["tier"] == "C"),
            "tier_D": sum(1 for j in scored if j["tier"] == "D"),
            "urgent": sum(1 for j in scored if j.get("urgent")),
        },
        "jobs": [_slim(j) for j in scored],
    }
    return payload, newly


# The body is kept out of the published JSON: it is megabytes of prose, the
# dashboard never shows it, and the advert is one click away.
_SLIM_DROP = ("body", "employer_blurb")


def _slim(job: dict) -> dict:
    out = {k: v for k, v in job.items() if k not in _SLIM_DROP}
    out["summary"] = (job.get("summary") or "")[:600]
    return out


# ---------------------------------------------------------------------------
# issues
# ---------------------------------------------------------------------------
def open_issues(jobs: list[dict], profile: dict) -> None:
    client = gh.from_env()
    if not client:
        print("  (no GITHUB_TOKEN/GITHUB_REPOSITORY, not opening issues)")
        return
    if not jobs:
        print("  no new tier A/B jobs, no issues to open")
        return

    client.ensure_labels()
    existing = {gh.job_id_of(i) for i in client.radar_issues()}
    cap = int(profile.get("issues", {}).get("max_per_run", 30))
    opened = 0
    for job in jobs:
        if job["id"] in existing:
            continue
        if opened >= cap:
            print(f"  hit the {cap}-issue cap; the rest stay on the dashboard "
                  f"and get issues next run")
            break
        labels = ["radar", f"tier:{job['tier']}"]
        if job.get("urgent"):
            labels.append("urgent")
        title = f"[{job['tier']}·{job['score']}] {job['title']} — {job['employer']}"
        client.create_issue(title[:250], gh.issue_body(job), labels)
        opened += 1
        print(f"  issue opened: {title[:90]}")
    print(f"  {opened} issue(s) opened")


# ---------------------------------------------------------------------------
# sync
# ---------------------------------------------------------------------------
def cmd_sync(args) -> int:
    client = gh.from_env()
    if not client:
        print("! GITHUB_TOKEN and GITHUB_REPOSITORY are required for sync")
        return 1

    decisions = {}
    for issue in client.radar_issues():
        jid = gh.job_id_of(issue)
        if not jid:
            continue
        decisions[jid] = {
            "decision": gh.decision_from(issue),
            "issue": issue["number"],
            "issue_url": issue["html_url"],
            "state": issue["state"],
            "updated_at": issue["updated_at"],
        }
    _save(DECISIONS, {"generated_at": now_iso(), "decisions": decisions})
    tally: dict[str, int] = {}
    for d in decisions.values():
        tally[d["decision"]] = tally.get(d["decision"], 0) + 1
    print(f"synced {len(decisions)} issue(s): {tally}")
    return 0


# ---------------------------------------------------------------------------
# rescore
# ---------------------------------------------------------------------------
def cmd_rescore(args) -> int:
    profile = load_profile()
    raw = _load(RAW, {})
    if not raw:
        print("! data/raw.json is empty, run `scan` first")
        return 1
    if args.forget_llm:
        _save(JOBS, {"jobs": []})   # build_jobs reads cached verdicts from here
    jobs, newly = build_jobs(raw, profile, run_llm=not args.no_llm)
    _save(JOBS, jobs)
    if args.open_issues:
        open_issues(newly, profile)
    print(f"rescored {len(jobs['jobs'])} jobs")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="radar")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="fetch new adverts, score, judge, open issues")
    s.add_argument("--limit", type=int, default=0, help="cap new adverts fetched this run")
    s.add_argument("--no-llm", action="store_true")
    s.add_argument("--no-issues", action="store_true")
    s.add_argument("--no-prefilter", action="store_true",
                   help="fetch every advert, even ones the slug rules out")
    s.add_argument("--reconsider-skipped", action="store_true",
                   help="re-examine adverts previously skipped on their slug "
                        "(use after widening hard_filters.position_types)")
    s.set_defaults(func=cmd_scan)

    s = sub.add_parser("sync", help="read triage labels into data/decisions.json")
    s.set_defaults(func=cmd_sync)

    s = sub.add_parser("rescore", help="re-apply profile.yaml to everything stored")
    s.add_argument("--no-llm", action="store_true")
    s.add_argument("--forget-llm", action="store_true", help="discard cached verdicts")
    s.add_argument("--open-issues", action="store_true")
    s.set_defaults(func=cmd_rescore)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
