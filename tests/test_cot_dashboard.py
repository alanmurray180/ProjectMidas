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
    """A Socrata row spelled the way the live datasets spell it.

    The field names are inconsistent on purpose here: some legs carry
    ``_all`` and some do not, and the swap columns carry a doubled
    underscore.  That is what the API returns — a fixture that tidied it up
    would pass while the dashboard read zeros, which is exactly what
    happened to the producer and other-reportable rows.  The ``_1``/``_2``
    fields are the old and other crop-year duplicates, present so a reader
    that grabs the wrong one is caught.
    """
    return {
        "report_date_as_yyyy_mm_dd": f"{day}T00:00:00.000",
        "market_and_exchange_names": market,
        "cftc_contract_market_code": "088691",
        "open_interest_all": str(oi),
        "prod_merc_positions_long": "50000",
        "prod_merc_positions_long_1": "50000",
        "prod_merc_positions_short": "180000",
        "prod_merc_positions_short_1": "180000",
        "swap_positions_long_all": "30000",
        "swap__positions_short_all": "40000",
        "swap__positions_spread_all": "10000",
        "m_money_positions_long_all": str(mm_long),
        "m_money_positions_short_all": str(mm_short),
        "m_money_positions_spread": "20000",
        "other_rept_positions_long": "60000",
        "other_rept_positions_short": "30000",
        "other_rept_positions_spread": "15000",
        "nonrept_positions_long_all": "40000",
        "nonrept_positions_short_all": "20000",
    }


@respx.mock
def test_history_asks_for_the_contract_by_its_own_code_first():
    """The precise filter returns one row per report, so it asks for one."""
    route = respx.get(SOCRATA_BASE).mock(
        return_value=httpx.Response(
            200,
            json=[_socrata_row("2025-05-13", 140_000, 28_000, "GOLD - COMMODITY EXCHANGE INC.", 500_000)],
        )
    )

    client = CFTCClient()
    client.get_history(weeks=260)

    url = route.calls[0].request.url
    assert "cftc_contract_market_code" in str(url)
    assert url.params["$limit"] == "260"
    assert client.source_used == "cftc_contract_market_code"


@respx.mock
def test_history_falls_back_to_the_name_filter_and_asks_for_more_rows():
    """The loose filter also matches micro gold, so it over-fetches."""
    rows = {"calls": 0}

    def _respond(request):
        rows["calls"] += 1
        # Every precise filter comes back empty, as the live dataset does
        # for the commodity code.
        if "like" not in str(request.url):
            return httpx.Response(200, json=[])
        return httpx.Response(
            200,
            json=[_socrata_row("2025-05-13", 140_000, 28_000, "GOLD - COMMODITY EXCHANGE INC.", 500_000)],
        )

    route = respx.get(SOCRATA_BASE).mock(side_effect=_respond)
    client = CFTCClient()
    client.get_history(weeks=260)

    assert client.source_used == "market_and_exchange_names"
    assert route.calls[-1].request.url.params["$limit"] == "1040"


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
    # The legs whose field names have no ``_all`` suffix — the ones that
    # silently read as zero and emptied the producer and other-reportable
    # rows on the published page.
    assert history[-1].prod_long == 50_000
    assert history[-1].prod_short == 180_000
    assert history[-1].other_long == 60_000
    assert history[-1].other_short == 30_000
    assert history[-1].other_spread == 15_000
    assert history[-1].mm_spread == 20_000


