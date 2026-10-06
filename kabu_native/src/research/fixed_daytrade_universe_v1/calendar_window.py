"""Official TSE session window from J-Quants calendar. Do not treat a hardcoded list as truth."""
from __future__ import annotations

from typing import Any

from research.fixed_daytrade_universe_v1 import (
    CASE_CALENDAR_MISMATCH,
    EXPECTED_WINDOW_FIRST,
    EXPECTED_WINDOW_LAST,
    EXPECTED_WINDOW_N,
    LAST_COMPLETE_TSE_SESSION,
    TRAIL_SESSIONS,
)
from research.fixed_daytrade_universe_v1.schema import TSE_SESSION_HOL_DIV, yyyymmdd


def tse_session_dates(calendar_rows: list[dict[str, Any]], *, cutoff: str) -> list[str]:
    cutoff_s = yyyymmdd(cutoff)
    days: list[str] = []
    for raw in calendar_rows:
        hol = str(raw.get("HolDiv") or "").strip()
        if hol not in TSE_SESSION_HOL_DIV:
            continue
        day = yyyymmdd(raw.get("Date"))
        if day > cutoff_s:
            continue
        days.append(day)
    return sorted(set(days))


def last_n_sessions_ending(days: list[str], *, end: str, n: int) -> list[str]:
    end_s = yyyymmdd(end)
    eligible = [d for d in days if d <= end_s]
    if not eligible or eligible[-1] != end_s:
        return []
    if len(eligible) < int(n):
        return list(eligible)
    return eligible[-int(n) :]


def build_official_window(calendar_rows: list[dict[str, Any]]) -> dict[str, Any]:
    cutoff = LAST_COMPLETE_TSE_SESSION
    days_all = tse_session_dates(calendar_rows, cutoff=cutoff)
    window = last_n_sessions_ending(days_all, end=cutoff, n=TRAIL_SESSIONS)
    first = window[0] if window else None
    last = window[-1] if window else None
    matched = (
        first == EXPECTED_WINDOW_FIRST
        and last == EXPECTED_WINDOW_LAST
        and len(window) == int(EXPECTED_WINDOW_N)
        and last == cutoff
    )
    max_date = max(days_all) if days_all else None
    return {
        "ok": bool(matched),
        "mismatch": not bool(matched),
        "verdict_if_mismatch": CASE_CALENDAR_MISMATCH,
        "first": first,
        "last": last,
        "n": len(window),
        "days": window,
        "expected_first": EXPECTED_WINDOW_FIRST,
        "expected_last": EXPECTED_WINDOW_LAST,
        "expected_n": int(EXPECTED_WINDOW_N),
        "cutoff": cutoff,
        "max_date": max_date,
        "max_date_ok": (max_date is None) or (max_date <= cutoff),
        "hol_div_tse_sessions": sorted(TSE_SESSION_HOL_DIV),
        "did_not_hardcode_as_truth": True,
    }


assert EXPECTED_WINDOW_LAST == LAST_COMPLETE_TSE_SESSION
assert EXPECTED_WINDOW_N == TRAIL_SESSIONS
