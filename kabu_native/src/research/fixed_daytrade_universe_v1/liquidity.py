"""Liquidity metrics. No threshold search. No PnL."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Sequence

import numpy as np

from research.e1_x29_prospective.calendar import is_jpx_trading_day
from research.fixed_daytrade_universe_v1 import (
    LAST_COMPLETE_TSE_SESSION,
    MIN_ACTIVE_SESSION_N,
    MIN_MEDIAN_VA_JPY,
    MIN_P20_VA_JPY,
    MIN_SESSION_COVERAGE,
    ROBUSTNESS_HALF_RATIO_FLOOR,
    TRAIL_SESSIONS,
)


def _ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def tse_sessions_ending(end_yyyymmdd: str, n: int = TRAIL_SESSIONS) -> list[str]:
    y, m, dd = int(end_yyyymmdd[:4]), int(end_yyyymmdd[4:6]), int(end_yyyymmdd[6:8])
    d = date(y, m, dd)
    out: list[str] = []
    guard = 0
    while len(out) < int(n) and guard < 400:
        day = _ymd(d)
        if is_jpx_trading_day(day):
            out.append(day)
        d -= timedelta(days=1)
        guard += 1
    out.reverse()
    return out


def _finite(xs: Sequence[float]) -> list[float]:
    out: list[float] = []
    for v in xs:
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f == f:
            out.append(f)
    return out


def percentile(xs: Sequence[float], q: float) -> float | None:
    arr = np.asarray(_finite(xs), dtype=float)
    if arr.size == 0:
        return None
    return float(np.percentile(arr, q))


def median(xs: Sequence[float]) -> float | None:
    arr = np.asarray(_finite(xs), dtype=float)
    if arr.size == 0:
        return None
    return float(np.median(arr))


def metrics_from_daily(
    *,
    va: Sequence[float],
    vo: Sequence[float],
    present_n: int,
    expected_n: int,
) -> dict[str, Any]:
    exp = max(int(expected_n), 1)
    act = int(present_n)
    half = max(exp // 2, 1)
    va_f = _finite(va)
    vo_f = _finite(vo)
    first = va_f[:half]
    last = va_f[-half:] if len(va_f) >= half else va_f
    med_full = median(va_f)
    med_first = median(first)
    med_last = median(last)
    p20 = percentile(va_f, 20.0)
    zero_missing = max(exp - act, 0)
    coverage = act / float(exp)
    surge_only = False
    dead_now = False
    if med_first and med_last and med_first > 0 and med_last > 0:
        if med_last < ROBUSTNESS_HALF_RATIO_FLOOR * med_first:
            dead_now = True
        if med_first < ROBUSTNESS_HALF_RATIO_FLOOR * med_last:
            surge_only = True
    return {
        "median_daily_trading_value_60d": med_full,
        "p20_daily_trading_value_60d": p20,
        "median_daily_volume_60d": median(vo_f),
        "median_va_first30": med_first,
        "median_va_last30": med_last,
        "median_trading_value_first30": med_first,
        "median_trading_value_last30": med_last,
        "median_volume_first30": median(vo_f[:half]),
        "median_volume_last30": median(vo_f[-half:] if len(vo_f) >= half else vo_f),
        "active_session_n": act,
        "expected_session_n": exp,
        "session_coverage": coverage,
        "zero_or_missing_day_n": zero_missing,
        "recent_surge_only_flag": surge_only,
        "recent_collapse_flag": dead_now,
    }


def liquidity_pass(m: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if int(m.get("active_session_n") or 0) < int(MIN_ACTIVE_SESSION_N):
        reasons.append("active_session_n_below_floor")
    if float(m.get("session_coverage") or 0.0) < float(MIN_SESSION_COVERAGE):
        reasons.append("session_coverage_below_floor")
    med = m.get("median_daily_trading_value_60d")
    p20 = m.get("p20_daily_trading_value_60d")
    if med is None or float(med) < float(MIN_MEDIAN_VA_JPY):
        reasons.append("median_va_below_floor")
    if p20 is None or float(p20) < float(MIN_P20_VA_JPY):
        reasons.append("p20_va_below_floor")
    if m.get("recent_surge_only_flag"):
        reasons.append("first30_vs_last30_surge_only")
    if m.get("recent_collapse_flag"):
        reasons.append("last30_vs_first30_collapse")
    return (len(reasons) == 0), reasons


def expected_window() -> dict[str, Any]:
    days = tse_sessions_ending(LAST_COMPLETE_TSE_SESSION, TRAIL_SESSIONS)
    return {
        "end": LAST_COMPLETE_TSE_SESSION,
        "n": len(days),
        "first": days[0] if days else None,
        "last": days[-1] if days else None,
        "days": days,
    }


WINDOW = expected_window()
assert WINDOW["n"] == int(TRAIL_SESSIONS)
assert WINDOW["last"] == LAST_COMPLETE_TSE_SESSION
assert "20260914" not in WINDOW["days"]
assert "20260912" not in WINDOW["days"]
assert "20260913" not in WINDOW["days"]


def metrics_aligned_to_sessions(
    *,
    session_days: Sequence[str],
    rows: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Official-session aligned metrics. Missing sessions stay missing; never filled as 0 volume."""
    by_date: dict[str, dict[str, Any]] = {}
    for r in rows:
        day = str(r.get("date") or "")
        if day:
            by_date[day] = dict(r)
    expected = [str(d) for d in session_days]
    exp = len(expected)
    half = max(exp // 2, 1)
    missing: list[str] = []
    null_trade: list[str] = []
    present = 0
    va_all: list[float] = []
    vo_all: list[float] = []

    def _nums(days: Sequence[str], field: str) -> list[float]:
        xs: list[float] = []
        for d in days:
            row = by_date.get(str(d))
            if not row:
                continue
            v = row.get("trading_value") if field == "trading_value" else row.get("volume")
            try:
                f = float(v)
            except (TypeError, ValueError):
                continue
            if f == f:
                xs.append(f)
        return xs

    for d in expected:
        row = by_date.get(d)
        if row is None:
            missing.append(d)
            continue
        present += 1
        va = row.get("trading_value")
        try:
            fv = float(va) if va is not None and va != "" else None
        except (TypeError, ValueError):
            fv = None
        if fv is None or fv != fv:
            null_trade.append(d)
        else:
            va_all.append(float(fv))
        vo = row.get("volume")
        try:
            fo = float(vo) if vo is not None and vo != "" else None
        except (TypeError, ValueError):
            fo = None
        if fo is not None and fo == fo:
            vo_all.append(float(fo))

    first_days = expected[:half]
    last_days = expected[-half:] if exp >= half else expected
    m = metrics_from_daily(va=va_all, vo=vo_all, present_n=len(va_all), expected_n=exp)
    m["session_n"] = present
    m["active_session_n"] = len(va_all)
    m["session_coverage"] = (len(va_all) / float(exp)) if exp else 0.0
    m["missing_session_n"] = len(missing)
    m["missing_sessions"] = missing
    m["null_trade_day_n"] = len(null_trade)
    m["zero_or_missing_day_n"] = len(missing) + len(null_trade)
    m["did_not_fill_missing_as_zero_volume"] = True
    m["median_trading_value_first30"] = median(_nums(first_days, "trading_value"))
    m["median_trading_value_last30"] = median(_nums(last_days, "trading_value"))
    m["median_volume_first30"] = median(_nums(first_days, "volume"))
    m["median_volume_last30"] = median(_nums(last_days, "volume"))
    m["median_va_first30"] = m["median_trading_value_first30"]
    m["median_va_last30"] = m["median_trading_value_last30"]
    surge_only = False
    dead_now = False
    med_first = m["median_trading_value_first30"]
    med_last = m["median_trading_value_last30"]
    if med_first and med_last and med_first > 0 and med_last > 0:
        if med_last < ROBUSTNESS_HALF_RATIO_FLOOR * med_first:
            dead_now = True
        if med_first < ROBUSTNESS_HALF_RATIO_FLOOR * med_last:
            surge_only = True
    m["recent_surge_only_flag"] = surge_only
    m["recent_collapse_flag"] = dead_now
    return m
