"""Pre-entry structural route, defense, exhaustion. Frozen 1R. No PnL search."""
from __future__ import annotations

from typing import Any

from research.pb1_playbook_redesign_v3 import STRUCTURAL_ROUTE_R_MULT
from research.pb1_structure_and_symbol_context_rca_v1.structure import (
    _finite,
    _hw,
    _slim_zone,
    collect_zones,
    merge_nearby,
)
from research.support_resistance_face_valid_first_interaction_rebuild_v1.select import select_salient
from research.support_resistance_face_valid_first_interaction_rebuild_v1.zones import snapshot_zones


def planned_r(*, sign: int, trigger_close: Any, retest_high: Any, retest_low: Any) -> float:
    if not _finite(trigger_close):
        return float("nan")
    if int(sign) > 0:
        if not _finite(retest_low):
            return float("nan")
        return float(trigger_close) - float(retest_low)
    if not _finite(retest_high):
        return float("nan")
    return float(retest_high) - float(trigger_close)


def route_end(*, sign: int, trigger_close: float, r_px: float) -> float:
    if int(sign) > 0:
        return float(trigger_close) + float(STRUCTURAL_ROUTE_R_MULT) * float(r_px)
    return float(trigger_close) - float(STRUCTURAL_ROUTE_R_MULT) * float(r_px)


def _overlap(lo: float, hi: float, a: float, b: float) -> bool:
    x0, x1 = (a, b) if a <= b else (b, a)
    return lo <= x1 and hi >= x0


def is_opposing(z: dict[str, Any], *, sign: int, px: float) -> bool:
    role = str(z.get("role") or "")
    if int(sign) > 0:
        if role != "RESISTANCE":
            return False
        return float(z["zone_high"]) >= float(px)
    if role != "SUPPORT":
        return False
    return float(z["zone_low"]) <= float(px)


def is_strictly_ahead(z: dict[str, Any], *, sign: int, px: float) -> bool:
    """Next objective beyond current price. Overlapping the print is not 'ahead'."""
    role = str(z.get("role") or "")
    if int(sign) > 0:
        return role == "RESISTANCE" and float(z["zone_low"]) > float(px)
    return role == "SUPPORT" and float(z["zone_high"]) < float(px)


def nearest_opposing(*, sign: int, px: float, zones: list[dict[str, Any]], strictly_ahead: bool = False) -> dict[str, Any] | None:
    cands = []
    for z in zones:
        if strictly_ahead:
            if not is_strictly_ahead(z, sign=sign, px=px):
                continue
        elif not is_opposing(z, sign=sign, px=px):
            continue
        if int(sign) > 0:
            dist = max(0.0, float(z["zone_low"]) - float(px))
        else:
            dist = max(0.0, float(px) - float(z["zone_high"]))
        cands.append((dist, abs(float(z["zone_mid"]) - float(px)), z))
    if not cands:
        return None
    cands.sort(key=lambda t: (t[0], t[1]))
    return cands[0][2]


def zone_entered(*, sign: int, high: float, low: float, zone: dict[str, Any]) -> bool:
    return _overlap(float(zone["zone_low"]), float(zone["zone_high"]), float(low), float(high))


def zone_cleared(*, sign: int, px: float, zone: dict[str, Any]) -> bool:
    if int(sign) > 0:
        return float(px) > float(zone["zone_high"])
    return float(px) < float(zone["zone_low"])


def retest_tests_zone(*, sign: int, retest_high: Any, retest_low: Any, zone: dict[str, Any]) -> bool:
    if not (_finite(retest_high) and _finite(retest_low)):
        return False
    return _overlap(float(zone["zone_low"]), float(zone["zone_high"]), float(retest_low), float(retest_high))


def units(dist: Any, *, px: float, atr: Any, med_1m: Any, med_or15: Any) -> dict[str, Any]:
    if not _finite(dist):
        return {"px": None, "bps": None, "atr": None, "med_1m": None, "or15": None}
    d = float(dist)
    return {
        "px": d,
        "bps": (d / float(px) * 10000.0) if _finite(px) and float(px) > 0 else None,
        "atr": (d / float(atr)) if _finite(atr) and float(atr) > 0 else None,
        "med_1m": (d / float(med_1m)) if _finite(med_1m) and float(med_1m) > 0 else None,
        "or15": (d / float(med_or15)) if _finite(med_or15) and float(med_or15) > 0 else None,
    }


