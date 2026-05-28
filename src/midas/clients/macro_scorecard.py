"""Macro Scorecard — composite signal from DXY, real yield, CPI, and VIX.

Four binary signals, each scored +1 or −1 from gold's perspective:

  1. **DXY vs 20-day MA** — weak dollar (+1) supports gold; strong
     dollar (−1) is headwind.
  2. **10Y real yield 5-day trend** — falling yields (+1) are
     bullish for gold; rising yields (−1) increase opportunity cost.
  3. **CPI YoY trend** — rising inflation (+1) drives safe-haven
     demand; falling inflation (−1) reduces it.
  4. **VIX vs 20-day MA** — elevated volatility (+1) drives
     safe-haven flows; low volatility (−1) favours risk assets.

Overall score ranges from **−4** (strongly bearish) to **+4**
(strongly bullish).
"""

from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger(__name__)


def _rolling_mean(values: list[float], window: int) -> list[Optional[float]]:
    result: list[Optional[float]] = []
    for i in range(len(values)):
        if i < window - 1:
            result.append(None)
        else:
            result.append(sum(values[i - window + 1 : i + 1]) / window)
    return result


class MacroScorecard:
    """Compute macro signals and overall score for gold."""

    def compute(self) -> dict:
        from midas.clients.dxy import DXYClient
        from midas.clients.fred import FREDClient
        from midas.clients.vix import VIXClient

        dxy_sig = self._score_dxy(self._safe(DXYClient().get_prices, range_="3mo"))
        yield_sig = self._score_yield(self._safe(FREDClient().get_real_yield_10y, days=60))
        cpi_sig = self._score_cpi(self._safe(FREDClient().get_cpi_yoy, months=6))
        vix_sig = self._score_vix(self._safe(VIXClient().get_prices, range_="3mo"))

        total = dxy_sig["score"] + yield_sig["score"] + cpi_sig["score"] + vix_sig["score"]

        return {
            "total_score": total,
            "dxy": dxy_sig,
            "yield_10y": yield_sig,
            "cpi": cpi_sig,
            "vix": vix_sig,
        }

    @staticmethod
    def _safe(fn, **kwargs):
        try:
            return fn(**kwargs)
        except Exception as exc:
            log.warning("MacroScorecard fetch failed: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Signal 1: DXY below 20d MA → bullish for gold (+1)
    # ------------------------------------------------------------------

    @staticmethod
    def _score_dxy(prices: list[dict]) -> dict:
        if len(prices) < 21:
            return {"score": 0, "label": "N/A", "reason": f"only {len(prices)} days of DXY data"}

        closes = [p["close"] for p in prices]
        ma20 = _rolling_mean(closes, 20)
        latest = closes[-1]
        latest_ma = ma20[-1]

        if latest_ma is None:
            return {"score": 0, "label": "N/A", "reason": "insufficient data for 20d MA"}

        score = 1 if latest < latest_ma else -1
        return {
            "score": score,
            "label": "Below 20d MA" if score == 1 else "Above 20d MA",
            "latest": round(latest, 2),
            "ma_20d": round(latest_ma, 2),
            "latest_date": prices[-1]["date"].isoformat(),
            "commentary": "Weak dollar supports gold" if score == 1 else "Strong dollar headwind for gold",
        }

    # ------------------------------------------------------------------
    # Signal 2: 10Y real yield falling → bullish for gold (+1)
    # ------------------------------------------------------------------

    @staticmethod
    def _score_yield(records: list[dict]) -> dict:
        if len(records) < 6:
            return {"score": 0, "label": "N/A", "reason": f"only {len(records)} yield records"}

        values = [r["value"] for r in records]
        ra5 = _rolling_mean(values, 5)

        current_ra = ra5[-1]
        prev_ra = ra5[-2]

        if current_ra is None or prev_ra is None:
            return {"score": 0, "label": "N/A", "reason": "insufficient data for 5d avg"}

        score = 1 if current_ra < prev_ra else -1
        return {
            "score": score,
            "label": "Falling" if score == 1 else "Rising",
            "latest": f"{values[-1]:.2f}%",
            "current_5d_avg": f"{current_ra:.2f}%",
            "prev_5d_avg": f"{prev_ra:.2f}%",
            "latest_date": records[-1]["date"].isoformat(),
            "commentary": "Falling real yields bullish for gold" if score == 1 else "Rising real yields increase opportunity cost",
        }

    # ------------------------------------------------------------------
    # Signal 3: CPI YoY rising → bullish for gold (+1)
    # ------------------------------------------------------------------

    @staticmethod
    def _score_cpi(records: list[dict]) -> dict:
        if len(records) < 2:
            return {"score": 0, "label": "N/A", "reason": f"only {len(records)} CPI records"}

        latest = records[-1]["value"]
        previous = records[-2]["value"]

        score = 1 if latest > previous else -1
        return {
            "score": score,
            "label": "Rising" if score == 1 else "Falling",
            "latest": f"{latest:.1f}%",
            "previous": f"{previous:.1f}%",
            "change": f"{latest - previous:+.1f}",
            "latest_date": records[-1]["date"].isoformat(),
            "commentary": "Rising inflation supports gold as hedge" if score == 1 else "Falling inflation reduces safe-haven demand",
        }

    # ------------------------------------------------------------------
    # Signal 4: VIX above 20d MA → bullish for gold (+1)
    # ------------------------------------------------------------------

    @staticmethod
    def _score_vix(prices: list[dict]) -> dict:
        if len(prices) < 21:
            return {"score": 0, "label": "N/A", "reason": f"only {len(prices)} days of VIX data"}

        closes = [p["close"] for p in prices]
        ma20 = _rolling_mean(closes, 20)
        latest = closes[-1]
        latest_ma = ma20[-1]

        if latest_ma is None:
            return {"score": 0, "label": "N/A", "reason": "insufficient data for 20d MA"}

        score = 1 if latest > latest_ma else -1
        return {
            "score": score,
            "label": "Above 20d MA" if score == 1 else "Below 20d MA",
            "latest": round(latest, 2),
            "ma_20d": round(latest_ma, 2),
            "latest_date": prices[-1]["date"].isoformat(),
            "commentary": "Elevated VIX drives safe-haven flows to gold" if score == 1 else "Low volatility favours risk assets over gold",
        }
