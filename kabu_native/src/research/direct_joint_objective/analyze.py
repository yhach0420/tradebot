"""Aggregate 9 RF classifier representations. Frozen A-K joint gate. No best-rep adoption."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np

from research.canonical_entry_performance_rebase.analyze import _f
from research.direct_joint_objective import ELIGIBLE_DAYS, JOINT_RATE_MIN, POS_REP_DENOM, POS_REP_MIN
from research.entry_rank_shape_audit.oof import delta_series_stats


def _median(xs: list[float]) -> Optional[float]:
    if not xs:
        return None
    return float(np.median(xs))


def spec_rows(bodies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for b in bodies:
        rec = dict(b)
        rec.pop("daily_mfe", None)
        rec.pop("daily_downside", None)
        rec.pop("daily_label_rate", None)
        out.append(rec)
    return out


def aggregate(bodies: list[dict[str, Any]]) -> dict[str, Any]:
    mfe = [float(v) for b in bodies if (v := _f(b.get("TOP3_MFE_DELTA"))) is not None]
    dn = [float(v) for b in bodies if (v := _f(b.get("TOP3_DOWNSIDE_DELTA"))) is not None]
    rates = [float(v) for b in bodies if (v := _f(b.get("JOINT_COHORT_SUCCESS_RATE"))) is not None]
    aucs = [float(v) for b in bodies if (v := _f(b.get("ROC_AUC"))) is not None]
    aps = [float(v) for b in bodies if (v := _f(b.get("AVERAGE_PRECISION"))) is not None]
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
    label0 = bodies[0] if bodies else {}
    gates = {
        "A_MEDIAN_DIRECT_MFE_DELTA_GT_0": bool(med_mfe is not None and med_mfe > 0),
        "B_MEDIAN_DIRECT_DOWNSIDE_DELTA_GT_0": bool(med_dn is not None and med_dn > 0),
        "C_MFE_POSITIVE_REP_N": bool(mfe_pos_rep >= POS_REP_MIN),
        "D_DOWNSIDE_POSITIVE_REP_N": bool(dn_pos_rep >= POS_REP_MIN),
        "E_MEDIAN_DIRECT_JOINT_COHORT_SUCCESS_RATE": bool(med_rate is not None and med_rate >= JOINT_RATE_MIN),
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
        "JOINT_LABEL_POSITIVE_N": label0.get("JOINT_LABEL_POSITIVE_N"),
        "JOINT_LABEL_NEGATIVE_N": label0.get("JOINT_LABEL_NEGATIVE_N"),
        "JOINT_LABEL_POSITIVE_RATE": label0.get("JOINT_LABEL_POSITIVE_RATE"),
        "LABELED_N": label0.get("LABELED_N"),
        "LABELED_COHORT_N": label0.get("LABELED_COHORT_N"),
        "SKIP_INCOMPLETE_CURRENT_TOP3_N": label0.get("SKIP_INCOMPLETE_CURRENT_TOP3_N"),
        "daily_label_rate": label0.get("daily_label_rate") or [],
        "MEDIAN_DIRECT_MFE_DELTA": med_mfe,
        "MEDIAN_DIRECT_DOWNSIDE_DELTA": med_dn,
        "MFE_POSITIVE_REP_N": mfe_pos_rep,
        "DOWNSIDE_POSITIVE_REP_N": dn_pos_rep,
        "POS_REP_MIN": POS_REP_MIN,
        "POS_REP_DENOM": POS_REP_DENOM,
        "MEDIAN_DIRECT_JOINT_COHORT_SUCCESS_RATE": med_rate,
        "CONSENSUS_MFE_POS_DAYS": mst.get("positive_days"),
        "CONSENSUS_MFE_NEG_DAYS": mst.get("negative_days"),
        "CONSENSUS_DOWNSIDE_POS_DAYS": dst.get("positive_days"),
        "CONSENSUS_DOWNSIDE_NEG_DAYS": dst.get("negative_days"),
        "CONSENSUS_MFE_EX_BEST_DAY": mst.get("ex_best_day"),
        "CONSENSUS_MFE_EX_TOP3_DAYS": mst.get("ex_top3_days"),
        "CONSENSUS_DOWNSIDE_EX_BEST_DAY": dst.get("ex_best_day"),
        "CONSENSUS_DOWNSIDE_EX_TOP3_DAYS": dst.get("ex_top3_days"),
        "MEDIAN_ROC_AUC": _median(aucs),
        "MEDIAN_AVERAGE_PRECISION": _median(aps),
        "ACTUAL_PARETO_MEMBER_RATE": _median(mem),
        "ACTUAL_DOMINATED_RATE": _median(dom),
        "gates": gates,
        "gate_fail": fail,
        "DIRECT_JOINT_OBJECTIVE_PASS": len(fail) == 0,
        "daily": daily,
    }


def decide(agg: dict[str, Any]) -> dict[str, Any]:
    passed = bool(agg.get("DIRECT_JOINT_OBJECTIVE_PASS"))
    mfe = agg.get("MEDIAN_DIRECT_MFE_DELTA")
    dn = agg.get("MEDIAN_DIRECT_DOWNSIDE_DELTA")
    mfe_up = bool(mfe is not None and float(mfe) > 0)
    dn_up = bool(dn is not None and float(dn) > 0)
    if passed:
        return {
            "CASE": "A",
            "VERDICT": "DIRECT_JOINT_OBJECTIVE_IDENTIFIABLE",
            "NEXT_RESEARCH": "DIRECT_JOINT_MODEL_PRECOMMITTED_DEVELOPMENT",
            "PRIMARY_FINDING": (
                "Direct joint-improvement classifier identifies CURRENT-beating joint candidates "
                "on historical 18-day OOF under frozen P(label=1) Top3. "
                "No representation adopted. Exact not run."
            ),
            "note": "CASE A. STOP. Do not start precommitted development this run.",
        }
    if mfe_up and dn_up:
        return {
            "CASE": "D",
            "VERDICT": "DIRECT_JOINT_SIGNAL_NOT_ROBUST",
            "NEXT_RESEARCH": "FEATURE_ARCHITECTURE_REDESIGN",
            "PRIMARY_FINDING": (
                "Both component medians improve vs CURRENT, but day/representation robustness "
                f"fails frozen A-K: {agg.get('gate_fail')}"
            ),
            "note": "CASE D. STOP. Feature redesign not started this run.",
        }
    if dn_up and not mfe_up:
        return {
            "CASE": "B",
            "VERDICT": "DIRECT_JOINT_OBJECTIVE_STILL_RISK_ONLY",
            "NEXT_RESEARCH": "FEATURE_ARCHITECTURE_REDESIGN",
            "PRIMARY_FINDING": (
                "Direct joint label still improves only downside-avoid vs CURRENT Top3. "
                "Joint identification is not established."
            ),
            "note": "CASE B. STOP. Feature redesign not started this run.",
        }
    if mfe_up and not dn_up:
        return {
            "CASE": "C",
            "VERDICT": "DIRECT_JOINT_OBJECTIVE_STILL_UPSIDE_ONLY",
            "NEXT_RESEARCH": "FEATURE_ARCHITECTURE_REDESIGN",
            "PRIMARY_FINDING": (
                "Direct joint label still improves only MFE vs CURRENT Top3. "
                "Joint identification is not established."
            ),
            "note": "CASE C. STOP. Feature redesign not started this run.",
        }
    return {
        "CASE": "E",
        "VERDICT": "EXISTING_FEATURES_CANNOT_IDENTIFY_JOINT_REGION",
        "NEXT_RESEARCH": "FEATURE_ARCHITECTURE_REDESIGN",
        "PRIMARY_FINDING": (
            "Frozen direct joint-improvement classifier on existing Canonical features "
            "does not improve either component vs CURRENT Top3."
        ),
        "note": "CASE E. STOP. Feature redesign not started this run.",
    }
