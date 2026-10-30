"""Turn an Academic Positions ad page into a structured record.

Every live ad carries a schema.org JobPosting JSON-LD block (title, datePosted,
validThrough, hiringOrganization, jobLocation) and the full prose body inside
`<div ... id="editor">`. Both are server-rendered, so no browser is needed.
"""
from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone

SITE = "https://en.academicpositions.se"
SITEMAP = f"{SITE}/sitemaps/en/jobs-0-xml"

_LD = re.compile(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
_LOC = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.S)
_TAG = re.compile(r"<[^>]+>")
_DROP = re.compile(r"<(script|style|svg|noscript)\b.*?</\1>", re.S | re.I)
_WS = re.compile(r"[ \t\r\f\v]+")
_BLANKS = re.compile(r"\n{3,}")
_EDITOR_OPEN = re.compile(r'<div[^>]*\bid="editor"[^>]*>', re.I)
_DIV = re.compile(r"<div\b|</div>", re.I)
_BLOCK_END = re.compile(r"</(p|div|li|h[1-6]|tr|section|ul|ol|table)>", re.I)
_ID_IN_URL = re.compile(r"/(\d+)/?$")


def sitemap_urls(xml: str) -> list[str]:
    """Job-ad URLs from the sitemap, rewritten onto the .se host.

    The sitemap lists academicpositions.com, which sits behind a Cloudflare
    challenge; en.academicpositions.se serves the identical pages without one.
    """
    out, seen = [], set()
    for loc in _LOC.findall(xml):
        url = html.unescape(loc).strip()
        if "/ad/" not in url:
            continue
        url = re.sub(r"^https?://(?:www\.)?academicpositions\.com", SITE, url)
        if not url.startswith(SITE):
            continue
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def job_id(url: str) -> str:
    """Stable id = the numeric ad id at the end of the URL."""
    m = _ID_IN_URL.search(url)
    return m.group(1) if m else url.rsplit("/", 1)[-1]


def _json_ld(page: str) -> dict | None:
    for block in _LD.findall(page):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                return item
    return None


def _text(fragment: str) -> str:
    fragment = _DROP.sub(" ", fragment)
    fragment = _BLOCK_END.sub("\n", fragment)
    fragment = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
    text = html.unescape(_TAG.sub(" ", fragment))
    text = _WS.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return _BLANKS.sub("\n\n", text).strip()


def _body(page: str) -> str:
    """Prose inside the ad's editor div, found by balancing <div> tags."""
    m = _EDITOR_OPEN.search(page)
    if not m:
        return ""
    depth, i = 1, m.end()
    for tag in _DIV.finditer(page, m.end()):
        depth += 1 if tag.group(0).lower().startswith("<div") else -1
        if depth == 0:
            i = tag.start()
            break
    else:
        i = min(len(page), m.end() + 120_000)
    return _text(page[m.end() : i])


def _iso(value) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value).astimezone(timezone.utc).isoformat()
    except ValueError:
        return value


def _flatten_org(node) -> tuple[str, str]:
    if isinstance(node, dict):
        return str(node.get("name") or ""), str(node.get("description") or "")
    return (str(node) if node else ""), ""


def _flatten_place(node) -> tuple[str, str]:
    if not isinstance(node, dict):
        return "", ""
    addr = node.get("address")
    if not isinstance(addr, dict):
        return "", ""
    return str(addr.get("addressLocality") or ""), str(addr.get("addressCountry") or "")


def parse_ad(url: str, page: str) -> dict | None:
    """Structured record, or None if the page carries no JobPosting."""
    ld = _json_ld(page)
    if not ld:
        return None

    employer, employer_blurb = _flatten_org(ld.get("hiringOrganization"))
    city, country = _flatten_place(ld.get("jobLocation"))
    body = _body(page)

    return {
        "id": job_id(url),
        "url": url,
        "title": html.unescape(str(ld.get("title") or "")).strip(),
        "summary": html.unescape(str(ld.get("description") or "")).strip(),
        "body": body,
        "employer": employer.strip(),
        "employer_blurb": employer_blurb.strip(),
        "city": city.strip(),
        "country": country.strip(),
        "posted": _iso(ld.get("datePosted")),
        "deadline": _iso(ld.get("validThrough")),
        "employment_type": str(ld.get("employmentType") or ""),
        "slug": url.rsplit("/", 2)[-2] if url.count("/") > 3 else "",
    }
