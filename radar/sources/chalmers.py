"""Chalmers' own vacancy listing, served by ReachMee.

chalmers.se embeds a ReachMee iframe; the parameters come from the customer
script at web103.reachmee.com/customerjs/I003-304-5.js. The list page is the
useful part: each row already carries the title, an ISO application deadline,
the department and ReachMee's own position category ("PhD Student Positions",
"Post Doctoral Positions"), so the position type is known before any advert is
fetched. The advert page adds the prose body in `div.jobad-body`.

If Chalmers ever rotates the validator, LIST_URL stops returning rows and the
scan logs the source as empty rather than failing; re-read it from the script.
"""
from __future__ import annotations

import html
import re

from .. import http, parse

NAME = "chalmers"
LABEL = "Chalmers (own listing)"
BASE = "https://web103.reachmee.com/ext/I003/304"
VALIDATOR = "a72aeedd63ec10de71e46f8d91d0d57c"
LIST_URL = f"{BASE}/main?site=5&validator={VALIDATOR}&lang=UK"
EMPLOYER = "Chalmers University of Technology"
REFERER = "https://www.chalmers.se/"

_BODY = re.compile(r'<div[^>]*class="[^"]*jobad-body[^"]*"[^>]*>', re.I)
_H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S)

# The list is a plain HTML table, one <tr> per advert:
#   <td>ref</td><td><a ...job_id=NNN>title</a></td>
#   <td><span>Application deadline:</span><span ...>YYYY-MM-DD</span> dd/mm/yyyy</td>
#   <td>department</td><td>category</td>
_JOB_ID = re.compile(r"job_id=(\d+)")
_TITLE = re.compile(r"job_id=\d+[^>]*>\s*(.*?)\s*</a>", re.S)
_DEADLINE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S | re.I)

# ReachMee's category names, mapped onto our own position vocabulary.
CATEGORY = {
    "phd student positions": "phd",
    "post doctoral positions": "postdoc",
    "technical and administrative staff": "other",
    "teachers and researchers": "professor",
}


def ad_url(job_id: str) -> str:
    return f"{BASE}/job?site=5&validator={VALIDATOR}&lang=UK&job_id={job_id}"


def list_ads() -> list[dict]:
    page = http.get(LIST_URL, headers={"Referer": REFERER})
    out, seen = [], set()
    for row in re.split(r"<tr\b", page, flags=re.I)[1:]:
        m = _JOB_ID.search(row)
        if not m or m.group(1) in seen:
            continue
        seen.add(m.group(1))
        cells = [parse.clean_text(c).replace("\n", " ").strip() for c in _CELL.findall(row)]
        title = _TITLE.search(row)
        deadline = _DEADLINE.search(row)
        # The department and category are the last two cells of the row.
        dept = cells[-2] if len(cells) >= 2 else ""
        category = cells[-1] if cells else ""
        out.append({
            "id": m.group(1),
            "url": ad_url(m.group(1)),
            "title": html.unescape(parse.clean_text(title.group(1))).strip() if title else "",
            "deadline": deadline.group(1) if deadline else None,
            "department": dept,
            "position_hint": CATEGORY.get(category.lower(), ""),
        })
    return out


def fetch(ad: dict) -> dict | None:
    page = http.get(ad["url"], headers={"Referer": REFERER})
    m = _H1.search(page)
    title = parse.clean_text(m.group(1)).strip() if m else ad.get("title", "")
    if not title:
        return None

    body = parse.clean_text(parse.block(page, _BODY))
    deadline = parse.iso(ad.get("deadline"))
    if not deadline:
        # The list row is the reliable place for this; the prose only says
        # "no later than November 27, 2026".
        m2 = re.search(r"no later than ([A-Za-z]+ \d{1,2},? \d{4})", body)
        deadline = parse.iso(m2.group(1)) if m2 else None

    return parse.record(
        id=ad["id"],
        url=ad["url"],
        source=NAME,
        title=title,
        summary=" ".join(body.split("\n")[:3])[:600],
        body=body,
        employer=EMPLOYER,
        city="Gothenburg",
        country="Sweden",
        deadline=deadline,
        department=ad.get("department", ""),
    )
