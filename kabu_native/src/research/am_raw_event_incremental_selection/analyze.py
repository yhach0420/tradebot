"""Raw incremental vs frozen bases. CASE A-E. No descriptor search."""
from __future__ import annotations

from typing import Any

from research.am_entry_profit_improvement.metrics import _pf_num
from research.am_entry_temporal_regime_information.analyze import (
    arm_sort_key,
    net_pf_dd_beat,
    pick_best,
)
from research.am_raw_event_incremental_selection import B0, B1, R0, R1
from research.canonical_entry_performance_rebase.analyze import _f


def _d(a: Any, b: Any) -> Any:
    try:
        return float(a) - float(b)
    except (TypeError, ValueError):
        return None


def pos_minus_neg(arm: dict[str, Any]) -> int:
    return int(arm.get("PAIRED_POS_DAYS") or 0) - int(arm.get("PAIRED_NEG_DAYS") or 0)


def increment_raw_vs_base(raw: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    return {
        "DELTA_NET_RAW_VS_BASE": _d(raw.get("OVERLAY_NET_PNL"), base.get("OVERLAY_NET_PNL")),
        "DELTA_PF_RAW_VS_BASE": _d(_pf_num(raw.get("OVERLAY_PF")), _pf_num(base.get("OVERLAY_PF"))),
        "DELTA_DD_RAW_VS_BASE": _d(raw.get("OVERLAY_MAX_DD"), base.get("OVERLAY_MAX_DD")),
        "DELTA_PAIRED_MEDIAN_RAW_VS_BASE": _d(
            raw.get("PAIRED_MEDIAN_DAILY_DELTA"), base.get("PAIRED_MEDIAN_DAILY_DELTA")
        ),
        "DELTA_POS_MINUS_NEG_RAW_VS_BASE": float(pos_minus_neg(raw) - pos_minus_neg(base)),
        "DELTA_EX_BEST_RAW_VS_BASE": _d(raw.get("EX_BEST_DAY_PNL_DELTA"), base.get("EX_BEST_DAY_PNL_DELTA")),
        "DELTA_EX_TOP3_RAW_VS_BASE": _d(raw.get("EX_TOP3_DAYS_PNL_DELTA"), base.get("EX_TOP3_DAYS_PNL_DELTA")),
    }


def _pos(v: Any) -> bool:
    try:
        return v is not None and float(v) > 0.0
    except (TypeError, ValueError):
        return False


def _nonworse(v: Any) -> bool:
    try:
        return v is not None and float(v) >= 0.0
    except (TypeError, ValueError):
        return False


def _dd_nonworse(raw: dict[str, Any], base: dict[str, Any]) -> bool:
    try:
        return abs(float(raw.get("OVERLAY_MAX_DD") or 0.0)) <= abs(float(base.get("OVERLAY_MAX_DD") or 0.0))
    except (TypeError, ValueError):
        return False


def pair_incremental_supported(raw: dict[str, Any], base: dict[str, Any]) -> bool:
    inc = increment_raw_vs_base(raw, base)
    econ = (
        _pos(inc.get("DELTA_NET_RAW_VS_BASE"))
        and _pos(inc.get("DELTA_PF_RAW_VS_BASE"))
        and _pos(inc.get("DELTA_PAIRED_MEDIAN_RAW_VS_BASE"))
        and _pos(inc.get("DELTA_POS_MINUS_NEG_RAW_VS_BASE"))
    )
    if not econ:
        return False
    tail_ok = 0
    if _dd_nonworse(raw, base):
        tail_ok += 1
    if _nonworse(inc.get("DELTA_EX_BEST_RAW_VS_BASE")):
        tail_ok += 1
    if _nonworse(inc.get("DELTA_EX_TOP3_RAW_VS_BASE")):
        tail_ok += 1
    return tail_ok >= 2


def pair_degrades(raw: dict[str, Any], base: dict[str, Any]) -> bool:
    inc = increment_raw_vs_base(raw, base)
    net = inc.get("DELTA_NET_RAW_VS_BASE")
    pf = inc.get("DELTA_PF_RAW_VS_BASE")
    try:
        return float(net) < 0.0 and float(pf) < 0.0
    except (TypeError, ValueError):
        return False


def decide_case(
    *,
    integrity_ok: bool,
    base_parity: bool,
    arms: list[dict[str, Any]],
    current_net: float,
    current_pf: float,
    current_dd: float,
    raw_incremental_supported: bool,
) -> dict[str, Any]:
    if not integrity_ok or not base_parity:
        return {
            "CASE": "E",
            "VERDICT": "AM_RAW_EVENT_INCREMENTAL_REASSESSMENT_INTEGRITY_FAILED",
            "NEXT": "STOP",
        }
    by = {str(a.get("architecture_id")): a for a in arms}
    r0 = by.get(R0) or {}
    r1 = by.get(R1) or {}
    b0 = by.get(B0) or {}
    b1 = by.get(B1) or {}
    any_pass = any(bool(a.get("ARM_PASS")) for a in (r0, r1) if a)
    if any_pass:
        return {
            "CASE": "A",
            "VERDICT": "AM_RAW_EVENT_INCREMENTAL_AUGMENT_SUPPORTED",
            "NEXT": "AM_RAW_EVENT_X14_AUGMENT_FREEZE_REVIEW",
        }
    r_arms = [a for a in (r0, r1) if a]
    any_beat_current = any(net_pf_dd_beat(a, current_net, current_pf, current_dd) for a in r_arms)
    if raw_incremental_supported and any_beat_current:
        return {
            "CASE": "B",
            "VERDICT": "AM_RAW_EVENT_EDGE_PARTIAL_NOT_FULLY_ROBUST",
            "NEXT": "AM_EVENT_SEQUENCE_ARCHITECTURE_REASSESSMENT",
        }
    if raw_incremental_supported:
        return {
            "CASE": "B",
            "VERDICT": "AM_RAW_EVENT_EDGE_PARTIAL_NOT_FULLY_ROBUST",
            "NEXT": "AM_EVENT_SEQUENCE_ARCHITECTURE_REASSESSMENT",
        }
    if pair_degrades(r0, b0) and pair_degrades(r1, b1):
        return {
            "CASE": "D",
            "VERDICT": "AM_RAW_EVENT_DESCRIPTOR_INTEGRATION_DEGRADES_EDGE",
            "NEXT": "AM_EVENT_SEQUENCE_ARCHITECTURE_REASSESSMENT",
        }
    return {
        "CASE": "C",
        "VERDICT": "AM_RAW_EVENT_DESCRIPTORS_NOT_INCREMENTAL_TO_X14_REGIME",
        "NEXT": "AM_EVENT_SEQUENCE_ARCHITECTURE_REASSESSMENT",
    }


def expected_match(arm: dict[str, Any], expected: dict[str, Any], *, abs_tol: float) -> bool:
    if int(arm.get("PAIRED_POS_DAYS") or -1) != int(expected["PAIRED_POS_DAYS"]):
        return False
    if int(arm.get("PAIRED_NEG_DAYS") or -1) != int(expected["PAIRED_NEG_DAYS"]):
        return False
    checks = (
        ("OVERLAY_NET_PNL", abs_tol),
        ("OVERLAY_PF", 1e-12),
        ("OVERLAY_MAX_DD", abs_tol),
        ("PAIRED_MEDIAN_DAILY_DELTA", abs_tol),
        ("EX_BEST_DAY_PNL_DELTA", abs_tol),
        ("EX_TOP3_DAYS_PNL_DELTA", abs_tol),
    )
    for key, tol in checks:
        a = _f(arm.get(key))
        b = _f(expected.get(key))
        if a is None or b is None:
            return False
        if abs(float(a) - float(b)) > float(tol):
            return False
    return True
