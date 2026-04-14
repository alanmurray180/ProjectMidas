"""Scrape the World Gold Council's monthly ETF commentary page.

The underlying xlsx with global holdings/flows numbers is gated behind
a WGC user account (``DU1: You must be logged in``) so we no longer
mirror it.  Instead we grab the publicly-accessible research write-up
for the most recent month and surface its title + lead paragraph on
the dashboard, with a link back to gold.org for the full commentary.

The research URL pattern is::

    https://www.gold.org/goldhub/research/gold-etfs-holdings-and-flows/<YYYY>/<MM>

We walk backwards from the current month until we find a published
page (the latest report usually lags a week or two behind the calendar
month end).
"""

from __future__ import annotations

import html
import logging
import re
from datetime import date
from typing import Optional

import httpx

log = logging.getLogger(__name__)

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Upgrade-Insecure-Requests": "1",
}

_GOLDHUB_LANDING = "https://www.gold.org/goldhub"
_RESEARCH_URL = (
    "https://www.gold.org/goldhub/research/"
    "gold-etfs-holdings-and-flows/{year}/{month:02d}"
)

_META_CONTENT = re.compile(
    r'<meta\s+[^>]*?(?:property|name)=["\'](?P<key>[^"\']+)["\']'
    r'\s+content=["\'](?P<val>[^"\']*)["\']',
    re.IGNORECASE,
)
_TITLE_TAG = re.compile(r"<title>(?P<t>.*?)</title>", re.IGNORECASE | re.DOTALL)


def _new_client() -> httpx.Client:
    try:
        return httpx.Client(
            headers=_BROWSER_HEADERS,
            http2=True,
            timeout=30,
            follow_redirects=True,
        )
    except Exception:
        return httpx.Client(
            headers=_BROWSER_HEADERS,
            timeout=30,
            follow_redirects=True,
        )


def _extract_meta(body: str) -> dict[str, str]:
    meta: dict[str, str] = {}
    for m in _META_CONTENT.finditer(body):
        meta.setdefault(m.group("key").lower(), html.unescape(m.group("val")))
    return meta


class WGCETFClient:
    """Fetch the latest public WGC gold-ETF commentary."""

    def _fetch_page(
        self, client: httpx.Client, year: int, month: int
    ) -> Optional[dict]:
        url = _RESEARCH_URL.format(year=year, month=month)
        try:
            resp = client.get(url, headers={"Referer": _GOLDHUB_LANDING})
        except Exception as exc:
            log.info("WGC page fetch failed %s: %s", url, exc)
            return None

        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            log.info("WGC page %s returned %d", url, resp.status_code)
            return None

        meta = _extract_meta(resp.text)
        title = (
            meta.get("og:title")
            or meta.get("twitter:title")
        )
        if not title:
            m = _TITLE_TAG.search(resp.text)
            if m:
                title = html.unescape(m.group("t")).strip()

        description = (
            meta.get("og:description")
            or meta.get("twitter:description")
            or meta.get("description")
        )

        # Only treat a page as valid if we actually got a title that
        # mentions ETFs. Not-yet-published months on gold.org's CMS
        # sometimes return a 200 with generic hub content instead of
        # a 404, so we want to filter those out.
        if not title or "ETF" not in title:
            log.info("Page %s had no recognisable report title", url)
            return None

        return {
            "title": (title or "").strip() or None,
            "description": (description or "").strip() or None,
            "page_url": url,
            "period": f"{year:04d}-{month:02d}",
        }

    def get_latest_commentary(
        self, max_months_back: int = 4
    ) -> Optional[dict]:
        """Return {title, description, page_url, period} or None."""
        today = date.today()
        year, month = today.year, today.month
        with _new_client() as client:
            for _ in range(max_months_back):
                info = self._fetch_page(client, year, month)
                if info:
                    log.info(
                        "Found WGC commentary for %d-%02d: %s",
                        year,
                        month,
                        info["title"],
                    )
                    return info
                month -= 1
                if month < 1:
                    month = 12
                    year -= 1
        return None
