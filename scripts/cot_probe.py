#!/usr/bin/env python3
"""Print the CFTC's own field names and figures for the latest gold report.

The COT card is only as right as the field names it reads, and those names
are neither documented alongside the report nor stable between the CFTC's
datasets: a leg whose name does not match parses as zero, which looks on
screen like a category that does not trade.  This dumps what the API
actually returns, so a mapping can be checked against the published report
instead of against memory.

It also fetches the options-and-futures-combined twin of the futures-only
dataset the dashboard uses, since the two differ by more than rounding and
the published tables people compare against are usually the combined ones.

Run it from a workflow — ``.github/workflows/cot-probe.yml`` — because the
CFTC host is not reachable from every environment::

    python scripts/cot_probe.py
"""

from __future__ import annotations

import json
import sys

import httpx

# Both disaggregated datasets, keyed by what the CFTC calls them.
DATASETS = {
    "futures_only": "https://publicreporting.cftc.gov/resource/72hh-3qpy.json",
    "options_and_futures_combined": (
        "https://publicreporting.cftc.gov/resource/kh3c-gbw2.json"
    ),
}

GOLD_CONTRACT_MARKET_CODE = "088691"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}

# The legs the dashboard reads, as it names them, mapped to the field it
# currently asks for.  Anything printed as MISSING below is a category
# rendering as zero on the page right now.
EXPECTED_FIELDS = {
    "prod_long": "prod_merc_positions_long_all",
    "prod_short": "prod_merc_positions_short_all",
    "swap_long": "swap_positions_long_all",
    "swap_short": "swap__positions_short_all",
    "swap_spread": "swap__positions_spread_all",
    "mm_long": "m_money_positions_long_all",
    "mm_short": "m_money_positions_short_all",
    "mm_spread": "m_money_positions_spread_all",
    "other_long": "other_rept_positions_long_all",
    "other_short": "other_rept_positions_short_all",
    "other_spread": "other_rept_positions_spread_all",
    "nonrep_long": "nonrept_positions_long_all",
    "nonrep_short": "nonrept_positions_short_all",
    "open_interest": "open_interest_all",
}


def fetch_latest(url: str) -> dict | None:
    params = {
        "$where": f"cftc_contract_market_code='{GOLD_CONTRACT_MARKET_CODE}'",
        "$order": "report_date_as_yyyy_mm_dd DESC",
        "$limit": "1",
    }
    resp = httpx.get(url, params=params, headers=HEADERS, timeout=30, follow_redirects=True)
    resp.raise_for_status()
    rows = resp.json()
    return rows[0] if rows else None


def main() -> int:
    for label, url in DATASETS.items():
        print(f"\n{'=' * 70}\n{label}\n{url}\n{'=' * 70}", flush=True)
        try:
            row = fetch_latest(url)
        except Exception as exc:
            print(f"  FAILED: {exc}", flush=True)
            continue
        if row is None:
            print("  no gold row returned", flush=True)
            continue

        print(f"\nreport date: {row.get('report_date_as_yyyy_mm_dd')}")
        print(f"market: {row.get('market_and_exchange_names')}")

        print("\nfields the dashboard reads:")
        for leg, field in EXPECTED_FIELDS.items():
            value = row.get(field)
            state = "MISSING" if value is None else value
            print(f"  {leg:<14} {field:<36} {state}")

        print("\nevery position field in the row:")
        for key in sorted(row):
            if "positions" in key or "open_interest" in key:
                print(f"  {key:<44} {row[key]}")

        print("\nall keys:")
        print("  " + json.dumps(sorted(row), indent=2).replace("\n", "\n  "))

    return 0


if __name__ == "__main__":
    sys.exit(main())
