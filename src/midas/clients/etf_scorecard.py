"""Gold ETF Scorecard — composite signal from tonnes, volume, and price.

Three binary signals, each scored +1 or -1:

  1. **GLD tonnes trend** — 5-day rolling average of GLD physical
     holdings (from SPDR's daily CSV) increasing vs decreasing.
  2. **Composite ETF volume** — summed daily volume across GLD, IAU,
     SGOL, GLDM, PHYS; scored above (+1) or below (-1) the 20-day MA.
  3. **Gold price vs 50-day MA** — GLD close above (+1) or below (-1)
     its 50-day simple moving average.

Overall score ranges from **-3** (strongly bearish) to **+3**
(strongly bullish).
"""

from __future__ import annotations

import io
import logging
from typing import Optional

import httpx
import pandas as pd

from midas.clients.etf import _fetch_yahoo_chart

log = logging.getLogger(__name__)

_GLD_TONNES_URL = (
    "https://www.spdrgoldshares.com/assets/dynamic/GLD/"
    "GLD_US_archive_EN.csv"
)

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,text/csv,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}

_TICKERS = ("GLD", "IAU", "SGOL", "GLDM", "PHYS")


def _rolling_mean(values: list[float], window: int) -> list[Optional[float]]:
    result: list[Optional[float]] = []
    for i in range(len(values)):
        if i < window - 1:
            result.append(None)
        else:
            result.append(sum(values[i - window + 1 : i + 1]) / window)
    return result


class GoldETFScorecard:
    """Compute ETF flow signals and overall score."""

    def compute(self) -> dict:
        tonnes_records = self._fetch_gld_tonnes()
        tonnes_sig = self._score_tonnes(tonnes_records)

        all_ohlcv: dict[str, list[dict]] = {}
        for ticker in _TICKERS:
            try:
                all_ohlcv[ticker] = _fetch_yahoo_chart(ticker, range_="3mo")
            except Exception as exc:
                log.warning("Scorecard: failed to fetch %s: %s", ticker, exc)

        volume_sig = self._score_composite_volume(all_ohlcv)
        price_sig = self._score_price_vs_ma(all_ohlcv.get("GLD", []))

        total = tonnes_sig["score"] + volume_sig["score"] + price_sig["score"]

        return {
            "total_score": total,
            "tonnes": tonnes_sig,
            "volume": volume_sig,
            "price": price_sig,
            "tickers_loaded": list(all_ohlcv.keys()),
        }

    # ------------------------------------------------------------------
    # GLD tonnes from SPDR CSV
    # ------------------------------------------------------------------

    @staticmethod
    def _fetch_gld_tonnes() -> list[dict]:
        try:
            resp = httpx.get(
                _GLD_TONNES_URL,
                headers=_BROWSER_HEADERS,
                timeout=30,
                follow_redirects=True,
            )
            resp.raise_for_status()
        except Exception as exc:
            log.warning("GLD tonnes CSV fetch failed: %s", exc)
            return []

        try:
            df = pd.read_csv(io.StringIO(resp.text))
        except Exception as exc:
            log.warning("GLD tonnes CSV parse failed: %s", exc)
            return []

        date_col = None
        tonnes_col = None
        for col in df.columns:
            lower = col.strip().lower()
            if "date" in lower and date_col is None:
                date_col = col
            if "tonne" in lower:
                tonnes_col = col

        if not date_col or not tonnes_col:
            log.warning(
                "GLD CSV: can't find date/tonnes columns in %s",
                list(df.columns),
            )
            return []

        records: list[dict] = []
        for _, row in df.iterrows():
            try:
                d = pd.to_datetime(row[date_col]).date()
                raw = str(row[tonnes_col]).replace(",", "").strip()
                if raw in ("", "-", "N/A"):
                    continue
                t = float(raw)
                records.append({"date": d, "tonnes": t})
            except (ValueError, TypeError):
                continue

        return sorted(records, key=lambda r: r["date"])

    # ------------------------------------------------------------------
    # Signal 1: GLD tonnes 5-day rolling average trend
    # ------------------------------------------------------------------

    @staticmethod
    def _score_tonnes(records: list[dict]) -> dict:
        if len(records) < 6:
            return {
                "score": 0,
                "label": "N/A",
                "reason": "insufficient tonnes data",
            }

        tonnes = [r["tonnes"] for r in records]
        ra5 = _rolling_mean(tonnes, 5)

        current_ra = ra5[-1]
        prev_ra = ra5[-2]

        if current_ra is None or prev_ra is None:
            return {
                "score": 0,
                "label": "N/A",
                "reason": "insufficient data for 5d avg",
            }

        score = 1 if current_ra > prev_ra else -1
        return {
            "score": score,
            "label": "Increasing" if score == 1 else "Decreasing",
            "current_5d_avg": round(current_ra, 2),
            "prev_5d_avg": round(prev_ra, 2),
            "latest_tonnes": tonnes[-1],
            "latest_date": records[-1]["date"].isoformat(),
        }

    # ------------------------------------------------------------------
    # Signal 2: composite volume vs 20-day MA
    # ------------------------------------------------------------------

    @staticmethod
    def _score_composite_volume(
        all_ohlcv: dict[str, list[dict]],
    ) -> dict:
        if not all_ohlcv:
            return {
                "score": 0,
                "label": "N/A",
                "reason": "no OHLCV data",
            }

        composite: dict = {}
        for ticker, records in all_ohlcv.items():
            for r in records:
                d = r["date"]
                vol = r.get("volume") or 0
                composite.setdefault(d, {"volume": 0, "tickers": 0})
                composite[d]["volume"] += vol
                composite[d]["tickers"] += 1

        sorted_dates = sorted(composite.keys())
        if len(sorted_dates) < 21:
            return {
                "score": 0,
                "label": "N/A",
                "reason": f"only {len(sorted_dates)} days of volume data",
            }

        volumes = [composite[d]["volume"] for d in sorted_dates]
        ma20 = _rolling_mean(volumes, 20)

        latest_vol = volumes[-1]
        latest_ma = ma20[-1]

        if latest_ma is None or latest_ma == 0:
            return {
                "score": 0,
                "label": "N/A",
                "reason": "insufficient data for 20d MA",
            }

        score = 1 if latest_vol > latest_ma else -1
        return {
            "score": score,
            "label": "Above 20d MA" if score == 1 else "Below 20d MA",
            "latest_volume": latest_vol,
            "ma_20d": round(latest_ma),
            "latest_date": sorted_dates[-1].isoformat(),
            "ticker_count": composite[sorted_dates[-1]]["tickers"],
        }

    # ------------------------------------------------------------------
    # Signal 3: gold price (GLD close) vs 50-day MA
    # ------------------------------------------------------------------

    @staticmethod
    def _score_price_vs_ma(gld_records: list[dict]) -> dict:
        if len(gld_records) < 50:
            return {
                "score": 0,
                "label": "N/A",
                "reason": f"only {len(gld_records)} days of GLD price data",
            }

        closes = [r["close"] for r in gld_records]
        ma50 = _rolling_mean(closes, 50)

        latest_close = closes[-1]
        latest_ma = ma50[-1]

        if latest_ma is None:
            return {
                "score": 0,
                "label": "N/A",
                "reason": "insufficient data for 50d MA",
            }

        score = 1 if latest_close > latest_ma else -1
        return {
            "score": score,
            "label": "Above 50d MA" if score == 1 else "Below 50d MA",
            "latest_close": latest_close,
            "ma_50d": round(latest_ma, 2),
            "latest_date": gld_records[-1]["date"].isoformat(),
        }
