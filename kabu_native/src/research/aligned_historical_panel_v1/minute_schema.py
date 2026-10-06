"""Official J-Quants minute schema. No field guessing. No missing-minute OHLC fill."""
from __future__ import annotations

from typing import Any

from research.aligned_historical_panel_v1 import MINUTE_OPTIONAL_RAW, MINUTE_REQUIRED_RAW
from research.fixed_daytrade_universe_v1.schema import symbol4, yyyymmdd


def minute_schema_status(sample: dict[str, Any] | None) -> dict[str, Any]:
    keys = list(sample.keys()) if isinstance(sample, dict) else []
    missing = [k for k in MINUTE_REQUIRED_RAW if k not in keys]
    has_va = sample is not None and "Va" in (sample or {})
    return {
        "raw_keys": keys,
        "missing_required": missing,
        "ok": not missing,
        "trading_value_raw_field": "Va" if has_va else None,
        "mapping": {
            "Date": "date",
            "Time": "time_label",
            "Code": "symbol",
            "O": "open",
            "H": "high",
            "L": "low",
            "C": "close",
            "Vo": "volume",
            "Va": "trading_value",
        },
        "did_not_guess_fields": True,
        "empty_minutes_omitted_official": True,
        "no_synthetic_ohlc_fill": True,
        "optional_present": [k for k in MINUTE_OPTIONAL_RAW if k in keys],
    }


def map_minute_row(raw: dict[str, Any]) -> dict[str, Any]:
    status = minute_schema_status(raw)
    if not status["ok"]:
        raise ValueError(f"minute_schema_mismatch:{status['missing_required']}")
    return {
        "date": yyyymmdd(raw["Date"]),
        "time_label": str(raw["Time"]),
        "symbol": symbol4(raw["Code"]),
        "symbol_raw": str(raw["Code"]),
        "open": raw.get("O"),
        "high": raw.get("H"),
        "low": raw.get("L"),
        "close": raw.get("C"),
        "volume": raw.get("Vo"),
        "trading_value": raw.get("Va"),
    }


MISSING_MINUTE_POLICY = {
    "no_unconditional_forward_fill": True,
    "NO_TRADE_MINUTE": "official_omission_when_no_prints; do_not_write_OHLC_prev_and_volume_0",
    "MISSING_DATA": "expected_session_minute_absent_for_non_no_trade_reason",
    "SESSION_CLOSED": "outside_TSE_AM_PM_or_calendar_holiday",
    "handling": "leave_absent_until_a_later_study_declares_explicit_rules",
}

CORPORATE_ACTION_POLICY = {
    "minute_raw": "as_traded_unadjusted",
    "long_horizon_return": "apply_daily_AdjFactor_CumAdj_separately",
    "do_not_mix": True,
}


assert MINUTE_REQUIRED_RAW[-1] == "Vo"
assert "C" in MINUTE_REQUIRED_RAW
