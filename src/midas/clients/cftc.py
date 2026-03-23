"""Client for CFTC Commitments of Traders (COT) data.

Downloads the weekly Disaggregated Futures report and extracts gold
(commodity code 088691) positioning data.  The CFTC publishes bulk
CSV/zip files at:
  https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm

We use the disaggregated report because it breaks out Managed Money
(hedge fund) positioning, which is the most useful signal for gold.
"""

from __future__ import annotations

import io
import zipfile
from datetime import date, datetime

import httpx
import pandas as pd

from midas.config import CFTC_BASE
from midas.models.gold import COTPosition

# CFTC commodity code for "GOLD" in COMEX
GOLD_COMMODITY_CODE = "088691"


class CFTCClient:
    """Fetch and parse CFTC COT disaggregated futures data for gold."""

    def __init__(self, base_url: str | None = None):
        self.base_url = base_url or CFTC_BASE

    def _download_zip(self, year: int) -> pd.DataFrame:
        """Download and unzip the disaggregated futures report for a year."""
        url = f"{self.base_url}/fut_disagg_txt_{year}.zip"
        resp = httpx.get(url, timeout=60, follow_redirects=True)
        resp.raise_for_status()

        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            # The zip contains a single CSV file
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
            """Resolve column name — CFTC CSVs use inconsistent spacing."""
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

    def get_positions(self, year: int | None = None) -> list[COTPosition]:
        """Get all weekly gold COT positions for a given year.

        Defaults to the current year.
        """
        year = year or date.today().year
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
