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

import csv
import io
import logging
import re
from datetime import date, datetime
from typing import Optional

import httpx

from midas.clients.etf import _fetch_yahoo_chart

log = logging.getLogger(__name__)

# SPDR endpoints — kept as first-choice but with short timeouts since
# Cloudflare blocks most server-side callers.
_GLD_API_URL = (
    "https://api.spdrgoldshares.com/api/v1/historical-archive"
)
_GLD_API_PARAMS = {"product": "gld", "exchange": "NYSE", "lang": "en"}
_GLD_CSV_URL = (
    "https://www.spdrgoldshares.com/assets/dynamic/GLD/"
    "GLD_US_archive_EN.csv"
)
_GLD_PAGE_URL = "https://www.spdrgoldshares.com/usa/gld/"
_SPDR_TIMEOUT = 5  # seconds — fail fast so Yahoo fallback has time

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html, application/json, text/csv, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Ch-Ua": '"Chromium";v="125", "Google Chrome";v="125", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "DNT": "1",
}

_OZ_PER_TONNE = 32_150.7466

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


def _parse_json_payload(payload) -> list[dict]:
    """Extract date/tonnes records from the SPDR JSON API response."""
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
        log.warning("GLD JSON: no rows in response (type=%s)", type(payload).__name__)
        return []

    sample = rows[0] if rows else {}
    date_key = _find_key(sample, "date")
    tonnes_key = _find_key(sample, "tonne")

    if not date_key or not tonnes_key:
        log.warning("GLD JSON: can't find date/tonnes in keys %s", list(sample.keys()))
        return []

    log.info("GLD JSON: date_key=%r, tonnes_key=%r, %d rows", date_key, tonnes_key, len(rows))

    records: list[dict] = []
    for row in rows:
        d = _parse_date(row.get(date_key))
        t = _parse_float(row.get(tonnes_key))
        if d is not None and t is not None and t > 0:
            records.append({"date": d, "tonnes": t})

    records.sort(key=lambda r: r["date"])
    return records


def _parse_csv_text(text: str) -> list[dict]:
    """Extract date/tonnes records from the SPDR CSV download."""
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if not header:
        return []

    date_idx: Optional[int] = None
    tonnes_idx: Optional[int] = None
    for i, col in enumerate(header):
        lower = col.strip().lower()
        if "date" in lower and date_idx is None:
            date_idx = i
        if "tonne" in lower:
            tonnes_idx = i

    if date_idx is None or tonnes_idx is None:
        log.warning("GLD CSV: can't find date/tonnes in columns %s", header)
        return []
    log.info("GLD CSV: date_idx=%d, tonnes_idx=%d", date_idx, tonnes_idx)

    max_idx = max(date_idx, tonnes_idx)
    records: list[dict] = []
    for row in reader:
        if len(row) <= max_idx:
            continue
        d = _parse_date(row[date_idx])
        t = _parse_float(row[tonnes_idx])
        if d is not None and t is not None and t > 0:
            records.append({"date": d, "tonnes": t})

    records.sort(key=lambda r: r["date"])
    return records


def _scrape_gld_page_tonnes(html: str) -> Optional[float]:
    """Extract the latest tonnes value from the SPDR GLD HTML page."""
    # Look for "Tonnes of Gold in Trust" or similar labels followed by a number
    patterns = [
        r'(?i)tonnes?\s*(?:of\s+gold)?[^0-9]{0,60}?([\d,]+\.?\d*)\s*(?:t\b|tonnes?)?',
        r'(?i)gold\s+in\s+(?:the\s+)?trust[^0-9]{0,40}?([\d,]+\.?\d*)',
        r'(?i)total\s+(?:gold\s+)?holdings?[^0-9]{0,40}?([\d,]+\.?\d*)',
    ]
    for pat in patterns:
        m = re.search(pat, html)
        if m:
            val = _parse_float(m.group(1))
            if val and 100 < val < 2000:
                log.info("GLD page scrape: matched %.2f t via pattern %r", val, pat[:40])
                return val

    # Also check for JSON embedded in <script> tags
    json_pat = re.findall(r'(?i)"tonne[^"]*"\s*:\s*"?([\d,]+\.?\d*)"?', html)
    for raw in json_pat:
        val = _parse_float(raw)
        if val and 100 < val < 2000:
            log.info("GLD page scrape: found %.2f t in embedded JSON", val)
            return val

    return None


