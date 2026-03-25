"""Client for CFTC Commitments of Traders (COT) data.

Fetches gold (commodity code 088691) positioning data from the CFTC's
Socrata open-data API (publicreporting.cftc.gov).  The Disaggregated
Futures-Only dataset (resource ``72hh-3qpy``) breaks out Managed Money
(hedge fund) positioning, which is the most useful signal for gold.

Falls back to the legacy bulk-ZIP download if the Socrata API is
unavailable.

Reference:
  https://publicreporting.cftc.gov/Commitments-of-Traders/Disaggregated-Futures-Only/72hh-3qpy
"""

from __future__ import annotations

import io
import logging
import zipfile
from datetime import date, datetime

import httpx
import pandas as pd

from midas.config import CFTC_BASE
from midas.models.gold import COTPosition

log = logging.getLogger(__name__)

# CFTC commodity code for "GOLD" in COMEX
GOLD_COMMODITY_CODE = "088691"

# Socrata open-data endpoint for the disaggregated futures report.
SOCRATA_BASE = "https://publicreporting.cftc.gov/resource/72hh-3qpy.json"

_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}


def _int(val: object) -> int:
    """Coerce a value to int, handling string/float from JSON."""
    if val is None:
        return 0
    return int(float(str(val).strip()))


class CFTCClient:
    """Fetch and parse CFTC COT disaggregated futures data for gold."""

    def __init__(self, base_url: str | None = None):
        self.base_url = base_url or CFTC_BASE

    # ------------------------------------------------------------------
    # Primary: Socrata JSON API
    # ------------------------------------------------------------------

    def _fetch_socrata(self, year: int, limit: int = 52) -> list[dict]:
        """Query the Socrata API for gold COT rows in a given year."""
        params = {
            "$where": (
                f"cftc_commodity_code='{GOLD_COMMODITY_CODE}' "
                f"AND report_date_as_yyyy_mm_dd >= '{year}-01-01' "
                f"AND report_date_as_yyyy_mm_dd <= '{year}-12-31'"
            ),
            "$order": "report_date_as_yyyy_mm_dd DESC",
            "$limit": str(limit),
        }
        resp = httpx.get(
            SOCRATA_BASE,
            params=params,
            headers=_HTTP_HEADERS,
            timeout=30,
            follow_redirects=True,
        )
        resp.raise_for_status()
        rows = resp.json()
        log.debug("Socrata returned %d rows for year %d", len(rows), year)
        return rows

    def _socrata_row_to_position(self, row: dict) -> COTPosition:
        """Convert a Socrata JSON row to a COTPosition."""
        report_date = datetime.strptime(
            row["report_date_as_yyyy_mm_dd"][:10], "%Y-%m-%d"
        ).date()

        return COTPosition(
            report_date=report_date,
            prod_long=_int(row.get("prod_merc_positions_long_all")),
            prod_short=_int(row.get("prod_merc_positions_short_all")),
            swap_long=_int(row.get("swap_positions_long_all") or row.get("swap__positions_long_all")),
            swap_short=_int(row.get("swap__positions_short_all") or row.get("swap_positions_short_all")),
            swap_spread=_int(row.get("swap__positions_spread_all") or row.get("swap_positions_spread_all")),
            mm_long=_int(row.get("m_money_positions_long_all")),
            mm_short=_int(row.get("m_money_positions_short_all")),
            mm_spread=_int(row.get("m_money_positions_spread_all")),
            other_long=_int(row.get("other_rept_positions_long_all")),
            other_short=_int(row.get("other_rept_positions_short_all")),
            other_spread=_int(row.get("other_rept_positions_spread_all")),
            nonrep_long=_int(row.get("nonrept_positions_long_all")),
            nonrep_short=_int(row.get("nonrept_positions_short_all")),
            open_interest=_int(row.get("open_interest_all")),
        )

    # ------------------------------------------------------------------
    # Fallback: bulk ZIP download from www.cftc.gov
    # ------------------------------------------------------------------

    def _download_zip(self, year: int) -> pd.DataFrame:
        """Download and unzip the disaggregated futures report for a year."""
        url = f"{self.base_url}/fut_disagg_txt_{year}.zip"
        resp = httpx.get(url, timeout=60, follow_redirects=True, headers=_HTTP_HEADERS)
        resp.raise_for_status()

        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            csv_name = [n for n in zf.namelist() if n.endswith(".txt")][0]
            with zf.open(csv_name) as f:
                df = pd.read_csv(f, low_memory=False)
        return df

    def _filter_gold(self, df: pd.DataFrame) -> pd.DataFrame:
        """Filter to gold futures rows only."""
        code_col = "CFTC_Commodity_Code" if "CFTC_Commodity_Code" in df.columns else "CFTC Commodity Code"
        return df[df[code_col].astype(str).str.strip() == GOLD_COMMODITY_CODE].copy()

    def _row_to_position(self, row: pd.Series) -> COTPosition:
        """Convert a DataFrame row to a COTPosition."""
        def _col(name: str) -> str:
            for c in row.index:
                if c.strip().replace("  ", " ") == name.strip():
                    return c
            raise KeyError(f"Column '{name}' not found. Available: {list(row.index)}")

        report_date = datetime.strptime(
            str(row[_col("Report_Date_as_YYYY-MM-DD")]).strip(), "%Y-%m-%d"
        ).date()

        return COTPosition(
            report_date=report_date,
            prod_long=int(row[_col("Prod_Merc_Positions_Long_All")]),
            prod_short=int(row[_col("Prod_Merc_Positions_Short_All")]),
            swap_long=int(row[_col("Swap_Positions_Long_All")]),
            swap_short=int(row[_col("Swap__Positions_Short_All")]),
            swap_spread=int(row[_col("Swap__Positions_Spread_All")]),
            mm_long=int(row[_col("M_Money_Positions_Long_All")]),
            mm_short=int(row[_col("M_Money_Positions_Short_All")]),
            mm_spread=int(row[_col("M_Money_Positions_Spread_All")]),
            other_long=int(row[_col("Other_Rept_Positions_Long_All")]),
            other_short=int(row[_col("Other_Rept_Positions_Short_All")]),
            other_spread=int(row[_col("Other_Rept_Positions_Spread_All")]),
            nonrep_long=int(row[_col("NonRept_Positions_Long_All")]),
            nonrep_short=int(row[_col("NonRept_Positions_Short_All")]),
            open_interest=int(row[_col("Open_Interest_All")]),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_positions(self, year: int | None = None) -> list[COTPosition]:
        """Get all weekly gold COT positions for a given year.

        Tries the Socrata JSON API first, falls back to ZIP download.
        """
        year = year or date.today().year

        # Try Socrata API first (faster, more reliable)
        try:
            rows = self._fetch_socrata(year)
            if rows:
                positions = [self._socrata_row_to_position(r) for r in rows]
                return sorted(positions, key=lambda p: p.report_date)
            log.warning("Socrata returned empty results for %d", year)
        except Exception as exc:
            log.warning("Socrata API failed (%s), falling back to ZIP download", exc)

        # Fallback: bulk ZIP download
        df = self._download_zip(year)
        gold_df = self._filter_gold(df)
        positions = [self._row_to_position(row) for _, row in gold_df.iterrows()]
        return sorted(positions, key=lambda p: p.report_date)

    def latest_position(self, year: int | None = None) -> COTPosition:
        """Get the most recent weekly gold COT position."""
        positions = self.get_positions(year)
        if not positions:
            raise ValueError(f"No gold COT data found for {year}")
        return positions[-1]
