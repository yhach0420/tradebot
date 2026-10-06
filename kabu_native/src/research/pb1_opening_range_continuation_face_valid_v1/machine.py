"""Causal OR15 continuation state machine. One side at a time. No false-break emit."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from research.pb1_opening_range_continuation_face_valid_v1 import LAST_BREAK, LAST_TRIGGER, OR_KNOWN_FROM


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


@dataclass
class SideState:
    sign: int
    broken: bool = False
    break_pos: int | None = None
    break_t: str | None = None
    wick_only_n: int = 0
    follow_through: bool | None = None
    left: bool = False
    left_pos: int | None = None
    retest_pos: int | None = None
    retest_t: str | None = None
    retest_high: float | None = None
    retest_low: float | None = None
    held: bool = False
    dead: bool = False
    death: str | None = None
    emitted: bool = False


def new_sides() -> dict[int, SideState]:
    return {1: SideState(sign=1), -1: SideState(sign=-1)}


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


def classify_triggers(
    *,
    sign: int,
    high: float,
    low: float,
    close: float,
    boundary: float,
    retest_high: float | None,
    retest_low: float | None,
    swing_high: float | None,
    swing_low: float | None,
) -> list[str]:
    out: list[str] = []
    if sign > 0:
        if _finite(retest_high) and close > float(retest_high):
            out.append("RECLAIM_RETEST_MICRO_HIGH")
        if low < boundary and close >= boundary:
            out.append("FAILED_PUSH_THEN_CLOSE_BACK")
        if _finite(swing_high) and close > float(swing_high):
            out.append("HIGHER_LOW_MINOR_SWING_BREAK")
    else:
        if _finite(retest_low) and close < float(retest_low):
            out.append("RECLAIM_RETEST_MICRO_HIGH")
        if high > boundary and close <= boundary:
            out.append("FAILED_PUSH_THEN_CLOSE_BACK")
        if _finite(swing_low) and close < float(swing_low):
            out.append("HIGHER_LOW_MINOR_SWING_BREAK")
    return out


def step_side(
    st: SideState,
    *,
    pos: int,
    t: str,
    high: float,
    low: float,
    close: float,
    or_high: float,
    or_low: float,
    swing_high: float | None,
    swing_low: float | None,
) -> dict[str, Any] | None:
    """Advance one completed AM bar. Returns a trigger event or None. Never emits a false-break."""
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
    boundary = _boundary(or_high, or_low, st.sign)

    if not st.broken:
        if t > LAST_BREAK:
            return None
        if _wick_beyond(h, l, c, boundary, st.sign):
            st.wick_only_n += 1
            return None
        if _beyond_close(c, boundary, st.sign):
            st.broken = True
            st.break_pos = pos
            st.break_t = t
        return None

    if st.follow_through is None and st.break_pos is not None and pos == int(st.break_pos) + 1:
        st.follow_through = _beyond_close(c, boundary, st.sign)

    if st.retest_pos is None:
        if pos <= int(st.break_pos or -1):
            return None
        if not _beyond_close(c, boundary, st.sign):
            st.dead = True
            st.death = "immediate_collapse" if not st.left else "failed_retest"
            if st.left:
                st.retest_pos = pos
                st.retest_t = t
                st.retest_high = h
                st.retest_low = l
                st.held = False
            return None
        if not st.left:
            if _fully_away(h, l, boundary, st.sign):
                st.left = True
                st.left_pos = pos
            return None
        if _returns_to(h, l, boundary, st.sign):
            st.retest_pos = pos
            st.retest_t = t
            st.retest_high = h
            st.retest_low = l
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
        swing_high=swing_high,
        swing_low=swing_low,
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
        "follow_through": st.follow_through,
        "left_pos": st.left_pos,
        "wick_only_n": st.wick_only_n,
        "retest_pos": st.retest_pos,
        "retest_t": st.retest_t,
        "retest_high": st.retest_high,
        "retest_low": st.retest_low,
        "held": True,
        "or_high": float(or_high),
        "or_low": float(or_low),
        "invalidation": float(st.retest_low) if st.sign > 0 else float(st.retest_high),
        "false_break": False,
    }
