"""Client for World Gold Council gold-ETF holdings & flows data.

The WGC publishes a monthly XLSX with global gold ETF holdings (tonnes)
and flows (USD), broken out by region (North America, Europe, Asia,
Other) and by individual fund.  There is no JSON/REST API, so this
client:

  1. Scrapes the monthly commentary page at
     ``/goldhub/research/gold-etfs-holdings-and-flows/<YYYY>/<MM>`` to
     find the current month's ``.xlsx`` download link.
  2. Downloads the spreadsheet and tries to extract the global
     headline numbers and regional flow breakdown.

The xlsx layout changes occasionally, so parsing is best-effort and
defensive — if the structure isn't recognised, we still return the
download URL so the user can grab the file manually.
"""

from __future__ import annotations

import io
import logging
import re
from datetime import date
from typing import Optional

import httpx
import pandas as pd

log = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

_RESEARCH_URL = (
    "https://www.gold.org/goldhub/research/"
    "gold-etfs-holdings-and-flows/{year}/{month:02d}"
)
_XLSX_HREF = re.compile(
    r'href=["\'](?P<href>[^"\']*/download/file/\d+/[^"\']*\.xlsx)["\']',
    re.IGNORECASE,
)

# Regions we care about on the summary card
_REGIONS = ("North America", "Europe", "Asia", "Other")


class WGCETFClient:
    """Fetch aggregated gold-ETF holdings/flows from World Gold Council."""

    # ------------------------------------------------------------------
    # Locating the latest xlsx
    # ------------------------------------------------------------------

    def _find_xlsx_on_page(self, year: int, month: int) -> Optional[str]:
        url = _RESEARCH_URL.format(year=year, month=month)
        try:
            resp = httpx.get(
                url, headers=_HEADERS, timeout=30, follow_redirects=True
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

    def get_latest_xlsx_url(self, max_months_back: int = 4) -> Optional[str]:
        """Find the most recent monthly page that links to an xlsx."""
        today = date.today()
        year, month = today.year, today.month
        for _ in range(max_months_back):
            u = self._find_xlsx_on_page(year, month)
            if u:
                log.info("Found WGC xlsx for %d-%02d: %s", year, month, u)
                return u
            month -= 1
            if month < 1:
                month = 12
                year -= 1
        return None

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

                    # Global tonnes total
                    if (
                        global_tonnes is None
                        and "total" in label
                        and "tonne" in label
                    ):
                        num = self._find_number_near(df, i, j)
                        # Global gold ETF holdings are ~3,000–4,000 tonnes
                        if num and 1500 < num < 10000:
                            global_tonnes = num

                    # Global AUM in USD billions
                    if (
                        global_aum_bn is None
                        and "aum" in label
                        and ("us$" in label or "usd" in label or "$" in label)
                    ):
                        num = self._find_number_near(df, i, j)
                        if num and 50 < num < 1000:
                            global_aum_bn = num

                    # Regional flow rows — region name in col, flow in adjacent cell
                    for region in _REGIONS:
                        if label == region.lower() and not any(
                            r["region"] == region for r in regional_flows
                        ):
                            num = self._find_number_near(df, i, j)
                            if num is not None:
                                regional_flows.append(
                                    {"region": region, "flow_usd_mn": num}
                                )

                    # Report period e.g. "March 2026"
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
        # Scan right on the same row
        for k in range(j + 1, min(j + 8, df.shape[1])):
            num = WGCETFClient._to_float(df.iat[i, k])
            if num is not None:
                return num
        # Scan down in the same column
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

        Raises RuntimeError if no xlsx can be located.
        """
        url = self.get_latest_xlsx_url()
        if not url:
            raise RuntimeError(
                "Could not locate a recent WGC gold-ETF flows xlsx on "
                "gold.org. The site may be blocking requests, or the "
                "monthly report page hasn't been published yet."
            )

        resp = httpx.get(
            url, headers=_HEADERS, timeout=60, follow_redirects=True
        )
        resp.raise_for_status()

        try:
            xls = pd.ExcelFile(io.BytesIO(resp.content), engine="openpyxl")
        except Exception as exc:
            raise RuntimeError(f"Could not open WGC xlsx: {exc}") from exc

        data = self._extract_from_sheets(xls)
        data["source_url"] = url
        data["sheets"] = xls.sheet_names
        return data