def _fetch_gld_tonnes_yahoo() -> list[dict]:
    """Estimate daily GLD tonnes from the v8 chart API for trend scoring.

    Uses GLD_price / gold_price ≈ oz_per_share for each trading day,
    then estimated_shares × oz_per_share / 32,150 ≈ tonnes.
    Day-to-day changes in this ratio reflect GLD's premium/discount
    to NAV, which is a leading indicator of creation/redemption.
    """
    try:
        gld_data = _fetch_yahoo_chart("GLD", range_="1mo")
    except Exception as exc:
        log.warning("Yahoo GLD chart failed: %s", exc)
        return []
    if not gld_data:
        log.warning("Yahoo GLD chart returned no data")
        return []

    gold_by_date: dict = {}
    try:
        gold_data = _fetch_yahoo_chart("GC=F", range_="1mo")
        gold_by_date = {r["date"]: r["close"] for r in gold_data}
    except Exception as exc:
        log.info("GC=F chart failed, will derive gold price from GLD: %s", exc)

    _GLD_SHARES_APPROX = 278_000_000  # ~278M as of Q1 2025

    records: list[dict] = []
    for r in gld_data:
        gld_close = r["close"]
        gold_close = gold_by_date.get(r["date"])
        if not gold_close or gold_close <= 0:
            gold_close = gld_close / 0.0920
        oz_per_share = gld_close / gold_close
        tonnes = _GLD_SHARES_APPROX * oz_per_share / _OZ_PER_TONNE
        records.append({"date": r["date"], "tonnes": round(tonnes, 2), "estimated": True})

    if records:
        log.info(
            "Yahoo GLD estimate: %d days, latest=%.1f t, first=%.1f t",
            len(records), records[-1]["tonnes"], records[0]["tonnes"],
        )
    return records


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
    # GLD tonnes — SPDR endpoints (fast-fail) then Yahoo fallback
    # ------------------------------------------------------------------

    @staticmethod
    def _fetch_gld_tonnes() -> list[dict]:
        # SPDR attempts use very short timeouts (5s) because Cloudflare
        # often holds connections open for 30s+ before rejecting them,
        # which would starve the Yahoo fallback of Render's request budget.
        with httpx.Client(
            http2=True,
            follow_redirects=True,
            timeout=_SPDR_TIMEOUT,
        ) as client:
            # --- Attempt 1: SPDR JSON API ---
            try:
                resp = client.get(
                    _GLD_API_URL,
                    params=_GLD_API_PARAMS,
                    headers={
                        **_BROWSER_HEADERS,
                        "Referer": "https://www.spdrgoldshares.com/",
                        "Origin": "https://www.spdrgoldshares.com",
                        "Sec-Fetch-Mode": "cors",
                        "Sec-Fetch-Site": "same-site",
                        "Sec-Fetch-Dest": "empty",
                    },
                )
                log.info("GLD JSON API: status=%d, len=%d", resp.status_code, len(resp.content))
                resp.raise_for_status()
                records = _parse_json_payload(resp.json())
                if records:
                    log.info("GLD JSON API: %d records, latest=%.2f t", len(records), records[-1]["tonnes"])
                    return records
            except Exception as exc:
                log.warning("GLD JSON API failed: %s", exc)

            # --- Attempt 2: SPDR CSV ---
            try:
                resp = client.get(
                    _GLD_CSV_URL,
                    headers={**_BROWSER_HEADERS, "Accept": "text/csv, */*"},
                )
                log.info("GLD CSV: status=%d, len=%d", resp.status_code, len(resp.content))
                resp.raise_for_status()
                records = _parse_csv_text(resp.text)
                if records:
                    log.info("GLD CSV: %d records, latest=%.2f t", len(records), records[-1]["tonnes"])
                    return records
            except Exception as exc:
                log.warning("GLD CSV failed: %s", exc)

            # --- Attempt 3: scrape the SPDR GLD product page ---
            try:
                resp = client.get(
                    _GLD_PAGE_URL,
                    headers={
                        **_BROWSER_HEADERS,
                        "Sec-Fetch-Mode": "navigate",
                        "Sec-Fetch-Site": "none",
                        "Sec-Fetch-Dest": "document",
                    },
                )
                log.info("GLD page: status=%d, len=%d", resp.status_code, len(resp.content))
                resp.raise_for_status()
                tonnes = _scrape_gld_page_tonnes(resp.text)
                if tonnes:
                    return [{"date": date.today(), "tonnes": tonnes}]
            except Exception as exc:
                log.warning("GLD page scrape failed: %s", exc)

        # --- Attempt 4: Yahoo Finance estimate ---
        try:
            records = _fetch_gld_tonnes_yahoo()
            if records:
                return records
        except Exception as exc:
            log.warning("Yahoo GLD fallback failed: %s", exc)

        log.warning("GLD: all data sources failed")
        return []

    # ------------------------------------------------------------------
    # Signal 1: GLD tonnes 5-day rolling average trend
    # ------------------------------------------------------------------

    @staticmethod
    def _score_tonnes(records: list[dict]) -> dict:
        if len(records) < 6:
            result: dict = {
                "score": 0,
                "label": "N/A",
                "reason": f"insufficient tonnes data ({len(records)} records)",
            }
            if records:
                is_estimate = any(r.get("estimated") for r in records)
                result["latest_tonnes"] = records[-1]["tonnes"]
                result["latest_date"] = records[-1]["date"].isoformat()
                result["estimated"] = is_estimate
                result["label"] = "Estimated (Yahoo)" if is_estimate else "N/A"
            return result

        is_estimate = any(r.get("estimated") for r in records)
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
        result = {
            "score": score,
            "label": "Increasing" if score == 1 else "Decreasing",
            "current_5d_avg": round(current_ra, 2),
            "prev_5d_avg": round(prev_ra, 2),
            "latest_tonnes": tonnes[-1],
            "latest_date": records[-1]["date"].isoformat(),
        }
        if is_estimate:
            result["estimated"] = True
        return result

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
