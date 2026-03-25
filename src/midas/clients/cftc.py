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

from midas.config import CFTC_APP_TOKEN, CFTC_BASE
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

    def _fetch_socrata(self, limit: int = 10) -> list[dict]:
        """Query the Socrata API for the latest gold COT rows.

        Rather than filtering by year (which may return nothing if the
        current year's data isn't published yet), we query for the most
        recent gold rows across all years.
        """
        params = {
            "$where": f"cftc_commodity_code='{GOLD_COMMODITY_CODE}'",
            "$order": "report_date_as_yyyy_mm_dd DESC",
            "$limit": str(limit),
        }
        headers = {**_HTTP_HEADERS}
        if CFTC_APP_TOKEN:
            headers["X-App-Token"] = CFTC_APP_TOKEN
            log.info("Using CFTC app token for Socrata request")
        else:
            log.warning("No CFTC app token set (FINANCIAL_ANALYSIS_COT env var)")
        log.info("Socrata query: %s params=%s", SOCRATA_BASE, params)
        resp = httpx.get(
            SOCRATA_BASE,
            params=params,
            headers=headers,
            timeout=30,
            follow_redirects=True,
        )
        resp.raise_for_status()
        rows = resp.json()
        log.info("Socrata returned %d rows", len(rows))

        # If the commodity code filter returned nothing, the field name
        # may differ.  Try without the filter and inspect the data.
        if not rows:
            log.warning("No rows with cftc_commodity_code='%s'; fetching sample to inspect field names",
                        GOLD_COMMODITY_CODE)
            resp2 = httpx.get(
                SOCRATA_BASE,
                params={"$limit": "1"},
                headers=headers,
                timeout=30,
                follow_redirects=True,
            )
            resp2.raise_for_status()
            sample = resp2.json()
            if sample:
                log.warning("Sample row keys: %s", list(sample[0].keys()))
                # Try filtering by commodity_name instead
                for r in [sample[0]]:
                    for k, v in r.items():
                        if "gold" in str(v).lower() or "088691" in str(v):
                            log.warning("Found gold reference: %s=%s", k, v)

        return rows

    def _socrata_row_to_position(self, row: dict) -> COTPosition:
        """Convert a Socrata JSON row to a COTPosition."""
        # The date field may be ISO datetime or plain date
        date_str = row.get("report_date_as_yyyy_mm_dd", "")
        report_date = datetime.strptime(date_str[:10], "%Y-%m-%d").date()

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
        """Get weekly gold COT positions.

        Tries the Socrata JSON API first (latest rows regardless of year),
        then falls back to the ZIP download for the given year and the
        previous year.
        """
        year = year or date.today().year

        # Try Socrata API first (faster, more reliable)
        try:
            rows = self._fetch_socrata()
            if rows:
                positions = [self._socrata_row_to_position(r) for r in rows]
                return sorted(positions, key=lambda p: p.report_date)
            log.warning("Socrata returned empty results")
        except Exception as exc:
            log.warning("Socrata API failed (%s), falling back to ZIP download", exc)

        # Fallback: bulk ZIP download — try current year, then previous year
        for try_year in [year, year - 1]:
            try:
                log.info("Trying ZIP download for year %d", try_year)
                df = self._download_zip(try_year)
                gold_df = self._filter_gold(df)
                if gold_df.empty:
                    log.warning("No gold rows in ZIP for %d", try_year)
                    continue
                positions = [self._row_to_position(row) for _, row in gold_df.iterrows()]
                return sorted(positions, key=lambda p: p.report_date)
            except Exception as exc:
                log.warning("ZIP download failed for %d: %s", try_year, exc)

        return []

    def latest_position(self, year: int | None = None) -> COTPosition:
        """Get the most recent weekly gold COT position."""
        positions = self.get_positions(year)
        if not positions:
            raise ValueError(
                "No gold COT data found. Both the Socrata API and CFTC ZIP "
                "download returned no data. Check network connectivity to "
                "publicreporting.cftc.gov and www.cftc.gov."
            )
        return positions[-1]
