"""Aggregate 9 RF representations. Frozen A-K joint gate. No best-rep adoption."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np

from research.canonical_entry_performance_rebase.analyze import _f
from research.entry_rank_shape_audit.oof import delta_series_stats
from research.multioutput_model_probe import ELIGIBLE_DAYS, JOINT_RATE_MIN, POS_REP_DENOM, POS_REP_MIN


def _median(xs: list[float]) -> Optional[float]:
    if not xs:
        return None
    return float(np.median(xs))


def _mean(xs: list[float]) -> Optional[float]:
    if not xs:
        return None
    return float(np.mean(xs))


def spec_rows(bodies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for b in bodies:
        rec = dict(b)
        rec.pop("daily_mfe", None)
        rec.pop("daily_downside", None)
        out.append(rec)
    return out


def aggregate(bodies: list[dict[str, Any]]) -> dict[str, Any]:
    mfe = [float(v) for b in bodies if (v := _f(b.get("JOINT_TOP3_MFE_DELTA"))) is not None]
    dn = [float(v) for b in bodies if (v := _f(b.get("JOINT_TOP3_DOWNSIDE_DELTA"))) is not None]
    rates = [float(v) for b in bodies if (v := _f(b.get("JOINT_IMPROVEMENT_COHORT_RATE"))) is not None]
    sp = [float(v) for b in bodies if (v := _f(b.get("PREDICTED_UD_SPEARMAN_MEDIAN"))) is not None]
    psz = [float(v) for b in bodies if (v := _f(b.get("PREDICTED_PARETO_SIZE_MEAN"))) is not None]
    psh = [float(v) for b in bodies if (v := _f(b.get("PREDICTED_PARETO_SHARE_MEAN"))) is not None]
    mem = [float(v) for b in bodies if (v := _f(b.get("ACTUAL_PARETO_MEMBER_RATE"))) is not None]
    dom = [float(v) for b in bodies if (v := _f(b.get("ACTUAL_DOMINATED_RATE"))) is not None]
    mfe_pos_rep = sum(1 for v in mfe if v > 0)
    dn_pos_rep = sum(1 for v in dn if v > 0)
    by_mfe: dict[str, list[float]] = defaultdict(list)
    by_dn: dict[str, list[float]] = defaultdict(list)
    for b in bodies:
        for rec in b.get("daily_mfe") or []:
            v = _f(rec.get("value"))
            if v is None:
                continue
            by_mfe[str(rec.get("date"))].append(float(v))
        for rec in b.get("daily_downside") or []:
            v = _f(rec.get("value"))
            if v is None:
                continue
            by_dn[str(rec.get("date"))].append(float(v))
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
    gates = {
        "A_MEDIAN_RF_MFE_DELTA_GT_0": bool(med_mfe is not None and med_mfe > 0),
        "B_MEDIAN_RF_DOWNSIDE_DELTA_GT_0": bool(med_dn is not None and med_dn > 0),
        "C_RF_MFE_POSITIVE_REP_N": bool(mfe_pos_rep >= POS_REP_MIN),
        "D_RF_DOWNSIDE_POSITIVE_REP_N": bool(dn_pos_rep >= POS_REP_MIN),
        "E_MEDIAN_JOINT_IMPROVEMENT_COHORT_RATE": bool(med_rate is not None and med_rate >= JOINT_RATE_MIN),
        "F_CONSENSUS_MFE_POS_GT_NEG": bool(int(mst.get("positive_days") or 0) > int(mst.get("negative_days") or 0)),
        "G_CONSENSUS_DOWNSIDE_POS_GT_NEG": bool(int(dst.get("positive_days") or 0) > int(dst.get("negative_days") or 0)),
        "H_CONSENSUS_MFE_EX_BEST_GT_0": bool(mst.get("ex_best_day") is not None and float(mst["ex_best_day"]) > 0),
        "I_CONSENSUS_MFE_EX_TOP3_GE_0": bool(mst.get("ex_top3_days") is not None and float(mst["ex_top3_days"]) >= 0),
        "J_CONSENSUS_DOWNSIDE_EX_BEST_GT_0": bool(dst.get("ex_best_day") is not None and float(dst["ex_best_day"]) > 0),
        "K_CONSENSUS_DOWNSIDE_EX_TOP3_GE_0": bool(dst.get("ex_top3_days") is not None and float(dst["ex_top3_days"]) >= 0),
    }
    fail = [k for k, v in gates.items() if not v]
    return {
        "REPRESENTATION_N": len(bodies),
        "MEDIAN_RF_MFE_DELTA": med_mfe,
        "MEDIAN_RF_DOWNSIDE_DELTA": med_dn,
        "RF_MFE_POSITIVE_REP_N": mfe_pos_rep,
        "RF_DOWNSIDE_POSITIVE_REP_N": dn_pos_rep,
        "POS_REP_MIN": POS_REP_MIN,
        "POS_REP_DENOM": POS_REP_DENOM,
        "MEDIAN_JOINT_IMPROVEMENT_COHORT_RATE": med_rate,
        "CONSENSUS_MFE_POS_DAYS": mst.get("positive_days"),
        "CONSENSUS_MFE_NEG_DAYS": mst.get("negative_days"),
        "CONSENSUS_DOWNSIDE_POS_DAYS": dst.get("positive_days"),
        "CONSENSUS_DOWNSIDE_NEG_DAYS": dst.get("negative_days"),
        "CONSENSUS_MFE_EX_BEST_DAY": mst.get("ex_best_day"),
        "CONSENSUS_MFE_EX_TOP3_DAYS": mst.get("ex_top3_days"),
        "CONSENSUS_DOWNSIDE_EX_BEST_DAY": dst.get("ex_best_day"),
        "CONSENSUS_DOWNSIDE_EX_TOP3_DAYS": dst.get("ex_top3_days"),
        "MEDIAN_RF_PREDICTED_UD_SPEARMAN": _median(sp),
        "RF_PREDICTED_PARETO_SIZE_MEAN": _mean(psz),
        "RF_PREDICTED_PARETO_SHARE_MEAN": _mean(psh),
        "ACTUAL_PARETO_MEMBER_RATE": _median(mem),
        "ACTUAL_DOMINATED_RATE": _median(dom),
        "gates": gates,
        "gate_fail": fail,
        "JOINT_MODEL_EDGE_PASS": len(fail) == 0,
        "daily": daily,
    }


def decide(agg: dict[str, Any]) -> dict[str, Any]:
    passed = bool(agg.get("JOINT_MODEL_EDGE_PASS"))
    mfe = agg.get("MEDIAN_RF_MFE_DELTA")
    dn = agg.get("MEDIAN_RF_DOWNSIDE_DELTA")
    mfe_up = bool(mfe is not None and float(mfe) > 0)
    dn_up = bool(dn is not None and float(dn) > 0)
    if passed:
        return {
            "CASE": "A",
            "VERDICT": "NONLINEAR_JOINT_REGION_IDENTIFIABLE",
            "NEXT_RESEARCH": "JOINT_MODEL_PRECOMMITTED_DEVELOPMENT",
            "PRIMARY_FINDING": (
                "Shared multi-output RandomForest identifies the joint MFE/downside region "
                "on historical 18-day OOF under frozen maximin JOINT_SCORE. "
                "No representation adopted. Exact not run."
            ),
            "note": "CASE A. STOP. Do not start precommitted development this run.",
        }
    if mfe_up and dn_up:
        return {
            "CASE": "C",
            "VERDICT": "NONLINEAR_JOINT_SIGNAL_NOT_ROBUST",
            "NEXT_RESEARCH": "MODEL_ARCHITECTURE_RECONSIDERATION",
            "PRIMARY_FINDING": (
                "Both component medians improve vs CURRENT, but day/representation robustness "
                f"fails frozen A-K: {agg.get('gate_fail')}"
            ),
            "note": "CASE C. STOP. No tuning this run.",
        }
    if mfe_up or dn_up:
        side = "MFE" if mfe_up and not dn_up else "downside-avoid"
        return {
            "CASE": "B",
            "VERDICT": "NONLINEAR_MODEL_STILL_SINGLE_OBJECTIVE",
            "NEXT_RESEARCH": "DIRECT_JOINT_OBJECTIVE_DESIGN",
            "PRIMARY_FINDING": (
                f"Multi-output RF still improves only {side} vs CURRENT Top3. "
                "Joint region identification is not established."
            ),
            "note": "CASE B. STOP. No weight search this run.",
        }
    return {
        "CASE": "D",
        "VERDICT": "EXISTING_FEATURES_CANNOT_IDENTIFY_JOINT_REGION",
        "NEXT_RESEARCH": "FEATURE_ARCHITECTURE_REDESIGN",
        "PRIMARY_FINDING": (
            "Frozen multi-output RF on existing Canonical features does not identify "
            "the joint MFE/downside region vs CURRENT."
        ),
        "note": "CASE D. STOP. No new feature this run.",
    }
