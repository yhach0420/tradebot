"""NON_ELIGIBILITY_DIAGNOSTIC persistence. Never decides S2/S3/S4."""
from __future__ import annotations

from typing import Any

from research.pb1_opening_range_continuation_face_valid_v2.machine import _finite
from research.pb1_playbook_redesign_v3.structure_route import nearest_opposing, planned_r, structural_route


def persist_route_diagnostics(
    *,
    sign: int,
    close: float,
    retest_high: Any,
    retest_low: Any,
    n1m: Any,
    zones: list[dict[str, Any]],
) -> dict[str, Any]:
    r_px = planned_r(sign=int(sign), trigger_close=close, retest_high=retest_high, retest_low=retest_low)
    route = structural_route(sign=int(sign), trigger_close=float(close), r_px=float(r_px) if _finite(r_px) else float("nan"), zones=list(zones or []))
    opp = nearest_opposing(sign=int(sign), px=float(close), zones=list(zones or []), strictly_ahead=True)
    room_n1m = (float(r_px) / float(n1m)) if _finite(r_px) and _finite(n1m) and float(n1m) > 0 else None
    return {
        "NON_ELIGIBILITY_DIAGNOSTIC": True,
        "planned_R": r_px,
        "planned_R_over_N1M": room_n1m,
        "structural_route_status": route.get("status"),
        "nearest_opposing": None if opp is None else {"zone_low": opp.get("zone_low"), "zone_high": opp.get("zone_high"), "role": opp.get("role")},
        "eligibility_used": False,
    }
