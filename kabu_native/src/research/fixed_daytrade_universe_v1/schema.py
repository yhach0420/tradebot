"""Official J-Quants V2 field mapping. No field guessing. No Close*Volume Va substitute."""
from __future__ import annotations

from typing import Any

# Documented V2 daily bar fields: https://jpx-jquants.com/en/spec/eq-bars-daily
DAILY_RAW_TO_CANONICAL = {
    "Date": "date",
    "Code": "symbol",
    "O": "open",
    "H": "high",
    "L": "low",
    "C": "close",
    "Vo": "volume",
    "Va": "trading_value",
    "AdjFactor": "adjustment_factor",
}
DAILY_REQUIRED_RAW = ("Date", "Code", "O", "H", "L", "C", "Vo", "Va")
DAILY_OPTIONAL_RAW = ("AdjFactor",)

# Documented V2 listed master: https://jpx-jquants.com/en/spec/eq-master
# Trading unit / 売買単位 is not in the official master schema.
MASTER_DOCUMENTED_FIELDS = (
    "Date",
    "Code",
    "CoName",
    "CoNameEn",
    "S17",
    "S17Nm",
    "S33",
    "S33Nm",
    "ScaleCat",
    "Mkt",
    "MktNm",
    "Mrgn",
    "MrgnNm",
    "ProdCat",
)
MASTER_REQUIRED_RAW = ("Date", "Code", "ProdCat", "Mkt", "MktNm", "S33", "S33Nm")
PROD_CAT_DOMESTIC_STOCK = "011"
TRADING_UNIT_FIELD_IN_MASTER = False
TRADING_UNIT_FIELD_NAME = None

CALENDAR_REQUIRED_RAW = ("Date", "HolDiv")
TSE_SESSION_HOL_DIV = frozenset({"1", "2"})  # 1=business day, 2=TSE half-day


def yyyymmdd(value: Any) -> str:
    s = str(value or "").strip().replace("-", "")
    if len(s) < 8:
        raise ValueError(f"bad_date:{value!r}")
    return s[:8]


def symbol4(value: Any) -> str:
    s = str(value or "").strip()
    if not s:
        raise ValueError("empty_code")
    if len(s) >= 5 and s[:4].isdigit() and s[4] == "0":
        return s[:4]
    if len(s) == 4 and s.isdigit():
        return s
    if s.isdigit() and len(s) > 4:
        return s[:4]
    return s


def raw_keys(row: dict[str, Any] | None) -> list[str]:
    if not isinstance(row, dict):
        return []
    return list(row.keys())


def missing_required(row: dict[str, Any] | None, required: tuple[str, ...]) -> list[str]:
    keys = set(raw_keys(row))
    return [k for k in required if k not in keys]


def daily_schema_status(sample: dict[str, Any] | None) -> dict[str, Any]:
    missing = missing_required(sample, DAILY_REQUIRED_RAW)
    has_va = sample is not None and "Va" in sample
    return {
        "raw_keys": raw_keys(sample),
        "missing_required": missing,
        "ok": not missing,
        "trading_value_field_available": bool(has_va),
        "trading_value_raw_field": "Va" if has_va else None,
        "did_not_substitute_close_times_volume": True,
        "mapping": dict(DAILY_RAW_TO_CANONICAL),
    }


def map_daily_row(raw: dict[str, Any]) -> dict[str, Any]:
    status = daily_schema_status(raw)
    if not status["ok"]:
        raise ValueError(f"daily_schema_mismatch:{status['missing_required']}")
    out: dict[str, Any] = {
        "date": yyyymmdd(raw["Date"]),
        "symbol": symbol4(raw["Code"]),
        "symbol_raw": str(raw["Code"]),
        "open": raw.get("O"),
        "high": raw.get("H"),
        "low": raw.get("L"),
        "close": raw.get("C"),
        "volume": raw.get("Vo"),
        "trading_value": raw.get("Va"),
        "adjustment_factor": raw.get("AdjFactor"),
    }
    return out


def map_master_row(raw: dict[str, Any]) -> dict[str, Any]:
    missing = missing_required(raw, MASTER_REQUIRED_RAW)
    if missing:
        raise ValueError(f"master_schema_mismatch:{missing}")
    prod = str(raw.get("ProdCat") or "")
    return {
        "date": yyyymmdd(raw["Date"]),
        "symbol": symbol4(raw["Code"]),
        "symbol_raw": str(raw["Code"]),
        "name_ja": raw.get("CoName"),
        "name_en": raw.get("CoNameEn"),
        "sector17": raw.get("S17"),
        "sector17_name": raw.get("S17Nm"),
        "sector33": raw.get("S33"),
        "sector33_name": raw.get("S33Nm"),
        "scale_category": raw.get("ScaleCat"),
        "market": raw.get("Mkt"),
        "market_name": raw.get("MktNm"),
        "margin": raw.get("Mrgn"),
        "margin_name": raw.get("MrgnNm"),
        "prod_cat": prod,
        "common_stock_domestic": prod == PROD_CAT_DOMESTIC_STOCK,
        "etf": prod in {"014", "023"},
        "trading_unit": None,
        "trading_unit_field_in_master": False,
        "raw_keys": raw_keys(raw),
    }


def finite_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:
        return None
    return f


assert DAILY_RAW_TO_CANONICAL["Va"] == "trading_value"
assert DAILY_RAW_TO_CANONICAL["Vo"] == "volume"
assert TRADING_UNIT_FIELD_IN_MASTER is False
assert "1" in TSE_SESSION_HOL_DIV and "2" in TSE_SESSION_HOL_DIV
assert "0" not in TSE_SESSION_HOL_DIV and "3" not in TSE_SESSION_HOL_DIV
