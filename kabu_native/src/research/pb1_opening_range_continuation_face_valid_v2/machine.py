"""V2 continuation machine: real break, real leave, fresh retest. No false-break emit."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from research.one_minute_native_playbook_discovery_v1.states import to_min
from research.pb1_opening_range_continuation_face_valid_v2 import (
    BREAK_BEYOND_ATR_FRAC,
    BREAK_BEYOND_OR_FRAC,
    BREAK_CLOSE_LOC,
    LAST_BREAK,
    LAST_TRIGGER,
    LEAVE_EXT_OR_FRAC,
    MIN_AWAY_BARS,
    OR_KNOWN_FROM,
    RETEST_FRESHNESS_MIN,
)


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def vwap_location(high: Any, low: Any, close: Any, vwap: Any, sign: int) -> str:
    if not (_finite(high) and _finite(low) and _finite(close) and _finite(vwap)):
        return "na"
    h, l, c, vw = float(high), float(low), float(close), float(vwap)
    if min(h, l) <= vw <= max(h, l):
        return "testing"
    if sign > 0:
        return "trend_side" if c > vw else "opposed"
    return "trend_side" if c < vw else "opposed"


def trading_minutes(t0: str | None, t1: str | None) -> float:
    if not t0 or not t1:
        return float("nan")
    a, b = to_min(str(t0)), to_min(str(t1))
    if a is None or b is None:
        return float("nan")
    return float(b - a)


def close_loc(high: float, low: float, close: float) -> float:
    if high <= low:
        return float("nan")
    return float((close - low) / (high - low))


def is_micro_break(
    *,
    sign: int,
    close: float,
    high: float,
    low: float,
    boundary: float,
    or_range: float,
    atr: float,
) -> bool:
    beyond = (close - boundary) if sign > 0 else (boundary - close)
    if beyond <= 0:
        return True
    loc = close_loc(high, low, close)
    weak_loc = (loc < float(BREAK_CLOSE_LOC)) if sign > 0 else (loc > 1.0 - float(BREAK_CLOSE_LOC))
    tiny_or = (not _finite(or_range)) or or_range <= 0 or beyond < float(BREAK_BEYOND_OR_FRAC) * float(or_range)
    tiny_atr = (not _finite(atr)) or atr <= 0 or beyond < float(BREAK_BEYOND_ATR_FRAC) * float(atr)
    return bool(tiny_or and tiny_atr) or (tiny_or and weak_loc)


@dataclass
class SideState:
    sign: int
    broken: bool = False
    break_pos: int | None = None
    break_t: str | None = None
    break_beyond: float | None = None
    break_body: float | None = None
    break_range: float | None = None
    break_close_loc: float | None = None
    break_open: float | None = None
    prev_close: float | None = None
    wick_only_n: int = 0
    micro_break_n: int = 0
    follow_through: bool | None = None
    away_n: int = 0
    max_away: float = 0.0
    left: bool = False
    left_pos: int | None = None
    retest_pos: int | None = None
    retest_t: str | None = None
    retest_high: float | None = None
    retest_low: float | None = None
    break_to_retest_minutes: float | None = None
    held: bool = False
    dead: bool = False
    death: str | None = None
    emitted: bool = False


def new_side(sign: int) -> SideState:
    return SideState(sign=int(sign))


def _boundary(or_high: float, or_low: float, sign: int) -> float:
    return float(or_high) if sign > 0 else float(or_low)


def _beyond_close(close: float, boundary: float, sign: int) -> bool:
    return bool(close > boundary) if sign > 0 else bool(close < boundary)


def _wick_beyond(high: float, low: float, close: float, boundary: float, sign: int) -> bool:
    if sign > 0:
        return bool(high > boundary and close <= boundary)
    return bool(low < boundary and close >= boundary)


def _returns_to(high: float, low: float, boundary: float, sign: int) -> bool:
    if sign > 0:
        return bool(low <= boundary)
    return bool(high >= boundary)


def _fully_away(high: float, low: float, boundary: float, sign: int) -> bool:
    if sign > 0:
        return bool(low > boundary)
    return bool(high < boundary)


def _hold_close(close: float, boundary: float, sign: int) -> bool:
    return bool(close >= boundary) if sign > 0 else bool(close <= boundary)


def _away_dist(high: float, low: float, boundary: float, sign: int) -> float:
    if sign > 0:
        return float(high - boundary)
    return float(boundary - low)


def classify_triggers(
    *,
    sign: int,
    high: float,
    low: float,
    close: float,
    boundary: float,
    retest_high: float | None,
    retest_low: float | None,
) -> list[str]:
    out: list[str] = []
    if sign > 0:
        if _finite(retest_high) and close > float(retest_high):
            out.append("RECLAIM_RETEST_MICRO_HIGH")
        if low < boundary and close >= boundary:
            out.append("FAILED_PUSH_THEN_CLOSE_BACK")
    else:
        if _finite(retest_low) and close < float(retest_low):
            out.append("RECLAIM_RETEST_MICRO_HIGH")
        if high > boundary and close <= boundary:
            out.append("FAILED_PUSH_THEN_CLOSE_BACK")
    return out


def step_side(
    st: SideState,
    *,
    pos: int,
    t: str,
    open_px: float,
    high: float,
    low: float,
    close: float,
    prev_close: float | None,
    or_high: float,
    or_low: float,
    atr: float,
) -> dict[str, Any] | None:
    if st.dead or st.emitted:
        return None
    if t < OR_KNOWN_FROM or t > LAST_TRIGGER:
        return None
    if not (_finite(or_high) and _finite(or_low) and or_high > or_low):
        st.dead = True
        st.death = "or_invalid"
        return None
    if not (_finite(high) and _finite(low) and _finite(close)):
        return None
    h, l, c = float(high), float(low), float(close)
    opn = float(open_px) if _finite(open_px) else c
    boundary = _boundary(or_high, or_low, st.sign)
    or_range = float(or_high - or_low)

    if not st.broken:
        if t > LAST_BREAK:
            return None
        if _wick_beyond(h, l, c, boundary, st.sign):
            st.wick_only_n += 1
            return None
        if not _beyond_close(c, boundary, st.sign):
            return None
        if is_micro_break(sign=st.sign, close=c, high=h, low=l, boundary=boundary, or_range=or_range, atr=atr):
            st.micro_break_n += 1
            return None
        st.broken = True
        st.break_pos = pos
        st.break_t = t
        st.break_open = opn
        st.prev_close = float(prev_close) if _finite(prev_close) else None
        st.break_beyond = (c - boundary) if st.sign > 0 else (boundary - c)
        st.break_body = abs(c - opn)
        st.break_range = h - l
        st.break_close_loc = close_loc(h, l, c)
        return None

    if st.follow_through is None and st.break_pos is not None and pos == int(st.break_pos) + 1:
        st.follow_through = _beyond_close(c, boundary, st.sign)

    if st.retest_pos is None:
        if pos <= int(st.break_pos or -1):
            return None
        mins = trading_minutes(st.break_t, t)
        if _finite(mins) and mins > float(RETEST_FRESHNESS_MIN) and not st.left:
            st.dead = True
            st.death = "stale_no_retest"
            return None
        if not _beyond_close(c, boundary, st.sign):
            if not st.left:
                st.dead = True
                st.death = "immediate_collapse"
                return None
            if _finite(mins) and mins > float(RETEST_FRESHNESS_MIN):
                st.dead = True
                st.death = "stale_retest"
                st.retest_pos = pos
                st.retest_t = t
                st.break_to_retest_minutes = mins
                return None
            st.retest_pos = pos
            st.retest_t = t
            st.retest_high = h
            st.retest_low = l
            st.break_to_retest_minutes = mins
            st.held = False
            st.dead = True
            st.death = "failed_retest"
            return None
        if _fully_away(h, l, boundary, st.sign):
            st.away_n += 1
            st.max_away = max(float(st.max_away), _away_dist(h, l, boundary, st.sign))
            if (not st.left) and (
                st.away_n >= int(MIN_AWAY_BARS) or (_finite(or_range) and or_range > 0 and st.max_away >= float(LEAVE_EXT_OR_FRAC) * or_range)
            ):
                st.left = True
                st.left_pos = pos
            return None
        if st.left and _returns_to(h, l, boundary, st.sign):
            if _finite(mins) and mins > float(RETEST_FRESHNESS_MIN):
                st.dead = True
                st.death = "stale_retest"
                st.retest_pos = pos
                st.retest_t = t
                st.break_to_retest_minutes = mins
                return None
            st.retest_pos = pos
            st.retest_t = t
            st.retest_high = h
            st.retest_low = l
            st.break_to_retest_minutes = mins
            st.held = _hold_close(c, boundary, st.sign)
            if not st.held:
                st.dead = True
                st.death = "failed_retest"
            return None
        return None

    if not st.held:
        return None
    if pos <= int(st.retest_pos or -1):
        return None
    if not _hold_close(c, boundary, st.sign):
        st.dead = True
        st.death = "acceptance_back_inside"
        return None

    labels = classify_triggers(
        sign=st.sign,
        high=h,
        low=l,
        close=c,
        boundary=boundary,
        retest_high=st.retest_high,
        retest_low=st.retest_low,
    )
    if not labels:
        return None
    st.emitted = True
    return {
        "DIR": st.sign,
        "direction": "bull" if st.sign > 0 else "bear",
        "trigger_pos": pos,
        "trigger_t": t,
        "trigger_labels": labels,
        "trigger_primary": labels[0],
        "break_pos": st.break_pos,
        "break_t": st.break_t,
        "break_beyond": st.break_beyond,
        "break_body": st.break_body,
        "break_range": st.break_range,
        "break_close_loc": st.break_close_loc,
        "break_open": st.break_open,
        "prev_close": st.prev_close,
        "follow_through": st.follow_through,
        "away_n": st.away_n,
        "max_away": st.max_away,
        "left_pos": st.left_pos,
        "wick_only_n": st.wick_only_n,
        "micro_break_n": st.micro_break_n,
        "retest_pos": st.retest_pos,
        "retest_t": st.retest_t,
        "retest_high": st.retest_high,
        "retest_low": st.retest_low,
        "break_to_retest_minutes": st.break_to_retest_minutes,
        "held": True,
        "or_high": float(or_high),
        "or_low": float(or_low),
        "invalidation": float(st.retest_low) if st.sign > 0 else float(st.retest_high),
        "false_break": False,
        "stale_retest": False,
    }
