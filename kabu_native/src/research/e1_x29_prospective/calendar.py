"""JPX trading-day helpers for X29 prospective window (rule freeze only)."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from . import CONSUMED_ALPHA_DATES, JPX_HOLIDAYS_2026, RISK_INFRASTRUCTURE_FROM

JST = ZoneInfo("Asia/Tokyo")


def _ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def is_jpx_trading_day(day: str) -> bool:
    """Regular equity session day (weekdays excluding JPX holidays)."""
    if len(day) != 8:
        return False
    y, m, dd = int(day[:4]), int(day[4:6]), int(day[6:8])
    dt = date(y, m, dd)
    if dt.weekday() >= 5:
        return False
    if day in JPX_HOLIDAYS_2026:
        return False
    # extend holiday set for other years if needed via weekday-only fallback
    return True


def is_eligible_prospective_day(day: str, *, precommit_date: str) -> bool:
    """Day must be JPX trading, after precommit calendar day, not consumed alpha."""
    if day in CONSUMED_ALPHA_DATES:
        return False
    if day <= precommit_date:
        return False
    # Do not retroactively treat already-stored risk-infra days as alpha if before/on precommit
    # After precommit, new arrivals are eligible even if date >= 20260805
    if day < RISK_INFRASTRUCTURE_FROM and day in CONSUMED_ALPHA_DATES:
        return False
    return is_jpx_trading_day(day)


def first_eligible_prospective_day(precommit_ts: datetime) -> str:
    """First unused JPX trading day strictly after precommit local calendar date."""
    if precommit_ts.tzinfo is None:
        precommit_ts = precommit_ts.replace(tzinfo=JST)
    local = precommit_ts.astimezone(JST)
    precommit_date = _ymd(local.date())
    d = local.date() + timedelta(days=1)
    for _ in range(370):
        day = _ymd(d)
        if is_eligible_prospective_day(day, precommit_date=precommit_date):
            return day
        d += timedelta(days=1)
    raise RuntimeError("no eligible prospective day within 370 days")


def planned_5_valid_days(first_day: str) -> list[str]:
    """Illustrative planned window from first day (invalid days extend forward at runtime)."""
    y, m, dd = int(first_day[:4]), int(first_day[4:6]), int(first_day[6:8])
    d = date(y, m, dd)
    out: list[str] = []
    while len(out) < 5:
        day = _ymd(d)
        if is_jpx_trading_day(day) and day not in CONSUMED_ALPHA_DATES:
            out.append(day)
        d += timedelta(days=1)
        if (d - date(y, m, dd)).days > 60:
            break
    return out


def window_rule_text() -> dict:
    return {
        "rule": "next_5_valid_JPX_regular_trading_days_after_precommit",
        "count_holidays": False,
        "invalid_day_reasons_allowed": [
            "capture_integrity_failure",
            "board_data_unavailable_globally",
            "clock_session_corruption",
            "observer_process_failure",
        ],
        "invalid_day_action": "append_next_unused_JPX_trading_day_until_5_VALID",
        "forbid_exclusion_for_pnl": True,
        "no_midwindow_resize": True,
        "consumed_alpha_dates": list(CONSUMED_ALPHA_DATES),
        "risk_infrastructure_from": RISK_INFRASTRUCTURE_FROM,
        "risk_infra_not_retroactive_alpha": True,
    }
