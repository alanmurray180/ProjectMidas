"""Tests for the COT panel: Socrata parsing, formatting, CSV and rendering."""

from __future__ import annotations

from datetime import date, timedelta

import httpx
import pytest
import respx

from midas import app as midas_app
from midas.clients.cftc import SOCRATA_BASE, CFTCClient
from midas.models.gold import COTPosition


def _socrata_row(day: str, mm_long: int, mm_short: int, market: str, oi: int) -> dict:
    """A Socrata row with the field names the live dataset uses."""
    return {
        "report_date_as_yyyy_mm_dd": f"{day}T00:00:00.000",
        "market_and_exchange_names": market,
        "cftc_commodity_code": "088691",
        "open_interest_all": str(oi),
        "prod_merc_positions_long_all": "50000",
        "prod_merc_positions_short_all": "180000",
        "swap_positions_long_all": "30000",
        "swap__positions_short_all": "40000",
        "swap__positions_spread_all": "10000",
        "m_money_positions_long_all": str(mm_long),
        "m_money_positions_short_all": str(mm_short),
        "m_money_positions_spread_all": "20000",
        "other_rept_positions_long_all": "60000",
        "other_rept_positions_short_all": "30000",
        "other_rept_positions_spread_all": "15000",
        "nonrept_positions_long_all": "40000",
        "nonrept_positions_short_all": "20000",
    }


@respx.mock
def test_get_history_parses_and_dedupes_socrata_rows():
    rows = [
        _socrata_row("2025-05-13", 140_000, 28_000, "GOLD - COMMODITY EXCHANGE INC.", 500_000),
        _socrata_row("2025-05-13", 4_000, 900, "MICRO GOLD - COMMODITY EXCHANGE INC.", 9_000),
        _socrata_row("2025-05-06", 130_000, 30_000, "GOLD - COMMODITY EXCHANGE INC.", 495_000),
    ]
    respx.get(SOCRATA_BASE).mock(return_value=httpx.Response(200, json=rows))

    history = CFTCClient().get_history(weeks=52)

    assert [p.report_date for p in history] == [date(2025, 5, 6), date(2025, 5, 13)]
    assert history[-1].mm_long == 140_000  # not the micro contract
    assert history[-1].mm_net == 112_000
    assert history[-1].market_name == "GOLD - COMMODITY EXCHANGE INC."
    assert history[-1].swap_short == 40_000


def _history(weeks: int = 60) -> list[COTPosition]:
    """A rising managed-money net, one report a week up to last Tuesday."""
    end = date.today() - timedelta(days=(date.today().weekday() - 1) % 7)
    out = []
    for i in range(weeks):
        mm_long = 120_000 + 1_000 * i
        out.append(
            COTPosition(
                report_date=end - timedelta(weeks=weeks - 1 - i),
                prod_long=50_000,
                prod_short=140_000 + 1_000 * i,
                swap_long=30_000,
                swap_short=40_000,
                swap_spread=10_000,
                mm_long=mm_long,
                mm_short=30_000,
                mm_spread=20_000,
                other_long=60_000,
                other_short=30_000,
                other_spread=15_000,
                nonrep_long=40_000,
                nonrep_short=20_000,
                open_interest=345_000 + 1_000 * i,
                market_name="GOLD - COMMODITY EXCHANGE INC.",
            )
        )
    return out


@pytest.fixture()
def cot(monkeypatch) -> dict:
    monkeypatch.setattr(CFTCClient, "get_history", lambda self, *a, **kw: _history())
    payload = midas_app._fetch_cot_positions()
    assert payload and not payload.get("error"), payload
    return payload


def test_panel_payload_splits_contracts_and_trends(cot):
    assert cot["mm_long"] == "179,000"
    assert cot["mm_short"] == "30,000"
    assert cot["mm_net"] == "+149,000"
    assert cot["mm_net_change_1w"] == "+1,000"
    assert cot["mm_net_change_4w"] == "+4,000"
    assert cot["mm_net_change_52w"] == "+52,000"
    assert cot["context_label"] == "Crowded long"
    assert cot["history_weeks"] == 60

    labels = [c["label"] for c in cot["categories"]]
    assert labels == [
        "Managed Money",
        "Producer / Merchant",
        "Swap Dealers",
        "Other Reportables",
        "Non-Reportable",
        "All categories",
    ]
    mm = cot["categories"][0]
    assert mm["long"] == "179,000"
    assert mm["trend"]["long"]["13w"]["text"] == "+13,000"
    # Shorts are flat in this fixture: a bare 0, and uncoloured.
    assert mm["trend"]["short"]["13w"]["text"] == "0"
    assert mm["trend"]["short"]["13w"]["flat"] is True
    assert mm["long_pct_oi"].endswith("%")


def test_panel_marks_unreachable_lookbacks(monkeypatch):
    """Ten weeks of history cannot answer a 52-week change."""
    monkeypatch.setattr(CFTCClient, "get_history", lambda self, *a, **kw: _history(10))
    cot = midas_app._fetch_cot_positions()

    assert cot["mm_net_change_4w"] == "+4,000"
    assert cot["mm_net_change_52w"] == "—"


def test_missing_history_degrades_to_an_error_card(monkeypatch):
    monkeypatch.setattr(CFTCClient, "get_history", lambda self, *a, **kw: [])
    cot = midas_app._fetch_cot_positions()

    assert cot["error"]


def test_csv_export_carries_every_week(cot):
    csv_text = midas_app.cot_history_csv(cot["series"])
    lines = csv_text.strip().split("\n")

    assert len(lines) == 61  # header plus 60 weekly reports
    assert lines[0].startswith("report_date,market_name,open_interest,mm_long,mm_short")
    assert lines[-1].split(",")[3] == "179000"


def test_card_renders_with_the_new_panel(cot):
    html = midas_app.render_context(
        {
            "cot": cot,
            "period": "30d",
            "range_label": "30-day",
            "links": midas_app.STATIC_LINKS,
            "workflows": midas_app.WORKFLOW_LINKS,
            "generated_at": "2025-05-16 09:00 UTC",
            "generated_at_iso": "2025-05-16T09:00:00+00:00",
            "health": midas_app.panel_health({"cot": cot}),
        }
    )

    assert "Contracts by Side" in html
    assert "Managed Money" in html
    assert "179,000" in html
    assert "cot_history.csv" in html
    # The totals row is the visible cross-check that the split balances.
    assert "All categories" in html


def test_panel_health_sees_the_populated_card(cot):
    health = midas_app.panel_health({"cot": cot})
    assert health["detail"]["cot"]["state"] in {"ok", "degraded"}
