"""Opposing-zone eligibility at A2 decision-bar close. Never next-bar open."""
from __future__ import annotations

from typing import Any

from research.support_resistance_mechanism_to_complete_strategy_v1.targets import structural_target


ELIGIBILITY_PRICE_KIND = "decision_bar_close"


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def opposing_at_decision(
    *,
    side: str,
    decision_px: float,
    entry_zone_id: str,
    session_date: str,
    resistance_active: list[dict[str, Any]],
    support_active: list[dict[str, Any]],
) -> dict[str, Any]:
    """Resolve opposing S/R from causal decision price only. No entry open. No future bar."""
    tgt = structural_target(
        side=side,
        entry_px=float(decision_px) if _finite(decision_px) else float("nan"),
        entry_zone_id=str(entry_zone_id or ""),
        session_date=str(session_date or ""),
        resistance_active=list(resistance_active or []),
        support_active=list(support_active or []),
    )
    available = tgt.get("target_zone_id") is not None and tgt.get("target_price") is not None
    dist = None
    if available and _finite(decision_px) and float(decision_px) > 0 and _finite(tgt.get("target_price")):
        raw = float(tgt["target_price"]) - float(decision_px)
        if side != "LONG":
            raw = -raw
        dist = raw / float(decision_px) * 10_000.0
    leak = False
    act = tgt.get("target_activated_at")
    if act and str(act) > str(session_date or ""):
        leak = True
        available = False
    return {
        "opposing_zone_available": bool(available) and not leak,
        "eligibility_price_kind": ELIGIBILITY_PRICE_KIND,
        "eligibility_px": float(decision_px) if _finite(decision_px) else None,
        "used_entry_open": False,
        "used_future_bar": False,
        "target_distance_bps": dist,
        "n_active_resistance": len(list(resistance_active or [])),
        "n_active_support": len(list(support_active or [])),
        "n_active_sr": len(list(resistance_active or [])) + len(list(support_active or [])),
        "target_future_leakage": bool(leak),
        **tgt,
    }


def audit_vs_entry_open(decision_pack: dict[str, Any], entry_pack: dict[str, Any]) -> dict[str, Any]:
    """Diagnostic only. Entry-open must not control admission."""
    a = bool(decision_pack.get("opposing_zone_available"))
    b = bool(entry_pack.get("opposing_zone_available"))
    return {
        "decision_available": a,
        "entry_open_available": b,
        "disagrees_with_entry_open": a != b,
        "decision_target_zone_id": decision_pack.get("target_zone_id"),
        "entry_open_target_zone_id": entry_pack.get("target_zone_id"),
    }
