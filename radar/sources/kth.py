"""KTH's own vacancy listing — the authoritative source for the target school.

kth.se/lediga-jobb server-renders the whole open list on one page (24 adverts
on 2026-10-30), and each advert page is plain HTML: an <h1>, a prose body in
`div.job-details-content`, and a `div.details-list` run of <b>label</b><p>value
</p> pairs carrying the deadline, the start date, the location and — unlike the
aggregator — the hiring contact's name and email.

There is no JSON-LD here, so the fields are read off those pairs.
"""
from __future__ import annotations

import html
import re

from .. import http, parse

NAME = "kth"
LABEL = "KTH (own listing)"
SITE = "https://www.kth.se"
LIST_URL = f"{SITE}/lediga-jobb?l=en"
EMPLOYER = "KTH Royal Institute of Technology"

_LINK = re.compile(r'/lediga-jobb/(\d{4,})[^"]*"[^>]*>\s*([^<]{6,160}?)\s*<')
_H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S)
_BODY = re.compile(r'<div[^>]*class="[^"]*job-details-content[^"]*"[^>]*>', re.I)
_DETAIL_OPEN = re.compile(r'<div[^>]*class="[^"]*details-list[^"]*"[^>]*>', re.I)
_LABEL = re.compile(r"<b>(.*?)</b>(.*)", re.S | re.I)


def ad_url(job_id: str) -> str:
    return f"{SITE}/lediga-jobb/{job_id}?l=en"


def list_ads() -> list[dict]:
    page = http.get(LIST_URL)
    out, seen = [], set()
    for job_id, title in _LINK.findall(page):
        if job_id in seen:
            continue
        seen.add(job_id)
        title = html.unescape(title).strip().lstrip(":").strip()
        out.append({"id": job_id, "url": ad_url(job_id), "title": title})
    return out


def _details(page: str) -> dict[str, str]:
    """The <b>label</b><p>value</p> pairs, read with balanced <div> walks.

    The Contact cell nests its own divs for the name and the mailto link, so a
    flat non-greedy regex stops at the inner </div> and drops the email.
    """
    out, pos = {}, 0
    while True:
        m = _DETAIL_OPEN.search(page, pos)
        if not m:
            return out
        inner = parse.block(page[m.start():], _DETAIL_OPEN)
        pos = m.end()
        pair = _LABEL.match(inner.strip())
        if not pair:
            continue
        key = parse.clean_text(pair.group(1)).strip().lower()
        if not key:
            continue
        value = parse.clean_text(pair.group(2)).replace("\n", " ").strip()
        if key == "contact":
            # Keep the mailto address, which clean_text would throw away.
            mails = re.findall(r"mailto:([^\"\'>]+)", pair.group(2))
            extra = " ".join(m for m in mails if m not in value)
            value = f"{value} {extra}".strip()
        out[key] = value


def fetch(ad: dict) -> dict | None:
    page = http.get(ad["url"])
    m = _H1.search(page)
    title = parse.clean_text(m.group(1)) if m else ad.get("title", "")
    title = title.strip().lstrip(":").strip()
    if not title:
        return None

    d = _details(page)
    body = parse.clean_text(parse.block(page, _BODY))

    return parse.record(
        id=ad["id"],
        url=ad["url"],
        source=NAME,
        title=title,
        # KTH writes no abstract, so the opening of the advert stands in for one.
        summary=" ".join(body.split("\n")[:3])[:600],
        body=body,
        employer=EMPLOYER,
        city=d.get("location", "Stockholm"),
        country=d.get("country", "Sweden"),
        posted=parse.iso(d.get("published")),
        deadline=parse.iso(d.get("last application date")),
        employment_type=d.get("type of employment", ""),
        contact=d.get("contact", ""),
        department=d.get("reference number", ""),
    )
