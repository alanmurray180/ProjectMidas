"""Swiss gold trade data from BAZG (Federal Office for Customs).

Primary source: the official OGD_GOLD_LAND.csv published by BAZG on
the I14Y interoperability platform.  Contains monthly import/export
data for HS 7108.1200 (gold in unwrought form) broken down by partner
country and gold type (mining gold key 911, refined ≥99.5% keys
912/913, refined <99.5% key 914) from January 2021 onwards.

Fallback: UN Comtrade public preview API (no key required).
"""

from __future__ import annotations

import csv
import io
import logging
from collections import defaultdict
from datetime import date

import httpx

log = logging.getLogger(__name__)

_BAZG_CSV_URL = (
    "https://www.bazg.admin.ch/dam/bazg/de/dokumente/abgaben/"
    "aussenhandelstatistik/diffusion/Daten/Waren/"
    "OGD_GOLD_LAND.csv.download.csv/OGD_GOLD_LAND.csv"
)

_COMTRADE_BASE = "https://comtradeapi.un.org/public/v1/preview/C/A/HS"
_COMTRADE_REPORTER = "757"
_COMTRADE_CMD = "7108"

_TIMEOUT = 15

_GOLD_TYPE_LABELS = {
    "911": "Mining gold",
    "912": "Refined ≥99.5% (for refining)",
    "913": "Refined ≥99.5% (other)",
    "914": "Refined <99.5%",
}

_COUNTRY_EN = {
    "Österreich": "Austria", "Deutschland": "Germany", "Frankreich": "France",
    "Italien": "Italy", "Spanien": "Spain", "Niederlande": "Netherlands",
    "Belgien": "Belgium", "Vereinigtes Königreich": "United Kingdom",
    "Schweden": "Sweden", "Dänemark": "Denmark", "Norwegen": "Norway",
    "Finnland": "Finland", "Polen": "Poland", "Tschechien": "Czech Republic",
    "Ungarn": "Hungary", "Rumänien": "Romania", "Bulgarien": "Bulgaria",
    "Griechenland": "Greece", "Portugal": "Portugal", "Irland": "Ireland",
    "Türkei": "Turkey", "Russland": "Russia",
    "Vereinigte Staaten": "United States", "Kanada": "Canada",
    "Mexiko": "Mexico", "Brasilien": "Brazil", "Argentinien": "Argentina",
    "Kolumbien": "Colombia", "Peru": "Peru", "Chile": "Chile",
    "China": "China", "Japan": "Japan", "Indien": "India",
    "Südkorea": "South Korea", "Indonesien": "Indonesia",
    "Thailand": "Thailand", "Vietnam": "Vietnam", "Malaysia": "Malaysia",
    "Philippinen": "Philippines", "Singapur": "Singapore",
    "Hongkong": "Hong Kong", "Taiwan": "Taiwan",
    "Vereinigte Arabische Emirate": "UAE", "Saudi-Arabien": "Saudi Arabia",
    "Südafrika": "South Africa", "Australien": "Australia",
    "Neuseeland": "New Zealand", "Ägypten": "Egypt",
    "Ghana": "Ghana", "Tansania": "Tanzania", "Burkina Faso": "Burkina Faso",
    "Mali": "Mali", "Guinea": "Guinea", "Usbekistan": "Uzbekistan",
    "Kasachstan": "Kazakhstan", "Tadschikistan": "Tajikistan",
    "Kirgisistan": "Kyrgyzstan",
}


