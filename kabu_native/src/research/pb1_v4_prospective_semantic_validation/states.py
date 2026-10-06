"""Operational session states. Calendar/precommit unchanged. No V4 semantics."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
AM_WINDOW_LAST_BAR = "11:19"
AM_WINDOW_COMPLETE_FROM = "11:20"

STATE_NOT_YET = "NOT_YET_OCCURRED"
STATE_WAITING = "WAITING_FOR_AM_WINDOW_COMPLETION"
STATE_INELIGIBLE = "INELIGIBLE_SESSION"
STATE_ELIGIBLE = "ELIGIBLE"


def yyyymmdd(raw: str | None) -> str:
    return str(raw or "").replace("-", "")[:8]


def am_window_complete(*, now: datetime) -> bool:
    local = now.astimezone(JST) if now.tzinfo else now.replace(tzinfo=JST)
    return local.strftime("%H:%M") >= AM_WINDOW_COMPLETE_FROM


def classify_clock(*, session: str, today_jst: str, now: datetime) -> dict[str, Any]:
    day = yyyymmdd(session)
    today = yyyymmdd(today_jst)
    if day > today:
        return {
            "SESSION_STATE": STATE_NOT_YET,
            "may_search_bars": False,
            "may_run_v4": False,
            "am_window_complete": False,
            "reason": "session_date_after_today_jst",
        }
    if day == today and not am_window_complete(now=now):
        return {
            "SESSION_STATE": STATE_WAITING,
            "may_search_bars": False,
            "may_run_v4": False,
            "am_window_complete": False,
            "reason": "am_window_09:00_11:19_not_complete",
        }
    return {
        "SESSION_STATE": "CLOCK_READY_FOR_QUALITY",
        "may_search_bars": True,
        "may_run_v4": False,
        "am_window_complete": True,
        "reason": "am_window_complete_or_session_in_past",
    }


def apply_quality(*, clock: dict[str, Any], data_complete: bool) -> dict[str, Any]:
    state = str(clock.get("SESSION_STATE") or "")
    if state in {STATE_NOT_YET, STATE_WAITING}:
        return {
            "SESSION_STATE": state,
            "SESSION_ACCEPTED_FOR_PROSPECTIVE": False,
            "INELIGIBLE_SESSION": False,
            "counts_as_session": False,
            "semantic_fail": False,
            "may_run_v4": False,
            "PROSPECTIVE_DATA_OPENED": False,
            "data_complete": False,
        }
    if data_complete:
        return {
            "SESSION_STATE": STATE_ELIGIBLE,
            "SESSION_ACCEPTED_FOR_PROSPECTIVE": True,
            "INELIGIBLE_SESSION": False,
            "counts_as_session": True,
            "semantic_fail": False,
            "may_run_v4": True,
            "PROSPECTIVE_DATA_OPENED": True,
            "data_complete": True,
        }
    return {
        "SESSION_STATE": STATE_INELIGIBLE,
        "SESSION_ACCEPTED_FOR_PROSPECTIVE": False,
        "INELIGIBLE_SESSION": True,
        "counts_as_session": False,
        "semantic_fail": False,
        "may_run_v4": False,
        "PROSPECTIVE_DATA_OPENED": False,
        "data_complete": False,
    }
