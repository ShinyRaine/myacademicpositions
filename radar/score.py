"""Topic scoring against profile.yaml.

The number on a job is a *content* match and nothing else: how much of the
advert's research subject overlaps the candidate's. Which university posted it,
which country it sits in, whose name is on it and how it is funded add no
points — they ride along as flags so the dashboard can show and filter on them.
Letting them score is what put a Chalmers web-security PhD (+18 home, +10
Sweden) above half the robotics adverts on the board.

Content evidence is weighted by *where* a term appears. An advert says what it
is about in its title and its opening summary; a word that occurs once in
twelve thousand characters of body is usually the department boilerplate
listing everything the faculty does, and must not count like a subject.

Every point is traceable to a matched term and the field it matched in, and
the matched terms travel with the job into the dashboard.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone


def _norm(text: str) -> str:
    """Fold diacritics and punctuation so advert and profile terms line up.

    Hyphens and slashes become spaces on both sides, so how an advert chose to
    write it stops mattering: `dynamical system` now also matches
    "dynamical-system", and `force/torque` matches "force-torque".
    """
    text = unicodedata.normalize("NFKD", text or "").lower()
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9+. ]+", " ", text.replace("-", " ").replace("/", " "))
    return re.sub(r"\s+", " ", text)


def _pattern(term: str, prefix: bool = False) -> re.Pattern | None:
    """Whole-word matcher for a profile term.

    Topic terms are written in the singular, so a trailing plural is allowed:
    without it `robot` missed "Autonomous Robots" and `precondition` missed
    "preconditions". Short terms and ones already ending in -s are left alone,
    so `ros` does not match "roses".

    Exclusion terms are written as stems (`geolog`, `pharmac`, `immunolog`),
    which a closing word boundary can never match; they match as prefixes.
    """
    t = _norm(term).strip()
    if not t:
        return None
    tail = "" if prefix else r"(?![a-z0-9])"
    if not prefix and len(t) >= 4 and t[-1].isalpha() and t[-1] != "s":
        tail = r"(?:e?s)?" + tail
    return re.compile(r"(?<![a-z0-9])" + re.escape(t) + tail)


def _scan(haystack: str, terms, prefix: bool = False) -> dict:
    """What a group of terms found in one field.

    Counting is done over merged match spans, not over profile entries: the
    list holds overlapping terms on purpose (`robotic` and `robotics`, now
    also `robot` + the plural rule), and one occurrence of "robotics" in the
    text must not read as three separate pieces of vocabulary.
    """
    spans: list[tuple[int, int]] = []
    hit_terms: list[str] = []
    for term in terms:
        pat = _pattern(term, prefix)
        if pat is None:
            continue
        found = [(m.start(), m.end()) for m in pat.finditer(haystack)]
        if found:
            hit_terms.append(term)
            spans += found

    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    return {
        "terms": hit_terms,
        "count": len(merged),
        "distinct": len({haystack[s:e] for s, e in merged}),
    }


def _hits(haystack: str, terms, prefix: bool = False) -> list[str]:
    return _scan(haystack, terms, prefix)["terms"]


def days_left(deadline: str | None, now: datetime | None = None) -> int | None:
    if not deadline:
        return None
    now = now or datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(deadline)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (dt - now).days


# Fallbacks, so a profile that has not been updated still scores sensibly.
EVIDENCE = {
    "title": 1.0,
    "summary": 0.75,
    "body_strong": 0.5,
    "body_repeated": 0.3,
    "body_once": 0.15,
}


def _evidence(title, summary, body, table) -> tuple[float, str, str]:
    """How strongly the advert claims this group as its own subject.

    Returns (factor, field, why) where why is readable in the breakdown.
    """
    if title["count"]:
        return table["title"], "title", "标题"
    if summary["count"]:
        return table["summary"], "summary", "摘要"
    if not body["count"]:
        return 0.0, "", ""
    if body["distinct"] >= 2 or body["count"] >= 3:
        return (table["body_strong"], "body",
                f"正文 {body['distinct']} 种说法/共 {body['count']} 次")
    if body["count"] >= 2:
        return table["body_repeated"], "body", "正文 2 次"
    return table["body_once"], "body", "正文仅 1 次"


def _evidence_table(profile: dict) -> dict:
    table = dict(EVIDENCE)
    table.update(profile.get("scoring", {}).get("evidence", {}) or {})
    return {k: float(v) for k, v in table.items()}


def score_topics(fields: dict, profile: dict) -> tuple[int, list[dict]]:
    """Content score and its breakdown. Nothing here knows about employers."""
    table = _evidence_table(profile)
    topics = profile.get("topics", {})
    floor = float(profile.get("scoring", {}).get("context_floor", 0.4))

    # First pass: how strongly does each group look like this advert's subject?
    found: dict[str, dict] = {}
    for group in topics.get("groups", []):
        terms = group.get("terms", [])
        in_title = _scan(fields["title"], terms)
        in_summary = _scan(fields["summary"], terms)
        in_body = _scan(fields["body"], terms)
        factor, field, why = _evidence(in_title, in_summary, in_body, table)
        label = group.get("label", "topic")
        # Show the terms from the field the evidence came from, not every
        # stray body mention: that is what the score was read off.
        source = {"title": in_title, "summary": in_summary, "body": in_body}.get(field)
        found[label] = {
            "group": group, "factor": factor, "field": field, "note": why,
            "hits": source["terms"] if source else [],
        }

    # Second pass: a specialised vocabulary only means the candidate's subject
    # inside the right field. "Dynamical systems" in a nonlinear-control
    # advert, "formal methods" in a web-security one and "precondition" in a
    # compiler one are different subjects that happen to share a word, and
    # before this they each bought a full core group. A group with `requires`
    # is scaled by how strongly the advert is about the required field, and
    # keeps only `context_floor` of its weight when the field is absent.
    total, breakdown = 0.0, []
    for label, hit in found.items():
        factor = hit["factor"]
        if not factor:
            continue
        group = hit["group"]
        note = hit["note"]
        needs = group.get("requires")
        if needs:
            ctx = found.get(needs, {}).get("factor", 0.0)
            scale = max(ctx, floor)
            if scale < 1.0:
                note = f"{note}；{needs} 语境 ×{scale:g}"
            factor *= scale
        weight = int(group.get("weight", 0))
        applied = weight * factor
        total += applied
        breakdown.append({
            "kind": "topic",
            "label": label,
            "weight": weight,
            "factor": round(factor, 2),
            "applied": round(applied, 1),
            "where": hit["field"],
            "note": note,
            "hits": hit["hits"][:6],
        })

    cap = topics.get("cap")
    if cap is not None:
        total = min(float(cap), total)
    return int(round(total)), breakdown


def score_exclusions(fields: dict, profile: dict) -> tuple[int, list[dict]]:
    """Off-field subjects. Judged from title and summary: that is where an
    advert names its subject, and a body mention is as likely to be boilerplate
    here as anywhere else."""
    excl = profile.get("exclusions", {})
    hits = _hits(f"{fields['title']} {fields['summary']}",
                 excl.get("terms", []), prefix=True)
    if not hits:
        return 0, []
    points = int(excl.get("penalty", -25))
    return points, [{
        "kind": "exclusion", "label": "off-field", "weight": points,
        "applied": points, "factor": 1.0, "where": "title", "hits": hits[:6],
    }]


def context_flags(fields: dict, profile: dict) -> list[dict]:
    """Facts worth seeing and filtering on that are *not* topic match:
    the university, the country, a named supervisor, the funding wording, a
    language requirement. None of them move the score."""
    out = []
    for flag in profile.get("context", {}).get("flags", []) or []:
        field = flag.get("field", "text")
        hay = fields.get(field) or fields["text"]
        hits = _hits(hay, flag.get("terms", []))
        if not hits:
            continue
        out.append({
            "label": flag.get("label", "flag"),
            "name": flag.get("name", flag.get("label", "flag")),
            "severity": flag.get("severity", "note"),
            "hits": hits[:6],
        })
    return out


# A/B/C/D stay as the internal sort keys; the name is what anyone reads.
TIER_KEYS = ("A", "B", "C", "D")


def tier_table(profile: dict) -> dict:
    """{key: {min, name, github}} with defaults filled in."""
    raw = profile.get("tiers", {}) or {}
    out = {}
    for key in TIER_KEYS:
        entry = raw.get(key)
        if isinstance(entry, dict):
            out[key] = {"min": int(entry.get("min", 0)),
                        "name": str(entry.get("name", key)),
                        "github": entry.get("github")}
        else:  # a bare threshold, the older shape
            out[key] = {"min": int(entry) if entry is not None else 0,
                        "name": key, "github": None}
    return out


def tier_of(total: int, profile: dict) -> tuple[str, str]:
    table = tier_table(profile)
    for key in TIER_KEYS:
        if total >= table[key]["min"]:
            return key, table[key]["name"]
    return "D", table["D"]["name"]


def score_job(job: dict, profile: dict, now: datetime | None = None) -> dict:
    """Return the job with its topic score, tier, breakdown and flags."""
    fields = {
        "title": _norm(job.get("title", "")),
        "summary": _norm(job.get("summary", "")),
        "body": _norm(job.get("body", "")),
        "employer": _norm(job.get("employer", "")),
        "country": _norm(job.get("country", "")),
    }
    fields["text"] = f"{fields['title']} {fields['summary']} {fields['body']}"

    topic_total, breakdown = score_topics(fields, profile)
    excl_points, excl_detail = score_exclusions(fields, profile)
    breakdown += excl_detail

    total = max(0, topic_total + excl_points)
    tier, tier_name = tier_of(total, profile)

    left = days_left(job.get("deadline"), now)
    urgent_days = int(profile.get("hard_filters", {}).get("urgent_days", 14))

    job = dict(job)
    job["score"] = total
    job["topic_score"] = topic_total
    job["tier"] = tier
    job["tier_name"] = tier_name
    job["breakdown"] = breakdown
    job["flags"] = context_flags(fields, profile)
    job["days_left"] = left
    job["urgent"] = left is not None and 0 <= left <= urgent_days
    job["expired"] = left is not None and left < 0
    return job


def passes_hard_filters(job: dict, profile: dict) -> tuple[bool, str]:
    hf = profile.get("hard_filters", {})
    allowed = set(hf.get("position_types") or [])
    if allowed and job.get("position_type") not in allowed:
        return False, f"position_type={job.get('position_type')}"
    if hf.get("drop_if_expired", True) and job.get("expired"):
        return False, "deadline passed"
    return True, ""
