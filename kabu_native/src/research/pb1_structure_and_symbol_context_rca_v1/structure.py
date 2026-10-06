"""Entry-time causal structure. Frozen S/R zones + pre-known daily levels. No retune."""
from __future__ import annotations

from typing import Any

from research.pb1_structure_and_symbol_context_rca_v1 import (
    FROZEN_ZONE_HALF_ATR,
    NEAR_STRUCT_ATR,
    OPEN_SPACE_ATR,
    VERY_CLOSE_ATR,
)
from research.support_resistance_face_valid_first_interaction_rebuild_v1.select import select_salient
from research.support_resistance_face_valid_first_interaction_rebuild_v1.zones import snapshot_zones


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _hw(atr: float) -> float:
    return float(FROZEN_ZONE_HALF_ATR) * float(atr)


def _age_days(hist_dates: list[str], start: Any, session: str) -> int | None:
    if not start:
        return None
    s = str(start)
    n = 0
    for d in hist_dates:
        if s < str(d) < str(session):
            n += 1
    return n


def persist_sr_zone(z: dict[str, Any], *, session_date: str, hist_dates: list[str]) -> dict[str, Any]:
    lo = float(z["lo"])
    hi = float(z["hi"])
    mid = float(z["center"])
    return {
        "source": "SR_DETECTOR",
        "zone_id": z.get("zone_id"),
        "role": str(z.get("role") or ""),
        "state": str(z.get("state") or ""),
        "zone_low": lo,
        "zone_high": hi,
        "zone_mid": mid,
        "lo": lo,
        "hi": hi,
        "center": mid,
        "touch_count": int(z.get("touch_count") or 0),
        "reaction_count": int(z.get("distinct_swing_cycles") or z.get("touch_count") or 0),
        "last_touch_age": _age_days(hist_dates, z.get("last_pivot_date"), session_date),
        "zone_age": _age_days(hist_dates, z.get("ZONE_ACTIVATED_AT") or z.get("first_pivot_date"), session_date),
        "reaction_magnitude": z.get("reaction_magnitude_atr"),
        "is_sr_detector": True,
        "active": bool(z.get("active")),
        "broken_candidate": bool(z.get("broken_candidate")),
    }


def level_zone(name: str, role: str, px: Any, atr: float) -> dict[str, Any] | None:
    if not _finite(px) or not _finite(atr) or float(atr) <= 0:
        return None
    hw = _hw(float(atr))
    p = float(px)
    return {
        "source": name,
        "zone_id": f"LEVEL:{name}:{round(p, 4)}",
        "role": role,
        "state": f"LEVEL_{role}",
        "zone_low": p - hw,
        "zone_high": p + hw,
        "zone_mid": p,
        "lo": p - hw,
        "hi": p + hw,
        "center": p,
        "touch_count": 1,
        "reaction_count": 1,
        "last_touch_age": 1,
        "zone_age": 1,
        "reaction_magnitude": None,
        "is_sr_detector": False,
        "active": True,
        "broken_candidate": False,
    }


def _slim_zone(z: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "source",
        "zone_id",
        "role",
        "state",
        "zone_low",
        "zone_high",
        "zone_mid",
        "touch_count",
        "reaction_count",
        "last_touch_age",
        "zone_age",
        "reaction_magnitude",
        "is_sr_detector",
    )
    return {k: z.get(k) for k in keys}


