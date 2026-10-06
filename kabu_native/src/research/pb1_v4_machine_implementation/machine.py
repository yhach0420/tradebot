"""V4 1m geometry after a locked 5m opening drive. Setup eligibility is S4, not this file."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from research.pb1_opening_range_continuation_face_valid_v2 import LAST_BREAK, LAST_TRIGGER, LEAVE_EXT_OR_FRAC, MIN_AWAY_BARS, OR_KNOWN_FROM
from research.pb1_opening_range_continuation_face_valid_v2.machine import (
    _away_dist,
    _beyond_close,
    _boundary,
    _finite,
    _fully_away,
    _hold_close,
    _returns_to,
    _wick_beyond,
    close_loc,
    is_micro_break,
)
from research.pb1_v4_machine_implementation import ACCEPTANCE_FAIL_CLOSES, MEANINGFUL_LEAVE_NOISE_MULT
from research.pb1_v4_machine_implementation.location import classify_s2
from research.pb1_v4_machine_implementation.thesis import classify_thesis, opening_net


def _fav_extreme(*, sign: int, high: float, low: float) -> float:
    return float(high) if int(sign) > 0 else float(low)


def _through_defended(*, sign: int, close: float, defended_lo: Any, defended_hi: Any, or_high: float, or_low: float, level_type: str | None) -> bool:
    if str(level_type or "") == "CLEARED_ZONE" and _finite(defended_lo) and _finite(defended_hi):
        if int(sign) > 0:
            return float(close) < float(defended_lo)
        return float(close) > float(defended_hi)
    boundary = float(or_high) if int(sign) > 0 else float(or_low)
    return not _hold_close(float(close), boundary, int(sign))


def _breached_retest(*, sign: int, close: float, retest_high: Any, retest_low: Any) -> bool:
    if int(sign) > 0:
        return _finite(retest_low) and float(close) < float(retest_low)
    return _finite(retest_high) and float(close) > float(retest_high)


@dataclass
class SideState:
    sign: int
    s0: bool = False
    s1: bool = False
    opening_state: str | None = None
    broken: bool = False
    break_pos: int | None = None
    break_t: str | None = None
    break_beyond: float | None = None
    break_body: float | None = None
    break_range: float | None = None
    break_close_loc: float | None = None
    break_open: float | None = None
    break_close: float | None = None
    prev_close: float | None = None
    wick_only_n: int = 0
    micro_break_n: int = 0
    away_n: int = 0
    max_away: float = 0.0
    left: bool = False
    left_pos: int | None = None
    retest_pos: int | None = None
    retest_t: str | None = None
    retest_high: float | None = None
    retest_low: float | None = None
    retest_range: float | None = None
    held: bool = False
    dead: bool = False
    death: str | None = None
    max_fav: float | None = None
    peak_disp: float | None = None
    recross_closes: int = 0
    touched_or_high: bool = False
    touched_or_low: bool = False
    five_m_no_expansion_n: int = 0
    accept_through_n: int = 0
    s2: bool = False
    s3: bool = False
    s4: bool = False
    setup_eligible: bool = False
    setup_eligible_at: str | None = None
    setup_eligible_pos: int | None = None
    location: dict[str, Any] | None = None
    e0_emitted: bool = False
    e1_emitted: bool = False
    e1_cancel: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


def new_side(sign: int) -> SideState:
    return SideState(sign=int(sign))


def step_break_retest(
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
    n1m: float | None,
    open_0900: Any,
    zones: list[dict[str, Any]] | None = None,
    pdh: Any = None,
    pdl: Any = None,
    pdc: Any = None,
    vwap: Any = None,
    sma25: Any = None,
    sma75: Any = None,
) -> None:
    """1m break → leave → first retest. Location classified at hold. No 1m eligibility."""
    if st.dead:
        return
    if t < OR_KNOWN_FROM or t > LAST_TRIGGER:
        return
    if not (_finite(or_high) and _finite(or_low) and or_high > or_low):
        st.dead = True
        st.death = "or_invalid"
        return
    if not (_finite(high) and _finite(low) and _finite(close)):
        return
    h, l, c = float(high), float(low), float(close)
    opn = float(open_px) if _finite(open_px) else c
    boundary = _boundary(or_high, or_low, st.sign)
    or_range = float(or_high - or_low)
    zones = list(zones or [])

    disp = opening_net(sign=st.sign, open_0900=open_0900, close_now=c)
    if disp is not None:
        if st.peak_disp is None or float(disp) > float(st.peak_disp):
            st.peak_disp = float(disp)
    if h >= float(or_high):
        st.touched_or_high = True
    if l <= float(or_low):
        st.touched_or_low = True
    if st.left and not _hold_close(c, boundary, st.sign):
        st.recross_closes += 1
    else:
        if st.left and _hold_close(c, boundary, st.sign):
            st.recross_closes = 0

    th = classify_thesis(
        sign=st.sign,
        close=c,
        high=h,
        low=l,
        or_high=or_high,
        or_low=or_low,
        open_0900=open_0900,
        peak_disp=st.peak_disp,
        wick_only_n=st.wick_only_n,
        micro_break_n=st.micro_break_n,
        recross_closes=st.recross_closes,
        left=st.left,
        broken=st.broken,
        five_m_no_expansion_n=st.five_m_no_expansion_n,
        both_or_extremes_revisited=bool(st.left and st.touched_or_high and st.touched_or_low),
    )
    if th.get("lost"):
        st.dead = True
        st.death = str(th.get("reason") or "OPENING_THESIS_LOST")
        st.extra["thesis"] = th
        return

    if not st.broken:
        if t > LAST_BREAK:
            return
        if _wick_beyond(h, l, c, boundary, st.sign):
            st.wick_only_n += 1
            return
        if not _beyond_close(c, boundary, st.sign):
            return
        if is_micro_break(sign=st.sign, close=c, high=h, low=l, boundary=boundary, or_range=or_range, atr=atr):
            st.micro_break_n += 1
            return
        st.broken = True
        st.break_pos = pos
        st.break_t = t
        st.break_open = opn
        st.break_close = c
        st.prev_close = float(prev_close) if _finite(prev_close) else None
        st.break_beyond = (c - boundary) if st.sign > 0 else (boundary - c)
        st.break_body = abs(c - opn)
        st.break_range = h - l
        st.break_close_loc = close_loc(h, l, c)
        st.max_fav = _fav_extreme(sign=st.sign, high=h, low=l)
        return

    fav = _fav_extreme(sign=st.sign, high=h, low=l)
    if st.max_fav is None:
        st.max_fav = fav
    elif st.sign > 0:
        st.max_fav = max(float(st.max_fav), fav)
    else:
        st.max_fav = min(float(st.max_fav), fav)

    if st.retest_pos is None:
        if pos <= int(st.break_pos or -1):
            return
        if not _beyond_close(c, boundary, st.sign):
            if not st.left:
                st.dead = True
                st.death = "immediate_collapse"
                return
            st.retest_pos = pos
            st.retest_t = t
            st.retest_high = h
            st.retest_low = l
            st.held = False
            st.dead = True
            st.death = "failed_retest"
            return
        if _fully_away(h, l, boundary, st.sign):
            st.away_n += 1
            st.max_away = max(float(st.max_away), _away_dist(h, l, boundary, st.sign))
            if (not st.left) and (
                st.away_n >= int(MIN_AWAY_BARS)
                or (_finite(or_range) and or_range > 0 and st.max_away >= float(LEAVE_EXT_OR_FRAC) * or_range)
            ):
                floor = float(MEANINGFUL_LEAVE_NOISE_MULT) * float(n1m) if _finite(n1m) else None
                if floor is None or float(st.max_away) >= float(floor):
                    st.left = True
                    st.left_pos = pos
            return
        if st.left and _returns_to(h, l, boundary, st.sign):
            st.retest_pos = pos
            st.retest_t = t
            st.retest_high = h
            st.retest_low = l
            st.retest_range = float(h - l)
            st.held = _hold_close(c, boundary, st.sign)
            if not st.held:
                st.dead = True
                st.death = "failed_retest"
                return
            st.s3 = True
            loc = classify_s2(
                sign=st.sign,
                close=c,
                or_high=or_high,
                or_low=or_low,
                break_t=st.break_t,
                break_close=st.break_close,
                max_fav=st.max_fav,
                retest_t=st.retest_t,
                retest_high=st.retest_high,
                retest_low=st.retest_low,
                away_n=st.away_n,
                max_away=st.max_away,
                left=st.left,
                n1m=n1m,
                zones=zones,
                pdh=pdh,
                pdl=pdl,
                pdc=pdc,
                vwap=vwap,
                sma25=sma25,
                sma75=sma75,
                atr=atr,
            )
            st.location = loc
            if not loc.get("ok"):
                st.dead = True
                st.death = str(loc.get("reason") or "NO_VALID_LOCATION")
                return
            st.s2 = True
            return
        return

    if not st.held or not st.s2:
        return
    if _breached_retest(sign=st.sign, close=c, retest_high=st.retest_high, retest_low=st.retest_low):
        st.dead = True
        st.death = "RETEST_EXTREME_BREACH"
        return
    defn = ((st.location or {}).get("defended") or {}) if st.location else {}
    dlo = defn.get("defended_zone_low")
    dhi = defn.get("defended_zone_high")
    dtype = defn.get("defended_level_type")
    if _through_defended(sign=st.sign, close=c, defended_lo=dlo, defended_hi=dhi, or_high=or_high, or_low=or_low, level_type=dtype):
        st.accept_through_n += 1
        if st.accept_through_n >= int(ACCEPTANCE_FAIL_CLOSES):
            st.dead = True
            st.death = "PERSISTENT_ACCEPTANCE_FAILURE"
            return
    else:
        st.accept_through_n = 0
