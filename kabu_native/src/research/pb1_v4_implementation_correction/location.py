"""V4_LOCATION_CLASSIFIER. Families A/B/C. Frozen S/R detector is INPUT only.

Eligibility does not use V3 structural_route, 1R, N1M room, overhead ATR kill,
or classify_zone_path as an oracle.
"""
from __future__ import annotations

from typing import Any

from research.pb1_opening_range_continuation_face_valid_v2.machine import _finite
from research.pb1_v4_implementation_correction import CONFLUENCE_N1M
from research.pb1_v4_machine_implementation.s4 import dir_close_loc


def _overlap(lo: float, hi: float, a: float, b: float) -> bool:
    x0, x1 = (a, b) if a <= b else (b, a)
    return float(lo) <= x1 and float(hi) >= x0


def _zone_cleared(*, sign: int, px: Any, zone: dict[str, Any]) -> bool:
    if not _finite(px):
        return False
    if int(sign) > 0:
        return float(px) > float(zone["zone_high"])
    return float(px) < float(zone["zone_low"])


def _tests_zone(*, sign: int, retest_high: Any, retest_low: Any, zone: dict[str, Any]) -> bool:
    if not (_finite(retest_high) and _finite(retest_low)):
        return False
    return _overlap(float(zone["zone_low"]), float(zone["zone_high"]), float(retest_low), float(retest_high))


def _far_side_hold(*, sign: int, close: float, zone: dict[str, Any]) -> bool:
    if int(sign) > 0:
        return float(close) > float(zone["zone_high"])
    return float(close) < float(zone["zone_low"])


