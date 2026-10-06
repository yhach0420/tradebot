"""Causal 5m TREND → SMA25-zone PULLBACK → 1m PRICE-ACTION TRIGGER. No future 5m close."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from research.mtf_5min_sma5_25_75_with_1min_trigger_v1.ma import reached_sma25_zone
from research.sma5_25_75_trend_pullback_playbook_discovery_v1.ma import (
    aligned_reclaim,
    bar_touches,
    side_aligned,
    through_level,
)
from research.sma5_25_75_trend_pullback_playbook_discovery_v1.machine import confirm_minor_swing

_ = confirm_minor_swing


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


@dataclass
class SideState:
    stack_seen: bool = False
    had_trend_side: bool = False
    pullback: bool = False
    approached_25: bool = False
    extreme: float | None = None
    sma25_at_pullback: float | None = None
    sma75_at_pullback: float | None = None
    impulse_tv: list[float] = field(default_factory=list)
    pullback_tv: list[float] = field(default_factory=list)
    pullback_vwap_touch: bool = False
    triggered: bool = False
    last_b_seq: int | None = None


def new_sides() -> dict[int, SideState]:
    return {1: SideState(), -1: SideState()}


def reset_side(st: SideState) -> None:
    st.stack_seen = False
    st.had_trend_side = False
    st.pullback = False
    st.approached_25 = False
    st.extreme = None
    st.sma25_at_pullback = None
    st.sma75_at_pullback = None
    st.impulse_tv = []
    st.pullback_tv = []
    st.pullback_vwap_touch = False
    st.triggered = False


def _mean(xs: list[float]) -> float | None:
    if not xs:
        return None
    return float(sum(xs) / len(xs))


def location_cat(*, ma25: bool, vwap: bool, sr: bool) -> str:
    if ma25 and vwap and sr:
        return "MA25_VWAP_SR"
    if ma25 and vwap:
        return "MA25_VWAP"
    if ma25 and sr:
        return "MA25_SR"
    if ma25:
        return "MA25_ONLY"
    return "none"


def step_side(
    st: SideState,
    *,
    seq: int,
    sign: int,
    close: float,
    high: float,
    low: float,
    prev_c: Any,
    sma5: Any,
    sma25: Any,
    sma75: Any,
    prev_sma5: Any,
    vwap: Any,
    prev_vw: Any,
    tv_pctl: Any,
    swing_high: Any,
    swing_low: Any,
    stack_now: bool,
    atr1m: Any,
) -> dict[str, Any] | None:
    """Advance one DIR on live-causal 5m MAs. Never inspects a future 1m or 5m close."""
    s = int(sign)
    fail75 = through_level(close, sma75, s)
    if fail75:
        reset_side(st)
        return None
    if stack_now:
        st.stack_seen = True
    if not st.stack_seen:
        return None
    on5 = side_aligned(close, sma5, s)
    if on5 and not st.pullback:
        st.had_trend_side = True
        if _finite(tv_pctl):
            st.impulse_tv.append(float(tv_pctl))
    if (not st.pullback) and st.had_trend_side and (not on5):
        st.pullback = True
        st.approached_25 = False
        st.extreme = float(low) if s > 0 else float(high)
        st.sma25_at_pullback = float(sma25) if _finite(sma25) else None
        st.sma75_at_pullback = float(sma75) if _finite(sma75) else None
        st.pullback_tv = []
        st.pullback_vwap_touch = False
        st.triggered = False
    event = None
    reclaim = aligned_reclaim(prev_c, prev_sma5, close, sma5, s)
    if s > 0:
        swing_brk = _finite(swing_high) and _finite(close) and float(close) > float(swing_high)
    else:
        swing_brk = _finite(swing_low) and _finite(close) and float(close) < float(swing_low)
    trigger_pa = bool(reclaim or swing_brk)
    if st.pullback and not st.triggered:
        prior_ext = st.extreme
        if s > 0:
            st.extreme = min(float(st.extreme), float(low)) if _finite(st.extreme) else float(low)
            progress = _finite(prior_ext) and _finite(low) and float(low) > float(prior_ext)
        else:
            st.extreme = max(float(st.extreme), float(high)) if _finite(st.extreme) else float(high)
            progress = _finite(prior_ext) and _finite(high) and float(high) < float(prior_ext)
        if reached_sma25_zone(close, high, low, sma25, atr1m):
            st.approached_25 = True
            if _finite(sma25):
                st.sma25_at_pullback = float(sma25)
            if _finite(sma75):
                st.sma75_at_pullback = float(sma75)
        if bar_touches(high, low, vwap):
            st.pullback_vwap_touch = True
        if _finite(tv_pctl):
            st.pullback_tv.append(float(tv_pctl))
        vwap_reclaim = aligned_reclaim(prev_c, prev_vw, close, vwap, s)
        vwap_hold = bool(_finite(st.extreme) and _finite(vwap) and (not through_level(st.extreme, vwap, s)))
        vwap_opp = through_level(close, vwap, s)
        if st.approached_25 and trigger_pa and (progress or reclaim):
            st.triggered = True
            imp = _mean(st.impulse_tv)
            pb = _mean(st.pullback_tv)
            trg = float(tv_pctl) if _finite(tv_pctl) else None
            part = bool(imp is not None and pb is not None and trg is not None and float(imp) > float(pb) and float(trg) > float(pb))
            event = {
                "kind": "A",
                "reclaim": bool(reclaim),
                "swing_break": bool(swing_brk),
                "progress_stop": bool(progress or reclaim),
                "invalidation": st.extreme,
                "approached_25": True,
                "stack_seen": True,
                "impulse_tv": imp,
                "pullback_tv": pb,
                "trigger_tv": trg,
                "participation_sequence": part,
                "vwap_near": bool(st.pullback_vwap_touch or bar_touches(high, low, vwap)),
                "vwap_hold": vwap_hold,
                "vwap_reclaim": bool(vwap_reclaim),
                "vwap_opposition": bool(vwap_opp),
                "sma25_at_entry": st.sma25_at_pullback,
                "sma75_at_entry": st.sma75_at_pullback,
                "pullback_extreme": st.extreme,
            }
            reset_side(st)
            st.stack_seen = bool(stack_now)
            if on5 and stack_now:
                st.had_trend_side = True
        elif on5 and not st.approached_25:
            st.pullback = False
            st.extreme = None
            st.had_trend_side = True
    _ = seq
    return event


def b_trigger(*, stack_now: bool, reclaim: bool, swing_brk: bool, st: SideState, seq: int, refractory: int) -> bool:
    if stack_now:
        return False
    if not (reclaim or swing_brk):
        return False
    if st.last_b_seq is not None and (seq - int(st.last_b_seq)) < int(refractory):
        return False
    st.last_b_seq = seq
    return True


def c_trigger(*, stack_now: bool, approached_25: bool, reclaim: bool, swing_brk: bool) -> bool:
    if not stack_now:
        return False
    if approached_25:
        return False
    return bool(reclaim or swing_brk)
