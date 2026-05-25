"""Gold ETF Scorecard — composite signal from tonnes, volume, and price.

Three binary signals, each scored +1 or -1:

  1. **GLD tonnes trend** — 5-day rolling average of GLD physical
     holdings (from SPDR's JSON API) increasing vs decreasing.
  2. **Composite ETF volume** — summed daily volume across GLD, IAU,
     SGOL, GLDM, PHYS; scored above (+1) or below (-1) the 20-day MA.
  3. **Gold price vs 50-day MA** — GLD close above (+1) or below (-1)
     its 50-day simple moving average.

Overall score ranges from **-3** (strongly bearish) to **+3**
(strongly bullish).
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Optional

import httpx

from midas.clients.etf import _fetch_yahoo_chart

log = logging.getLogger(__name__)

_GLD_API_URL = (
    "https://api.spdrgoldshares.com/api/v1/historical-archive"
)
_GLD_API_PARAMS = {"product": "gld", "exchange": "NYSE", "lang": "en"}

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.spdrgoldshares.com/",
    "Origin": "https://www.spdrgoldshares.com",
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


def _find_key(record: dict, *candidates: str) -> Optional[str]:
    """Find a key in *record* matching any candidate (case-insensitive substring)."""
    lower_map = {k.lower(): k for k in record}
    for c in candidates:
        for lk, real_key in lower_map.items():
            if c in lk:
                return real_key
    return None


def _parse_float(val) -> Optional[float]:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).replace(",", "").replace("$", "").strip()
    if not s or s in ("-", "N/A", "n/a", "—", ""):
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _parse_date(val) -> Optional[date]:
    if val is None:
        return None
    if isinstance(val, date):
        return val
    s = str(val).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d-%b-%Y", "%d/%m/%Y",
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    try:
        from dateutil.parser import parse as du_parse
        return du_parse(s).date()
    except Exception:
        pass
    return None


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
    # GLD tonnes from SPDR JSON API
    # ------------------------------------------------------------------

    @staticmethod
    def _fetch_gld_tonnes() -> list[dict]:
        try:
            resp = httpx.get(
                _GLD_API_URL,
                params=_GLD_API_PARAMS,
                headers=_BROWSER_HEADERS,
                timeout=30,
                follow_redirects=True,
            )
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:
            log.warning("GLD API fetch failed: %s", exc)
            return []

        # The API may return a list directly or wrap it in a key.
        rows: list = []
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict):
            for key in ("data", "records", "results", "items", "rows"):
                if key in payload and isinstance(payload[key], list):
                    rows = payload[key]
                    break
            if not rows:
                for v in payload.values():
                    if isinstance(v, list) and len(v) > 10:
                        rows = v
                        break

        if not rows:
            log.warning("GLD API: no rows found in response (type=%s)", type(payload).__name__)
            return []

        # Discover field names from the first record.
        sample = rows[0] if rows else {}
        date_key = _find_key(sample, "date")
        tonnes_key = _find_key(sample, "tonne")

        if not date_key or not tonnes_key:
            log.warning(
                "GLD API: can't find date/tonnes fields in keys %s",
                list(sample.keys()),
            )
            return []

        log.info(
            "GLD API: using date_key=%r, tonnes_key=%r from %d rows",
            date_key, tonnes_key, len(rows),
        )

        records: list[dict] = []
        for row in rows:
            d = _parse_date(row.get(date_key))
            t = _parse_float(row.get(tonnes_key))
            if d is not None and t is not None and t > 0:
                records.append({"date": d, "tonnes": t})

        records.sort(key=lambda r: r["date"])

        if records:
            log.info(
                "GLD API: parsed %d records, range %s to %s, latest=%.2f t",
                len(records), records[0]["date"], records[-1]["date"],
                records[-1]["tonnes"],
            )
        else:
            log.warning("GLD API: parsed 0 valid records from %d rows", len(rows))

        return records

    # ------------------------------------------------------------------
    # Signal 1: GLD tonnes 5-day rolling average trend
    # ------------------------------------------------------------------

    @staticmethod
    def _score_tonnes(records: list[dict]) -> dict:
        if len(records) < 6:
            return {
                "score": 0,
                "label": "N/A",
                "reason": f"insufficient tonnes data ({len(records)} records)",
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
