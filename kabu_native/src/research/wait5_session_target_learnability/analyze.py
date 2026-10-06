"""Parity, 9-rep median/consensus gates, AM CASE A-D, PM CASE A-B. No selection."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np

from research.canonical_entry_performance_rebase.analyze import _f, session_of
from research.direct_joint_objective import ELIGIBLE_DAYS
from research.entry_execution_feasibility.analyze import labeled_rows
from research.entry_rank_shape_audit.oof import delta_series_stats
from research.passive_wait_policy_reassessment.analyze import _close, _median, _rate
from research.wait5_session_architecture_precommit.analyze import pm_top1_fill, sess_rows
from research.wait5_session_target_learnability import (
    AM_TOPK,
    PARITY_ABS_TOL,
    PARITY_EXPECTED,
    PM_TOPK,
    POS_REP_MIN,
)
from research.wait5_session_target_learnability.oof import admission_fill, quality_eval


def freeze_parity(
    *,
    am: list[dict[str, Any]],
    pm: list[dict[str, Any]],
    am_top3: dict[str, Any],
    pm_top1: dict[str, Any],
) -> dict[str, Any]:
    am_n = len(am)
    pm_n = len(pm)
    am_pos = sum(1 for r in am if int(r.get("Y_FILL5") or 0) == 1)
    pm_pos = sum(1 for r in pm if int(r.get("Y_FILL5") or 0) == 1)
    obs = {
        "AM_LABELED_N": am_n,
        "AM_Y_FILL5_POS_N": am_pos,
        "AM_Y_FILL5_RATE": _rate(am_pos, am_n),
        "PM_LABELED_N": pm_n,
        "PM_Y_FILL5_POS_N": pm_pos,
        "PM_Y_FILL5_RATE": _rate(pm_pos, pm_n),
        "AM_CURRENT_TOP3_FILL5_RATE": am_top3.get("FILL_RATE"),
        "PM_CURRENT_TOP1_FILL_RATE": pm_top1.get("PM_CURRENT_TOP1_FILL_RATE"),
        "AM_CONDITIONAL_FILL_N": am_pos,
    }
    checks = {
        "AM_LABELED_N": int(obs["AM_LABELED_N"]) == int(PARITY_EXPECTED["AM_LABELED_N"]),
        "AM_Y_FILL5_POS_N": int(obs["AM_Y_FILL5_POS_N"]) == int(PARITY_EXPECTED["AM_Y_FILL5_POS_N"]),
        "AM_Y_FILL5_RATE": _close(obs["AM_Y_FILL5_RATE"], PARITY_EXPECTED["AM_Y_FILL5_RATE"], PARITY_ABS_TOL),
        "PM_LABELED_N": int(obs["PM_LABELED_N"]) == int(PARITY_EXPECTED["PM_LABELED_N"]),
        "PM_Y_FILL5_POS_N": int(obs["PM_Y_FILL5_POS_N"]) == int(PARITY_EXPECTED["PM_Y_FILL5_POS_N"]),
        "PM_Y_FILL5_RATE": _close(obs["PM_Y_FILL5_RATE"], PARITY_EXPECTED["PM_Y_FILL5_RATE"], PARITY_ABS_TOL),
        "AM_CURRENT_TOP3_FILL5_RATE": _close(
            obs["AM_CURRENT_TOP3_FILL5_RATE"], PARITY_EXPECTED["AM_CURRENT_TOP3_FILL5_RATE"], PARITY_ABS_TOL
        ),
        "PM_CURRENT_TOP1_FILL_RATE": _close(
            obs["PM_CURRENT_TOP1_FILL_RATE"], PARITY_EXPECTED["PM_CURRENT_TOP1_FILL_RATE"], PARITY_ABS_TOL
        ),
        "AM_CONDITIONAL_FILL_N": int(obs["AM_CONDITIONAL_FILL_N"]) == int(PARITY_EXPECTED["AM_CONDITIONAL_FILL_N"]),
    }
    return {"BASE_PARITY": all(checks.values()), "checks": checks, "expected": dict(PARITY_EXPECTED), "observed": obs}


def current_refs(rows: list[dict[str, Any]]) -> dict[str, Any]:
    am = sess_rows(rows, "AM")
    pm = sess_rows(rows, "PM")
    am_top3 = admission_fill(am, "current_score", int(AM_TOPK))
    pm_adm = pm_top1_fill(rows)
    pm_top1 = admission_fill(pm, "current_score", int(PM_TOPK))
    am_u = quality_eval(am, "current_score", "U_FILL")
    am_d = quality_eval(am, "current_score", "D_FILL")
    return {
        "am": am,
        "pm": pm,
        "am_top3": am_top3,
        "pm_top1_pack": pm_adm,
        "pm_top1": pm_top1,
        "am_u": am_u,
        "am_d": am_d,
    }


def _daily_map(rows: list[dict[str, Any]], value_key: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for rec in rows or []:
        v = _f(rec.get(value_key))
        if v is None:
            continue
        out[str(rec.get("date"))] = float(v)
    return out


def _consensus_daily(bodies: list[dict[str, Any]], daily_key: str, value_key: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by: dict[str, list[float]] = defaultdict(list)
    for b in bodies:
        for rec in b.get(daily_key) or []:
            v = _f(rec.get(value_key))
            if v is None:
                continue
            by[str(rec.get("date"))].append(float(v))
    daily = []
    xs: list[float] = []
    for d in ELIGIBLE_DAYS:
        vals = by.get(d) or []
        if not vals:
            continue
        med = float(np.median(vals))
        xs.append(med)
        daily.append({"date": d, "CONSENSUS": med, "REP_N": len(vals)})
    return daily, delta_series_stats(xs) if xs else {
        "mean": None,
        "median": None,
        "positive_days": 0,
        "negative_days": 0,
        "ex_best_day": None,
        "ex_top3_days": None,
        "n_days": 0,
    }


def fill_spec_rows(
    bodies: list[dict[str, Any]],
    *,
    current_rate: Optional[float],
    current_daily: dict[str, float],
    k_label: str,
) -> list[dict[str, Any]]:
    out = []
    for b in bodies:
        model = _f(b.get("MODEL_FILL_RATE"))
        delta = None if model is None or current_rate is None else float(model) - float(current_rate)
        day_delta = []
        mmap = _daily_map(b.get("daily_fill") or [], "FILL_RATE")
        for d in ELIGIBLE_DAYS:
            mv = mmap.get(d)
            cv = current_daily.get(d)
            if mv is None or cv is None:
                continue
            day_delta.append({"date": d, "value": float(mv) - float(cv)})
        dst = delta_series_stats([float(r["value"]) for r in day_delta]) if day_delta else {}
        rec = {
            "representation_id": b.get("representation_id"),
            "feature_set": b.get("feature_set"),
            "normalization": b.get("normalization"),
            f"MODEL_{k_label}_FILL_RATE": model,
            f"CURRENT_{k_label}_FILL_RATE": current_rate,
            f"DELTA_{k_label}_FILL_RATE": delta,
            "ANY_FILL_COHORT_RATE": b.get("ANY_FILL_COHORT_RATE"),
            "MEAN_FILLS_SELECTED_PER_COHORT": b.get("MEAN_FILLS_SELECTED_PER_COHORT"),
            "ROC_AUC": b.get("ROC_AUC"),
            "AVERAGE_PRECISION": b.get("AVERAGE_PRECISION"),
            "COHORT_N": b.get("COHORT_N"),
            "POSITIVE_DAYS": dst.get("positive_days"),
            "NEGATIVE_DAYS": dst.get("negative_days"),
            "EX_BEST_DAY": dst.get("ex_best_day"),
            "EX_TOP3_DAYS": dst.get("ex_top3_days"),
            "daily_delta": day_delta,
        }
        out.append(rec)
    return out


def fill_aggregate(spec_rows: list[dict[str, Any]], *, k_label: str) -> dict[str, Any]:
    key = f"DELTA_{k_label}_FILL_RATE"
    deltas = [float(v) for r in spec_rows if (v := _f(r.get(key))) is not None]
    models = [float(v) for r in spec_rows if (v := _f(r.get(f"MODEL_{k_label}_FILL_RATE"))) is not None]
    pos_rep = sum(1 for v in deltas if v > 0)
    by: dict[str, list[float]] = defaultdict(list)
    for r in spec_rows:
        for rec in r.get("daily_delta") or []:
            v = _f(rec.get("value"))
            if v is None:
                continue
            by[str(rec.get("date"))].append(float(v))
    cons = []
    daily = []
    for d in ELIGIBLE_DAYS:
        xs = by.get(d) or []
        if not xs:
            continue
        med = float(np.median(xs))
        cons.append(med)
        daily.append({"date": d, "CONSENSUS_DELTA": med, "REP_N": len(xs)})
    st = delta_series_stats(cons) if cons else {
        "mean": None,
        "median": None,
        "positive_days": 0,
        "negative_days": 0,
        "ex_best_day": None,
        "ex_top3_days": None,
        "n_days": 0,
    }
    med_delta = _median(deltas)
    pos_days = int(st.get("positive_days") or 0)
    neg_days = int(st.get("negative_days") or 0)
    gates = {
        "A_MEDIAN_DELTA_GT_0": bool(med_delta is not None and med_delta > 0),
        "B_POSITIVE_REP_GE_6": bool(pos_rep >= int(POS_REP_MIN)),
        "C_CONSENSUS_POS_GT_NEG": bool(pos_days > neg_days),
        "D_CONSENSUS_EX_BEST_GT_0": bool(st.get("ex_best_day") is not None and float(st["ex_best_day"]) > 0),
        "E_CONSENSUS_EX_TOP3_GE_0": bool(
            st.get("ex_top3_days") is not None and float(st["ex_top3_days"]) + 1e-12 >= 0.0
        ),
    }
    learnable = all(gates.values())
    return {
        "REPRESENTATION_N": len(spec_rows),
        "MEDIAN_MODEL_FILL_RATE": _median(models),
        "MEDIAN_DELTA": med_delta,
        "POSITIVE_REP_N": pos_rep,
        "CONSENSUS_POS_DAYS": pos_days,
        "CONSENSUS_NEG_DAYS": neg_days,
        "CONSENSUS_EX_BEST_DAY": st.get("ex_best_day"),
        "CONSENSUS_EX_TOP3_DAYS": st.get("ex_top3_days"),
        "MEDIAN_ROC_AUC": _median([float(v) for r in spec_rows if (v := _f(r.get("ROC_AUC"))) is not None]),
        "MEDIAN_AVERAGE_PRECISION": _median(
            [float(v) for r in spec_rows if (v := _f(r.get("AVERAGE_PRECISION"))) is not None]
        ),
        "gates": gates,
        "LEARNABLE": learnable,
        "daily": daily,
        "spec_rows": spec_rows,
    }


def quality_spec_rows(bodies: list[dict[str, Any]], *, which: str) -> list[dict[str, Any]]:
    key = "u_quality" if which == "U" else "d_quality"
    out = []
    for b in bodies:
        q = dict(b.get(key) or {})
        out.append(
            {
                "representation_id": b.get("representation_id"),
                "feature_set": b.get("feature_set"),
                "normalization": b.get("normalization"),
                "OVERALL_SPEARMAN": q.get("OVERALL_SPEARMAN"),
                "EVAL_N": q.get("EVAL_N"),
                "EVAL_COHORT_N": q.get("EVAL_COHORT_N"),
                "TOP1_UPLIFT": q.get("TOP1_UPLIFT"),
                "MEDIAN_DAILY_SPEARMAN": q.get("MEDIAN_DAILY_SPEARMAN"),
                "MEDIAN_DAILY_TOP1_UPLIFT": q.get("MEDIAN_DAILY_TOP1_UPLIFT"),
                "POSITIVE_DAYS": q.get("POSITIVE_DAYS"),
                "NEGATIVE_DAYS": q.get("NEGATIVE_DAYS"),
                "EX_BEST_DAY": q.get("EX_BEST_DAY"),
                "EX_TOP3_DAYS": q.get("EX_TOP3_DAYS"),
                "daily": q.get("daily") or [],
            }
        )
    return out


def quality_aggregate(spec_rows: list[dict[str, Any]], *, current: dict[str, Any]) -> dict[str, Any]:
    spears = [float(v) for r in spec_rows if (v := _f(r.get("OVERALL_SPEARMAN"))) is not None]
    upls = [float(v) for r in spec_rows if (v := _f(r.get("TOP1_UPLIFT"))) is not None]
    pos_rep = sum(1 for v in spears if v > 0)
    cons_daily, st = _consensus_daily(spec_rows, "daily", "SPEARMAN")
    med_sp = _median(spears)
    cur_sp = _f(current.get("OVERALL_SPEARMAN"))
    pos_days = int(st.get("positive_days") or 0)
    neg_days = int(st.get("negative_days") or 0)
    med_upl = _median(upls)
    gates = {
        "A_MEDIAN_SPEARMAN_GT_0": bool(med_sp is not None and med_sp > 0),
        "B_MEDIAN_SPEARMAN_GT_CURRENT": bool(
            med_sp is not None and cur_sp is not None and float(med_sp) > float(cur_sp)
        ),
        "C_POSITIVE_REP_GE_6": bool(pos_rep >= int(POS_REP_MIN)),
        "D_CONSENSUS_POS_GT_NEG": bool(pos_days > neg_days),
        "E_CONSENSUS_EX_BEST_GT_0": bool(st.get("ex_best_day") is not None and float(st["ex_best_day"]) > 0),
        "F_CONSENSUS_EX_TOP3_GE_0": bool(
            st.get("ex_top3_days") is not None and float(st["ex_top3_days"]) + 1e-12 >= 0.0
        ),
        "G_MEDIAN_TOP1_UPLIFT_GT_0": bool(med_upl is not None and med_upl > 0),
    }
    return {
        "REPRESENTATION_N": len(spec_rows),
        "MEDIAN_SPEARMAN": med_sp,
        "CURRENT_SPEARMAN": cur_sp,
        "CURRENT_TOP1_UPLIFT": current.get("TOP1_UPLIFT"),
        "POSITIVE_REP_N": pos_rep,
        "MEDIAN_TOP1_UPLIFT": med_upl,
        "CONSENSUS_POS_DAYS": pos_days,
        "CONSENSUS_NEG_DAYS": neg_days,
        "CONSENSUS_EX_BEST_DAY": st.get("ex_best_day"),
        "CONSENSUS_EX_TOP3_DAYS": st.get("ex_top3_days"),
        "gates": gates,
        "LEARNABLE": all(gates.values()),
        "daily": cons_daily,
        "spec_rows": spec_rows,
    }


def sum_integrity(bodies: list[dict[str, Any]]) -> dict[str, int]:
    keys = (
        "AM_TRAIN_PM_ROW_N",
        "PM_TRAIN_AM_ROW_N",
        "AM_NORMALIZER_PM_ROW_N",
        "PM_NORMALIZER_AM_ROW_N",
        "FUTURE_EVENT_USE_N",
        "TARGET_CONTAMINATION_N",
        "HELDOUT_FIT_LEAK_N",
    )
    tot = {k: 0 for k in keys}
    for b in bodies:
        leak = b.get("integrity") or {}
        for k in keys:
            tot[k] += int(leak.get(k) or 0)
    return tot


def decide(
    *,
    am_fill: dict[str, Any],
    am_u: dict[str, Any],
    am_d: dict[str, Any],
    pm_fill: dict[str, Any],
) -> dict[str, Any]:
    am_f = bool(am_fill.get("LEARNABLE"))
    u_ok = bool(am_u.get("LEARNABLE"))
    d_ok = bool(am_d.get("LEARNABLE"))
    pm_ok = bool(pm_fill.get("LEARNABLE"))

    if am_f and u_ok and d_ok:
        am_case = "A"
        am_status = "TWO_STAGE_TARGETS_LEARNABLE"
        am_next = "AM_WAIT5_TWO_STAGE_OBJECTIVE_PRECOMMIT"
    elif am_f and (u_ok ^ d_ok):
        am_case = "B"
        am_status = "QUALITY_PARTIALLY_LEARNABLE"
        am_next = "AM_QUALITY_ARCHITECTURE_REASSESSMENT"
    elif am_f and (not u_ok) and (not d_ok):
        am_case = "C"
        am_status = "FILLABILITY_ONLY_LEARNABLE"
        am_next = "AM_FILLABILITY_FIRST_ARCHITECTURE_REASSESSMENT"
    else:
        am_case = "D"
        am_status = "WAIT5_FILLABILITY_NOT_LEARNABLE"
        am_next = "AM_ENTRY_INFORMATION_REASSESSMENT"

    if pm_ok:
        pm_case = "A"
        pm_status = "FILLABILITY_TARGET_LEARNABLE"
        pm_next = "PM_WAIT5_FILLABILITY_PRECOMMIT_DEVELOPMENT"
    else:
        pm_case = "B"
        pm_status = "FILLABILITY_TARGET_NOT_LEARNABLE"
        pm_next = "PM_ENTRY_INFORMATION_REASSESSMENT"

    if am_case == "A" and pm_case == "A":
        overall = "WAIT5_SESSION_TARGETS_LEARNABLE"
    elif am_case == "D" and pm_case == "B":
        overall = "WAIT5_SESSION_TARGETS_NOT_ESTABLISHED"
    else:
        overall = "WAIT5_SESSION_TARGETS_PARTIALLY_LEARNABLE"

    return {
        "AM_CASE": am_case,
        "AM_FILLABILITY_LEARNABLE": am_f,
        "AM_U_FILL_LEARNABLE": u_ok,
        "AM_D_FILL_LEARNABLE": d_ok,
        "AM_TARGET_STATUS": am_status,
        "AM_VERDICT": am_status,
        "AM_NEXT": am_next,
        "PM_CASE": pm_case,
        "PM_FILLABILITY_LEARNABLE": pm_ok,
        "PM_TARGET_STATUS": pm_status,
        "PM_VERDICT": pm_status,
        "PM_NEXT": pm_next,
        "OVERALL_VERDICT": overall,
        "PRIMARY_FINDING": (
            f"AM CASE {am_case} {am_status}. PM CASE {pm_case} {pm_status}. "
            "AM and PM remain independent research tasks. No common model."
        ),
        "note": "STOP. No deployable strategy. Runtime WAIT_SEC remains 1.0.",
    }


def labeled_session(rows: list[dict[str, Any]], sess: str) -> list[dict[str, Any]]:
    return [r for r in labeled_rows(rows) if session_of(r) == sess]
