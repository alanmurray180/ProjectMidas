"""Swiss gold trade data via the UN Comtrade public preview API.

Fetches annual import/export data for HS 7108 (gold, including gold
plated with platinum, unwrought or semi-manufactured forms) reported
by Switzerland, broken down by partner country.

The public preview endpoint requires no API key and returns up to
500 records per call — more than enough for country-level annual data.
"""

from __future__ import annotations

import logging
from datetime import date

import httpx

log = logging.getLogger(__name__)

_BASE = "https://comtradeapi.un.org/public/v1/preview/C/A/HS"
_REPORTER = "757"  # Switzerland
_CMD_CODE = "7108"  # Gold (HS heading)
_TIMEOUT = 15


class SwissGoldTradeClient:
    """Fetch Swiss gold import/export data by partner country."""

    def get_trade(self, year: int | None = None) -> dict:
        """Return Swiss gold trade data for *year* (default: latest available).

        Returns dict with keys:
          - year: int
          - imports: list of {country, value_usd, weight_kg}
          - exports: list of {country, value_usd, weight_kg}
          - total_import_usd: float
          - total_export_usd: float
          - total_import_kg: float
          - total_export_kg: float
        """
        if year is None:
            year = date.today().year - 1

        imports = self._fetch_flow(year, "M")
        exports = self._fetch_flow(year, "X")

        if not imports and not exports and year == date.today().year - 1:
            year -= 1
            imports = self._fetch_flow(year, "M")
            exports = self._fetch_flow(year, "X")

        return {
            "year": year,
            "imports": imports,
            "exports": exports,
            "total_import_usd": sum(r["value_usd"] for r in imports),
            "total_export_usd": sum(r["value_usd"] for r in exports),
            "total_import_kg": sum(r["weight_kg"] for r in imports),
            "total_export_kg": sum(r["weight_kg"] for r in exports),
        }

    def _fetch_flow(self, year: int, flow_code: str) -> list[dict]:
        params = {
            "reporterCode": _REPORTER,
            "cmdCode": _CMD_CODE,
            "flowCode": flow_code,
            "period": str(year),
            "partnerCode": None,
            "partner2Code": None,
            "customsCode": None,
            "motCode": None,
            "maxRecords": "500",
            "includeDesc": "true",
        }
        params = {k: v for k, v in params.items() if v is not None}

        try:
            with httpx.Client(timeout=_TIMEOUT) as client:
                resp = client.get(_BASE, params=params)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            log.warning("Comtrade fetch failed (%s %d): %s", flow_code, year, exc)
            return []

        records = data.get("data", [])
        results = []
        for rec in records:
            partner = rec.get("partnerDesc", "Unknown")
            if partner.lower() in ("world", "areas, nes"):
                continue
            value = rec.get("primaryValue") or 0
            weight = rec.get("netWgt") or 0
            if value <= 0 and weight <= 0:
                continue
            results.append({
                "country": partner,
                "value_usd": float(value),
                "weight_kg": float(weight),
            })

        results.sort(key=lambda r: r["value_usd"], reverse=True)
        return results
