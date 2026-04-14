"""Client for World Gold Council gold-ETF holdings & flows data.

The WGC publishes a monthly XLSX with global gold ETF holdings (tonnes)
and flows (USD), broken out by region (North America, Europe, Asia,
Other) and by individual fund.  There is no JSON/REST API, so this
client:

  1. First tries a local disk cache (populated by a scheduled GitHub
     Actions mirror in ``src/midas/data/wgc/``).
  2. Falls back to scraping the monthly commentary page at
     ``/goldhub/research/gold-etfs-holdings-and-flows/<YYYY>/<MM>`` to
     locate the current ``.xlsx`` link, then downloading it.

Network fetches use an ``httpx.Client`` with HTTP/2 and a realistic
set of browser headers, plus a three-step Referer chain (landing page
-> monthly page -> xlsx) to mimic a real navigation. Gold.org's CDN
blocks datacenter IPs with bare User-Agent-only requests.

The xlsx layout changes occasionally, so parsing is best-effort and
defensive — if the structure isn't recognised, we still return the
download URL so the user can grab the file manually.
"""

from __future__ import annotations

import io
import json
import logging
import re
from datetime import date
from pathlib import Path
from typing import Optional

import httpx
import pandas as pd

log = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# HTTP configuration
# ----------------------------------------------------------------------

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8,"
        "application/signed-exchange;v=b3;q=0.7"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Ch-Ua": (
        '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"'
    ),
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"macOS"',
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-User": "?1",
    "Sec-Fetch-Dest": "document",
    "Upgrade-Insecure-Requests": "1",
}

_GOLDHUB_LANDING = "https://www.gold.org/goldhub"
_RESEARCH_URL = (
    "https://www.gold.org/goldhub/research/"
    "gold-etfs-holdings-and-flows/{year}/{month:02d}"
)
_XLSX_HREF = re.compile(
    r'href=["\'](?P<href>[^"\']*/download/file/\d+/[^"\']*\.xlsx)["\']',
    re.IGNORECASE,
)

# ----------------------------------------------------------------------
# Disk cache (populated by the WGC mirror Action; absent otherwise)
# ----------------------------------------------------------------------

# Relative to the ``midas`` package root (i.e. ``src/midas/data/wgc/``)
_CACHE_SUBPATH = Path("data") / "wgc"
_CACHE_XLSX = "latest.xlsx"
_CACHE_META = "metadata.json"

# Regions we care about on the summary card
_REGIONS = ("North America", "Europe", "Asia", "Other")


def _new_client() -> httpx.Client:
    """Create an httpx.Client configured like a real browser."""
    try:
        return httpx.Client(
            headers=_BROWSER_HEADERS,
            http2=True,
            timeout=30,
            follow_redirects=True,
        )
    except Exception:
        # h2 not installed — fall back to HTTP/1.1
        return httpx.Client(
            headers=_BROWSER_HEADERS,
            timeout=30,
            follow_redirects=True,
        )