def opposing_in_route(*, sign: int, start: float, end: float, zones: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hit = []
    for z in zones:
        if not is_opposing(z, sign=sign, px=start):
            continue
        if _overlap(float(z["zone_low"]), float(z["zone_high"]), float(start), float(end)):
            hit.append(z)
    return hit


def structural_route(*, sign: int, trigger_close: float, r_px: float, zones: list[dict[str, Any]]) -> dict[str, Any]:
    if not (_finite(r_px) and float(r_px) > 0):
        return {
            "status": "RETEST_EXTREME_INVALID",
            "planned_R": r_px,
            "blocking_n": 0,
            "blocking": [],
            "one_r_searched": False,
            "route_r_mult": STRUCTURAL_ROUTE_R_MULT,
        }
    end = route_end(sign=sign, trigger_close=float(trigger_close), r_px=float(r_px))
    hit = opposing_in_route(sign=sign, start=float(trigger_close), end=end, zones=zones)
    status = "STRUCTURALLY_BLOCKED" if hit else "STRUCTURAL_ROUTE_CLEAR"
    return {
        "status": status,
        "planned_R": float(r_px),
        "route_start": float(trigger_close),
        "route_end": float(end),
        "blocking_n": len(hit),
        "blocking": [_slim_zone(z) for z in hit[:6]],
        "one_r_searched": False,
        "route_r_mult": STRUCTURAL_ROUTE_R_MULT,
        "why_1R": "unobstructed structural room equal to the amount structurally risked; not a return grid",
    }


def classify_zone_path(
    *,
    sign: int,
    or_high: Any,
    or_low: Any,
    break_close: Any,
    max_fav: Any,
    retest_high: Any,
    retest_low: Any,
    zones: list[dict[str, Any]],
) -> dict[str, Any]:
    """Did the opening move involve a prior opposing zone, and was it defended?"""
    or_ref = float(or_high) if int(sign) > 0 else float(or_low)
    involved = []
    for z in zones:
        if int(sign) > 0 and str(z.get("role")) != "RESISTANCE":
            continue
        if int(sign) < 0 and str(z.get("role")) != "SUPPORT":
            continue
        if int(sign) > 0:
            was_overhead_at_or = float(z["zone_high"]) >= or_ref
        else:
            was_overhead_at_or = float(z["zone_low"]) <= or_ref
        if not was_overhead_at_or:
            continue
        cleared = _finite(max_fav) and zone_cleared(sign=sign, px=float(max_fav), zone=z)
        tested = retest_tests_zone(sign=sign, retest_high=retest_high, retest_low=retest_low, zone=z)
        if cleared or tested:
            involved.append({"zone": z, "cleared": cleared, "tested": tested})
    if not involved:
        return {"zone_class": "ZONE_NOT_INVOLVED", "cleared_zone": None, "tested": False}
    cleared = [x for x in involved if x["cleared"]]
    if not cleared:
        return {"zone_class": "ZONE_NOT_CLEARED", "cleared_zone": None, "tested": False}
    pick = min(cleared, key=lambda x: abs(float(x["zone"]["zone_mid"]) - or_ref))
    if pick["tested"]:
        return {"zone_class": "ZONE_CLEARED_AND_DEFENDED", "cleared_zone": pick["zone"], "tested": True}
    return {"zone_class": "ZONE_CLEARED_BUT_NOT_DEFENDED", "cleared_zone": pick["zone"], "tested": False}


def defended_location(
    *,
    sign: int,
    or_high: float,
    or_low: float,
    retest_high: Any,
    retest_low: Any,
    zone_path: dict[str, Any],
) -> dict[str, Any]:
    zc = str(zone_path.get("zone_class") or "")
    if zc == "ZONE_CLEARED_BUT_NOT_DEFENDED":
        z = zone_path.get("cleared_zone") or {}
        return {
            "ok": False,
            "defended_level_type": None,
            "defended_zone_low": z.get("zone_low"),
            "defended_zone_high": z.get("zone_high"),
            "retest_extreme": float(retest_low) if int(sign) > 0 else float(retest_high),
            "reason": "ZONE_CLEARED_BUT_NOT_DEFENDED",
        }
    if zc == "ZONE_CLEARED_AND_DEFENDED":
        z = zone_path.get("cleared_zone") or {}
        return {
            "ok": True,
            "defended_level_type": "CLEARED_ZONE",
            "defended_zone_low": z.get("zone_low"),
            "defended_zone_high": z.get("zone_high"),
            "retest_extreme": float(retest_low) if int(sign) > 0 else float(retest_high),
            "reason": "ZONE_CLEARED_AND_DEFENDED",
        }
    # ZONE_NOT_INVOLVED or ZONE_NOT_CLEARED: OR boundary is the coherent level.
    boundary = float(or_high) if int(sign) > 0 else float(or_low)
    hw = abs(float(or_high) - float(or_low)) * 0.0
    tests_or = False
    if _finite(retest_high) and _finite(retest_low):
        if int(sign) > 0:
            tests_or = float(retest_low) <= boundary <= float(retest_high)
        else:
            tests_or = float(retest_low) <= boundary <= float(retest_high)
    if not tests_or:
        return {
            "ok": False,
            "defended_level_type": None,
            "defended_zone_low": None,
            "defended_zone_high": None,
            "retest_extreme": float(retest_low) if int(sign) > 0 and _finite(retest_low) else (float(retest_high) if _finite(retest_high) else None),
            "reason": "NO_DEFENDED_LOCATION",
        }
    return {
        "ok": True,
        "defended_level_type": "OR_BOUNDARY",
        "defended_zone_low": boundary,
        "defended_zone_high": boundary,
        "retest_extreme": float(retest_low) if int(sign) > 0 else float(retest_high),
        "reason": "OR_BOUNDARY",
        "or_half_unused": hw,
    }


def build_day_zones(
    *,
    symbol: str,
    reactions: list[dict[str, Any]],
    atr: float,
    session_date: str,
    hist: list[dict[str, Any]],
    pdh: Any,
    pdl: Any,
    pdc: Any,
    d5h: Any,
    d5l: Any,
    sma25: Any,
    sma75: Any,
    vwap: Any,
    ref_px: float,
) -> list[dict[str, Any]]:
    lookback = [str(d.get("date") or "") for d in hist]
    snap = snapshot_zones(
        symbol=symbol,
        reactions=reactions,
        atr=float(atr) if _finite(atr) else float("nan"),
        session_date=str(session_date),
        lookback_dates=lookback,
        hist=list(hist),
    )
    _ = select_salient(snap=snap, open_px=float(ref_px) if _finite(ref_px) else float("nan"))
    return collect_zones(
        snap=snap,
        session_date=str(session_date),
        hist_dates=lookback,
        atr=float(atr) if _finite(atr) else float("nan"),
        pdh=pdh,
        pdl=pdl,
        pdc=pdc,
        d5h=d5h,
        d5l=d5l,
        sma25=sma25,
        sma75=sma75,
        vwap=vwap,
        entry=float(ref_px),
    )


def merge_unique(zones: list[dict[str, Any]], atr: float) -> list[dict[str, Any]]:
    groups = merge_nearby(zones, atr)
    out = []
    for g in groups:
        roles = {str(z.get("role") or "") for z in g}
        rep = dict(g[0])
        rep["zone_low"] = min(float(z["zone_low"]) for z in g)
        rep["zone_high"] = max(float(z["zone_high"]) for z in g)
        if "RESISTANCE" in roles and "SUPPORT" not in roles:
            rep["role"] = "RESISTANCE"
        elif "SUPPORT" in roles and "RESISTANCE" not in roles:
            rep["role"] = "SUPPORT"
        out.append(rep)
        if "RESISTANCE" in roles and "SUPPORT" in roles:
            extra = dict(rep)
            extra["role"] = "SUPPORT" if rep["role"] == "RESISTANCE" else "RESISTANCE"
            out.append(extra)
    return out


_ = _hw
