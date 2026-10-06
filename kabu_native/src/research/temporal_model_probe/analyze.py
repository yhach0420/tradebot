"""Control parity, A-K temporal gate, incremental vs flattened RF, CASE A-D."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.canonical_entry_performance_rebase.analyze import _f
from research.entry_rank_shape_audit.oof import delta_series_stats
from research.temporal_model_probe import (
    ELIGIBLE_DAYS,
    JOINT_RATE_MIN,
    PARITY_ABS_TOL,
    S0_EXPECTED,
    S1_EXPECTED,
    SEED_N,
)


def _close(a: Any, b: Any, tol: float) -> bool:
    x, y = _f(a), _f(b)
    if x is None or y is None:
        return False
    return abs(float(x) - float(y)) <= float(tol)


def _median(xs: list[float]) -> Optional[float]:
    if not xs:
        return None
    return float(np.median(xs))


def _sub(a: Any, b: Any) -> Optional[float]:
    x, y = _f(a), _f(b)
    if x is None or y is None:
        return None
    return float(x) - float(y)


def control_parity(s0: dict[str, Any], s1: dict[str, Any]) -> dict[str, Any]:
    checks = {
        "S0_MFE": _close(s0.get("TOP3_MFE_DELTA"), S0_EXPECTED["TOP3_MFE_DELTA"], PARITY_ABS_TOL),
        "S0_DOWNSIDE": _close(s0.get("TOP3_DOWNSIDE_DELTA"), S0_EXPECTED["TOP3_DOWNSIDE_DELTA"], PARITY_ABS_TOL),
        "S0_JOINT": _close(s0.get("JOINT_COHORT_SUCCESS_RATE"), S0_EXPECTED["JOINT_COHORT_SUCCESS_RATE"], PARITY_ABS_TOL),
        "S1_MFE": _close(s1.get("TOP3_MFE_DELTA"), S1_EXPECTED["TOP3_MFE_DELTA"], PARITY_ABS_TOL),
        "S1_DOWNSIDE": _close(s1.get("TOP3_DOWNSIDE_DELTA"), S1_EXPECTED["TOP3_DOWNSIDE_DELTA"], PARITY_ABS_TOL),
        "S1_JOINT": _close(s1.get("JOINT_COHORT_SUCCESS_RATE"), S1_EXPECTED["JOINT_COHORT_SUCCESS_RATE"], PARITY_ABS_TOL),
    }
    return {
        "BASE_PARITY": all(checks.values()),
        "checks": checks,
        "s0_expected": dict(S0_EXPECTED),
        "s1_expected": dict(S1_EXPECTED),
        "s0_observed": {
            "TOP3_MFE_DELTA": s0.get("TOP3_MFE_DELTA"),
            "TOP3_DOWNSIDE_DELTA": s0.get("TOP3_DOWNSIDE_DELTA"),
            "JOINT_COHORT_SUCCESS_RATE": s0.get("JOINT_COHORT_SUCCESS_RATE"),
        },
        "s1_observed": {
            "TOP3_MFE_DELTA": s1.get("TOP3_MFE_DELTA"),
            "TOP3_DOWNSIDE_DELTA": s1.get("TOP3_DOWNSIDE_DELTA"),
            "JOINT_COHORT_SUCCESS_RATE": s1.get("JOINT_COHORT_SUCCESS_RATE"),
        },
    }


def seed_row(body: dict[str, Any]) -> dict[str, Any]:
    return {
        "seed": body.get("seed"),
        "TCN_MFE_DELTA": body.get("TOP3_MFE_DELTA"),
        "TCN_DOWNSIDE_DELTA": body.get("TOP3_DOWNSIDE_DELTA"),
        "TCN_JOINT_RATE": body.get("JOINT_COHORT_SUCCESS_RATE"),
        "MFE_POSITIVE_DAYS": body.get("MFE_POSITIVE_DAYS"),
        "MFE_NEGATIVE_DAYS": body.get("MFE_NEGATIVE_DAYS"),
        "DOWNSIDE_POSITIVE_DAYS": body.get("DOWNSIDE_POSITIVE_DAYS"),
        "DOWNSIDE_NEGATIVE_DAYS": body.get("DOWNSIDE_NEGATIVE_DAYS"),
        "MFE_EX_BEST_DAY": body.get("MFE_EX_BEST_DAY"),
        "MFE_EX_TOP3_DAYS": body.get("MFE_EX_TOP3_DAYS"),
        "DOWNSIDE_EX_BEST_DAY": body.get("DOWNSIDE_EX_BEST_DAY"),
        "DOWNSIDE_EX_TOP3_DAYS": body.get("DOWNSIDE_EX_TOP3_DAYS"),
        "ROC_AUC": body.get("ROC_AUC"),
        "AVERAGE_PRECISION": body.get("AVERAGE_PRECISION"),
        "n_cohorts": body.get("n_cohorts"),
        "outer_folds": body.get("outer_folds"),
        "heldout_fit_leak_n": body.get("heldout_fit_leak_n"),
    }


def aggregate(bodies: list[dict[str, Any]]) -> dict[str, Any]:
    mfe = [float(v) for b in bodies if (v := _f(b.get("TOP3_MFE_DELTA"))) is not None]
    dn = [float(v) for b in bodies if (v := _f(b.get("TOP3_DOWNSIDE_DELTA"))) is not None]
    rates = [float(v) for b in bodies if (v := _f(b.get("JOINT_COHORT_SUCCESS_RATE"))) is not None]
    aucs = [float(v) for b in bodies if (v := _f(b.get("ROC_AUC"))) is not None]
    aps = [float(v) for b in bodies if (v := _f(b.get("AVERAGE_PRECISION"))) is not None]
    by_mfe: dict[str, list[float]] = {}
    by_dn: dict[str, list[float]] = {}
    for d in ELIGIBLE_DAYS:
        by_mfe[d] = []
        by_dn[d] = []
    for b in bodies:
        for rec in b.get("daily_mfe") or []:
            v = _f(rec.get("value"))
            if v is None:
                continue
            by_mfe.setdefault(str(rec.get("date")), []).append(float(v))
        for rec in b.get("daily_downside") or []:
            v = _f(rec.get("value"))
            if v is None:
                continue
            by_dn.setdefault(str(rec.get("date")), []).append(float(v))
    cons_mfe = []
    cons_dn = []
    daily = []
    for d in ELIGIBLE_DAYS:
        xm = by_mfe.get(d) or []
        xd = by_dn.get(d) or []
        if not xm or not xd:
            continue
        mm = float(np.median(xm))
        md = float(np.median(xd))
        cons_mfe.append(mm)
        cons_dn.append(md)
        daily.append({"date": d, "CONSENSUS_MFE_DELTA": mm, "CONSENSUS_DOWNSIDE_DELTA": md})
    mst = delta_series_stats(cons_mfe)
    dst = delta_series_stats(cons_dn)
    med_mfe = _median(mfe)
    med_dn = _median(dn)
    med_rate = _median(rates)
    d_mfe = _sub(med_mfe, S1_EXPECTED["TOP3_MFE_DELTA"])
    d_dn = _sub(med_dn, S1_EXPECTED["TOP3_DOWNSIDE_DELTA"])
    d_jt = _sub(med_rate, S1_EXPECTED["JOINT_COHORT_SUCCESS_RATE"])
    incremental = bool(
        med_rate is not None
        and med_mfe is not None
        and med_dn is not None
        and float(med_rate) > float(S1_EXPECTED["JOINT_COHORT_SUCCESS_RATE"])
        and float(med_mfe) >= float(S1_EXPECTED["TOP3_MFE_DELTA"])
        and float(med_dn) >= float(S1_EXPECTED["TOP3_DOWNSIDE_DELTA"])
    )
    seeds_mfe_pos = sum(1 for v in mfe if v > 0)
    seeds_dn_pos = sum(1 for v in dn if v > 0)
    gates = {
        "A_MEDIAN_TCN_MFE_DELTA_GT_0": bool(med_mfe is not None and med_mfe > 0),
        "B_MEDIAN_TCN_DOWNSIDE_DELTA_GT_0": bool(med_dn is not None and med_dn > 0),
        "C_MEDIAN_TCN_JOINT_RATE": bool(med_rate is not None and med_rate >= JOINT_RATE_MIN),
        "D_SEEDS_MFE_POSITIVE_N": bool(seeds_mfe_pos == int(SEED_N)),
        "E_SEEDS_DOWNSIDE_POSITIVE_N": bool(seeds_dn_pos == int(SEED_N)),
        "F_CONSENSUS_MFE_POS_GT_NEG": bool(int(mst.get("positive_days") or 0) > int(mst.get("negative_days") or 0)),
        "G_CONSENSUS_DOWNSIDE_POS_GT_NEG": bool(int(dst.get("positive_days") or 0) > int(dst.get("negative_days") or 0)),
        "H_CONSENSUS_MFE_EX_BEST_GT_0": bool(mst.get("ex_best_day") is not None and float(mst["ex_best_day"]) > 0),
        "I_CONSENSUS_MFE_EX_TOP3_GE_0": bool(mst.get("ex_top3_days") is not None and float(mst["ex_top3_days"]) >= 0),
        "J_CONSENSUS_DOWNSIDE_EX_BEST_GT_0": bool(dst.get("ex_best_day") is not None and float(dst["ex_best_day"]) > 0),
        "K_CONSENSUS_DOWNSIDE_EX_TOP3_GE_0": bool(dst.get("ex_top3_days") is not None and float(dst["ex_top3_days"]) >= 0),
    }
    fail = [k for k, v in gates.items() if not v]
    return {
        "SEED_N": len(bodies),
        "MEDIAN_TCN_MFE_DELTA": med_mfe,
        "MEDIAN_TCN_DOWNSIDE_DELTA": med_dn,
        "MEDIAN_TCN_JOINT_RATE": med_rate,
        "SEEDS_MFE_POSITIVE_N": seeds_mfe_pos,
        "SEEDS_DOWNSIDE_POSITIVE_N": seeds_dn_pos,
        "CONSENSUS_MFE_POS_DAYS": mst.get("positive_days"),
        "CONSENSUS_MFE_NEG_DAYS": mst.get("negative_days"),
        "CONSENSUS_DOWNSIDE_POS_DAYS": dst.get("positive_days"),
        "CONSENSUS_DOWNSIDE_NEG_DAYS": dst.get("negative_days"),
        "CONSENSUS_MFE_EX_BEST_DAY": mst.get("ex_best_day"),
        "CONSENSUS_MFE_EX_TOP3_DAYS": mst.get("ex_top3_days"),
        "CONSENSUS_DOWNSIDE_EX_BEST_DAY": dst.get("ex_best_day"),
        "CONSENSUS_DOWNSIDE_EX_TOP3_DAYS": dst.get("ex_top3_days"),
        "DELTA_MFE_VS_FLAT_RF": d_mfe,
        "DELTA_DOWNSIDE_VS_FLAT_RF": d_dn,
        "DELTA_JOINT_RATE_VS_FLAT_RF": d_jt,
        "TEMPORAL_ORDER_INCREMENTAL": incremental,
        "MEDIAN_ROC_AUC": _median(aucs),
        "MEDIAN_AVERAGE_PRECISION": _median(aps),
        "gates": gates,
        "gate_fail": fail,
        "TEMPORAL_MODEL_PASS": len(fail) == 0,
        "daily": daily,
    }


def decide(
    agg: dict[str, Any],
    *,
    parity_ok: bool,
    integ_ok: bool,
    integ_note: str,
) -> dict[str, Any]:
    if not parity_ok:
        return {
            "CASE": None,
            "VERDICT": "TEMPORAL_MODEL_INTEGRITY_FAILED",
            "NEXT_RESEARCH": "NONE",
            "PRIMARY_FINDING": "S0/S1 controls did not reproduce the frozen sequence-probe numbers.",
            "TEMPORAL_MODEL_PASS": False,
            "note": "STOP. BASE_PARITY failed.",
        }
    if not integ_ok:
        return {
            "CASE": None,
            "VERDICT": "TEMPORAL_MODEL_INTEGRITY_FAILED",
            "NEXT_RESEARCH": "NONE",
            "PRIMARY_FINDING": f"Integrity fail: {integ_note}.",
            "TEMPORAL_MODEL_PASS": False,
            "note": "STOP. TEMPORAL_MODEL_INTEGRITY_FAILED.",
        }
    passed = bool(agg.get("TEMPORAL_MODEL_PASS"))
    mfe = agg.get("MEDIAN_TCN_MFE_DELTA")
    dn = agg.get("MEDIAN_TCN_DOWNSIDE_DELTA")
    mfe_up = bool(mfe is not None and float(mfe) > 0)
    dn_up = bool(dn is not None and float(dn) > 0)
    incremental = bool(agg.get("TEMPORAL_ORDER_INCREMENTAL"))
    if passed:
        return {
            "CASE": "A",
            "VERDICT": "TEMPORAL_JOINT_REGION_IDENTIFIABLE",
            "NEXT_RESEARCH": "TEMPORAL_MODEL_PRECOMMITTED_DEVELOPMENT",
            "PRIMARY_FINDING": (
                "Frozen causal 1D TCN identifies CURRENT-beating joint candidates on 18-day OOF "
                "under median/consensus of 3 seeds. Model not adopted. Exact not run."
            ),
            "TEMPORAL_MODEL_PASS": True,
            "note": "CASE A. STOP. Do not start precommitted development this run.",
        }
    if incremental:
        return {
            "CASE": "B",
            "VERDICT": "TEMPORAL_SIGNAL_PRESENT_NOT_ROBUST",
            "NEXT_RESEARCH": "TEMPORAL_ARCHITECTURE_RECONSIDERATION",
            "PRIMARY_FINDING": (
                "Causal TCN improves vs flattened RF on joint+MFE+downside, but frozen A-K fail: "
                f"{agg.get('gate_fail')}"
            ),
            "TEMPORAL_MODEL_PASS": False,
            "note": "CASE B. STOP. Additional temporal models not started this run.",
        }
    if (mfe_up and not dn_up) or (dn_up and not mfe_up):
        return {
            "CASE": "C",
            "VERDICT": "TEMPORAL_MODEL_STILL_SINGLE_OBJECTIVE",
            "NEXT_RESEARCH": "RAW_EVENT_REPRESENTATION_AUDIT",
            "PRIMARY_FINDING": "Causal TCN still moves only one of MFE / downside-avoid vs CURRENT Top3.",
            "TEMPORAL_MODEL_PASS": False,
            "note": "CASE C. STOP. Event-level audit not started this run.",
        }
    return {
        "CASE": "D",
        "VERDICT": "FIXED_GRID_TEMPORAL_MODEL_INSUFFICIENT",
        "NEXT_RESEARCH": "RAW_EVENT_REPRESENTATION_AUDIT",
        "PRIMARY_FINDING": "Fixed-grid causal TCN does not improve joint identification vs flattened RF S1.",
        "TEMPORAL_MODEL_PASS": False,
        "note": "CASE D. STOP. Event-level audit not started this run.",
    }
