"""S2 meaningful price location. Families A/B/C. No additive score. OR touch alone invalid."""
from __future__ import annotations

from typing import Any

from research.pb1_opening_range_continuation_face_valid_v2.machine import _finite
from research.pb1_playbook_redesign_v3.structure_route import (
    classify_zone_path,
    nearest_opposing,
    planned_r,
    structural_route,
    zone_cleared,
    zone_entered,
)
from research.pb1_v3_2_opening_drive_and_reacceleration_semantics.machine import classify_defense
from research.pb1_v4_machine_implementation import (
    CONFLUENCE_N1M,
    LEAVE_EXT_OR_FRAC,
    LEAVE_N1M_MIN,
    MEANINGFUL_LEAVE_NOISE_MULT,
    MEANINGFUL_R_NOISE_MULT,
    MIN_AWAY_BARS,
    NEAR_OPP_R_FRAC,
    OVERHEAD_ATR_FRAC,
    ROOM_N1M_MIN,
    STRUCTURAL_ROUTE_R_MULT,
)


def _level_dist(px: float, level: Any) -> float | None:
    if not _finite(level):
        return None
    return abs(float(px) - float(level))


def classify_s2(
    *,
    sign: int,
    close: float,
    or_high: float,
    or_low: float,
    break_t: str | None,
    break_close: Any,
    max_fav: Any,
    retest_t: str | None,
    retest_high: Any,
    retest_low: Any,
    away_n: int,
    max_away: float,
    left: bool,
    n1m: Any,
    zones: list[dict[str, Any]],
    pdh: Any = None,
    pdl: Any = None,
    pdc: Any = None,
    vwap: Any = None,
    sma25: Any = None,
    sma75: Any = None,
    atr: Any = None,
) -> dict[str, Any]:
    or_range = float(or_high) - float(or_low) if _finite(or_high) and _finite(or_low) else float("nan")
    leave_floor = float(MEANINGFUL_LEAVE_NOISE_MULT) * float(LEAVE_N1M_MIN) * float(n1m) if _finite(n1m) else None
    real_leave = bool(left) and (
        int(away_n) >= int(MIN_AWAY_BARS)
        or (_finite(or_range) and or_range > 0 and float(max_away) >= float(LEAVE_EXT_OR_FRAC) * or_range)
    )
    if leave_floor is not None:
        real_leave = real_leave and float(max_away) >= float(leave_floor)
    if not real_leave:
        return {
            "ok": False,
            "family": None,
            "reason": "NO_REAL_LEAVE",
            "or_touch_alone": False,
        }

    zp = classify_zone_path(
        sign=int(sign),
        or_high=or_high,
        or_low=or_low,
        break_close=break_close,
        max_fav=max_fav,
        retest_high=retest_high,
        retest_low=retest_low,
        zones=list(zones or []),
    )
    defn = classify_defense(
        sign=int(sign),
        close=float(close),
        or_high=float(or_high),
        or_low=float(or_low),
        retest_high=retest_high,
        retest_low=retest_low,
        zone_path=zp,
    )
    r_px = planned_r(sign=int(sign), trigger_close=close, retest_high=retest_high, retest_low=retest_low)
    r_floor = float(MEANINGFUL_R_NOISE_MULT) * float(ROOM_N1M_MIN) * float(n1m) if _finite(n1m) else None
    room_ok = r_floor is not None and _finite(r_px) and float(r_px) >= float(r_floor)
    or_wide_enough = _finite(n1m) and _finite(or_range) and float(or_range) >= float(n1m)

    if str(zp.get("zone_class") or "") == "ZONE_CLEARED_AND_DEFENDED" and defn.get("ok"):
        z = zp.get("cleared_zone") or {}
        return {
            "ok": True,
            "family": "CLEARED_ZONE_RETEST",
            "reason": "zone_known_cleared_far_side_hold",
            "zone_identity": z.get("name") or z.get("source") or z.get("kind") or z.get("role"),
            "zone_low": z.get("zone_low"),
            "zone_high": z.get("zone_high"),
            "break_t": break_t,
            "clear_evidence": "max_fav_cleared_prior_opposing_zone",
            "retest_t": retest_t,
            "hold_evidence": defn.get("defense_quality"),
            "planned_R": r_px,
            "zone_path": zp,
            "defended": defn,
            "or_touch_alone": False,
            "STRUCTURAL_ROUTE_R_MULT": float(STRUCTURAL_ROUTE_R_MULT),
        }

    if _finite(max_fav) and _finite(atr) and float(atr) > 0:
        tagged_refs: list[str] = []
        if int(sign) > 0:
            pairs = (("SMA25", sma25), ("SMA75", sma75), ("PDH", pdh))
        else:
            pairs = (("SMA25", sma25), ("SMA75", sma75), ("PDL", pdl))
        for name, lvl in pairs:
            if not _finite(lvl):
                continue
            if int(sign) > 0:
                tagged = float(max_fav) >= float(lvl) - float(OVERHEAD_ATR_FRAC) * float(atr)
                cleared = float(max_fav) > float(lvl) + 0.15 * float(atr)
            else:
                tagged = float(max_fav) <= float(lvl) + float(OVERHEAD_ATR_FRAC) * float(atr)
                cleared = float(max_fav) < float(lvl) - 0.15 * float(atr)
            if tagged and not cleared:
                tagged_refs.append(name)
        if tagged_refs:
            return {
                "ok": False,
                "family": None,
                "reason": "OVERHEAD_UNCLEARED_REFERENCE",
                "overhead_level": tagged_refs[0],
                "planned_R": r_px,
                "zone_path": zp,
                "defended": defn,
                "or_touch_alone": False,
            }

    opp = nearest_opposing(sign=int(sign), px=float(close), zones=list(zones or []), strictly_ahead=True)
    if opp is not None and _finite(max_fav):
        entered = zone_entered(
            sign=int(sign),
            high=float(max_fav) if int(sign) > 0 else float(close),
            low=float(close) if int(sign) > 0 else float(max_fav),
            zone=opp,
        )
        cleared = zone_cleared(sign=int(sign), px=float(max_fav), zone=opp)
        if entered and not cleared:
            return {
                "ok": False,
                "family": None,
                "reason": "LOCATION_ALREADY_INSIDE_OPPOSING",
                "planned_R": r_px,
                "nearest_opposing": opp,
                "zone_path": zp,
                "defended": defn,
                "or_touch_alone": False,
            }

    if not room_ok:
        return {
            "ok": False,
            "family": None,
            "reason": "NO_MEANINGFUL_ROOM",
            "planned_R": r_px,
            "zone_path": zp,
            "defended": defn,
            "or_touch_alone": False,
        }

    route = structural_route(sign=int(sign), trigger_close=float(close), r_px=float(r_px), zones=list(zones or []))
    if opp is not None and _finite(r_px) and float(r_px) > 0:
        if int(sign) > 0:
            dist = max(0.0, float(opp["zone_low"]) - float(close))
        else:
            dist = max(0.0, float(close) - float(opp["zone_high"]))
        if dist < float(NEAR_OPP_R_FRAC) * float(r_px):
            return {
                "ok": False,
                "family": None,
                "reason": "NEAREST_OPPOSING_TOO_CLOSE",
                "planned_R": r_px,
                "nearest_opposing": opp,
                "zone_path": zp,
                "defended": defn,
                "or_touch_alone": False,
            }
    if route.get("status") != "STRUCTURAL_ROUTE_CLEAR":
        return {
            "ok": False,
            "family": None,
            "reason": "STRUCTURALLY_BLOCKED",
            "planned_R": r_px,
            "route": route,
            "zone_path": zp,
            "defended": defn,
            "or_touch_alone": False,
        }

    if defn.get("ok") and str(defn.get("defense_quality") or "") == "MEANINGFUL_DEFENSE":
        if str(defn.get("defended_level_type") or "") in ("OR_BOUNDARY", "OR"):
            if not or_wide_enough:
                return {
                    "ok": False,
                    "family": None,
                    "reason": "OR_NOT_MEANINGFUL_LOCATION",
                    "planned_R": r_px,
                    "zone_path": zp,
                    "defended": defn,
                    "or_touch_alone": False,
                }
            return {
                "ok": True,
                "family": "OR_HELD_AFTER_REAL_DRIVE",
                "reason": "real_leave_then_or_retest_hold",
                "break_t": break_t,
                "retest_t": retest_t,
                "hold_evidence": defn.get("defense_quality"),
                "planned_R": r_px,
                "zone_path": zp,
                "defended": defn,
                "or_touch_alone": False,
            }

    # Family C: one identified non-OR causal level + visible hold. Not "2 of 7".
    boundary = float(or_high) if int(sign) > 0 else float(or_low)
    refs = (
        ("PDH", pdh, "prior_day_high_known_before_session"),
        ("PDL", pdl, "prior_day_low_known_before_session"),
        ("PDC", pdc, "prior_day_close_known_before_session"),
        ("VWAP", vwap, "session_vwap_from_completed_minutes"),
        ("SMA25", sma25, "prior_daily_closes_only"),
        ("SMA75", sma75, "prior_daily_closes_only"),
        ("recent_causal_swing", (opp or {}).get("zone_mid") if opp else None, "frozen_causal_sr_swing_zone"),
    )
    hit = None
    if _finite(n1m) and float(n1m) > 0:
        for name, lvl, why in refs:
            d = _level_dist(boundary, lvl)
            if d is None:
                continue
            if float(d) <= float(CONFLUENCE_N1M) * float(n1m):
                hit = {"level": name, "why_known": why, "distance": d, "level_px": lvl}
                break
    if hit is not None and defn.get("ok") and str(defn.get("defense_quality") or "") == "MEANINGFUL_DEFENSE":
        return {
            "ok": True,
            "family": "VISIBLE_CONFLUENT_LOCATION",
            "reason": "identified_causal_level_plus_reaction",
            "confluent_level": hit,
            "reaction": "retest_hold_on_continuation_side",
            "planned_R": r_px,
            "zone_path": zp,
            "defended": defn,
            "or_touch_alone": False,
            "two_of_seven_rule": False,
        }

    touch_only = str(defn.get("defense_quality") or "") == "TOUCH_ONLY" or not defn.get("ok")
    return {
        "ok": False,
        "family": None,
        "reason": "OR_TOUCH_ONLY" if touch_only else "NO_VALID_LOCATION_FAMILY",
        "planned_R": r_px,
        "zone_path": zp,
        "defended": defn,
        "or_touch_alone": True if touch_only else False,
    }
