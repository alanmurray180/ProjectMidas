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
_RESEARCH_INDEX = "https://www.gold.org/goldhub/research"
_RESEARCH_URL = (
    "https://www.gold.org/goldhub/research/"
    "gold-etfs-holdings-and-flows/{year}/{month:02d}"
)

_META_TAG = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
_META_ATTR = re.compile(
    r'(?P<name>[\w:.-]+)\s*=\s*(?P<q>["\'])(?P<val>.*?)(?P=q)',
    re.IGNORECASE | re.DOTALL,
)
_TITLE_TAG = re.compile(r"<title[^>]*>(?P<t>.*?)</title>", re.IGNORECASE | re.DOTALL)

# Links back to a monthly report, used to discover the latest published
# month rather than guessing URLs that may 200 with a placeholder.
_REPORT_LINK = re.compile(
    r"gold-etfs-holdings-and-flows/(?P<year>\d{4})/(?P<month>\d{2})",
    re.IGNORECASE,
)

# A real report page names the instrument one way or another.  Kept broad
# and case-insensitive: the previous exact "ETF" substring test rejected
# perfectly good titles that said "exchange-traded".
_TITLE_MARKERS = ("etf", "exchange-traded", "exchange traded")


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
    """Map meta ``property``/``name`` to ``content``.

    Attributes are parsed per tag rather than in a fixed order — plenty of
    CMSs emit ``content`` before ``property``, and an order-sensitive match
    silently returns nothing for those pages.
    """
    meta: dict[str, str] = {}
    for tag in _META_TAG.finditer(body):
        attrs = {
            m.group("name").lower(): m.group("val")
            for m in _META_ATTR.finditer(tag.group(0))
        }
        key = attrs.get("property") or attrs.get("name")
        content = attrs.get("content")
        if key and content is not None:
            meta.setdefault(key.lower(), html.unescape(content).strip())
    return meta


def _looks_like_report(title: str) -> bool:
    lowered = title.lower()
    return any(marker in lowered for marker in _TITLE_MARKERS)


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

        # Only treat a page as valid if we actually got a title naming the
        # instrument.  Not-yet-published months on gold.org's CMS sometimes
        # return a 200 with generic hub content instead of a 404, so we want
        # to filter those out.  The rejected title is logged: without it a
        # markup change here is indistinguishable from an unpublished month.
        if not title:
            log.info(
                "Page %s: no title in %d bytes of HTML (meta keys: %s)",
                url,
                len(resp.text),
                ", ".join(sorted(meta)[:8]) or "none",
            )
            return None
        if not _looks_like_report(title):
            log.info("Page %s: title %r is not a report", url, title[:120])
            return None

        return {
            "title": (title or "").strip() or None,
            "description": (description or "").strip() or None,
            "page_url": url,
            "period": f"{year:04d}-{month:02d}",
        }

    def _discover_months(self, client: httpx.Client) -> list[tuple[int, int]]:
        """Read the research index for months that actually have a report.

        Walking backwards from today guesses URLs that can 200 with
        placeholder content; the index tells us which months exist.
        """
        try:
            resp = client.get(_RESEARCH_INDEX, headers={"Referer": _GOLDHUB_LANDING})
            resp.raise_for_status()
        except Exception as exc:
            log.info("WGC research index unavailable: %s", exc)
            return []

        months = {
            (int(m.group("year")), int(m.group("month")))
            for m in _REPORT_LINK.finditer(resp.text)
        }
        found = sorted(months, reverse=True)
        log.info("WGC index lists %d report month(s)", len(found))
        return found

    def get_latest_commentary(
        self, max_months_back: int = 4
    ) -> Optional[dict]:
        """Return {title, description, page_url, period} or None."""
        today = date.today()
        year, month = today.year, today.month

        guesses: list[tuple[int, int]] = []
        for _ in range(max_months_back):
            guesses.append((year, month))
            month -= 1
            if month < 1:
                month = 12
                year -= 1

        with _new_client() as client:
            # Try the calendar guesses first — they are the common case and
            # cost nothing extra — then fall back to whatever the index
            # actually advertises.
            candidates = list(guesses)
            for candidate in self._discover_months(client)[:max_months_back]:
                if candidate not in candidates:
                    candidates.append(candidate)

            for cand_year, cand_month in candidates:
                info = self._fetch_page(client, cand_year, cand_month)
                if info:
                    log.info(
                        "Found WGC commentary for %d-%02d: %s",
                        cand_year,
                        cand_month,
                        info["title"],
                    )
                    return info

        log.info("No WGC commentary found across %d candidates", len(candidates))
        return None
