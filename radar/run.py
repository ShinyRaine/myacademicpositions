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

from . import classify, issues as gh, llm, score, sources

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SEEN = DATA / "seen.json"
JOBS = DATA / "jobs.json"
DECISIONS = DATA / "decisions.json"
BODIES = DATA / "bodies.json"
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
                   if str(v.get("skipped", "")).startswith(("slug-type:", "listed-type:"))]
        for k in dropped:
            seen.pop(k)
        print(f"  reconsidering {len(dropped)} advert(s) skipped on their listed type")

    wanted = set(profile.get("hard_filters", {}).get("position_types") or [])
    live: dict[str, dict] = {}
    budget = args.limit or 0
    fetched = failures = 0

    for src in sources.active(profile):
        print(f"[{now_iso()}] {src.LABEL}: listing")
        try:
            ads = src.list_ads()
        except Exception as exc:  # noqa: BLE001 - one bad source must not sink the run
            print(f"  ! listing failed, keeping previous data for this source: {exc}")
            failures += 1
            # Keep this source's stored adverts alive so they are not pruned.
            for jid, rec in raw.items():
                if rec.get("source") == src.NAME:
                    live[jid] = {"url": rec["url"]}
            continue
        print(f"  {len(ads)} live advert(s)")

        todo = []
        for ad in ads:
            jid = sources.key(src.NAME, ad["id"])
            live[jid] = ad
            if jid in seen:
                continue
            # The listing already names the position type (Chalmers) or spells
            # it in the title (KTH, and the AP slug), so a type we would drop
            # anyway never costs a request.
            guess = ad.get("position_hint") or classify.position_type(ad.get("title", ""))
            if not args.no_prefilter and wanted and guess != "other" and guess not in wanted:
                seen[jid] = {"first_seen": now_iso(), "url": ad["url"],
                             "source": src.NAME, "skipped": f"listed-type:{guess}"}
            else:
                todo.append((jid, ad))

        print(f"  {len(todo)} to fetch")
        if budget:
            todo = todo[:budget]
            print(f"  limited to {len(todo)} this run")

        for n, (jid, ad) in enumerate(todo, 1):
            try:
                rec = src.fetch(ad)
            except Exception as exc:  # noqa: BLE001
                print(f"  [{n}/{len(todo)}] FAIL {ad['url']}: {exc}")
                continue
            if not rec:
                print(f"  [{n}/{len(todo)}] unparseable: {ad['url']}")
                seen[jid] = {"first_seen": now_iso(), "url": ad["url"],
                             "source": src.NAME, "skipped": "unparseable"}
                continue
            rec["id"] = jid
            rec["body"] = (rec.get("body") or "")[:BODY_CAP]
            rec["language"] = classify.language(rec["title"], rec["body"])
            rec["position_type"] = (ad.get("position_hint")
                                    or classify.position_type(rec["title"], rec["body"]))
            rec["first_seen"] = now_iso()
            raw[jid] = rec
            seen[jid] = {"first_seen": rec["first_seen"], "url": ad["url"], "source": src.NAME}
            fetched += 1
            if n % 20 == 0 or n == len(todo):
                # Checkpoint: the first run walks several hundred adverts over a
                # quarter of an hour, and a timeout must not throw that away.
                _save(RAW, raw)
                _save(SEEN, seen)
                print(f"  [{n}/{len(todo)}] fetched")

        if budget:
            budget = max(0, budget - len(todo))

    if failures == len(sources.active(profile)):
        print("  ! every source failed, leaving stored data untouched")
        return 1

    print(f"  parsed {fetched} new advert(s) across all sources")

    # Drop records whose advert has left every listing (filled or expired).
    gone = [jid for jid in raw if jid not in live]
    for jid in gone:
        raw.pop(jid, None)
    if gone:
        print(f"  dropped {len(gone)} advert(s) no longer listed")

    _save(RAW, raw)
    _save(SEEN, seen)

    jobs, shortlist = build_jobs(raw, profile, run_llm=not args.no_llm)
    _save(JOBS, jobs)

    if not args.no_issues:
        open_issues(shortlist, profile)

    print(f"[{now_iso()}] scan done: {len(jobs['jobs'])} jobs on the board")
    return 0


