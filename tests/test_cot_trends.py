"""Tests for the COT contract split and trend maths."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from midas.clients.cftc import CFTCClient
from midas.clients.cot_trends import COTTrends, _percentile_rank
from midas.models.gold import COTPosition


# The other categories are held flat so every move in a fixture is the
# managed-money leg under test.
_OTHER_LEGS = dict(
    prod_long=50_000,
    swap_long=30_000,
    swap_short=40_000,
    swap_spread=10_000,
    mm_spread=20_000,
    other_long=60_000,
    other_short=30_000,
    other_spread=15_000,
    nonrep_long=40_000,
    nonrep_short=20_000,
)
_SPREADS = _OTHER_LEGS["swap_spread"] + _OTHER_LEGS["mm_spread"] + _OTHER_LEGS["other_spread"]


def _position(report_date: date, mm_long: int, mm_short: int, **kw) -> COTPosition:
    """A COT row that balances, the way a real report does.

    Every long is someone's short, so the producer short leg absorbs
    whatever managed money is carrying, and open interest is the gross long
    book plus the spreads.  A fixture that did not balance would let a
    totals bug through.
    """
    gross_long = mm_long + _OTHER_LEGS["prod_long"] + _OTHER_LEGS["swap_long"]
    gross_long += _OTHER_LEGS["other_long"] + _OTHER_LEGS["nonrep_long"]
    other_shorts = (
        mm_short
        + _OTHER_LEGS["swap_short"]
        + _OTHER_LEGS["other_short"]
        + _OTHER_LEGS["nonrep_short"]
    )
    base = dict(
        _OTHER_LEGS,
        prod_short=gross_long - other_shorts,
        open_interest=gross_long + _SPREADS,
        market_name="GOLD - COMMODITY EXCHANGE INC.",
    )
    base.update(kw)
    return COTPosition(
        report_date=report_date, mm_long=mm_long, mm_short=mm_short, **base
    )


def _history(nets: list[int], shorts: int = 30_000) -> list[COTPosition]:
    """Weekly history ending today, with managed-money net following *nets*."""
    start = date(2024, 1, 2)
    return [
        _position(start + timedelta(weeks=i), mm_long=net + shorts, mm_short=shorts)
        for i, net in enumerate(nets)
    ]


def test_contract_split_by_side():
    data = COTTrends(_history([100_000])).compute()
    mm = next(c for c in data["categories"] if c["key"] == "mm")

    assert mm["long"] == 130_000
    assert mm["short"] == 30_000
    assert mm["net"] == 100_000
    assert mm["spread"] == 20_000
    assert mm["long_short_ratio"] == pytest.approx(130_000 / 30_000)
    # 130,000 of a 310,000 long book carried in 355,000 open interest.
    assert mm["long_pct_oi"] == pytest.approx(130_000 / 355_000 * 100)


def test_totals_row_balances_longs_and_shorts():
    """Gross long equals gross short in the report; the row proves the parse."""
    data = COTTrends(_history([100_000])).compute()
    total = next(c for c in data["categories"] if c["key"] == "total")

    assert total["long"] == 130_000 + 50_000 + 30_000 + 60_000 + 40_000
    assert total["long"] == total["short"]
    assert total["net"] == 0
    assert total["spread"] == 45_000


def test_changes_over_each_lookback():
    # 53 weeks so the 52-week lookback has a row to reach.
    nets = [50_000 + 1_000 * i for i in range(53)]
    data = COTTrends(_history(nets)).compute()
    mm = next(c for c in data["categories"] if c["key"] == "mm")

    assert mm["changes"]["net"]["1w"] == 1_000
    assert mm["changes"]["net"]["4w"] == 4_000
    assert mm["changes"]["net"]["13w"] == 13_000
    assert mm["changes"]["net"]["52w"] == 52_000
    # Shorts are flat in this fixture, so the whole move is on the long leg.
    assert mm["changes"]["long"]["4w"] == 4_000
    assert mm["changes"]["short"]["4w"] == 0


def test_lookback_beyond_history_is_none_not_zero():
    """A missing week must read as unknown; zero would mean "unchanged"."""
    data = COTTrends(_history([10_000, 20_000])).compute()
    mm = next(c for c in data["categories"] if c["key"] == "mm")

    assert mm["changes"]["net"]["1w"] == 10_000
    assert mm["changes"]["net"]["4w"] is None
    assert mm["changes"]["net"]["52w"] is None


def test_context_places_net_in_its_52_week_range():
    nets = [100_000] * 51 + [250_000]
    data = COTTrends(_history(nets)).compute()
    ctx = data["context"]

    assert ctx["current"] == 250_000
    assert ctx["high"] == 250_000
    assert ctx["low"] == 100_000
    assert ctx["percentile"] == pytest.approx(99.04, abs=0.05)
    assert ctx["label"] == "Crowded long"
    assert ctx["range_pct"] == pytest.approx(100.0)


def test_context_window_ignores_older_history():
    """Only the last 52 reports frame the reading, whatever came before."""
    nets = [900_000] * 60 + [100_000] * 51 + [120_000]
    ctx = COTTrends(_history(nets)).compute()["context"]

    assert ctx["weeks"] == 52
    assert ctx["high"] == 120_000
    assert ctx["label"] == "Crowded long"


def test_washed_out_short_positioning():
    nets = [100_000] * 51 + [-60_000]
    ctx = COTTrends(_history(nets)).compute()["context"]

    assert ctx["current"] == -60_000
    assert ctx["label"] == "Washed out"


def test_percentile_rank_of_flat_series_is_mid():
    assert _percentile_rank([5, 5, 5], 5) == pytest.approx(50.0)
    assert _percentile_rank([], 5) is None


def test_series_carries_every_category():
    rows = COTTrends(_history([10_000, 20_000])).compute()["series"]

    assert len(rows) == 2
    assert rows[0]["report_date"] < rows[1]["report_date"]
    assert rows[1]["mm_net"] == 20_000
    # Producers hold the other side of the speculative book in the fixture.
    assert rows[1]["prod_net"] == -rows[1]["mm_net"] - 40_000
    # Non-reportables have no spread leg in the report, so it reads zero
    # rather than raising on a field the dataclass does not have.
    assert rows[1]["nonrep_spread"] == 0
    assert rows[1]["total_long"] == rows[1]["total_short"]


def test_empty_history_raises():
    with pytest.raises(ValueError):
        COTTrends([]).compute()


def test_dedupe_keeps_the_full_size_comex_contract():
    """Micro gold answers the same loose filter; it must not enter the series."""
    day = date(2025, 5, 6)
    full = _position(day, mm_long=130_000, mm_short=30_000)
    micro = _position(
        day,
        mm_long=4_000,
        mm_short=1_000,
        open_interest=9_000,
        market_name="MICRO GOLD - COMMODITY EXCHANGE INC.",
    )

    picked = CFTCClient._dedupe_by_date([micro, full])

    assert len(picked) == 1
    assert picked[0].mm_long == 130_000


def test_dedupe_falls_back_to_open_interest_without_a_name():
    """The ZIP fallback can arrive without a usable market name."""
    day = date(2025, 5, 6)
    small = _position(day, mm_long=4_000, mm_short=1_000, open_interest=9_000, market_name="")
    big = _position(day, mm_long=130_000, mm_short=30_000, market_name="")

    picked = CFTCClient._dedupe_by_date([small, big])

    assert len(picked) == 1
    assert picked[0].mm_long == 130_000
