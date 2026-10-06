"""Consensus increment, CASE A-E, blocked/retained diagnostic. No rule change from outcomes."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.am_entry_architecture_final_reassessment import C0, C1, C2
from research.am_entry_profit_improvement.metrics import _pf_num
from research.am_entry_temporal_regime_information.analyze import net_pf_dd_beat
from research.canonical_entry_performance_rebase.analyze import _f


def _d(a: Any, b: Any) -> Any:
    try:
        return float(a) - float(b)
    except (TypeError, ValueError):
        return None


def pos_minus_neg(arm: dict[str, Any]) -> int:
    return int(arm.get("PAIRED_POS_DAYS") or 0) - int(arm.get("PAIRED_NEG_DAYS") or 0)


def increment_vs(arm: dict[str, Any], base: dict[str, Any], *, arm_id: str, vs: str) -> dict[str, Any]:
    return {
        f"{arm_id}_DELTA_NET_VS_{vs}": _d(arm.get("OVERLAY_NET_PNL"), base.get("OVERLAY_NET_PNL")),
        f"{arm_id}_DELTA_PF_VS_{vs}": _d(_pf_num(arm.get("OVERLAY_PF")), _pf_num(base.get("OVERLAY_PF"))),
        f"{arm_id}_DELTA_DD_VS_{vs}": _d(arm.get("OVERLAY_MAX_DD"), base.get("OVERLAY_MAX_DD")),
        f"{arm_id}_DELTA_PAIRED_MEDIAN_VS_{vs}": _d(
            arm.get("PAIRED_MEDIAN_DAILY_DELTA"), base.get("PAIRED_MEDIAN_DAILY_DELTA")
        ),
        f"{arm_id}_DELTA_POS_MINUS_NEG_VS_{vs}": float(pos_minus_neg(arm) - pos_minus_neg(base)),
        f"{arm_id}_DELTA_EX_BEST_VS_{vs}": _d(arm.get("EX_BEST_DAY_PNL_DELTA"), base.get("EX_BEST_DAY_PNL_DELTA")),
        f"{arm_id}_DELTA_EX_TOP3_VS_{vs}": _d(arm.get("EX_TOP3_DAYS_PNL_DELTA"), base.get("EX_TOP3_DAYS_PNL_DELTA")),
    }


def daily_ok(arm: dict[str, Any]) -> bool:
    med = _f(arm.get("PAIRED_MEDIAN_DAILY_DELTA"))
    return (
        med is not None
        and float(med) > 0.0
        and int(arm.get("PAIRED_POS_DAYS") or 0) > int(arm.get("PAIRED_NEG_DAYS") or 0)
    )


def tail_ok(arm: dict[str, Any]) -> bool:
    exb = _f(arm.get("EX_BEST_DAY_PNL_DELTA"))
    ext = _f(arm.get("EX_TOP3_DAYS_PNL_DELTA"))
    return exb is not None and float(exb) > 0.0 and ext is not None and float(ext) >= 0.0


def net_pf_beat(arm: dict[str, Any], current_net: float, current_pf: float) -> bool:
    return float(arm.get("OVERLAY_NET_PNL") or 0.0) > float(current_net) and _pf_num(arm.get("OVERLAY_PF")) > float(
        current_pf
    )


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


def _outcome_pack(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    fill_n = sum(1 for r in rows if int(r.get("Y_FILL5") or 0) == 1)
    pnls = []
    for r in rows:
        if int(r.get("Y_FILL5") or 0) != 1:
            continue
        p = _f(r.get("pnl_yen_100"))
        if p is not None:
            pnls.append(float(p))
    return {
        "N": n,
        "FILL_RATE": (float(fill_n) / float(n)) if n else None,
        "COND_PNL": float(np.mean(pnls)) if pnls else None,
        "FILL_N": fill_n,
    }


def selection_change_diagnostic(dec: dict[str, Any]) -> dict[str, Any]:
    b0_b = _outcome_pack(list(dec.get("blocked_b0") or []))
    b0_r = _outcome_pack(list(dec.get("retained_b0") or []))
    b1_b = _outcome_pack(list(dec.get("blocked_b1") or []))
    b1_r = _outcome_pack(list(dec.get("retained_b1") or []))
    return {
        "BLOCKED_B0_N": b0_b["N"],
        "BLOCKED_B0_FILL_RATE": b0_b["FILL_RATE"],
        "BLOCKED_B0_COND_PNL": b0_b["COND_PNL"],
        "RETAINED_B0_N": b0_r["N"],
        "RETAINED_B0_FILL_RATE": b0_r["FILL_RATE"],
        "RETAINED_B0_COND_PNL": b0_r["COND_PNL"],
        "BLOCKED_B1_N": b1_b["N"],
        "BLOCKED_B1_FILL_RATE": b1_b["FILL_RATE"],
        "BLOCKED_B1_COND_PNL": b1_b["COND_PNL"],
        "RETAINED_B1_N": b1_r["N"],
        "RETAINED_B1_FILL_RATE": b1_r["FILL_RATE"],
        "RETAINED_B1_COND_PNL": b1_r["COND_PNL"],
        "note": "Diagnostic only. Outcomes not used to change confirmation rules.",
    }


def decide_case(
    *,
    integrity_ok: bool,
    base_parity: bool,
    arms: list[dict[str, Any]],
    current_net: float,
    current_pf: float,
    current_dd: float,
) -> dict[str, Any]:
    if not integrity_ok or not base_parity:
        return {
            "CASE": "E",
            "VERDICT": "AM_ENTRY_FINAL_REASSESSMENT_INTEGRITY_FAILED",
            "NEXT": "STOP",
        }
    by = {str(a.get("architecture_id")): a for a in arms}
    c_arms = [by[k] for k in (C0, C1, C2) if by.get(k)]
    if any(bool(a.get("ARM_PASS")) for a in c_arms):
        return {
            "CASE": "A",
            "VERDICT": "AM_DUAL_CONFIRMATION_AUGMENT_SUPPORTED",
            "NEXT": "AM_DUAL_CONFIRMATION_AUGMENT_FREEZE_REVIEW",
        }
    beat_dd = [a for a in c_arms if net_pf_dd_beat(a, current_net, current_pf, current_dd)]
    if any(daily_ok(a) and not tail_ok(a) for a in beat_dd):
        return {
            "CASE": "B",
            "VERDICT": "AM_DUAL_CONFIRMATION_EDGE_REMAINS_TAIL_CONCENTRATED",
            "NEXT": "AM_ENTRY_RESEARCH_FINAL_DECISION",
        }
    if any(not daily_ok(a) for a in beat_dd):
        return {
            "CASE": "C",
            "VERDICT": "AM_DUAL_CONFIRMATION_NOT_DAY_ROBUST",
            "NEXT": "AM_ENTRY_RESEARCH_FINAL_DECISION",
        }
    if all(
        float(a.get("OVERLAY_NET_PNL") or 0.0) <= float(current_net) or _pf_num(a.get("OVERLAY_PF")) <= float(current_pf)
        for a in c_arms
    ):
        return {
            "CASE": "D",
            "VERDICT": "AM_DUAL_CONFIRMATION_DESTROYS_ECONOMIC_EDGE",
            "NEXT": "AM_ENTRY_RESEARCH_FINAL_DECISION",
        }
    if any(net_pf_beat(a, current_net, current_pf) and daily_ok(a) for a in c_arms):
        return {
            "CASE": "B",
            "VERDICT": "AM_DUAL_CONFIRMATION_EDGE_REMAINS_TAIL_CONCENTRATED",
            "NEXT": "AM_ENTRY_RESEARCH_FINAL_DECISION",
        }
    return {
        "CASE": "C",
        "VERDICT": "AM_DUAL_CONFIRMATION_NOT_DAY_ROBUST",
        "NEXT": "AM_ENTRY_RESEARCH_FINAL_DECISION",
    }
