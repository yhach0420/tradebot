"""
Market session entry window (JST) — structural rules, not time-series optimization.

東証現物の板安定化・制度に沿った ENTRY 枠:
- 寄り直後の乱高下を避けるため 09:05 以降
- 引け付近の新規建てを避けるため 14:50 以前まで
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore[misc, assignment]

JST = ZoneInfo("Asia/Tokyo") if ZoneInfo else None

# Formal market session entry window (JST)
ENTRY_START_HHMM = "09:05"
ENTRY_END_HHMM = "14:50"

# Legacy replay baseline: cash session open, no optimized gate
LEGACY_ENTRY_START_HHMM = "09:00"


def _minutes_jst(ts: datetime) -> int:
    if JST is None:
        return 0
    dt = ts.astimezone(JST)
    return dt.hour * 60 + dt.minute


def _hhmm_to_minutes(hhmm: str) -> int:
    h, m = hhmm.strip().split(":")
    return int(h) * 60 + int(m)


ENTRY_START_MIN = _hhmm_to_minutes(ENTRY_START_HHMM)
ENTRY_END_MIN = _hhmm_to_minutes(ENTRY_END_HHMM)
LEGACY_ENTRY_START_MIN = _hhmm_to_minutes(LEGACY_ENTRY_START_HHMM)


def entry_allowed(ts: datetime, *, market_session_control: bool) -> bool:
    """
  Return True if a new ENTRY is allowed at ``ts``.

  - ``market_session_control=True``: [09:05, 14:50) JST
  - ``market_session_control=False``: legacy baseline [09:00, session end)
    """
    m = _minutes_jst(ts)
    if market_session_control:
        return ENTRY_START_MIN <= m < ENTRY_END_MIN
    return m >= LEGACY_ENTRY_START_MIN


def session_control_dict(*, enabled: bool) -> dict[str, Any]:
    return {
        "market_session_control": enabled,
        "entry_start_jst": ENTRY_START_HHMM if enabled else LEGACY_ENTRY_START_HHMM,
        "entry_end_jst": ENTRY_END_HHMM if enabled else None,
        "purpose": (
            "board_stabilization_and_market_structure"
            if enabled
            else "legacy_baseline_open"
        ),
    }
