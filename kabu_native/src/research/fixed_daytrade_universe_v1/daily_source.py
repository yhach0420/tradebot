"""Credential / source probe. Acquisition lives in acquire.py. No yfinance. No key in output."""
from __future__ import annotations

from typing import Any

from research.fixed_daytrade_universe_v1 import FORBIDDEN_DATA_FROM, LAST_COMPLETE_TSE_SESSION, TRAIL_SESSIONS, YFINANCE_ALLOWED
from research.fixed_daytrade_universe_v1.jquants_auth import resolve_api_key_meta


def probe_daily_source() -> dict[str, Any]:
    cred = resolve_api_key_meta()
    present = bool(cred.get("present"))
    return {
        "available": False,
        "loaded_series_n": 0,
        "credentials_present": present,
        "jquants_env_var_name": cred.get("env_var_name"),
        "yfinance_used": False,
        "yfinance_allowed": bool(YFINANCE_ALLOWED),
        "kabu_board_is_not_60d": True,
        "did_not_fetch_network": True,
        "forbidden_from": FORBIDDEN_DATA_FROM,
        "last_complete_tse_session": LAST_COMPLETE_TSE_SESSION,
        "trail_sessions_required": int(TRAIL_SESSIONS),
        "reason": "jquants_api_key_absent" if not present else "probe_does_not_fetch_use_acquire",
        "CASE_HINT": "JQUANTS_API_KEY_REQUIRED_FOR_UNIVERSE_FREEZE_V1" if not present else None,
    }


assert YFINANCE_ALLOWED is False
assert probe_daily_source()["yfinance_used"] is False
assert "api_key_value" not in probe_daily_source()
