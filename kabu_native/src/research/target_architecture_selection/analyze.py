"""T3/T1/T2 component alignment gates and CASE A-E. No raw-magnitude selection."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.canonical_entry_performance_rebase.analyze import _f
from research.entry_rank_shape_audit.oof import delta_series_stats
from research.target_architecture_selection import ELIGIBLE_DAYS, POS_SPEC_DENOM, POS_SPEC_MIN


def _median(xs: list[float]) -> Optional[float]:
    if not xs:
        return None
    return float(np.median(xs))


def spec_component_rows(bodies: list[dict[str, Any]], train_id: str, component: str) -> list[dict[str, Any]]:
    out = []
    for body in bodies:
        tr = (body.get("by_train") or {}).get(train_id) or {}
        comp = (tr.get("components") or {}).get(component) or {}
        out.append(
            {
                "spec_id": body.get("spec_id"),
                "feature_set": body.get("feature_set"),
                "normalization": body.get("normalization"),
                "alpha": body.get("alpha"),
                "train_id": train_id,
                "component": component,
                "outer_folds": tr.get("outer_folds"),
                "MATCHED_RANKING_ROWS": tr.get("MATCHED_RANKING_ROWS"),
                "TOP3_DELTA": comp.get("TOP3_DELTA"),
                "MODEL_TOP3": comp.get("MODEL_TOP3"),
                "CURRENT_TOP3": comp.get("CURRENT_TOP3"),
                "days": comp.get("days") or [],
            }
        )
    return out


def aggregate_component(spec_rows: list[dict[str, Any]], days: list[str] | None = None) -> dict[str, Any]:
    day_ids = list(days or ELIGIBLE_DAYS)
    deltas = [float(v) for r in spec_rows if (v := _f(r.get("TOP3_DELTA"))) is not None]
    pos_spec = sum(1 for v in deltas if v > 0)
    by_day: dict[str, list[float]] = {d: [] for d in day_ids}
    for r in spec_rows:
        for rec in r.get("days") or []:
            dd = str(rec.get("date") or "")
            v = _f(rec.get("DELTA_TOP3_UPLIFT"))
            if dd in by_day and v is not None:
                by_day[dd].append(float(v))
    consensus = []
    vals = []
    for d in day_ids:
        xs = by_day.get(d) or []
        if not xs:
            continue
        med = float(np.median(xs))
        consensus.append({"date": d, "CONSENSUS_DAILY_TOP3_DELTA": med, "n_specs": len(xs)})
        vals.append(med)
    st = delta_series_stats(vals)
    return {
        "SPEC_N": len(spec_rows),
        "MEDIAN_SPEC_DELTA": _median(deltas),
        "POSITIVE_SPEC_N": pos_spec,
        "POS_SPEC_MIN": POS_SPEC_MIN,
        "POS_SPEC_DENOM": POS_SPEC_DENOM,
        "CONSENSUS_POSITIVE_DAYS": int(st.get("positive_days") or 0),
        "CONSENSUS_NEGATIVE_DAYS": int(st.get("negative_days") or 0),
        "CONSENSUS_EX_BEST_DAY": st.get("ex_best_day"),
        "CONSENSUS_EX_TOP3_DAYS": st.get("ex_top3_days"),
        "CONSENSUS_DAILY": consensus,
        "n_consensus_days": len(vals),
    }


def t3_alignment(*, t3_aj_pass: bool, mfe: dict[str, Any], down: dict[str, Any]) -> dict[str, Any]:
    mfe_med = mfe.get("MEDIAN_SPEC_DELTA")
    dn_med = down.get("MEDIAN_SPEC_DELTA")
    mfe_pos = int(mfe.get("POSITIVE_SPEC_N") or 0)
    dn_pos = int(down.get("POSITIVE_SPEC_N") or 0)
    mfe_cp = int(mfe.get("CONSENSUS_POSITIVE_DAYS") or 0)
    mfe_cn = int(mfe.get("CONSENSUS_NEGATIVE_DAYS") or 0)
    dn_cp = int(down.get("CONSENSUS_POSITIVE_DAYS") or 0)
    dn_cn = int(down.get("CONSENSUS_NEGATIVE_DAYS") or 0)
    mfe_ex1 = mfe.get("CONSENSUS_EX_BEST_DAY")
    mfe_ex3 = mfe.get("CONSENSUS_EX_TOP3_DAYS")
    dn_ex1 = down.get("CONSENSUS_EX_BEST_DAY")
    dn_ex3 = down.get("CONSENSUS_EX_TOP3_DAYS")
    gates = {
        "A_T3_AJ_PASS": bool(t3_aj_pass),
        "B_T3_MFE_MEDIAN_GT_0": bool(mfe_med is not None and float(mfe_med) > 0),
        "C_T3_DOWNSIDE_MEDIAN_GE_0": bool(dn_med is not None and float(dn_med) >= 0),
        "D_T3_MFE_POSITIVE_SPEC_N": bool(mfe_pos >= POS_SPEC_MIN),
        "E_T3_DOWNSIDE_POSITIVE_SPEC_N": bool(dn_pos >= POS_SPEC_MIN),
        "F_MFE_CONSENSUS_POS_GT_NEG": bool(mfe_cp > mfe_cn),
        "G_DOWNSIDE_CONSENSUS_POS_GE_NEG": bool(dn_cp >= dn_cn),
        "H_MFE_EX_BEST_GT_0": bool(mfe_ex1 is not None and float(mfe_ex1) > 0),
        "I_MFE_EX_TOP3_GE_0": bool(mfe_ex3 is not None and float(mfe_ex3) >= 0),
        "J_DOWNSIDE_EX_BEST_GE_0": bool(dn_ex1 is not None and float(dn_ex1) >= 0),
        "K_DOWNSIDE_EX_TOP3_GE_0": bool(dn_ex3 is not None and float(dn_ex3) >= 0),
    }
    fail = [k for k, v in gates.items() if not v]
    return {"gates": gates, "gate_fail": fail, "T3_COMPONENT_ALIGNMENT_PASS": len(fail) == 0}


def t1_clean(*, t1_aj_pass: bool, mfe: dict[str, Any], down: dict[str, Any]) -> dict[str, Any]:
    mfe_med = mfe.get("MEDIAN_SPEC_DELTA")
    dn_med = down.get("MEDIAN_SPEC_DELTA")
    dn_pos = int(down.get("POSITIVE_SPEC_N") or 0)
    dn_cp = int(down.get("CONSENSUS_POSITIVE_DAYS") or 0)
    dn_cn = int(down.get("CONSENSUS_NEGATIVE_DAYS") or 0)
    dn_ex3 = down.get("CONSENSUS_EX_TOP3_DAYS")
    gates = {
        "T1_AJ_PASS": bool(t1_aj_pass),
        "MFE_MEDIAN_GT_0": bool(mfe_med is not None and float(mfe_med) > 0),
        "DOWNSIDE_MEDIAN_GE_0": bool(dn_med is not None and float(dn_med) >= 0),
        "DOWNSIDE_POSITIVE_SPEC_N": bool(dn_pos >= POS_SPEC_MIN),
        "DOWNSIDE_CONSENSUS_POS_GE_NEG": bool(dn_cp >= dn_cn),
        "DOWNSIDE_EX_TOP3_GE_0": bool(dn_ex3 is not None and float(dn_ex3) >= 0),
    }
    fail = [k for k, v in gates.items() if not v]
    return {"gates": gates, "gate_fail": fail, "T1_CLEAN_OPPORTUNITY": len(fail) == 0}


def t2_clean(*, t2_aj_pass: bool, down: dict[str, Any], mfe: dict[str, Any]) -> dict[str, Any]:
    dn_med = down.get("MEDIAN_SPEC_DELTA")
    mfe_med = mfe.get("MEDIAN_SPEC_DELTA")
    mfe_pos = int(mfe.get("POSITIVE_SPEC_N") or 0)
    mfe_cp = int(mfe.get("CONSENSUS_POSITIVE_DAYS") or 0)
    mfe_cn = int(mfe.get("CONSENSUS_NEGATIVE_DAYS") or 0)
    mfe_ex3 = mfe.get("CONSENSUS_EX_TOP3_DAYS")
    gates = {
        "T2_AJ_PASS": bool(t2_aj_pass),
        "DOWNSIDE_MEDIAN_GT_0": bool(dn_med is not None and float(dn_med) > 0),
        "MFE_MEDIAN_GE_0": bool(mfe_med is not None and float(mfe_med) >= 0),
        "MFE_POSITIVE_SPEC_N": bool(mfe_pos >= POS_SPEC_MIN),
        "MFE_CONSENSUS_POS_GE_NEG": bool(mfe_cp >= mfe_cn),
        "MFE_EX_TOP3_GE_0": bool(mfe_ex3 is not None and float(mfe_ex3) >= 0),
    }
    fail = [k for k, v in gates.items() if not v]
    return {"gates": gates, "gate_fail": fail, "T2_CLEAN_RISK": len(fail) == 0}


def t1_upside_predictable(*, t1_aj_pass: bool, mfe: dict[str, Any]) -> bool:
    med = mfe.get("MEDIAN_SPEC_DELTA")
    return bool(t1_aj_pass and med is not None and float(med) > 0)


def t2_downside_predictable(*, t2_aj_pass: bool, down: dict[str, Any]) -> bool:
    med = down.get("MEDIAN_SPEC_DELTA")
    return bool(t2_aj_pass and med is not None and float(med) > 0)


def decide(
    *,
    t3_pass: bool,
    t1_clean_flag: bool,
    t2_clean_flag: bool,
    t1_upside: bool,
    t2_downside: bool,
) -> dict[str, Any]:
    if t3_pass:
        return {
            "CASE": "A",
            "PRIMARY_TARGET": "T3_PATH_QUALITY_600",
            "VERDICT": "PATH_QUALITY_TARGET_PRECOMMITTED",
            "NEXT_RESEARCH": "T3_TARGET_SPECIFIC_MODEL_DEVELOPMENT",
            "PRIMARY_FINDING": (
                "T3 scalar path-quality ranking keeps both MFE and downside aligned vs CURRENT. "
                "Primary Target is T3_PATH_QUALITY_600. Not selected by raw uplift."
            ),
            "note": "CASE A. STOP. Target-specific model development is not started this run.",
        }
    if t1_clean_flag:
        return {
            "CASE": "B",
            "PRIMARY_TARGET": "T1_MFE_600",
            "VERDICT": "MFE_TARGET_PRECOMMITTED",
            "NEXT_RESEARCH": "T1_TARGET_SPECIFIC_MODEL_DEVELOPMENT",
            "PRIMARY_FINDING": (
                "T3 cannot hold both components. T1 is a clean opportunity target "
                "(MFE up, downside not degraded). Primary Target is T1_MFE_600."
            ),
            "note": "CASE B. STOP. Target-specific model development is not started this run.",
        }
    if t2_clean_flag:
        return {
            "CASE": "C",
            "PRIMARY_TARGET": "T2_DOWNSIDE_AVOID_600",
            "VERDICT": "DOWNSIDE_RISK_TARGET_ONLY",
            "NEXT_RESEARCH": "RISK_AWARE_ENTRY_ARCHITECTURE_DESIGN",
            "PRIMARY_FINDING": (
                "T3 alignment failed and T1 is not a clean opportunity. "
                "T2 is a clean risk object. This is not an alpha-target discovery."
            ),
            "note": "CASE C. STOP. Do not call T2 an alpha target. No model this run.",
        }
    if t1_upside and t2_downside:
        return {
            "CASE": "D",
            "PRIMARY_TARGET": "NONE",
            "VERDICT": "MULTI_OBJECTIVE_TARGET_REQUIRED",
            "NEXT_RESEARCH": "MULTI_OBJECTIVE_ENTRY_DESIGN",
            "PRIMARY_FINDING": (
                "T1 predicts upside and T2 predicts downside, but T3 scalar ranking cannot "
                "hold both components at once, and neither family is clean. No weight search this run."
            ),
            "note": "CASE D. STOP. Do not invent T3 weights this run.",
        }
    return {
        "CASE": "E",
        "PRIMARY_TARGET": "NONE",
        "VERDICT": "TARGET_ARCHITECTURE_SELECTION_INCONCLUSIVE",
        "NEXT_RESEARCH": "NONE",
        "PRIMARY_FINDING": "Cross-component alignment does not map onto CASE A-D.",
        "note": "CASE E. STOP.",
    }


def slim_component(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if k != "CONSENSUS_DAILY"}