def _entered_uncleared_ahead_zone(
    *,
    sign: int,
    max_fav: Any,
    or_high: float,
    or_low: float,
    zones: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Drive into a prior-known band that was not far-side cleared. Identity, not 1R."""
    boundary = _or_boundary(sign=sign, or_high=or_high, or_low=or_low)
    hits = []
    if not _finite(max_fav):
        return hits
    for z in _prior_zones(zones, sign=sign):
        zlo, zhi = float(z["zone_low"]), float(z["zone_high"])
        if int(sign) > 0:
            ahead = zhi > float(boundary)
            entered = float(max_fav) >= zlo
        else:
            ahead = zlo < float(boundary)
            entered = float(max_fav) <= zhi
        cleared = _zone_cleared(sign=sign, px=max_fav, zone=z)
        if ahead and entered and not cleared:
            hits.append({"level": _zone_id(z), "zone": z, "ahead_of_or": True, "cleared": False, "kind": "entered_uncleared_zone"})
    return hits


def _or_boundary(*, sign: int, or_high: float, or_low: float) -> float:
    return float(or_high) if int(sign) > 0 else float(or_low)


def _five_m_hold_ok(*, sign: int, bar: dict[str, Any] | None, level: float) -> bool:
    if not bar:
        return False
    o, h, l, c = bar.get("o"), bar.get("h"), bar.get("l"), bar.get("c")
    if not (_finite(o) and _finite(h) and _finite(l) and _finite(c)):
        return False
    rng = float(h) - float(l)
    body = abs(float(c) - float(o))
    loc = dir_close_loc(sign=sign, high=float(h), low=float(l), close=float(c))
    tests = float(l) <= float(level) <= float(h)
    if int(sign) > 0:
        holds = float(c) > float(level)
    else:
        holds = float(c) < float(level)
    loc_ok = loc is not None and float(loc) >= 0.50
    # Visible 5m hold/reject. Do not reuse S4 body-expansion as a location-hold gate.
    _ = o, rng, body
    return bool(tests and holds and loc_ok)


def _level_near(*, px: Any, level: Any, n1m: Any) -> bool:
    if not (_finite(px) and _finite(level) and _finite(n1m) and float(n1m) > 0):
        return False
    return abs(float(px) - float(level)) <= float(CONFLUENCE_N1M) * float(n1m)


def _zone_id(z: dict[str, Any]) -> str:
    return str(z.get("name") or z.get("source") or z.get("kind") or z.get("role") or z.get("zone_id") or "sr_zone")


def _prior_zones(zones: list[dict[str, Any]], *, sign: int) -> list[dict[str, Any]]:
    out = []
    for z in zones or []:
        if not (_finite(z.get("zone_low")) and _finite(z.get("zone_high"))):
            continue
        role = str(z.get("role") or "")
        if int(sign) > 0 and role not in ("RESISTANCE", "SUPPORT", ""):
            continue
        if int(sign) < 0 and role not in ("SUPPORT", "RESISTANCE", ""):
            continue
        out.append(z)
    return out


def _family_a(
    *,
    sign: int,
    close: float,
    max_fav: Any,
    retest_high: Any,
    retest_low: Any,
    five_m_high: Any,
    five_m_low: Any,
    five_m_bar: dict[str, Any] | None,
    zones: list[dict[str, Any]],
    break_t: str | None,
    retest_t: str | None,
) -> dict[str, Any] | None:
    """CLEARED_ZONE_RETEST. Known before the move. Far-side clear then far-side hold."""
    best = None
    for z in _prior_zones(zones, sign=sign):
        clear_px = max_fav
        if int(sign) > 0 and _finite(five_m_high):
            clear_px = max(float(max_fav) if _finite(max_fav) else float(five_m_high), float(five_m_high))
        if int(sign) < 0 and _finite(five_m_low):
            clear_px = min(float(max_fav) if _finite(max_fav) else float(five_m_low), float(five_m_low))
        if not _zone_cleared(sign=sign, px=clear_px, zone=z):
            continue
        tested = _tests_zone(sign=sign, retest_high=retest_high, retest_low=retest_low, zone=z)
        if five_m_bar is not None:
            tested = tested or _overlap(
                float(z["zone_low"]),
                float(z["zone_high"]),
                float(five_m_bar.get("l") or retest_low or 0),
                float(five_m_bar.get("h") or retest_high or 0),
            )
        if not tested:
            continue
        hold_c = float(five_m_bar["c"]) if five_m_bar and _finite(five_m_bar.get("c")) else float(close)
        if not _far_side_hold(sign=sign, close=hold_c, zone=z):
            continue
        level = float(z["zone_high"]) if int(sign) > 0 else float(z["zone_low"])
        if five_m_bar is not None and not _five_m_hold_ok(sign=sign, bar=five_m_bar, level=level):
            # 5m close still on far side is enough if the bar tested the zone.
            if not _far_side_hold(sign=sign, close=hold_c, zone=z):
                continue
        pick = {
            "ok": True,
            "family": "CLEARED_ZONE_RETEST",
            "reason": "zone_known_cleared_far_side_hold",
            "zone_id": _zone_id(z),
            "zone_low": z.get("zone_low"),
            "zone_high": z.get("zone_high"),
            "known_at": z.get("known_at") or z.get("created_at") or "prior_session_detector",
            "clear_t": break_t,
            "retest_t": retest_t,
            "hold_t": (five_m_bar or {}).get("t1") or retest_t,
            "hold_evidence": "completed_5m_far_side_hold" if five_m_bar else "close_far_side_of_cleared_zone",
            "or_touch_alone": False,
            "defended": {
                "ok": True,
                "defended_level_type": "CLEARED_ZONE",
                "defended_zone_low": z.get("zone_low"),
                "defended_zone_high": z.get("zone_high"),
                "defense_quality": "MEANINGFUL_DEFENSE",
            },
        }
        if best is None:
            best = pick
            continue
        # Prefer the zone whose far side is closest to the retest (the one actually tested).
        mid = 0.5 * (float(z["zone_low"]) + float(z["zone_high"]))
        prev_mid = 0.5 * (float(best["zone_low"]) + float(best["zone_high"]))
        ref = float(close)
        if abs(mid - ref) < abs(prev_mid - ref):
            best = pick
    return best


def _structural_context_at_or(
    *,
    sign: int,
    or_high: float,
    or_low: float,
    max_fav: Any,
    n1m: Any,
    zones: list[dict[str, Any]],
    pdh: Any,
    pdl: Any,
    pdc: Any,
    vwap: Any,
    sma25: Any,
    sma75: Any,
) -> list[dict[str, Any]]:
    """Pre-known references occupying the OR retest area. Identity, not a 1R kill."""
    boundary = _or_boundary(sign=sign, or_high=or_high, or_low=or_low)
    hits: list[dict[str, Any]] = []
    refs = [
        ("PDH", pdh, "prior_day_high_known_before_session"),
        ("PDL", pdl, "prior_day_low_known_before_session"),
        ("PDC", pdc, "prior_day_close_known_before_session"),
        ("VWAP", vwap, "session_vwap_from_completed_minutes"),
        ("SMA25", sma25, "prior_daily_closes_only"),
        ("SMA75", sma75, "prior_daily_closes_only"),
    ]
    for name, lvl, why in refs:
        if _level_near(px=boundary, level=lvl, n1m=n1m):
            ahead = (int(sign) > 0 and float(lvl) > float(boundary)) or (int(sign) < 0 and float(lvl) < float(boundary))
            cleared = False
            if _finite(max_fav) and _finite(lvl):
                if int(sign) > 0:
                    cleared = float(max_fav) > float(lvl)
                else:
                    cleared = float(max_fav) < float(lvl)
            hits.append(
                {
                    "level": name,
                    "px": lvl,
                    "why_known": why,
                    "ahead_of_or": ahead,
                    "cleared": cleared,
                    "kind": "reference",
                }
            )
    for z in _prior_zones(zones, sign=sign):
        if _overlap(float(z["zone_low"]), float(z["zone_high"]), float(boundary), float(boundary)):
            cleared = _zone_cleared(sign=sign, px=max_fav, zone=z)
            ahead = not cleared
            hits.append(
                {
                    "level": _zone_id(z),
                    "px": z.get("zone_mid") or 0.5 * (float(z["zone_low"]) + float(z["zone_high"])),
                    "why_known": "frozen_causal_sr_zone",
                    "ahead_of_or": ahead,
                    "cleared": cleared,
                    "kind": "zone",
                    "zone": z,
                }
            )
        elif _level_near(px=boundary, level=z.get("zone_mid") or z.get("zone_high"), n1m=n1m) or _level_near(
            px=boundary, level=z.get("zone_low"), n1m=n1m
        ):
            cleared = _zone_cleared(sign=sign, px=max_fav, zone=z)
            hits.append(
                {
                    "level": _zone_id(z),
                    "px": z.get("zone_mid"),
                    "why_known": "frozen_causal_sr_zone",
                    "ahead_of_or": not cleared,
                    "cleared": cleared,
                    "kind": "zone",
                    "zone": z,
                }
            )
    return hits


def _family_c(
    *,
    sign: int,
    close: float,
    five_m_bar: dict[str, Any] | None,
    or_high: float,
    or_low: float,
    context: list[dict[str, Any]],
    retest_t: str | None,
) -> dict[str, Any] | None:
    """One identifiable causal relationship held on the defended side. Not 2-of-7. Not uncleared overhead."""
    boundary = _or_boundary(sign=sign, or_high=or_high, or_low=or_low)
    if not _five_m_hold_ok(sign=sign, bar=five_m_bar, level=boundary):
        return None
    for hit in context:
        if hit.get("ahead_of_or") and not hit.get("cleared"):
            continue
        lvl = hit.get("px")
        if not _finite(lvl):
            continue
        # Defended-side confluence: extra level at/behind the OR hold, not remaining overhead.
        if int(sign) > 0 and float(lvl) > float(boundary):
            continue
        if int(sign) < 0 and float(lvl) < float(boundary):
            continue
        return {
            "ok": True,
            "family": "VISIBLE_CONFLUENT_LOCATION",
            "reason": "identified_causal_level_plus_reaction",
            "confluent_level": {
                "level": hit.get("level"),
                "why_known": hit.get("why_known"),
                "level_px": lvl,
            },
            "reaction": "completed_5m_hold_on_continuation_side",
            "retest_t": retest_t,
            "hold_t": (five_m_bar or {}).get("t1") or retest_t,
            "or_touch_alone": False,
            "two_of_seven_rule": False,
            "defended": {
                "ok": True,
                "defended_level_type": "OR_PLUS_" + str(hit.get("level") or "LEVEL"),
                "defended_zone_low": min(float(boundary), float(lvl)),
                "defended_zone_high": max(float(boundary), float(lvl)),
                "defense_quality": "MEANINGFUL_DEFENSE",
            },
        }
    return None


def _family_b(
    *,
    sign: int,
    five_m_left: bool,
    five_m_bar: dict[str, Any] | None,
    or_high: float,
    or_low: float,
    context: list[dict[str, Any]],
    retest_t: str | None,
    break_t: str | None,
) -> dict[str, Any] | None:
    """OR held after real 5m leave. Not mere 1m geometric hold. Not if structural context occupies the OR."""
    uncleared = [h for h in context if h.get("ahead_of_or") and not h.get("cleared")]
    if uncleared:
        return None
    overlapping = [h for h in context if h.get("kind") != "entered_uncleared_zone" and (not h.get("ahead_of_or") or h.get("cleared"))]
    if overlapping:
        # OR retest area is not pure OR; caller should have tried A then C.
        return None
    if not five_m_left:
        return None
    boundary = _or_boundary(sign=sign, or_high=or_high, or_low=or_low)
    if not _five_m_hold_ok(sign=sign, bar=five_m_bar, level=boundary):
        return None
    return {
        "ok": True,
        "family": "OR_HELD_AFTER_REAL_DRIVE",
        "reason": "completed_5m_leave_then_or_reject_hold",
        "break_t": break_t,
        "retest_t": retest_t,
        "hold_t": (five_m_bar or {}).get("t1") or retest_t,
        "hold_evidence": "completed_5m_or_reject_hold",
        "or_touch_alone": False,
        "defended": {
            "ok": True,
            "defended_level_type": "OR_BOUNDARY",
            "defended_zone_low": boundary,
            "defended_zone_high": boundary,
            "defense_quality": "MEANINGFUL_DEFENSE",
        },
    }


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
    five_m_left: bool,
    five_m_bar: dict[str, Any] | None,
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
    _ = break_close, atr
    five_m_high = five_m_bar.get("h") if five_m_bar else None
    five_m_low = five_m_bar.get("l") if five_m_bar else None
    context = _structural_context_at_or(
        sign=int(sign),
        or_high=float(or_high),
        or_low=float(or_low),
        max_fav=max_fav,
        n1m=n1m,
        zones=list(zones or []),
        pdh=pdh,
        pdl=pdl,
        pdc=pdc,
        vwap=vwap,
        sma25=sma25,
        sma75=sma75,
    )
    context.extend(
        _entered_uncleared_ahead_zone(
            sign=int(sign),
            max_fav=max_fav,
            or_high=float(or_high),
            or_low=float(or_low),
            zones=list(zones or []),
        )
    )
    a = _family_a(
        sign=int(sign),
        close=float(close),
        max_fav=max_fav,
        retest_high=retest_high,
        retest_low=retest_low,
        five_m_high=five_m_high,
        five_m_low=five_m_low,
        five_m_bar=five_m_bar,
        zones=list(zones or []),
        break_t=break_t,
        retest_t=retest_t,
    )
    if a is not None:
        a["structural_context"] = context
        a["location_id"] = f"A|{a.get('zone_id')}|{a.get('retest_t')}"
        return a

    c = _family_c(
        sign=int(sign),
        close=float(close),
        five_m_bar=five_m_bar,
        or_high=float(or_high),
        or_low=float(or_low),
        context=context,
        retest_t=retest_t,
    )
    if c is not None:
        c["structural_context"] = context
        c["location_id"] = f"C|{(c.get('confluent_level') or {}).get('level')}|{c.get('retest_t')}"
        return c

    b = _family_b(
        sign=int(sign),
        five_m_left=bool(five_m_left),
        five_m_bar=five_m_bar,
        or_high=float(or_high),
        or_low=float(or_low),
        context=context,
        retest_t=retest_t,
        break_t=break_t,
    )
    if b is not None:
        b["structural_context"] = context
        b["location_id"] = f"B|OR|{b.get('retest_t')}"
        return b

    uncleared = [h for h in context if h.get("ahead_of_or") and not h.get("cleared")]
    reason = "NO_DEFENSIBLE_LOCATION"
    if not five_m_left:
        reason = "NO_COMPLETED_5M_LEAVE"
    elif five_m_bar is None:
        reason = "NO_COMPLETED_5M_HOLD"
    elif uncleared:
        reason = "OR_RETEST_NOT_PURE_AND_NO_A_OR_C"
    elif not _five_m_hold_ok(sign=int(sign), bar=five_m_bar, level=_or_boundary(sign=sign, or_high=or_high, or_low=or_low)):
        reason = "OR_TOUCH_ONLY"
    return {
        "ok": False,
        "family": None,
        "reason": reason,
        "or_touch_alone": reason == "OR_TOUCH_ONLY",
        "structural_context": context,
        "location_id": None,
    }
