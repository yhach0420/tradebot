"""Discover prospective sessions. Historical development days are not reopened."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from research.anchor_vs_event_driven.run_comparison import find_capture_dir
from research.pb1_v4_prospective_semantic_validation_preflight.eligibility import tse_cash_calendar
from research.symbol_setup_exit_causal_evidence import HISTORICAL_LAST_DATE
from research.symbol_setup_exit_causal_evidence.isolation import NATIVE


def _days_after(last: str, as_of: str) -> list[str]:
    cur = datetime.strptime(last, "%Y%m%d") + timedelta(days=1)
    end = datetime.strptime(as_of, "%Y%m%d")
    out = []
    while cur <= end:
        out.append(cur.strftime("%Y%m%d"))
        cur += timedelta(days=1)
    return out


def discover(as_of: str) -> list[dict[str, Any]]:
    """Calendar audit through as_of. A day is admitted only when push capture exists."""
    rows = []
    root = NATIVE / "data" / "market_capture"
    for day in _days_after(HISTORICAL_LAST_DATE, as_of):
        cal = tse_cash_calendar(day)
        cap = find_capture_dir(day) if cal["is_tse_cash_session"] else None
        folder = root / day
        rows.append(
            {
                "date": day,
                "tse_cash_session": bool(cal["is_tse_cash_session"]),
                "calendar_label": cal["label"],
                "capture_dir_present": folder.is_dir(),
                "push_capture_present": cap is not None,
                "admitted": bool(cal["is_tse_cash_session"] and cap is not None),
                "population": "PROSPECTIVE_EXIT_EVIDENCE",
            }
        )
    return rows