# ---------------------------------------------------------------------------
# scoring + dedup + LLM, shared by scan and rescore
# ---------------------------------------------------------------------------
def issue_worthy(job: dict, profile: dict) -> bool:
    """Does this job's band open a GitHub issue?

    The tier table already says so: a band with a `github:` label is one worth
    the issue and the Gemini spend. Reading it from there means renaming or
    re-banding in profile.yaml needs no change here.
    """
    band = score.tier_table(profile).get(job.get("tier"))
    return bool(band and band.get("github"))


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
    # Then the same vacancy seen through two sources: KTH and Chalmers adverts
    # appear both on their own site and on the aggregator.
    scored, cross = classify.merge_sources(scored, sources.DEFAULT)
    print(f"  filtered out {dropped['position_type']} by type, {dropped['expired']} expired, "
          f"{duplicates} translation duplicates, {cross} cross-source duplicates")

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
                try:
                    verdict = llm.judge(job, profile, key)
                except llm.QuotaExhausted:
                    left = len(todo) - n + 1
                    print(f"    ! free-tier quota gone, {left} job(s) left unjudged; "
                          f"the next scan picks them up")
                    break
                if verdict:
                    job["llm"] = verdict
                    print(f"    [{n}/{len(todo)}] {verdict['verdict']:5s} fit {verdict['fit']}/10  "
                          f"{job['tier_name']} {job['score']:3d}  {job['title'][:52]}")
                else:
                    print(f"    [{n}/{len(todo)}] no verdict  {job['title'][:60]}")

    scored.sort(key=lambda j: (order.get(j["tier"], 9),
                               -(j.get("llm", {}).get("fit", 0)),
                               -j["score"]))

    # Everything in a band that opens issues. open_issues() skips whatever
    # already has one, open or closed, so dedup does not belong here — and the
    # `id not in previous` test this used to do was not dedup but a permanent
    # exclusion: the board was bootstrapped locally and committed with its
    # scores, so by the first scan that had a token every advert was already
    # in the previous jobs.json and no issue was ever opened. An advert that
    # climbs into the band later was shut out the same way.
    shortlist = [j for j in scored if issue_worthy(j, profile)]

    # The prepare endpoint reads the advert text, which jobs.json deliberately
    # leaves out. Publish it for the bands worth applying to and no further.
    body_tiers = set(profile.get("publish_bodies_for") or ["A", "B", "C"])
    _save(BODIES, {j["id"]: (j.get("body") or "")[:BODY_CAP]
                   for j in scored if j["tier"] in body_tiers})

    payload = {
        "generated_at": now_iso(),
        "sources": [s.LABEL for s in sources.active(profile)],
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
        # The dashboard labels its filters from this, so renaming a tier in
        # profile.yaml renames it on the site too.
        "tiers": score.tier_table(profile),
        "jobs": [_slim(j) for j in scored],
    }
    return payload, shortlist


# The body is kept out of the published JSON: it is megabytes of prose, the
# dashboard never shows it, and the advert is one click away. Everything else,
# including the hiring contact the university listings give us, is kept.
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
        print("  nothing in an issue-opening band")
        return

    table = score.tier_table(profile)
    client.ensure_labels([t["github"] for t in table.values() if t.get("github")])
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
        tier = table[job["tier"]]
        labels = ["radar"]
        if tier.get("github"):
            labels.append(tier["github"])
        if job.get("urgent"):
            labels.append("urgent")
        title = f"[{tier['name']}·{job['score']}] {job['title']} — {job['employer']}"
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
    jobs, shortlist = build_jobs(raw, profile, run_llm=not args.no_llm)
    _save(JOBS, jobs)
    if args.open_issues:
        open_issues(shortlist, profile)
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
