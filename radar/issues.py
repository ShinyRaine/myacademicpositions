"""GitHub Issues as the triage surface.

One issue per shortlisted vacancy. You decide by applying a label; the
`triage-sync` workflow reads the labels back into data/decisions.json, which is
what the dashboard renders. Nothing else writes decisions, so the issue list and
the site can never disagree for longer than one workflow run.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

API = "https://api.github.com"

DECISION_LABELS = {
    "decision:apply": "apply",
    "decision:maybe": "maybe",
    "decision:skip": "skip",
    "decision:applied": "applied",
}

LABEL_DEFS = [
    ("radar", "0e8a16", "Opened by the Academic Positions radar"),
    ("urgent", "fbca04", "Deadline within the urgency window"),
    ("decision:apply", "0052cc", "Decided: write an application"),
    ("decision:maybe", "5319e7", "Undecided: needs a closer look"),
    ("decision:skip", "cfd3d7", "Decided: do not apply"),
    ("decision:applied", "006b75", "Application submitted"),
]

MARKER = "<!-- radar:job-id="
# One issue per vacancy, written by three hands: the scan owns the match
# block, /api/prepare the material checklist, /api/tailor the CV. Each is
# delimited so the others can be replaced without touching it, and whichever
# gets to the vacancy first writes the skeleton the rest slot into.
MATCH_OPEN = "<!-- radar:match -->"
MATCH_CLOSE = "<!-- /radar:match -->"

# The first line of the footer doubles as the end marker of the unsplit match
# section in issues opened before the blocks existed.
FOOTER_HEAD = "---\n**Decide by labelling this issue:**"
FOOTER = (
    "---\n"
    "**Decide by labelling this issue:** `decision:apply` · `decision:maybe` · "
    "`decision:skip` · `decision:applied`\n\n"
    "Closing the issue without a decision label counts as `skip`. "
    "The dashboard refreshes a few minutes after you label."
)


class GitHub:
    def __init__(self, repo: str, token: str):
        self.repo = repo
        self.token = token

    def _request(self, method: str, path: str, body=None, *, params=None):
        url = f"{API}{path}"
        if params:
            url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
                "User-Agent": "academic-radar",
            },
        )
        with urllib.request.urlopen(req, timeout=45) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None

    # -- labels ------------------------------------------------------------
    def ensure_labels(self, tier_labels: list[str] | None = None) -> None:
        """Create the fixed labels, plus one per tier that opens issues.

        Tier label names come from profile.yaml, so renaming a tier there adds
        the new label here; the old one is left in place rather than deleted,
        because issues already carry it.
        """
        defs = list(LABEL_DEFS)
        shades = ("06245e", "2a72d4", "9fc6ea", "d6e4f2")
        for i, name in enumerate(tier_labels or []):
            defs.append((name, shades[i % len(shades)], "Rule-score band"))
        for name, color, desc in defs:
            try:
                self._request("POST", f"/repos/{self.repo}/labels",
                              {"name": name, "color": color, "description": desc})
            except urllib.error.HTTPError as exc:
                if exc.code != 422:  # 422 = already exists
                    raise

    # -- issues ------------------------------------------------------------
    def radar_issues(self) -> list[dict]:
        out, page = [], 1
        while True:
            batch = self._request(
                "GET", f"/repos/{self.repo}/issues",
                params={"state": "all", "labels": "radar", "per_page": 100, "page": page},
            ) or []
            out += [i for i in batch if "pull_request" not in i]
            if len(batch) < 100:
                return out
            page += 1

    def create_issue(self, title: str, body: str, labels: list[str]) -> dict:
        return self._request("POST", f"/repos/{self.repo}/issues",
                             {"title": title, "body": body, "labels": labels})

    def update_body(self, number: int, body: str) -> None:
        self._request("PATCH", f"/repos/{self.repo}/issues/{number}", {"body": body})

    def update_labels(self, number: int, labels: list[str]) -> None:
        self._request("PUT", f"/repos/{self.repo}/issues/{number}/labels", {"labels": labels})


def match_block(job: dict) -> str:
    """Everything the radar knows about the vacancy, as one replaceable block.

    This is the half of the issue that goes stale: the deadline counts down,
    a re-score moves the number, a verdict arrives later than the issue did.
    So the scan rewrites it in place on every run rather than only at the
    moment the issue is opened.
    """
    v = job.get("llm") or {}
    lines = [
        MATCH_OPEN,
        "",
        f"**{job.get('employer', '?')}** — {job.get('city', '')}, {job.get('country', '')}",
        "",
        f"- **Deadline:** {(job.get('deadline') or 'not stated')[:10]}"
        + (f" (**{job['days_left']} days left**)" if job.get("days_left") is not None else ""),
        f"- **Posted:** {(job.get('posted') or '')[:10]}",
        f"- **Type:** {job.get('position_type')}",
        f"- **Topic match:** {job.get('score')}/100 — {job.get('tier_name', job.get('tier'))}"
        " (research content only; employer, country and supervisor score nothing)",
        f"- **Advert:** {job.get('url')}",
        "",
    ]

    flags = job.get("flags") or []
    if flags:
        mark = {"blocker": "⛔", "good": "✅"}
        lines += ["**Context (not scored):** " + " · ".join(
            f"{mark.get(f.get('severity'), '·')} {f['name']} ({', '.join(f['hits'])})"
            for f in flags), ""]

    if v:
        lines += [
            f"### Gemini verdict: `{v.get('verdict')}` — fit {v.get('fit')}/10",
            "",
            f"> {v.get('headline', '')}",
            "",
        ]
        if v.get("group_or_supervisor"):
            lines += [f"**Group / supervisor:** {v['group_or_supervisor']}", ""]
        for heading, key in (("Why it matches", "why_match"),
                             ("Why it might not", "why_not"),
                             ("Blockers", "blockers"),
                             ("To prepare", "prep")):
            items = v.get(key) or []
            if items:
                lines += [f"**{heading}**"] + [f"- {x}" for x in items] + [""]
    else:
        lines += ["_No LLM verdict for this one (below threshold, or the call failed)._", ""]

    # Each line says what was matched, where, and what the position of the
    # match did to the weight — the whole number is re-derivable from it.
    hits = [f"`{d['label']}` {d.get('applied', d['weight']):+g}"
            f" = {d['weight']:+d} × {d.get('factor', 1)}"
            f" ({d.get('note') or d.get('where') or ''}): {', '.join(d['hits'])}"
            for d in job.get("breakdown", []) if d.get("hits")]
    if hits:
        lines += ["<details><summary>Topic match breakdown</summary>", ""] \
                 + [f"- {h}" for h in hits] + ["", "</details>", ""]

    lines += [MATCH_CLOSE]
    return "\n".join(lines)


def with_match(body: str, block: str) -> str:
    """Replace the match block, leaving the checklist and the CV alone.

    Three shapes turn up. One already has the block. One was opened from the
    dashboard and has none, so the match goes directly under the id marker,
    above the checklist: it is the context for everything below it. And one
    was opened by a scan from before the markers existed, where the match ran
    unsplit from the marker to the decision footer — that whole stretch is
    what the block replaces, or the issue would show its match twice.
    """
    start, end = body.find(MATCH_OPEN), body.find(MATCH_CLOSE)
    if start != -1 and end > start:
        return body[:start] + block + body[end + len(MATCH_CLOSE):]

    head, sep, rest = body.partition("-->")
    if not sep:
        return f"{block}\n\n{body}"
    legacy = rest.find(FOOTER_HEAD)
    rest = rest[legacy:] if legacy != -1 else rest.lstrip("\n")
    return f"{head}{sep}\n\n{block}\n\n{rest}"


def issue_body(job: dict) -> str:
    return "\n".join([f"{MARKER}{job['id']} -->", "", match_block(job), "", FOOTER])


def decision_from(issue: dict) -> str:
    names = {l["name"] for l in issue.get("labels", []) if isinstance(l, dict)}
    for label in ("decision:applied", "decision:apply", "decision:skip", "decision:maybe"):
        if label in names:
            return DECISION_LABELS[label]
    return "skip" if issue.get("state") == "closed" else "new"


def job_id_of(issue: dict) -> str | None:
    body = issue.get("body") or ""
    if MARKER not in body:
        return None
    return body.split(MARKER, 1)[1].split("-->", 1)[0].strip()


def from_env() -> GitHub | None:
    repo, token = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
    return GitHub(repo, token) if repo and token else None
