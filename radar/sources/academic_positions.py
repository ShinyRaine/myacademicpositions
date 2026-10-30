"""Academic Positions — a paid aggregator, broad but uneven.

The jobs sitemap lists every live advert on one page, so new postings are a set
difference rather than a crawl. Every advert page carries a schema.org
JobPosting block and the prose body, both server-rendered.

Coverage is not complete: it is whatever each employer pays to list. Measured
2026-10-30, it carried all 20 of KTH's open PhD and research-assistant adverts
but only 1 from Uppsala, 1 from Linköping and none from Umeå, and it has no
TU Delft, TUM, KIT, Edinburgh, IIT or Idiap at all. Hence the direct sources.
"""
from __future__ import annotations

import re

from .. import http, parse

NAME = "ap"
LABEL = "Academic Positions"
SITE = "https://en.academicpositions.se"
SITEMAP = f"{SITE}/sitemaps/en/jobs-0-xml"

_LOC = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.S)
_ID_IN_URL = re.compile(r"/(\d+)/?$")
_EDITOR = re.compile(r'<div[^>]*\bid="editor"[^>]*>', re.I)


def sitemap_urls(xml: str) -> list[str]:
    """Advert URLs, rewritten onto the .se host.

    The sitemap names academicpositions.com, which answers a plain GET with a
    Cloudflare challenge; en.academicpositions.se serves the same pages without
    one.
    """
    import html as _html

    out, seen = [], set()
    for loc in _LOC.findall(xml):
        url = _html.unescape(loc).strip()
        if "/ad/" not in url:
            continue
        url = re.sub(r"^https?://(?:www\.)?academicpositions\.com", SITE, url)
        if url.startswith(SITE) and url not in seen:
            seen.add(url)
            out.append(url)
    return out


def job_id(url: str) -> str:
    m = _ID_IN_URL.search(url)
    return m.group(1) if m else url.rsplit("/", 1)[-1]


def list_ads() -> list[dict]:
    urls = sitemap_urls(http.get(SITEMAP))
    out = []
    for url in urls:
        # The slug is the advert title, which is enough to rule out a position
        # type before spending a request on it.
        slug = url.rsplit("/", 2)[-2] if url.count("/") > 3 else ""
        out.append({"id": job_id(url), "url": url, "title": slug.replace("-", " ")})
    return out


def _org(node) -> tuple[str, str]:
    if isinstance(node, dict):
        return str(node.get("name") or ""), str(node.get("description") or "")
    return (str(node) if node else ""), ""


def _place(node) -> tuple[str, str]:
    addr = node.get("address") if isinstance(node, dict) else None
    if not isinstance(addr, dict):
        return "", ""
    return str(addr.get("addressLocality") or ""), str(addr.get("addressCountry") or "")


def fetch(ad: dict) -> dict | None:
    import html as _html

    page = http.get(ad["url"])
    ld = parse.json_ld(page, "JobPosting")
    if not ld:
        return None

    employer, blurb = _org(ld.get("hiringOrganization"))
    city, country = _place(ld.get("jobLocation"))
    return parse.record(
        id=ad["id"],
        url=ad["url"],
        source=NAME,
        title=_html.unescape(str(ld.get("title") or "")).strip(),
        summary=_html.unescape(str(ld.get("description") or "")).strip(),
        body=parse.clean_text(parse.block(page, _EDITOR)),
        employer=employer.strip(),
        employer_blurb=blurb.strip(),
        city=city.strip(),
        country=country.strip(),
        posted=parse.iso(ld.get("datePosted")),
        deadline=parse.iso(ld.get("validThrough")),
        employment_type=str(ld.get("employmentType") or ""),
    )
