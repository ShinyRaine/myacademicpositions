"""Gemini verdict on a shortlisted job.

Rules rank; the LLM decides. It gets the candidate profile and the ad body and
must answer in a fixed JSON shape, so the verdict is comparable across jobs and
can be rendered as a table. Failures are non-fatal: a job without a verdict
still shows up with its rule score.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["apply", "maybe", "skip"]},
        "fit": {"type": "integer"},
        "headline": {"type": "string"},
        "why_match": {"type": "array", "items": {"type": "string"}},
        "why_not": {"type": "array", "items": {"type": "string"}},
        "blockers": {"type": "array", "items": {"type": "string"}},
        "prep": {"type": "array", "items": {"type": "string"}},
        "group_or_supervisor": {"type": "string"},
    },
    "required": ["verdict", "fit", "headline", "why_match", "why_not", "blockers", "prep"],
}

SYSTEM = """You screen academic vacancies for one specific candidate.

You are blunt and evidence-bound. Judge only from the advert text given to you.
Never invent a supervisor, a funding source, a deadline or a requirement that
the advert does not state. If the advert is vague about the research topic, say
so in why_not rather than filling the gap with a guess.

fit is 0-10 for how well the candidate's actual research background matches the
advert's actual topic. Be strict: a generic "PhD in computer science" advert
with no robotics content is at most a 4, however prestigious the university.

verdict:
  apply - topic and requirements line up; worth the effort of a real application
  maybe - plausible but something important is unclear or only partly matching
  skip  - off-topic, ineligible, or the deadline makes it pointless

blockers lists only hard problems: a language requirement the candidate does
not meet, a start date before they are available, a degree they do not hold, a
nationality or residency restriction. Empty list if there are none.

why_match and why_not: at most 3 short items each, each naming something
concrete from the advert. prep: at most 3 items, each a specific thing the
candidate would need to prepare or find out before applying.

group_or_supervisor: the named group, lab or supervisor if the advert states
one, otherwise an empty string.

Answer in English."""


def _key() -> str | None:
    for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        if os.environ.get(var):
            return os.environ[var]
    path = os.path.expanduser("~/.config/gemini/api_key")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    return None


def _prompt(job: dict, profile: dict) -> str:
    cand = profile.get("candidate", {})
    cons = cand.get("constraints", {})
    body = (job.get("body") or job.get("summary") or "")
    limit = int(profile.get("llm", {}).get("body_chars", 9000))
    return f"""CANDIDATE PROFILE
{cand.get('summary', '').strip()}

Hard constraints:
- Earliest possible start: {cons.get('earliest_start', 'unspecified')}
- Degree held at start: {cons.get('degree_at_start', 'unspecified')}
- Needs visa sponsorship: {cons.get('needs_visa_sponsorship', 'unknown')}
- A requirement to speak Swedish is a blocker: {cons.get('swedish_required_is_blocker', 'unknown')}

ADVERT
Title: {job.get('title')}
Employer: {job.get('employer')}
Location: {job.get('city')}, {job.get('country')}
Posted: {(job.get('posted') or '')[:10]}
Application deadline: {(job.get('deadline') or 'not stated')[:10]} ({job.get('days_left')} days from today)
Position type as classified: {job.get('position_type')}

Advert text:
\"\"\"
{body[:limit]}
\"\"\"

Screen this advert for the candidate."""


def _call(model: str, prompt: str, key: str, timeout: int = 120) -> dict:
    payload = {
        "systemInstruction": {"parts": [{"text": SYSTEM}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
            "responseSchema": SCHEMA,
            "maxOutputTokens": 2048,
        },
    }
    req = urllib.request.Request(
        API.format(model=model),
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        raise RuntimeError(f"empty response ({data.get('candidates', [{}])[0].get('finishReason')})")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise
        return json.loads(m.group(0))


def judge(job: dict, profile: dict, key: str | None = None) -> dict | None:
    """Verdict dict, or None if the call failed (never raises)."""
    key = key or _key()
    if not key:
        return None
    model = profile.get("llm", {}).get("model", "gemini-3.5-flash")

    for attempt in range(3):
        try:
            raw = _call(model, _prompt(job, profile), key)
            return {
                "verdict": str(raw.get("verdict", "maybe")).lower(),
                "fit": max(0, min(10, int(raw.get("fit", 0)))),
                "headline": str(raw.get("headline", "")).strip(),
                "why_match": [str(x) for x in (raw.get("why_match") or [])][:3],
                "why_not": [str(x) for x in (raw.get("why_not") or [])][:3],
                "blockers": [str(x) for x in (raw.get("blockers") or [])][:4],
                "prep": [str(x) for x in (raw.get("prep") or [])][:3],
                "group_or_supervisor": str(raw.get("group_or_supervisor", "")).strip(),
                "model": model,
            }
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:300]
            print(f"    ! gemini HTTP {exc.code}: {body}")
            if exc.code == 429:
                return None  # free-tier exhausted; stop burning wall time
            time.sleep(2 ** attempt)
        except Exception as exc:  # noqa: BLE001
            print(f"    ! gemini: {exc}")
            time.sleep(2 ** attempt)
    return None