class WGCETFClient:
    """Fetch aggregated gold-ETF holdings/flows from World Gold Council."""

    # ------------------------------------------------------------------
    # Disk cache
    # ------------------------------------------------------------------

    @staticmethod
    def _cache_dir() -> Path:
        # src/midas/clients/wgc.py -> src/midas/
        return Path(__file__).resolve().parent.parent / _CACHE_SUBPATH

    def _load_from_cache(self) -> Optional[dict]:
        cache_dir = self._cache_dir()
        xlsx_path = cache_dir / _CACHE_XLSX
        meta_path = cache_dir / _CACHE_META
        if not xlsx_path.exists():
            return None
        try:
            metadata: dict = {}
            if meta_path.exists():
                try:
                    metadata = json.loads(meta_path.read_text())
                except json.JSONDecodeError as exc:
                    log.warning("Bad WGC metadata.json: %s", exc)
            xls = pd.ExcelFile(xlsx_path, engine="openpyxl")
            data = self._extract_from_sheets(xls)
            data["source_url"] = metadata.get("source_url", "")
            data["sheets"] = xls.sheet_names
            data["cached_at"] = metadata.get("fetched_at")
            log.info(
                "WGC disk cache hit (%s, fetched_at=%s)",
                xlsx_path,
                data["cached_at"],
            )
            return data
        except Exception as exc:
            log.warning("Failed to load WGC cache: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Locating the latest xlsx (network path)
    # ------------------------------------------------------------------

    def _find_xlsx_on_page(
        self, client: httpx.Client, year: int, month: int
    ) -> Optional[str]:
        url = _RESEARCH_URL.format(year=year, month=month)
        try:
            resp = client.get(
                url,
                headers={"Referer": _GOLDHUB_LANDING},
            )
        except Exception as exc:
            log.warning("WGC page fetch failed %s: %s", url, exc)
            return None

        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            log.warning("WGC page %s returned %d", url, resp.status_code)
            return None

        match = _XLSX_HREF.search(resp.text)
        if not match:
            log.info("No xlsx link found on %s", url)
            return None

        href = match.group("href")
        if href.startswith("/"):
            href = "https://www.gold.org" + href
        return href

    def _find_latest_xlsx(
        self, client: httpx.Client, max_months_back: int = 4
    ) -> tuple[Optional[str], Optional[str]]:
        """Return (xlsx_url, originating_monthly_page_url)."""
        today = date.today()
        year, month = today.year, today.month
        for _ in range(max_months_back):
            page_url = _RESEARCH_URL.format(year=year, month=month)
            u = self._find_xlsx_on_page(client, year, month)
            if u:
                log.info("Found WGC xlsx for %d-%02d: %s", year, month, u)
                return u, page_url
            month -= 1
            if month < 1:
                month = 12
                year -= 1
        return None, None

    def get_latest_xlsx_url(self, max_months_back: int = 4) -> Optional[str]:
        """Public: locate the most recent xlsx URL (warm session first)."""
        with _new_client() as client:
            self._warm_session(client)
            url, _ = self._find_latest_xlsx(client, max_months_back)
            return url

    # ------------------------------------------------------------------
    # Session warmup — hit the landing page so cookies get set
    # ------------------------------------------------------------------

    @staticmethod
    def _warm_session(client: httpx.Client) -> None:
        try:
            resp = client.get(_GOLDHUB_LANDING)
            log.info("Warmed gold.org session: %d", resp.status_code)
        except Exception as exc:
            log.warning("Session warmup failed (continuing): %s", exc)

    # ------------------------------------------------------------------
    # Parsing the xlsx — defensive, since layouts vary
    # ------------------------------------------------------------------

    @staticmethod
    def _to_float(val) -> Optional[float]:
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return None
        try:
            return float(str(val).replace(",", "").strip())
        except (ValueError, TypeError):
            return None

    def _extract_from_sheets(self, xls: pd.ExcelFile) -> dict:
        """Best-effort extraction of headline & regional numbers."""
        global_tonnes: Optional[float] = None
        global_aum_bn: Optional[float] = None
        regional_flows: list[dict] = []
        report_period: Optional[str] = None

        for sheet_name in xls.sheet_names:
            try:
                df = xls.parse(sheet_name, header=None)
            except Exception as exc:
                log.debug("Could not parse sheet %s: %s", sheet_name, exc)
                continue

            for i in range(len(df)):
                row = df.iloc[i]
                for j in range(len(row)):
                    cell = row.iloc[j]
                    if pd.isna(cell):
                        continue
                    label = str(cell).strip().lower()

                    if (
                        global_tonnes is None
                        and "total" in label
                        and "tonne" in label
                    ):
                        num = self._find_number_near(df, i, j)
                        if num and 1500 < num < 10000:
                            global_tonnes = num

                    if (
                        global_aum_bn is None
                        and "aum" in label
                        and ("us$" in label or "usd" in label or "$" in label)
                    ):
                        num = self._find_number_near(df, i, j)
                        if num and 50 < num < 1000:
                            global_aum_bn = num

                    for region in _REGIONS:
                        if label == region.lower() and not any(
                            r["region"] == region for r in regional_flows
                        ):
                            num = self._find_number_near(df, i, j)
                            if num is not None:
                                regional_flows.append(
                                    {"region": region, "flow_usd_mn": num}
                                )

                    if report_period is None and re.match(
                        r"^(january|february|march|april|may|june|july|"
                        r"august|september|october|november|december)\s+\d{4}$",
                        label,
                    ):
                        report_period = str(cell).strip()

        return {
            "global_tonnes": global_tonnes,
            "global_aum_bn": global_aum_bn,
            "regional_flows": regional_flows,
            "report_period": report_period,
        }

    @staticmethod
    def _find_number_near(df: pd.DataFrame, i: int, j: int) -> Optional[float]:
        """Look right then down for the nearest numeric cell."""
        for k in range(j + 1, min(j + 8, df.shape[1])):
            num = WGCETFClient._to_float(df.iat[i, k])
            if num is not None:
                return num
        for k in range(i + 1, min(i + 5, df.shape[0])):
            num = WGCETFClient._to_float(df.iat[k, j])
            if num is not None:
                return num
        return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_latest(self) -> dict:
        """Return the latest WGC ETF holdings/flows summary.

        Tries disk cache first, then falls back to scraping gold.org.
        Raises RuntimeError if neither path yields data.
        """
        cached = self._load_from_cache()
        if cached:
            return cached

        with _new_client() as client:
            self._warm_session(client)
            xlsx_url, page_url = self._find_latest_xlsx(client)
            if not xlsx_url:
                raise RuntimeError(
                    "Could not locate a recent WGC gold-ETF flows xlsx on "
                    "gold.org. The site may be blocking requests from "
                    "this IP, or the monthly report hasn't been published "
                    "yet. If this persists, populate the disk cache via "
                    "the GitHub Actions mirror."
                )

            xlsx_headers = {
                "Referer": page_url or _GOLDHUB_LANDING,
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1",
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,"
                    "image/avif,image/webp,image/apng,*/*;q=0.8,"
                    "application/signed-exchange;v=b3;q=0.7"
                ),
            }
            resp = client.get(xlsx_url, headers=xlsx_headers, timeout=60)
            resp.raise_for_status()

            try:
                xls = pd.ExcelFile(
                    io.BytesIO(resp.content), engine="openpyxl"
                )
            except Exception as exc:
                raise RuntimeError(
                    f"Could not open WGC xlsx: {exc}"
                ) from exc

            data = self._extract_from_sheets(xls)
            data["source_url"] = xlsx_url
            data["sheets"] = xls.sheet_names
            data["cached_at"] = None
            return data
