"""V3.1 continuation machine. V3 reclaim/route plus drive continuity, noise-floor leave/R, meaningful defense."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from research.pb1_opening_range_continuation_face_valid_v2 import LAST_BREAK, LAST_TRIGGER, LEAVE_EXT_OR_FRAC, MIN_AWAY_BARS, OR_KNOWN_FROM, RETEST_FRESHNESS_MIN
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
    trading_minutes,
    vwap_location,
)
from research.pb1_playbook_redesign_v3.structure_route import (
    classify_zone_path,
    defended_location,
    nearest_opposing,
    planned_r,
    structural_route,
    zone_entered,
)
from research.pb1_v3_1_face_validity_fix import (
    ACCEPTANCE_FAIL_CLOSES,
    ARCHIVED_TRIGGER,
    MEANINGFUL_LEAVE_NOISE_MULT,
    MEANINGFUL_R_NOISE_MULT,
    PRIMARY_TRIGGER,
)
from research.pb1_v3_1_face_validity_fix.drive import opening_impulse_lost


def is_reclaim(*, sign: int, close: float, retest_high: Any, retest_low: Any) -> bool:
    if int(sign) > 0:
        return _finite(retest_high) and float(close) > float(retest_high)
    return _finite(retest_low) and float(close) < float(retest_low)


def is_failed_push(*, sign: int, high: float, low: float, close: float, boundary: float) -> bool:
    if int(sign) > 0:
        return float(low) < float(boundary) and float(close) >= float(boundary)
    return float(high) > float(boundary) and float(close) <= float(boundary)


def reclaim_move(*, sign: int, close: float, retest_high: Any, retest_low: Any) -> float:
    if int(sign) > 0:
        if not _finite(retest_high):
            return float("nan")
        return float(close) - float(retest_high)
    if not _finite(retest_low):
        return float("nan")
    return float(retest_low) - float(close)


def _fav_extreme(*, sign: int, high: float, low: float) -> float:
    return float(high) if int(sign) > 0 else float(low)


def _through_defended(*, sign: int, close: float, defended_lo: Any, defended_hi: Any, or_high: float, or_low: float, level_type: str | None) -> bool:
    if level_type == "CLEARED_ZONE" and _finite(defended_lo) and _finite(defended_hi):
        if int(sign) > 0:
            return float(close) < float(defended_lo)
        return float(close) > float(defended_hi)
    boundary = float(or_high) if int(sign) > 0 else float(or_low)
    return not _hold_close(float(close), boundary, int(sign))


def _breached_retest(*, sign: int, high: float, low: float, retest_high: Any, retest_low: Any) -> bool:
    if int(sign) > 0:
        return _finite(retest_low) and float(low) < float(retest_low)
    return _finite(retest_high) and float(high) > float(retest_high)


def _close_continuation_side(*, sign: int, close: float, defended_lo: Any, defended_hi: Any, level_type: str | None, or_high: float, or_low: float) -> bool:
    if level_type == "CLEARED_ZONE" and _finite(defended_lo) and _finite(defended_hi):
        if int(sign) > 0:
            return float(close) > float(defended_hi)
        return float(close) < float(defended_lo)
    boundary = float(or_high) if int(sign) > 0 else float(or_low)
    return _hold_close(float(close), boundary, int(sign))


def classify_defense(
    *,
    sign: int,
    close: float,
    or_high: float,
    or_low: float,
    retest_high: Any,
    retest_low: Any,
    zone_path: dict[str, Any],
) -> dict[str, Any]:
    raw = defended_location(
        sign=sign,
        or_high=float(or_high),
        or_low=float(or_low),
        retest_high=retest_high,
        retest_low=retest_low,
        zone_path=zone_path,
    )
    if not raw.get("ok"):
        return {**raw, "defense_quality": None}
    reaches = True
    cont = _close_continuation_side(
        sign=sign,
        close=float(close),
        defended_lo=raw.get("defended_zone_low"),
        defended_hi=raw.get("defended_zone_high"),
        level_type=raw.get("defended_level_type"),
        or_high=or_high,
        or_low=or_low,
    )
    if not cont:
        return {**raw, "ok": False, "defense_quality": "TOUCH_ONLY", "reason": "TOUCH_ONLY"}
    return {**raw, "ok": True, "defense_quality": "MEANINGFUL_DEFENSE", "reaches_defended_area": reaches}


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
    break_close: float | None = None
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
    max_fav: float | None = None
    reached_structure: bool = False
    nearest_opp_at_break: dict[str, Any] | None = None
    failed_push_seen: bool = False
    failed_push_archive: list[dict[str, Any]] = field(default_factory=list)
    retest_extreme_breached: bool = False
    accept_through_n: int = 0
    persistent_accept_fail: bool = False
    defended: dict[str, Any] | None = None
    zone_path: dict[str, Any] | None = None
    s2: bool = False
    s3: bool = False
    s4: bool = False
    s5: bool = False
    impulse_lost: bool = False


def new_side(sign: int) -> SideState:
    return SideState(sign=int(sign))


def _emit_base(st: SideState, *, pos: int, t: str, or_high: float, or_low: float) -> dict[str, Any]:
    return {
        "DIR": st.sign,
        "direction": "bull" if st.sign > 0 else "bear",
        "trigger_pos": pos,
        "trigger_t": t,
        "trigger_labels": [PRIMARY_TRIGGER],
        "trigger_primary": PRIMARY_TRIGGER,
        "break_pos": st.break_pos,
        "break_t": st.break_t,
        "break_beyond": st.break_beyond,
        "break_body": st.break_body,
        "break_range": st.break_range,
        "break_close_loc": st.break_close_loc,
        "break_open": st.break_open,
        "break_close": st.break_close,
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
        "failed_push_seen": st.failed_push_seen,
        "reached_structure_before_retest": st.reached_structure,
        "retest_extreme_breached_before_trigger": st.retest_extreme_breached,
        "persistent_accept_fail_before_trigger": st.persistent_accept_fail,
        "invalidation_primary": "RETEST_EXTREME_BREACH",
        "invalidation_secondary": "PERSISTENT_ACCEPTANCE_FAILURE",
        "acceptance_fail_closes": ACCEPTANCE_FAIL_CLOSES,
        "one_close_or_not_immediate_fail": True,
        "defense_quality": (st.defended or {}).get("defense_quality"),
    }


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
    n1m: float | None,
    zones: list[dict[str, Any]] | None = None,
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
    zones = list(zones or [])

    if not st.broken:
        if t > LAST_BREAK:
            return None
        if opening_impulse_lost(sign=st.sign, close=c, or_high=or_high, or_low=or_low):
            st.impulse_lost = True
            st.dead = True
            st.death = "OPENING_IMPULSE_LOST"
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
        st.s2 = True
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
        st.nearest_opp_at_break = nearest_opposing(sign=st.sign, px=c, zones=zones, strictly_ahead=True)
        if st.nearest_opp_at_break is not None and zone_entered(sign=st.sign, high=h, low=l, zone=st.nearest_opp_at_break):
            st.reached_structure = True
        return None

    fav = _fav_extreme(sign=st.sign, high=h, low=l)
    if st.max_fav is None:
        st.max_fav = fav
    else:
        if st.sign > 0:
            st.max_fav = max(float(st.max_fav), fav)
        else:
            st.max_fav = min(float(st.max_fav), fav)
    if (not st.reached_structure) and st.nearest_opp_at_break is not None:
        if zone_entered(sign=st.sign, high=h, low=l, zone=st.nearest_opp_at_break):
            st.reached_structure = True

    if st.follow_through is None and st.break_pos is not None and pos == int(st.break_pos) + 1:
        st.follow_through = _beyond_close(c, boundary, st.sign)

    if st.retest_pos is None:
        if pos <= int(st.break_pos or -1):
            return None
        mins = trading_minutes(st.break_t, t)
        if _finite(mins) and mins > float(RETEST_FRESHNESS_MIN) and not st.left:
            st.dead = True
            st.death = "STALE_30M"
            return None
        if not _beyond_close(c, boundary, st.sign):
            if not st.left:
                st.dead = True
                st.death = "immediate_collapse"
                return None
            if _finite(mins) and mins > float(RETEST_FRESHNESS_MIN):
                st.dead = True
                st.death = "STALE_30M"
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
                st.away_n >= int(MIN_AWAY_BARS)
                or (_finite(or_range) and or_range > 0 and st.max_away >= float(LEAVE_EXT_OR_FRAC) * or_range)
            ):
                st.left = True
                st.left_pos = pos
            floor = float(MEANINGFUL_LEAVE_NOISE_MULT) * float(n1m) if _finite(n1m) else None
            if st.left and floor is not None and float(st.max_away) >= float(floor):
                st.s3 = True
            return None
        if st.left and _returns_to(h, l, boundary, st.sign):
            if _finite(mins) and mins > float(RETEST_FRESHNESS_MIN):
                st.dead = True
                st.death = "STALE_30M"
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
            floor = float(MEANINGFUL_LEAVE_NOISE_MULT) * float(n1m) if _finite(n1m) else None
            if (not st.s3) or floor is None or float(st.max_away) < float(floor):
                st.dead = True
                st.death = "MICRO_OR_LEAK"
                return None
            st.s4 = True
            if st.reached_structure:
                st.dead = True
                st.death = "MOVE_ALREADY_REACHED_STRUCTURE"
                return None
            zp = classify_zone_path(
                sign=st.sign,
                or_high=or_high,
                or_low=or_low,
                break_close=st.break_close,
                max_fav=st.max_fav,
                retest_high=st.retest_high,
                retest_low=st.retest_low,
                zones=zones,
            )
            st.zone_path = zp
            defn = classify_defense(
                sign=st.sign,
                close=c,
                or_high=float(or_high),
                or_low=float(or_low),
                retest_high=st.retest_high,
                retest_low=st.retest_low,
                zone_path=zp,
            )
            st.defended = defn
            if not defn.get("ok"):
                st.dead = True
                st.death = "OTHER"
                return None
            st.s5 = True
            return None
        return None

    if not st.held or not st.s5:
        return None
    if pos <= int(st.retest_pos or -1):
        return None

    if _breached_retest(sign=st.sign, high=h, low=l, retest_high=st.retest_high, retest_low=st.retest_low):
        st.retest_extreme_breached = True
        st.dead = True
        st.death = "OTHER"
        return None

    dlo = (st.defended or {}).get("defended_zone_low")
    dhi = (st.defended or {}).get("defended_zone_high")
    dtype = (st.defended or {}).get("defended_level_type")
    if _through_defended(sign=st.sign, close=c, defended_lo=dlo, defended_hi=dhi, or_high=or_high, or_low=or_low, level_type=dtype):
        st.accept_through_n += 1
        if st.accept_through_n >= int(ACCEPTANCE_FAIL_CLOSES):
            st.persistent_accept_fail = True
            st.dead = True
            st.death = "OTHER"
            return None
    else:
        st.accept_through_n = 0

    if is_failed_push(sign=st.sign, high=h, low=l, close=c, boundary=boundary):
        st.failed_push_seen = True
        st.failed_push_archive.append(
            {
                "t": t,
                "pos": pos,
                "trigger_archived": ARCHIVED_TRIGGER,
                "mechanism": "FAILED_PUSH_SEPARATE_MECHANISM",
                "removed_for_return": False,
            }
        )
        return None

    if not is_reclaim(sign=st.sign, close=c, retest_high=st.retest_high, retest_low=st.retest_low):
        return None

    r_px = planned_r(sign=st.sign, trigger_close=c, retest_high=st.retest_high, retest_low=st.retest_low)
    if not (_finite(r_px) and float(r_px) > 0):
        st.dead = True
        st.death = "OTHER"
        return None
    r_floor = float(MEANINGFUL_R_NOISE_MULT) * float(n1m) if _finite(n1m) else None
    if r_floor is None or float(r_px) < float(r_floor):
        st.dead = True
        st.death = "MICRO_STRUCTURE_NOT_TRADABLE"
        return None
    route = structural_route(sign=st.sign, trigger_close=c, r_px=float(r_px), zones=zones)
    if route.get("status") != "STRUCTURAL_ROUTE_CLEAR":
        st.dead = True
        st.death = "STRUCTURALLY_BLOCKED"
        return None

    rc = reclaim_move(sign=st.sign, close=c, retest_high=st.retest_high, retest_low=st.retest_low)
    st.emitted = True
    out = _emit_base(st, pos=pos, t=t, or_high=or_high, or_low=or_low)
    out["planned_entry_reference"] = float(c)
    out["structural_stop_reference"] = float(st.retest_low) if st.sign > 0 else float(st.retest_high)
    out["planned_R"] = float(r_px)
    out["NORMAL_1M_RANGE"] = float(n1m) if _finite(n1m) else None
    out["planned_R_over_NORMAL_1M_RANGE"] = (float(r_px) / float(n1m)) if _finite(n1m) and float(n1m) > 0 else None
    out["max_away_over_NORMAL_1M_RANGE"] = (float(st.max_away) / float(n1m)) if _finite(n1m) and float(n1m) > 0 else None
    out["reclaim_move"] = float(rc) if _finite(rc) else None
    out["reclaim_move_over_NORMAL_1M_RANGE"] = (float(rc) / float(n1m)) if _finite(rc) and _finite(n1m) and float(n1m) > 0 else None
    out["reclaim_gated"] = False
    out["structural_route"] = route.get("status")
    out["route"] = route
    out["zone_class"] = (st.zone_path or {}).get("zone_class")
    out["defended_level_type"] = (st.defended or {}).get("defended_level_type")
    out["defended_zone_low"] = (st.defended or {}).get("defended_zone_low")
    out["defended_zone_high"] = (st.defended or {}).get("defended_zone_high")
    out["retest_extreme"] = (st.defended or {}).get("retest_extreme")
    out["s6"] = True
    out["s7"] = True
    out["s8"] = True
    out["noise_mult_searched"] = False
    out["leave_threshold_optimized"] = False
    return out


_ = vwap_location
