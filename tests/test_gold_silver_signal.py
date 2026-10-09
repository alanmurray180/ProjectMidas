"""Tests for the gold/silver ratio index in the one-pager's Other column."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from midas import app as midas_app
from midas.clients.gold_silver import GoldSilverRatioClient, gsr_signal


def _records(ratios: list[float]) -> list[dict]:
    start = date(2025, 1, 1)
    return [
        {"date": start + timedelta(days=i), "gold": r * 30.0, "silver": 30.0, "ratio": r}
        for i, r in enumerate(ratios)
    ]


def test_mid_range_ratio_scores_zero():
    sig = gsr_signal(_records([float(80 + i) for i in range(19)] + [89.0]))

    assert sig["percentile"] == pytest.approx(50.0)
    assert sig["label"] == "Mid-range"
    assert sig["score"] == 0


def test_high_ratio_is_silver_lagging_and_bullish():
    """The backtest's sign: gold has outperformed after a high ratio."""
    sig = gsr_signal(_records([float(80 + i) for i in range(19)] + [120.0]))

    assert sig["percentile"] >= 80
    assert sig["label"] == "Silver lagging"
    assert sig["score"] == 1
    assert sig["high"] == 120.0


def test_low_ratio_is_silver_leading_and_bearish():
    sig = gsr_signal(_records([float(80 + i) for i in range(19)] + [60.0]))

    assert sig["percentile"] <= 20
    assert sig["label"] == "Silver leading"
    assert sig["score"] == -1
    assert sig["low"] == 60.0


def test_bands_sit_at_the_20th_and_80th_percentile():
    # 15 of 20 below and one equal: exactly the 80th.
    ratios = [float(80 + i) for i in range(15)] + [100.0, 110.0, 111.0, 112.0]
    assert gsr_signal(_records(ratios + [100.0]))["score"] == 1

    # And exactly the 20th, the other way up.
    ratios = [60.0, 61.0, 62.0, 70.0] + [float(80 + i) for i in range(15)]
    assert gsr_signal(_records(ratios + [70.0]))["score"] == -1


def test_empty_series_raises():
    with pytest.raises(ValueError):
        gsr_signal([])


def test_other_row_uses_a_fixed_one_year_window(monkeypatch):
    seen = {}

    def _fake(self, range_="1mo"):
        seen["range"] = range_
        return _records([float(80 + i) for i in range(19)] + [120.0])

    monkeypatch.setattr(GoldSilverRatioClient, "get_ratio", _fake)
    row = midas_app._fetch_gsr_signal()

    assert seen["range"] == "1y"
    assert row["name"] == "Gold/silver ratio"
    assert row["label"] == "Silver lagging"
    assert row["score"] == 1
    assert row["index"] == "98"
    assert "120.0x" in row["note"]


def test_dead_feed_is_na_and_counts_towards_nothing(monkeypatch):
    def _boom(self, range_="1mo"):
        raise RuntimeError("Yahoo down")

    monkeypatch.setattr(GoldSilverRatioClient, "get_ratio", _boom)
    row = midas_app._fetch_gsr_signal()
    other = midas_app.build_other({"cot": {"error": "CFTC down"}, "gsr_signal": row})

    assert row["score"] is None
    assert row["label"] == "N/A"
    assert other["unavailable"] == 2
    assert (other["bullish"], other["bearish"], other["neutral"]) == (0, 0, 0)


def test_other_column_tallies_both_rows(monkeypatch):
    monkeypatch.setattr(
        GoldSilverRatioClient,
        "get_ratio",
        lambda self, range_="1mo": _records([float(80 + i) for i in range(19)] + [60.0]),
    )
    cot = {
        "context_percentile": "85",
        "context_label": "Crowded long",
        "context_score": -1,
        "context_current": "+30.0%",
        "report_date": "2026-09-29",
    }
    other = midas_app.build_other({"cot": cot, "gsr_signal": midas_app._fetch_gsr_signal()})

    assert [s["name"] for s in other["signals"]] == ["COT positioning", "Gold/silver ratio"]
    # COT crowded long −1, ratio low in its range −1.
    assert (other["bullish"], other["bearish"], other["neutral"]) == (0, 2, 0)
