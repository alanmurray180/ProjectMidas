#!/usr/bin/env python3
"""Check the CFTC field mapping against what the API actually returns.

The COT card is only as right as the field names it reads, and those names
are neither documented alongside the report nor consistent: some legs carry
an ``_all`` suffix and some do not, the swap columns carry a doubled
underscore, and Socrata appends ``_1``/``_2`` to the old and other
crop-year duplicates.  A leg whose name does not match parses as zero,
which on screen is indistinguishable from a category holding no position —
which is how the producer and other-reportable rows once sat at zero
looking entirely plausible.

This resolves every leg in :data:`midas.clients.cftc.SOCRATA_FIELDS`
against a live row, says which field name answered, and totals the result
so the books can be checked: gross long equals gross short in a clean
report.  It does the same for the options-and-futures-combined dataset,
because the published tables people reconcile against are usually the
combined ones and the two differ by far more than rounding.

Run it from the **Check data sources** workflow — the CFTC host is not
reachable from every environment::

    python scripts/cot_probe.py            # resolved mapping and totals
    python scripts/cot_probe.py --verbose  # plus every field in the row
"""

from __future__ import annotations

import sys

import httpx

from midas.clients.cftc import GOLD_CONTRACT_MARKET_CODE, SOCRATA_FIELDS, _pick

# Both disaggregated datasets, keyed by what the CFTC calls them.  The
# dashboard reads the first; the second is what a published "Options and
# Futures Combined" table is quoting.
DATASETS = {
    "futures_only (the dashboard reads this)": (
        "https://publicreporting.cftc.gov/resource/72hh-3qpy.json"
    ),
    "options_and_futures_combined": (
        "https://publicreporting.cftc.gov/resource/kh3c-gbw2.json"
    ),
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}

LONG_LEGS = ("prod_long", "swap_long", "mm_long", "other_long", "nonrep_long")
SHORT_LEGS = ("prod_short", "swap_short", "mm_short", "other_short", "nonrep_short")
SPREAD_LEGS = ("swap_spread", "mm_spread", "other_spread")


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


def report(label: str, url: str, verbose: bool) -> bool:
    """Print one dataset's mapping; return False if anything is unresolved."""
    print(f"\n{'=' * 72}\n{label}\n{url}\n{'=' * 72}", flush=True)
    try:
        row = fetch_latest(url)
    except Exception as exc:
        print(f"  FAILED: {exc}", flush=True)
        return False
    if row is None:
        print("  no gold row returned", flush=True)
        return False

    print(f"report date : {str(row.get('report_date_as_yyyy_mm_dd'))[:10]}")
    print(f"market      : {row.get('market_and_exchange_names')}\n")

    values: dict[str, int | None] = {}
    ok = True
    for leg, names in SOCRATA_FIELDS.items():
        answered = next((n for n in names if row.get(n) is not None), None)
        values[leg] = _pick(row, names)
        if answered is None:
            ok = False
            print(f"  {leg:<14} UNRESOLVED — tried {', '.join(names)}")
        else:
            print(f"  {leg:<14} {values[leg]:>10,}   via {answered}")

    gross_long = sum(values[leg] or 0 for leg in LONG_LEGS)
    gross_short = sum(values[leg] or 0 for leg in SHORT_LEGS)
    spreads = sum(values[leg] or 0 for leg in SPREAD_LEGS)
    oi = values["open_interest"] or 0

    print(f"\n  gross long   {gross_long:>10,}")
    print(f"  gross short  {gross_short:>10,}")
    print(f"  spreads      {spreads:>10,}")
    print(f"  long+spreads {gross_long + spreads:>10,}   open interest {oi:,}")
    balanced = gross_long == gross_short
    print(f"  balances     {'yes' if balanced else 'NO — a leg is mis-parsed'}")
    if gross_long + spreads != oi:
        print("  note: long + spreads does not equal open interest")
    ok = ok and balanced

    if verbose:
        print("\n  every position field in the row:")
        for key in sorted(row):
            if "positions" in key or "open_interest" in key:
                print(f"    {key:<44} {row[key]}")

    return ok


def main(argv: list[str]) -> int:
    verbose = "--verbose" in argv
    results = [report(label, url, verbose) for label, url in DATASETS.items()]
    if not all(results):
        print("\nmapping is incomplete or does not balance", flush=True)
        return 1
    print("\nevery leg resolved and both datasets balance", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
