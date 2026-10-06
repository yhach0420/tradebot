"""LOCATION_IDENTIFIED vs LOCATION_INTERACTION. Family B identity does not require a hold."""
from __future__ import annotations

from typing import Any

from research.pb1_opening_range_continuation_face_valid_v2.machine import _finite
from research.pb1_v4_clarified_machine_implementation import CONFLUENCE_N1M


def _overlap(lo: float, hi: float, a: float, b: float) -> bool:
    x0, x1 = (a, b) if a <= b else (b, a)
    return float(lo) <= x1 and float(hi) >= x0


def or_boundary(*, sign: int, or_high: float, or_low: float) -> float:
    return float(or_high) if int(sign) > 0 else float(or_low)


def five_m_left(*, sign: int, bar: dict[str, Any], or_high: float, or_low: float) -> bool:
    c, h, l = bar.get("c"), bar.get("h"), bar.get("l")
    if not (_finite(c) and _finite(h) and _finite(l)):
        return False
    if int(sign) > 0:
        return float(c) > float(or_high) or float(l) > float(or_high)
    return float(c) < float(or_low) or float(h) < float(or_low)


def _zone_cleared(*, sign: int, px: Any, zone: dict[str, Any]) -> bool:
    if not _finite(px):
        return False
    if int(sign) > 0:
        return float(px) > float(zone["zone_high"])
    return float(px) < float(zone["zone_low"])


def _zone_id(z: dict[str, Any]) -> str:
    return str(z.get("name") or z.get("source") or z.get("kind") or z.get("role") or z.get("zone_id") or "sr_zone")


def _prior_zones(zones: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for z in zones or []:
        if _finite(z.get("zone_low")) and _finite(z.get("zone_high")):
            out.append(z)
    return out


def _level_near(*, px: Any, level: Any, n1m: Any) -> bool:
    if not (_finite(px) and _finite(level) and _finite(n1m) and float(n1m) > 0):
        return False
    return abs(float(px) - float(level)) <= float(CONFLUENCE_N1M) * float(n1m)


def identify_location(
    *,
    sign: int,
    active: bool,
    bar: dict[str, Any],
    or_high: float,
    or_low: float,
    zones: list[dict[str, Any]],
    pdh: Any,
    pdl: Any,
    pdc: Any,
    vwap: Any,
    n1m: Any,
    left: bool,
) -> dict[str, Any] | None:
    """Thesis location identity. Hold is not required. 1m cannot call this."""
    if not active:
        return None
    h, l, c = bar.get("h"), bar.get("l"), bar.get("c")
    extreme = float(h) if int(sign) > 0 and _finite(h) else (float(l) if _finite(l) else None)
    # Family A: prior zone already far-side cleared. Identity exists at the cleared zone.
    for z in _prior_zones(zones):
        if _zone_cleared(sign=sign, px=extreme, zone=z):
            level = float(z["zone_high"]) if int(sign) > 0 else float(z["zone_low"])
            return {
                "family": "A_CLEARED_ZONE",
                "reason": "prior_causal_zone_cleared_far_side",
                "level": level,
                "level_kind": _zone_id(z),
                "hold_required_to_identify": False,
                "created_by_1m": False,
            }
    # Family B: real leave/displacement from OR → OR boundary is LOCATION_CANDIDATE.
    if left or five_m_left(sign=sign, bar=bar, or_high=or_high, or_low=or_low):
        level = or_boundary(sign=sign, or_high=or_high, or_low=or_low)
        kind = "OR_HIGH" if int(sign) > 0 else "OR_LOW"
        confluence = None
        refs = [("PDH", pdh), ("PDL", pdl), ("PDC", pdc), ("VWAP", vwap)]
        for name, px in refs:
            if _level_near(px=level, level=px, n1m=n1m):
                confluence = name
                break
        family = "C_CONFLUENT" if confluence else "B_OR_AFTER_LEAVE"
        return {
            "family": family,
            "reason": "or_boundary_after_real_leave" if family.startswith("B") else f"or_plus_{confluence}",
            "level": float(level),
            "level_kind": kind if family.startswith("B") else f"OR+{confluence}",
            "hold_required_to_identify": False,
            "created_by_1m": False,
            "or_touch_alone_execution_valid": False,
        }
    _ = c
    return None


def classify_interaction(
    *,
    sign: int,
    high: float,
    low: float,
    close: float,
    level: float,
    prev: str | None,
) -> dict[str, Any]:
    tests = float(low) <= float(level) <= float(high)
    if int(sign) > 0:
        holds = float(close) > float(level)
        accepted_fail = float(close) < float(level) and float(high) < float(level)
    else:
        holds = float(close) < float(level)
        accepted_fail = float(close) > float(level) and float(low) > float(level)
    if not tests:
        if prev in (None, "NO_INTERACTION_YET"):
            return {"state": "NO_INTERACTION_YET", "kills_thesis": False, "cancel_attempt": False}
        return {"state": str(prev), "kills_thesis": False, "cancel_attempt": False}
    if accepted_fail:
        return {"state": "ACCEPTED_FAILURE", "kills_thesis": True, "cancel_attempt": True}
    if holds:
        return {"state": "HOLD" if int(sign) > 0 else "REJECTION", "kills_thesis": False, "cancel_attempt": False, "validates_execution": True}
    if prev in ("HOLD", "REJECTION", "TESTING", "TOUCH"):
        return {"state": "TEMPORARY_PENETRATION", "kills_thesis": False, "cancel_attempt": True}
    return {"state": "TOUCH", "kills_thesis": False, "cancel_attempt": False}