def collect_zones(
    *,
    snap: dict[str, Any],
    session_date: str,
    hist_dates: list[str],
    atr: float,
    pdh: Any,
    pdl: Any,
    pdc: Any,
    d5h: Any,
    d5l: Any,
    sma25: Any,
    sma75: Any,
    vwap: Any,
    entry: float,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for key in ("resistance_active", "support_active", "resistance_broken", "support_broken"):
        for z in list(snap.get(key) or []):
            out.append(persist_sr_zone(z, session_date=session_date, hist_dates=hist_dates))
    for name, px, role in (
        ("PDH", pdh, "RESISTANCE"),
        ("PDL", pdl, "SUPPORT"),
        ("D5H", d5h, "RESISTANCE"),
        ("D5L", d5l, "SUPPORT"),
    ):
        z = level_zone(name, role, px, atr)
        if z:
            out.append(z)
    for name, px in (("PDC", pdc), ("SMA25", sma25), ("SMA75", sma75), ("VWAP", vwap)):
        if not _finite(px):
            continue
        role = "RESISTANCE" if float(px) >= float(entry) else "SUPPORT"
        z = level_zone(name, role, px, atr)
        if z:
            out.append(z)
    return out


def merge_nearby(zones: list[dict[str, Any]], atr: float) -> list[list[dict[str, Any]]]:
    if not zones or not _finite(atr) or float(atr) <= 0:
        return [[z] for z in zones]
    width = 2.0 * _hw(float(atr))
    xs = sorted(zones, key=lambda z: float(z["zone_mid"]))
    groups: list[list[dict[str, Any]]] = []
    cur = [xs[0]]
    for z in xs[1:]:
        if float(z["zone_mid"]) - float(cur[0]["zone_mid"]) <= width:
            cur.append(z)
        else:
            groups.append(cur)
            cur = [z]
    groups.append(cur)
    return groups


def _overlap_px(px: float, z: dict[str, Any]) -> bool:
    return float(z["zone_low"]) <= float(px) <= float(z["zone_high"])


def _range_overlap(lo: Any, hi: Any, z: dict[str, Any]) -> bool:
    if not (_finite(lo) and _finite(hi)):
        return False
    a, b = (float(lo), float(hi)) if float(lo) <= float(hi) else (float(hi), float(lo))
    return a <= float(z["zone_high"]) and b >= float(z["zone_low"])


def _dist_ahead(entry: float, z: dict[str, Any], sign: int) -> float:
    if sign > 0:
        if entry > float(z["zone_high"]):
            return float(entry) - float(z["zone_high"])
        if entry < float(z["zone_low"]):
            return float(z["zone_low"]) - float(entry)
        return 0.0
    if entry < float(z["zone_low"]):
        return float(z["zone_low"]) - float(entry)
    if entry > float(z["zone_high"]):
        return float(entry) - float(z["zone_high"])
    return 0.0


def _units(dist: Any, *, entry: float, atr: Any, med_1m: Any, med_or15: Any) -> dict[str, Any]:
    if not _finite(dist):
        return {"px": None, "bps": None, "atr": None, "med_1m": None, "or15": None}
    d = float(dist)
    return {
        "px": d,
        "bps": (d / float(entry) * 10000.0) if _finite(entry) and float(entry) > 0 else None,
        "atr": (d / float(atr)) if _finite(atr) and float(atr) > 0 else None,
        "med_1m": (d / float(med_1m)) if _finite(med_1m) and float(med_1m) > 0 else None,
        "or15": (d / float(med_or15)) if _finite(med_or15) and float(med_or15) > 0 else None,
    }


def map_or_vs_structure(
    *,
    sign: int,
    or_high: Any,
    or_low: Any,
    pdh: Any,
    pdl: Any,
    atr: float,
    groups: list[list[dict[str, Any]]],
) -> dict[str, Any]:
    or_px = float(or_high) if int(sign) > 0 else float(or_low)
    hw = _hw(float(atr)) if _finite(atr) and float(atr) > 0 else 0.0
    overlap = []
    for g in groups:
        z0 = g[0]
        lo = min(float(z["zone_low"]) for z in g)
        hi = max(float(z["zone_high"]) for z in g)
        if lo <= or_px <= hi:
            overlap.append({"source": z0.get("source"), "role": z0.get("role"), "zone_mid": z0.get("zone_mid")})
    pd_ref = pdh if int(sign) > 0 else pdl
    vs_pd = "UNKNOWN"
    if _finite(pd_ref) and hw > 0:
        if abs(or_px - float(pd_ref)) <= hw:
            vs_pd = "OR_AT_PD"
        elif (int(sign) > 0 and or_px < float(pd_ref) - hw) or (int(sign) < 0 and or_px > float(pd_ref) + hw):
            vs_pd = "OR_INSIDE_PD"
        else:
            vs_pd = "OR_BEYOND_PD"
    near_n = 0
    if _finite(atr) and float(atr) > 0:
        for g in groups:
            mid = float(g[0]["zone_mid"])
            if abs(or_px - mid) / float(atr) <= NEAR_STRUCT_ATR:
                near_n += 1
    return {
        "or_overlaps_prior_zone": bool(overlap),
        "or_overlap_n": len(overlap),
        "or_overlap_sources": overlap[:6],
        "or_vs_pd": vs_pd,
        "or_in_open_space": bool(not overlap and near_n == 0),
        "or_near_struct_n": near_n,
    }


def classify_structure(
    *,
    sign: int,
    entry: float,
    or_high: Any,
    or_low: Any,
    break_close: Any,
    retest_high: Any,
    retest_low: Any,
    trigger_close: Any,
    held: bool,
    atr: float,
    zones: list[dict[str, Any]],
    r_px: Any,
    med_1m: Any,
    med_or15: Any,
    or_width: Any,
) -> dict[str, Any]:
    sign = int(sign)
    groups = merge_nearby(zones, atr)
    opposing: list[dict[str, Any]] = []
    supporting: list[dict[str, Any]] = []
    for g in groups:
        roles = {str(z.get("role") or "") for z in g}
        rep = {
            "zone_low": min(float(z["zone_low"]) for z in g),
            "zone_high": max(float(z["zone_high"]) for z in g),
            "zone_mid": float(g[0]["zone_mid"]),
            "members": [_slim_zone(z) for z in g],
            "n_members": len(g),
            "has_sr": any(z.get("is_sr_detector") for z in g),
        }
        if "RESISTANCE" in roles:
            opposing.append(rep) if sign > 0 else supporting.append(rep)
        if "SUPPORT" in roles:
            supporting.append(rep) if sign > 0 else opposing.append(rep)
    atr_ok = _finite(atr) and float(atr) > 0

    def _ahead(z: dict[str, Any]) -> float:
        if sign > 0:
            return max(0.0, float(z["zone_low"]) - float(entry))
        return max(0.0, float(entry) - float(z["zone_high"]))

    def _cleared(z: dict[str, Any]) -> bool:
        if sign > 0:
            return float(entry) > float(z["zone_high"])
        return float(entry) < float(z["zone_low"])

    def _inside(z: dict[str, Any]) -> bool:
        return float(z["zone_low"]) <= float(entry) <= float(z["zone_high"])

    def _was_overhead(z: dict[str, Any]) -> bool:
        or_px = or_high if sign > 0 else or_low
        br = break_close
        if sign > 0:
            ref = min([x for x in (or_px, br) if _finite(x)], default=entry)
            return float(ref) <= float(z["zone_high"])
        ref = max([x for x in (or_px, br) if _finite(x)], default=entry)
        return float(ref) >= float(z["zone_low"])

    near_opp = []
    inside_opp = []
    cleared = []
    for z in opposing:
        d = _ahead(z)
        if _inside(z):
            inside_opp.append(z)
        if atr_ok and (d / float(atr) <= NEAR_STRUCT_ATR or _inside(z)):
            near_opp.append(z)
        if _cleared(z) and _was_overhead(z):
            cleared.append(z)

    primary_cleared = None
    if cleared:
        primary_cleared = min(cleared, key=lambda z: abs(float(entry) - float(z["zone_mid"])))

    retest_tests = False
    retest_near = False
    if primary_cleared is not None:
        if sign > 0:
            retest_tests = _range_overlap(retest_low, retest_high, primary_cleared) or (
                _finite(retest_low) and _overlap_px(float(retest_low), primary_cleared)
            )
            if _finite(retest_low) and atr_ok:
                retest_near = abs(float(retest_low) - float(primary_cleared["zone_high"])) / float(atr) <= VERY_CLOSE_ATR
        else:
            retest_tests = _range_overlap(retest_low, retest_high, primary_cleared) or (
                _finite(retest_high) and _overlap_px(float(retest_high), primary_cleared)
            )
            if _finite(retest_high) and atr_ok:
                retest_near = abs(float(retest_high) - float(primary_cleared["zone_low"])) / float(atr) <= VERY_CLOSE_ATR

    zone_held = False
    if primary_cleared is not None and _finite(trigger_close):
        if sign > 0:
            zone_held = float(trigger_close) >= float(primary_cleared["zone_low"])
        else:
            zone_held = float(trigger_close) <= float(primary_cleared["zone_high"])
    if primary_cleared is None:
        flip = "NO_CROSS"
    elif retest_tests and zone_held:
        flip = "CLEAN_FLIP"
    elif retest_tests and not zone_held:
        flip = "FAILED_FLIP"
    elif retest_near:
        flip = "PARTIAL_FLIP"
    else:
        flip = "NO_FLIP"
    _ = held

    nearest_opp = None
    nearest_d = None
    for z in opposing:
        d = _ahead(z)
        if nearest_d is None or d < nearest_d:
            nearest_d = d
            nearest_opp = z
    nearest_sup = None
    nearest_sup_d = None
    for z in supporting:
        if sign > 0:
            d = max(0.0, float(entry) - float(z["zone_high"])) if float(entry) > float(z["zone_high"]) else 0.0
            if _inside(z):
                d = 0.0
        else:
            d = max(0.0, float(z["zone_low"]) - float(entry)) if float(entry) < float(z["zone_low"]) else 0.0
            if _inside(z):
                d = 0.0
        if nearest_sup_d is None or d < nearest_sup_d:
            nearest_sup_d = d
            nearest_sup = z

    open_ok = nearest_opp is None or (atr_ok and nearest_d is not None and (nearest_d / float(atr) > OPEN_SPACE_ATR) and not inside_opp)
    into = bool(inside_opp or (nearest_opp is not None and atr_ok and nearest_d is not None and nearest_d / float(atr) <= OPEN_SPACE_ATR and not _cleared(nearest_opp)))

    if flip == "CLEAN_FLIP":
        klass = "RESISTANCE_TO_SUPPORT_FLIP"
    elif len(near_opp) >= 2:
        klass = "STRUCTURALLY_CONGESTED"
    elif primary_cleared is not None:
        klass = "BREAK_THROUGH_RESISTANCE"
    elif into:
        klass = "BREAK_INTO_RESISTANCE"
    elif open_ok:
        klass = "OPEN_SPACE_BREAK"
    else:
        klass = "BREAK_INTO_RESISTANCE"

    if nearest_opp is None:
        room = "NO_KNOWN_RESISTANCE_AHEAD"
    elif atr_ok and nearest_d is not None and nearest_d / float(atr) <= VERY_CLOSE_ATR:
        room = "RESISTANCE_VERY_CLOSE"
    else:
        room = "HAS_SPACE"

    next_u = _units(nearest_d, entry=entry, atr=atr, med_1m=med_1m, med_or15=med_or15)
    sup_u = _units(nearest_sup_d, entry=entry, atr=atr, med_1m=med_1m, med_or15=med_or15)
    structural_r = float(r_px) if _finite(r_px) and float(r_px) > 0 else None
    next_over_r = (float(nearest_d) / structural_r) if structural_r and nearest_d is not None else None
    or_map = map_or_vs_structure(
        sign=sign,
        or_high=or_high,
        or_low=or_low,
        pdh=None,
        pdl=None,
        atr=float(atr) if atr_ok else 0.0,
        groups=groups,
    )
    or_w = float(or_width) if _finite(or_width) else None
    or_vs_normal = (or_w / float(med_or15)) if or_w is not None and _finite(med_or15) and float(med_or15) > 0 else None
    return {
        "structural_class": klass,
        "flip_class": flip,
        "room_class": room,
        "n_opposing_groups": len(opposing),
        "n_supporting_groups": len(supporting),
        "n_near_opposing": len(near_opp),
        "n_inside_opposing": len(inside_opp),
        "n_cleared": len(cleared),
        "nearest_opposing": _slim_zone(nearest_opp["members"][0]) if nearest_opp and nearest_opp.get("members") else None,
        "nearest_support": _slim_zone(nearest_sup["members"][0]) if nearest_sup and nearest_sup.get("members") else None,
        "cleared_zone": nearest_opp and None,
        "primary_cleared": _slim_zone(primary_cleared["members"][0]) if primary_cleared and primary_cleared.get("members") else None,
        "retest_tests_cleared_zone": retest_tests,
        "distance_to_next_opposing": next_u,
        "distance_to_nearest_support": sup_u,
        "structural_R": structural_r,
        "next_zone_over_R": next_over_r,
        "or_width_over_normal_or15": or_vs_normal,
        "chart_zones": [_slim_zone(z) for z in zones[:24]],
        **or_map,
        "future_swing_used": False,
        "hindsight_line": False,
    }


def snapshot_and_classify(
    *,
    symbol: str,
    reactions: list[dict[str, Any]],
    atr: float,
    session_date: str,
    lookback_dates: list[str],
    hist: list[dict[str, Any]],
    open_px: Any,
    entry: float,
    sign: int,
    or_high: Any,
    or_low: Any,
    break_close: Any,
    retest_high: Any,
    retest_low: Any,
    trigger_close: Any,
    held: bool,
    pdh: Any,
    pdl: Any,
    pdc: Any,
    d5h: Any,
    d5l: Any,
    sma25: Any,
    sma75: Any,
    vwap: Any,
    r_px: Any,
    med_1m: Any,
    med_or15: Any,
    or_width: Any,
) -> dict[str, Any]:
    snap = snapshot_zones(
        symbol=symbol,
        reactions=reactions,
        atr=float(atr) if _finite(atr) else float("nan"),
        session_date=str(session_date),
        lookback_dates=list(lookback_dates),
        hist=list(hist),
    )
    sel = select_salient(snap=snap, open_px=float(entry) if _finite(entry) else (float(open_px) if _finite(open_px) else float("nan")))
    zones = collect_zones(
        snap=snap,
        session_date=str(session_date),
        hist_dates=[str(d.get("date") or "") for d in hist],
        atr=float(atr) if _finite(atr) else float("nan"),
        pdh=pdh,
        pdl=pdl,
        pdc=pdc,
        d5h=d5h,
        d5l=d5l,
        sma25=sma25,
        sma75=sma75,
        vwap=vwap,
        entry=float(entry),
    )
    mapped = classify_structure(
        sign=int(sign),
        entry=float(entry),
        or_high=or_high,
        or_low=or_low,
        break_close=break_close,
        retest_high=retest_high,
        retest_low=retest_low,
        trigger_close=trigger_close,
        held=bool(held),
        atr=float(atr) if _finite(atr) else float("nan"),
        zones=zones,
        r_px=r_px,
        med_1m=med_1m,
        med_or15=med_or15,
        or_width=or_width,
    )
    pdh_map = map_or_vs_structure(
        sign=int(sign),
        or_high=or_high,
        or_low=or_low,
        pdh=pdh,
        pdl=pdl,
        atr=float(atr) if _finite(atr) else 0.0,
        groups=merge_nearby(zones, float(atr) if _finite(atr) else 0.0),
    )
    mapped.update({k: pdh_map[k] for k in ("or_vs_pd",) if k in pdh_map})
    mapped["or_vs_pd"] = pdh_map.get("or_vs_pd")
    mapped["salient_resistance"] = bool(sel.get("resistance"))
    mapped["salient_support"] = bool(sel.get("support"))
    mapped["no_salient_resistance"] = bool(sel.get("no_salient_resistance"))
    mapped["sr_active_res_n"] = len(list(snap.get("resistance_active") or []))
    mapped["sr_active_sup_n"] = len(list(snap.get("support_active") or []))
    mapped["sr_broken_res_n"] = len(list(snap.get("resistance_broken") or []))
    mapped["sr_broken_sup_n"] = len(list(snap.get("support_broken") or []))
    return mapped
