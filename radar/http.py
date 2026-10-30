"""Polite HTTP with retries. Standard library only."""
from __future__ import annotations

import gzip
import random
import time
import urllib.error
import urllib.request
import zlib

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# robots.txt gives the generic agent Allow: / with no crawl-delay, but several
# named bots get 4-36 s. We stay well inside that band on purpose.
MIN_DELAY = 1.2

_last_request = 0.0


def _decode(resp) -> str:
    raw = resp.read()
    enc = (resp.headers.get("Content-Encoding") or "").lower()
    if enc == "gzip":
        raw = gzip.decompress(raw)
    elif enc == "deflate":
        raw = zlib.decompress(raw, -zlib.MAX_WBITS)
    charset = resp.headers.get_content_charset() or "utf-8"
    return raw.decode(charset, errors="replace")


def get(url: str, *, timeout: int = 40, retries: int = 3,
        headers: dict | None = None) -> str:
    """Fetch a URL as text, throttled and retried. Raises on final failure."""
    global _last_request

    last_err: Exception | None = None
    for attempt in range(retries):
        gap = time.monotonic() - _last_request
        if gap < MIN_DELAY:
            time.sleep(MIN_DELAY - gap)

        hdrs = {
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "close",
        }
        hdrs.update(headers or {})
        req = urllib.request.Request(url, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                _last_request = time.monotonic()
                return _decode(resp)
        except urllib.error.HTTPError as exc:
            _last_request = time.monotonic()
            last_err = exc
            if exc.code in (404, 410):
                raise  # gone for good, no point retrying
        except Exception as exc:  # noqa: BLE001 - network layer, anything goes
            _last_request = time.monotonic()
            last_err = exc

        time.sleep((2**attempt) + random.random())

    raise RuntimeError(f"GET failed after {retries} tries: {url}: {last_err}")