def _history(weeks: int = 60) -> list[COTPosition]:
    """A rising managed-money net, one report a week up to last Tuesday.

    Producers carry the other side, so gross long equals gross short the
    way a real report does and the balance check stays satisfied.
    """
    end = date.today() - timedelta(days=(date.today().weekday() - 1) % 7)
    out = []
    for i in range(weeks):
        mm_long = 120_000 + 1_000 * i
        out.append(
            COTPosition(
                report_date=end - timedelta(weeks=weeks - 1 - i),
                prod_long=50_000,
                prod_short=180_000 + 1_000 * i,
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

    # The one-pager reads the trend with its direction attached.
    assert cot["mm_net_trend"]["4w"] == {
        "text": "+4,000",
        "positive": True,
        "flat": False,
    }


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


def test_csv_export_carries_every_week_of_both_datasets(cot):
    csv_text = midas_app.cot_history_csv(cot["series"])
    lines = csv_text.strip().split("\n")

    # Header plus 60 weekly reports for each of the two datasets, tagged so
    # a spreadsheet can pivot on which report a row came from rather than
    # silently averaging two different instruments.
    assert len(lines) == 121
    assert lines[0].startswith("report_date,market_name,open_interest,mm_long,mm_short")
    assert "dataset" in lines[0]
    datasets = {line.split(",")[-1] for line in lines[1:]}
    assert datasets == {"futures_only", "combined"}


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
    # The one-pager carries the same net and its trend, condensed.
    assert "Positioning (COT)" in html
    assert "Crowded long" in html
    assert html.index("Positioning (COT)") < html.index("Contracts by Side")


def test_panel_health_accepts_either_precise_filter(cot):
    for source in ("cftc_contract_market_code", "market_and_exchange_name"):
        health = midas_app.panel_health({"cot": dict(cot, source=source)})
        assert health["detail"]["cot"]["state"] == "ok"


def test_panel_health_flags_a_loose_name_match(cot):
    """A filter that can return several gold contracts is worth a warning."""
    health = midas_app.panel_health({"cot": dict(cot, source="commodity_name")})
    panel = health["detail"]["cot"]

    assert panel["state"] == "degraded"
    assert "commodity_name" in panel["note"]


@respx.mock
def test_a_leg_the_dataset_renames_is_caught_not_zeroed():
    """The bug this guard exists for: a renamed field must not read as zero."""
    row = _socrata_row("2025-05-13", 140_000, 28_000, "GOLD - COMMODITY EXCHANGE INC.", 500_000)
    # The CFTC moves the producer legs behind a name we do not know.
    row["producer_merchant_long"] = row.pop("prod_merc_positions_long")
    row["producer_merchant_short"] = row.pop("prod_merc_positions_short")
    row.pop("prod_merc_positions_long_1")
    row.pop("prod_merc_positions_short_1")
    respx.get(SOCRATA_BASE).mock(return_value=httpx.Response(200, json=[row]))

    history = CFTCClient().get_history(weeks=52)

    # Still parses — one dead leg must not cost the whole panel — but the
    # books no longer balance, which is what the health check reads.
    assert history[-1].prod_long == 0
    assert history[-1].balances is False


def test_unbalanced_parse_degrades_the_panel(monkeypatch):
    broken = _history()
    # Wipe one leg the way a renamed field would.
    for pos in broken:
        pos.other_long = 0
    monkeypatch.setattr(CFTCClient, "get_history", lambda self, *a, **kw: broken)

    cot = midas_app._fetch_cot_positions()
    panel = midas_app.panel_health({"cot": cot})["detail"]["cot"]

    assert cot["balances"] is False
    assert panel["state"] == "degraded"
    assert "balance" in panel["note"]


def test_a_clean_report_balances(cot):
    assert cot["balances"] is True


def test_both_datasets_are_reported_side_by_side(cot):
    """Futures-only leads; the combined report rides alongside it."""
    assert cot["dataset"] == "futures_only"
    assert cot["dataset_label"] == "Futures only"
    assert cot["combined"]["dataset_label"] == "Options and futures combined"
    assert cot["combined"]["categories"][0]["label"] == "Managed Money"


def test_a_dead_combined_dataset_costs_only_its_block(monkeypatch):
    """The reconciliation view is not worth the whole card."""
    from midas.clients import cftc as cftc_module

    real = _history()

    def _history_or_fail(self, *a, **kw):
        if self.dataset == "combined":
            raise RuntimeError("combined dataset unavailable")
        return real

    monkeypatch.setattr(cftc_module.CFTCClient, "get_history", _history_or_fail)
    cot = midas_app._fetch_cot_positions()

    assert not cot.get("error")
    assert cot["mm_long"] == "179,000"
    assert cot["combined"]["error"]
    # The card still publishes, and the CSV carries the dataset that lived.
    assert {row["dataset"] for row in cot["series"]} == {"futures_only"}
    assert midas_app.panel_health({"cot": cot})["detail"]["cot"]["state"] == "ok"