class SwissGoldTradeClient:
    """Fetch Swiss gold import/export data by partner country."""

    def get_trade(self, year: int | None = None) -> dict:
        if year is None:
            year = date.today().year - 1

        result = self._try_bazg(year)
        if not result:
            result = self._try_bazg(year - 1)
            if result:
                year = year - 1

        if not result:
            result = self._try_comtrade(year)
            if not result:
                result = self._try_comtrade(year - 1)
                if result:
                    year = year - 1

        if not result:
            return {
                "year": year, "source": "none",
                "imports": [], "exports": [],
                "by_type": [],
                "total_import_usd": 0, "total_export_usd": 0,
                "total_import_kg": 0, "total_export_kg": 0,
            }

        result["year"] = year
        return result

    # ------------------------------------------------------------------
    # Primary: BAZG OGD CSV
    # ------------------------------------------------------------------

    def _try_bazg(self, year: int) -> dict | None:
        try:
            with httpx.Client(timeout=_TIMEOUT) as client:
                resp = client.get(_BAZG_CSV_URL, follow_redirects=True)
                resp.raise_for_status()
                text = resp.text
        except Exception as exc:
            log.warning("BAZG CSV fetch failed: %s", exc)
            return None

        return self._parse_bazg_csv(text, year)

    def _parse_bazg_csv(self, text: str, year: int) -> dict | None:
        reader = csv.DictReader(io.StringIO(text), delimiter=";")

        imp_by_country: dict[str, dict] = defaultdict(
            lambda: {"value_usd": 0.0, "weight_kg": 0.0}
        )
        exp_by_country: dict[str, dict] = defaultdict(
            lambda: {"value_usd": 0.0, "weight_kg": 0.0}
        )
        by_type: dict[str, dict] = defaultdict(
            lambda: {"import_kg": 0.0, "export_kg": 0.0}
        )

        found = False
        for row in reader:
            try:
                row_year = int(row.get("Jahr", 0))
            except (ValueError, TypeError):
                continue
            if row_year != year:
                continue
            found = True

            direction = row.get("Verkehrsrichtung", "").strip()
            country_raw = row.get("Land_Txt", "").strip()
            country = _COUNTRY_EN.get(country_raw, country_raw)
            gold_key = row.get("Steuerungselement", "").strip()

            try:
                kg = float(row.get("Menge_kg", 0) or 0)
            except (ValueError, TypeError):
                kg = 0.0
            try:
                usd = float(row.get("Wert_USD", 0) or 0)
            except (ValueError, TypeError):
                usd = 0.0

            if direction == "I":
                imp_by_country[country]["value_usd"] += usd
                imp_by_country[country]["weight_kg"] += kg
                by_type[gold_key]["import_kg"] += kg
            elif direction == "E":
                exp_by_country[country]["value_usd"] += usd
                exp_by_country[country]["weight_kg"] += kg
                by_type[gold_key]["export_kg"] += kg

        if not found:
            return None

        imports = [
            {"country": c, **v}
            for c, v in imp_by_country.items()
            if v["value_usd"] > 0 or v["weight_kg"] > 0
        ]
        imports.sort(key=lambda r: r["weight_kg"], reverse=True)

        exports = [
            {"country": c, **v}
            for c, v in exp_by_country.items()
            if v["value_usd"] > 0 or v["weight_kg"] > 0
        ]
        exports.sort(key=lambda r: r["weight_kg"], reverse=True)

        type_list = []
        for key, vals in sorted(by_type.items()):
            type_list.append({
                "key": key,
                "label": _GOLD_TYPE_LABELS.get(key, f"Key {key}"),
                "import_kg": vals["import_kg"],
                "export_kg": vals["export_kg"],
            })

        return {
            "source": "BAZG",
            "imports": imports,
            "exports": exports,
            "by_type": type_list,
            "total_import_usd": sum(r["value_usd"] for r in imports),
            "total_export_usd": sum(r["value_usd"] for r in exports),
            "total_import_kg": sum(r["weight_kg"] for r in imports),
            "total_export_kg": sum(r["weight_kg"] for r in exports),
        }

    # ------------------------------------------------------------------
    # Fallback: UN Comtrade public preview API
    # ------------------------------------------------------------------

    def _try_comtrade(self, year: int) -> dict | None:
        imports = self._comtrade_flow(year, "M")
        exports = self._comtrade_flow(year, "X")
        if not imports and not exports:
            return None
        return {
            "source": "UN Comtrade",
            "imports": imports,
            "exports": exports,
            "by_type": [],
            "total_import_usd": sum(r["value_usd"] for r in imports),
            "total_export_usd": sum(r["value_usd"] for r in exports),
            "total_import_kg": sum(r["weight_kg"] for r in imports),
            "total_export_kg": sum(r["weight_kg"] for r in exports),
        }

    def _comtrade_flow(self, year: int, flow_code: str) -> list[dict]:
        params = {
            "reporterCode": _COMTRADE_REPORTER,
            "cmdCode": _COMTRADE_CMD,
            "flowCode": flow_code,
            "period": str(year),
            "maxRecords": "500",
            "includeDesc": "true",
        }
        try:
            with httpx.Client(timeout=_TIMEOUT) as client:
                resp = client.get(_COMTRADE_BASE, params=params)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            log.warning("Comtrade fetch failed (%s %d): %s", flow_code, year, exc)
            return []

        results = []
        for rec in data.get("data", []):
            partner = rec.get("partnerDesc", "Unknown")
            if partner.lower() in ("world", "areas, nes"):
                continue
            value = float(rec.get("primaryValue") or 0)
            weight = float(rec.get("netWgt") or 0)
            if value <= 0 and weight <= 0:
                continue
            results.append({
                "country": partner,
                "value_usd": value,
                "weight_kg": weight,
            })
        results.sort(key=lambda r: r["weight_kg"], reverse=True)
        return results
