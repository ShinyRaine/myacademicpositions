"""Shared HTML toolkit for the source adapters.

Site-specific logic lives in radar/sources/*.py; what is here is only the
machinery every source needs: pulling a balanced block out of a page, turning
a fragment into readable prose, reading JSON-LD, and normalising dates.
"""
from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone

_LD = re.compile(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
_TAG = re.compile(r"<[^>]+>")
_DROP = re.compile(r"<(script|style|svg|noscript)\b.*?</\1>", re.S | re.I)
_WS = re.compile(r"[ \t\r\f\v]+")
_BLANKS = re.compile(r"\n{3,}")
_BLOCK_END = re.compile(r"</(p|div|li|h[1-6]|tr|section|ul|ol|table)>", re.I)
_BR = re.compile(r"<br\s*/?>", re.I)

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def clean_text(fragment: str) -> str:
    """HTML fragment -> readable prose, with block tags becoming newlines."""
    fragment = _DROP.sub(" ", fragment)
    fragment = _BR.sub("\n", _BLOCK_END.sub("\n", fragment))
    text = html.unescape(_TAG.sub(" ", fragment))
    text = _WS.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return _BLANKS.sub("\n\n", text).strip()


def block(page: str, open_tag: re.Pattern, tag: str = "div", limit: int = 150_000) -> str:
    """Inner HTML of the element whose opening tag matches `open_tag`.

    Walks opening and closing tags to find the real end, so a container with
    nested divs (which all three sites use) is not cut short at the first
    </div>.
    """
    m = open_tag.search(page)
    if not m:
        return ""
    pair = re.compile(rf"<{tag}\b|</{tag}>", re.I)
    depth, end = 1, min(len(page), m.end() + limit)
    for t in pair.finditer(page, m.end()):
        depth += 1 if t.group(0).lower().startswith(f"<{tag}") else -1
        if depth == 0:
            end = t.start()
            break
    return page[m.end():end]


def json_ld(page: str, want: str) -> dict | None:
    """First JSON-LD object of the given @type."""
    for raw in _LD.findall(page):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for item in (data if isinstance(data, list) else [data]):
            if isinstance(item, dict) and item.get("@type") == want:
                return item
    return None


def iso(value) -> str | None:
    """ISO-8601 in UTC from whatever the site printed, or None."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    text = str(value).strip()
    if not text:
        return None

    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        dt = None
    if dt is not None:
        if dt.tzinfo is None:
            # A bare date or a naive timestamp is the site's own wall clock, not
            # this machine's. Converting it with astimezone() would shift a
            # deadline of 2026-11-27 back into 2026-11-26.
            if len(text) <= 10:
                dt = dt.replace(hour=23, minute=59, second=59)
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()

    # 2026-11-27 embedded in a longer string
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        y, mo, d = (int(x) for x in m.groups())
        return datetime(y, mo, d, 23, 59, 59, tzinfo=timezone.utc).isoformat()

    # KTH prints 30.Oct.2026
    m = re.search(r"(\d{1,2})[./ ]([A-Za-z]{3,})[./ ](\d{4})", text)
    if m and m.group(2)[:3].lower() in MONTHS:
        d, mo, y = int(m.group(1)), MONTHS[m.group(2)[:3].lower()], int(m.group(3))
        return datetime(y, mo, d, 23, 59, 59, tzinfo=timezone.utc).isoformat()

    # Chalmers writes "November 27, 2026" in the prose
    m = re.search(r"([A-Za-z]{3,})\s+(\d{1,2}),?\s+(\d{4})", text)
    if m and m.group(1)[:3].lower() in MONTHS:
        mo, d, y = MONTHS[m.group(1)[:3].lower()], int(m.group(2)), int(m.group(3))
        return datetime(y, mo, d, 23, 59, 59, tzinfo=timezone.utc).isoformat()

    return None


def record(**kw) -> dict:
    """A job record with every field the scorer expects present."""
    base = {
        "id": "", "url": "", "title": "", "summary": "", "body": "",
        "employer": "", "employer_blurb": "", "city": "", "country": "",
        "posted": None, "deadline": None, "employment_type": "",
        "contact": "", "department": "", "source": "",
    }
    base.update(kw)
    return base
