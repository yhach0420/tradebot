"""Opposing structural target known at ENTRY. No future zone discovery."""
from __future__ import annotations

from typing import Any


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _zid(z: dict[str, Any]) -> str:
    return str(z.get("zone_id") or "")


def nearest_overhead_resistance(
    zones: list[dict[str, Any]],
    *,
    entry_px: float,
    exclude_id: str,
    session_date: str,
) -> dict[str, Any] | None:
    """LONG target: nearest boundary of next overhead resistance = zone lo."""
    cands: list[tuple[float, float, dict[str, Any]]] = []
    for z in zones:
        if _zid(z) == exclude_id:
            continue
        act = str(z.get("ZONE_ACTIVATED_AT") or "")
        if act and act > session_date:
            continue
        lo = float(z["lo"])
        if not _finite(lo) or lo <= float(entry_px):
            continue
        cands.append((lo - float(entry_px), abs(float(z.get("center") or lo) - float(entry_px)), z))
    if not cands:
        return None
    cands.sort(key=lambda t: (t[0], t[1], _zid(t[2])))
    z = cands[0][2]
    return {
        "target_zone_id": _zid(z),
        "target_price": float(z["lo"]),
        "target_lo": float(z["lo"]),
        "target_hi": float(z["hi"]),
        "target_known_at_entry": True,
        "target_activated_at": z.get("ZONE_ACTIVATED_AT"),
        "target_role": "RESISTANCE",
        "target_future_leakage": False,
    }


def nearest_underlying_support(
    zones: list[dict[str, Any]],
    *,
    entry_px: float,
    exclude_id: str,
    session_date: str,
) -> dict[str, Any] | None:
    """SHORT target: nearest boundary of next underlying support = zone hi."""
    cands: list[tuple[float, float, dict[str, Any]]] = []
    for z in zones:
        if _zid(z) == exclude_id:
            continue
        act = str(z.get("ZONE_ACTIVATED_AT") or "")
        if act and act > session_date:
            continue
        hi = float(z["hi"])
        if not _finite(hi) or hi >= float(entry_px):
            continue
        cands.append((float(entry_px) - hi, abs(float(entry_px) - float(z.get("center") or hi)), z))
    if not cands:
        return None
    cands.sort(key=lambda t: (t[0], t[1], _zid(t[2])))
    z = cands[0][2]
    return {
        "target_zone_id": _zid(z),
        "target_price": float(z["hi"]),
        "target_lo": float(z["lo"]),
        "target_hi": float(z["hi"]),
        "target_known_at_entry": True,
        "target_activated_at": z.get("ZONE_ACTIVATED_AT"),
        "target_role": "SUPPORT",
        "target_future_leakage": False,
    }


def structural_target(
    *,
    side: str,
    entry_px: float,
    entry_zone_id: str,
    session_date: str,
    resistance_active: list[dict[str, Any]],
    support_active: list[dict[str, Any]],
) -> dict[str, Any]:
    if not _finite(entry_px) or float(entry_px) <= 0:
        return {"target_price": None, "target_zone_id": None, "target_known_at_entry": False, "target_future_leakage": False}
    if side == "LONG":
        hit = nearest_overhead_resistance(
            list(resistance_active or []),
            entry_px=float(entry_px),
            exclude_id=str(entry_zone_id or ""),
            session_date=session_date,
        )
    else:
        hit = nearest_underlying_support(
            list(support_active or []),
            entry_px=float(entry_px),
            exclude_id=str(entry_zone_id or ""),
            session_date=session_date,
        )
    if hit is None:
        return {
            "target_price": None,
            "target_zone_id": None,
            "target_lo": None,
            "target_hi": None,
            "target_known_at_entry": False,
            "target_activated_at": None,
            "target_role": None,
            "target_future_leakage": False,
        }
    return hit
