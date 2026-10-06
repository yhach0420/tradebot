"""Parity, 9-rep median, predicted-frontier capture CASE A-E. No selection policy."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np

from research.am_constrained_execution_architecture_reassessment import (
    MAJORITY_RATE,
    PARITY_ABS_TOL,
    PARITY_EXPECTED,
)
from research.am_constrained_execution_architecture_reassessment.geometry import evaluate_scored
from research.am_direct_exec_u_development.oof import process_am_direct
from research.am_wait5_two_stage_development.analyze import _med_key
from research.canonical_entry_performance_rebase.analyze import _f
from research.direct_joint_objective import ELIGIBLE_DAYS
from research.passive_wait_policy_reassessment.analyze import _close, _median


def freeze_parity(obs: dict[str, Any]) -> dict[str, Any]:
    checks = {k: _close(obs.get(k), exp, PARITY_ABS_TOL) for k, exp in PARITY_EXPECTED.items()}
    return {"ok": all(checks.values()), "checks": checks, "observed": obs, "expected": dict(PARITY_EXPECTED)}


def process_constrained(payload: dict[str, Any]) -> dict[str, Any]:
    body = process_am_direct({**payload, "keep_scored": True})
    if not body.get("ok"):
        return body
    scored = list(body.pop("scored") or [])
    geo = evaluate_scored(scored, [str(d) for d in (payload.get("days") or ELIGIBLE_DAYS)])
    join = dict(geo.pop("join") or {})
    join["JOIN_MISS_N"] = int((body.get("integrity") or {}).get("JOIN_FILL_SCORE_MISS_N") or 0)
    body.pop("selected", None)
    body["geometry"] = geo
    body["join"] = join
    return body


def spec_rows(bodies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for b in bodies:
        g = b.get("geometry") or {}
        fo = b.get("FILL_ONLY") or {}
        du = b.get("DIRECT_EXEC_U") or {}
        rec = {
            "representation_id": b.get("representation_id"),
            "feature_set": b.get("feature_set"),
            "normalization": b.get("normalization"),
            "outer_folds": b.get("outer_folds"),
            "COHORT_N": g.get("COHORT_N"),
            "FILL_ONLY_FILL_RATE_W5": fo.get("SELECTED_FILL_RATE")
            if fo.get("SELECTED_FILL_RATE") is not None
            else g.get("FILL_ONLY_FILL_RATE"),
            "DIRECT_FILL_RATE_W5": du.get("SELECTED_FILL_RATE")
            if du.get("SELECTED_FILL_RATE") is not None
            else g.get("DIRECT_FILL_RATE"),
            "DIRECT_MINUS_FILL_ONLY_W5": g.get("DELTA_FILL"),
            "DIRECT_EXEC_U_DELTA_W5": g.get("DELTA_EXEC_U"),
            "DIRECT_EXEC_D_DELTA_W5": g.get("DELTA_EXEC_D"),
            "FILL_ONLY_EXEC_U": fo.get("EXEC_U") if fo.get("EXEC_U") is not None else g.get("FILL_ONLY_EXEC_U"),
            "DIRECT_EXEC_U": du.get("EXEC_U") if du.get("EXEC_U") is not None else g.get("DIRECT_EXEC_U"),
            "FILL_ONLY_EXEC_D": fo.get("EXEC_D") if fo.get("EXEC_D") is not None else g.get("FILL_ONLY_EXEC_D"),
            "DIRECT_EXEC_D": du.get("EXEC_D") if du.get("EXEC_D") is not None else g.get("DIRECT_EXEC_D"),
            "ANY_FU_GOOD_COHORT_N": g.get("ANY_FU_GOOD_COHORT_N"),
            "ANY_FU_GOOD_RATE": g.get("ANY_FU_GOOD_RATE"),
            "ANY_FUD_GOOD_COHORT_N": g.get("ANY_FUD_GOOD_COHORT_N"),
            "ANY_FUD_GOOD_RATE": g.get("ANY_FUD_GOOD_RATE"),
            "SAME_FILL_U_IMPROVE_RATE": g.get("SAME_FILL_U_IMPROVE_RATE"),
            "FRONTIER_FU_GOOD_COHORT_N": g.get("FRONTIER_FU_GOOD_COHORT_N"),
            "FRONTIER_FU_GOOD_RATE": g.get("FRONTIER_FU_GOOD_RATE"),
            "FRONTIER_FUD_GOOD_COHORT_N": g.get("FRONTIER_FUD_GOOD_COHORT_N"),
            "FRONTIER_FUD_GOOD_RATE": g.get("FRONTIER_FUD_GOOD_RATE"),
            "FU_FRONTIER_CAPTURE_RATIO": g.get("FU_FRONTIER_CAPTURE_RATIO"),
            "FUD_FRONTIER_CAPTURE_RATIO": g.get("FUD_FRONTIER_CAPTURE_RATIO"),
            "PRED_FRONTIER_SIZE_MEAN": g.get("PRED_FRONTIER_SIZE_MEAN"),
            "PRED_FRONTIER_SIZE_MEDIAN": g.get("PRED_FRONTIER_SIZE_MEDIAN"),
            "PRED_FRONTIER_SIZE_P75": g.get("PRED_FRONTIER_SIZE_P75"),
            "PRED_FRONTIER_SIZE_P90": g.get("PRED_FRONTIER_SIZE_P90"),
            "FRONTIER_TOO_LARGE_RATE": g.get("FRONTIER_TOO_LARGE_RATE"),
            "FILL_ONLY_ON_FRONTIER_RATE": g.get("FILL_ONLY_ON_FRONTIER_RATE"),
            "DIRECT_ON_FRONTIER_RATE": g.get("DIRECT_ON_FRONTIER_RATE"),
            "FU_GOOD_FILL_PERCENTILE_MEDIAN": g.get("FU_GOOD_FILL_PERCENTILE_MEDIAN"),
            "FU_GOOD_EXECU_PERCENTILE_MEDIAN": g.get("FU_GOOD_EXECU_PERCENTILE_MEDIAN"),
            "FUD_GOOD_FILL_PERCENTILE_MEDIAN": g.get("FUD_GOOD_FILL_PERCENTILE_MEDIAN"),
            "FUD_GOOD_EXECU_PERCENTILE_MEDIAN": g.get("FUD_GOOD_EXECU_PERCENTILE_MEDIAN"),
            "PRED_FILL_EXECU_SPEARMAN_OVERALL": g.get("PRED_FILL_EXECU_SPEARMAN_OVERALL"),
            "PRED_FILL_EXECU_SPEARMAN_DAILY_MEDIAN": g.get("PRED_FILL_EXECU_SPEARMAN_DAILY_MEDIAN"),
            "PRED_FILL_EXECU_SPEARMAN_COHORT_MEDIAN": g.get("PRED_FILL_EXECU_SPEARMAN_COHORT_MEDIAN"),
            "SUBSET_ENUMERATION_ERROR_N": g.get("SUBSET_ENUMERATION_ERROR_N"),
            "JOIN_MISS_N": (b.get("join") or {}).get("JOIN_MISS_N"),
            "DUPLICATE_KEY_N": (b.get("join") or {}).get("DUPLICATE_KEY_N"),
        }
        out.append(rec)
    return out


def daily_consensus(bodies: list[dict[str, Any]], key: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by: dict[str, list[float]] = defaultdict(list)
    for b in bodies:
        for rec in (b.get("geometry") or {}).get("daily") or []:
            v = _f(rec.get(key))
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
    return daily, {
        "median": _median(xs),
        "positive_days": sum(1 for v in xs if v > 0),
        "negative_days": sum(1 for v in xs if v < 0),
        "zero_days": sum(1 for v in xs if v == 0),
        "n_days": len(xs),
    }


def _ge_majority(v: Optional[float]) -> bool:
    return v is not None and float(v) + 1e-15 >= float(MAJORITY_RATE)


def _lt_majority(v: Optional[float]) -> bool:
    return v is not None and float(v) < float(MAJORITY_RATE)


def decide(
    *,
    parity_ok: bool,
    leak: dict[str, Any],
    any_fu: Optional[float],
    any_fud: Optional[float],
    frontier_fu: Optional[float],
    frontier_fud: Optional[float],
) -> dict[str, Any]:
    must0 = (
        "MODEL_REFIT_N",
        "PM_ROWS_USED_N",
        "FUTURE_EVENT_USE_N",
        "TARGET_CONTAMINATION_N",
        "HELDOUT_FIT_LEAK_N",
        "SELECTION_REOPTIMIZATION_N",
        "WAIT_SEARCH_N",
        "ORACLE_SELECTION_USE_N",
        "POLICY_FROM_PARETO_N",
        "SUBSET_ENUMERATION_ERROR_N",
        "JOIN_MISS_N",
        "DUPLICATE_KEY_N",
    )
    if (not parity_ok) or any(int(leak.get(k) or 0) != 0 for k in must0):
        return {
            "CASE": "E",
            "PRIMARY_MECHANISM": "INTEGRITY_FAIL",
            "NEXT_RESEARCH": "NONE",
            "VERDICT": "AM_CONSTRAINED_GEOMETRY_INTEGRITY_FAILED",
            "PRIMARY_FINDING": "Parity, join, isolation, or subset-enumeration integrity failed.",
            "note": "CASE E. STOP.",
        }
    if _ge_majority(frontier_fu) and _ge_majority(frontier_fud):
        return {
            "CASE": "A",
            "PRIMARY_MECHANISM": "EXISTING_ACCESS_AND_UPSIDE_SIGNALS_IDENTIFY_FEASIBLE_REGION",
            "NEXT_RESEARCH": "AM_CONSTRAINED_DUAL_SIGNAL_SELECTION_PRECOMMIT",
            "VERDICT": "AM_DUAL_SIGNAL_CONSTRAINT_GEOMETRY_SUPPORTED",
            "PRIMARY_FINDING": (
                "Predicted P_FILL5 / pred_EXEC_U non-dominated Top3 subsets capture actual "
                "FU_GOOD and FUD_GOOD sets in a majority of AM cohorts. No new target is required."
            ),
            "note": "CASE A. STOP. No constrained selection rule this run.",
        }
    if _ge_majority(frontier_fu) and _lt_majority(frontier_fud):
        return {
            "CASE": "B",
            "PRIMARY_MECHANISM": "UPSIDE_FEASIBLE_BUT_DOWNSIDE_NOT_IDENTIFIED",
            "NEXT_RESEARCH": "AM_ENTRY_EXIT_COUPLING_REASSESSMENT",
            "VERDICT": "AM_DUAL_SIGNAL_UPSIDE_ONLY_GEOMETRY",
            "PRIMARY_FINDING": (
                "Predicted dual-signal frontier captures fill-nonworse EXEC_U improvement, "
                "but FUD_GOOD is not majority-identified. D model addition is forbidden."
            ),
            "note": "CASE B. STOP. No D model.",
        }
    if _ge_majority(any_fu) and _lt_majority(frontier_fu):
        return {
            "CASE": "C",
            "PRIMARY_MECHANISM": "FEASIBLE_SET_EXISTS_BUT_CURRENT_PREDICTIONS_DO_NOT_IDENTIFY_IT",
            "NEXT_RESEARCH": "AM_ENTRY_INFORMATION_REASSESSMENT",
            "VERDICT": "AM_CURRENT_SIGNALS_CANNOT_IDENTIFY_JOINT_SET",
            "PRIMARY_FINDING": (
                "Actual joint-good Top3 sets exist in a majority of cohorts, but they are not "
                "on the predicted P_FILL5 / pred_EXEC_U Pareto frontier. No new scalar target."
            ),
            "note": "CASE C. STOP. No new scalar target.",
        }
    return {
        "CASE": "D",
        "PRIMARY_MECHANISM": "CONSTRAINED_GEOMETRY_MIXED",
        "NEXT_RESEARCH": "AM_ENTRY_ARCHITECTURE_HOLD",
        "VERDICT": "AM_CONSTRAINED_ARCHITECTURE_INCONCLUSIVE",
        "PRIMARY_FINDING": "Predicted dual-signal geometry is mixed or borderline under the 0.50 convention.",
        "note": "CASE D. STOP. No selection rule. No new target.",
        "any_fu": any_fu,
        "any_fud": any_fud,
        "frontier_fu": frontier_fu,
        "frontier_fud": frontier_fud,
    }
