"""Rule scoring against profile.yaml.

Deterministic and fully auditable: every point in the total is traceable to a
matched term, and the matched terms travel with the job into the dashboard.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone


def _norm(text: str) -> str:
    """Fold diacritics and punctuation so advert and profile terms line up."""
    text = unicodedata.normalize("NFKD", text or "").lower()
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9+\-/. ]+", " ", text))


def _hits(haystack: str, terms) -> list[str]:
    """Terms present as whole words/phrases, in profile order."""
    found = []
    for term in terms:
        t = _norm(term).strip()
        if not t:
            continue
        pattern = r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])"
        if re.search(pattern, haystack):
            found.append(term)
    return found


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


def _capped(groups, haystack: str, cap: int, kind: str) -> tuple[int, list[dict]]:
    total, detail = 0, []
    for group in groups:
        hits = _hits(haystack, group.get("terms", []))
        if not hits:
            continue
        weight = int(group.get("weight", 0))
        total += weight
        detail.append({
            "kind": kind,
            "label": group.get("label", kind),
            "weight": weight,
            "hits": hits[:6],
        })
    if cap is not None:
        total = max(-cap, min(cap, total))
    return total, detail


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
    """Return the job with score, tier, breakdown and gating flags attached."""
    title = _norm(job.get("title", ""))
    summary = _norm(job.get("summary", ""))
    body = _norm(job.get("body", ""))
    employer = _norm(job.get("employer", ""))
    country = _norm(job.get("country", ""))

    # Title and summary carry the signal; the body is long and dilutes it, so
    # topic terms count from title+summary+body but the title is doubled.
    topic_hay = f"{title} {title} {summary} {body}"

    breakdown: list[dict] = []

    topics = profile.get("topics", {})
    topic_total, topic_detail = _capped(
        topics.get("groups", []), topic_hay, topics.get("cap"), "topic")
    breakdown += topic_detail

    excl = profile.get("exclusions", {})
    excl_hits = _hits(f"{title} {summary}", excl.get("terms", []))
    excl_points = 0
    if excl_hits:
        excl_points = int(excl.get("penalty", -10))
        breakdown.append({
            "kind": "exclusion", "label": "off-field", "weight": excl_points,
            "hits": excl_hits[:6], "factor": 1.0,
        })

    # Topic gate. Without it a prestigious employer plus the right country adds
    # up to more than any amount of topic relevance, and a textile-composites
    # PhD at the home university outranks a robot-learning PhD abroad. So the
    # contextual points only count in full once the advert is actually on
    # topic; below that they are scaled down.
    scoring = profile.get("scoring", {})
    gate = int(scoring.get("topic_gate", 14))
    if topic_total >= gate:
        factor = 1.0
    elif topic_total >= gate / 2:
        factor = float(scoring.get("partial_factor", 0.5))
    else:
        factor = float(scoring.get("off_topic_factor", 0.15))

    # A named supervisor is specific enough to stand on its own; everything
    # else contextual rides on the gate.
    ungated = set(scoring.get("ungated_labels") or ["named-supervisor"])

    contextual = 0.0
    emp = profile.get("employers", {})
    ctry = profile.get("countries", {})
    bon = profile.get("bonuses", {})
    for groups, hay, cap, kind in (
        (emp.get("tiers", []), f" {employer} ", emp.get("cap"), "employer"),
        (ctry.get("tiers", []), f" {country} ", ctry.get("cap"), "country"),
        (bon.get("signals", []), topic_hay, bon.get("cap"), "bonus"),
    ):
        pts, det = _capped(groups, hay, cap, kind)
        for entry in det:
            f = 1.0 if entry["label"] in ungated else factor
            entry["factor"] = f
            entry["applied"] = round(entry["weight"] * f, 1)
        # Penalties (a language blocker) always count in full.
        contextual += sum(
            e["weight"] if e["weight"] < 0 else e["weight"] * (
                1.0 if e["label"] in ungated else factor)
            for e in det
        )
        breakdown += det

    for entry in topic_detail:
        entry["factor"] = 1.0
    total = int(round(topic_total + excl_points + contextual))
    breakdown.append({
        "kind": "gate", "label": "topic-gate", "weight": 0, "hits": [],
        "note": f"topic {topic_total} vs gate {gate} -> context x{factor:g}",
        "factor": factor,
    })

    total = max(0, total)

    tier, tier_name = tier_of(total, profile)

    left = days_left(job.get("deadline"), now)
    urgent_days = int(profile.get("hard_filters", {}).get("urgent_days", 14))

    job = dict(job)
    job["score"] = total
    job["tier"] = tier
    job["tier_name"] = tier_name
    job["breakdown"] = breakdown
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
