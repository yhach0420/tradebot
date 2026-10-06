"""AM two-stage interface contract. No training. No performance. No shortlist search."""
from __future__ import annotations

from typing import Any

from research.am_wait5_two_stage_interface_precommit import (
    ALLOWED_DEVELOPMENT_ARMS,
    CONTROL_ARM,
    FILL_ONLY_AND_TWO_STAGE_DISTINCT_BY_CONSTRUCTION,
    FILL_ONLY_ARM,
    FINAL_SELECTION_N,
    NEXT_RESEARCH,
    PRIOR_ANALYSIS_ID_REQUIRED,
    PRIOR_VERDICT_REQUIRED,
    PROBABILITY_THRESHOLD_ALLOWED,
    QUALITY_COMBINATION,
    REPRESENTATION_N,
    SHORTLIST_SEARCH_ALLOWED,
    STAGE1_SCORE,
    STAGE1_SHORTLIST_N,
    STAGE1_SHORTLIST_RULE,
    STAGE1_TARGET,
    STAGE2_D_TARGET,
    STAGE2_SELECTION_RULE,
    STAGE2_U_TARGET,
    TWO_STAGE_ARM,
    U_D_WEIGHT_SEARCH_ALLOWED,
)
from research.am_wait5_two_stage_objective_precommit.analyze import (
    joint_quality_tuple,
    percentile_ranks,
)
from research.canonical_entry_performance_rebase.analyze import rank_group


def stage2_sort_key(p_fill5: float, u_rank: float, d_rank: float, symbol: str) -> tuple:
    """Quality first inside the Stage1 shortlist. Not P_FILL5-first Top3."""
    joint, mean_ud = joint_quality_tuple(u_rank, d_rank)
    return (-float(joint), -float(mean_ud), -float(p_fill5), str(symbol or ""))


def fill_only_select(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = rank_group(rows, "P_FILL5")
    return [dict(r) for r in ranked[: int(FINAL_SELECTION_N)]]


def two_stage_select(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    short = [dict(r) for r in rank_group(rows, "P_FILL5")[: int(STAGE1_SHORTLIST_N)]]
    u_ranks = percentile_ranks([(float(r["pred_U"]), str(r.get("symbol") or "")) for r in short])
    d_ranks = percentile_ranks([(float(r["pred_D"]), str(r.get("symbol") or "")) for r in short])
    scored = []
    for r, ur, dr in zip(short, u_ranks, d_ranks):
        joint, mean_ud = joint_quality_tuple(ur, dr)
        rec = dict(r)
        rec["U_RANK"] = ur
        rec["D_RANK"] = dr
        rec["JOINT_QUALITY_SCORE"] = joint
        rec["TIE_MEAN_UD_RANK"] = mean_ud
        scored.append(rec)
    scored.sort(key=lambda r: stage2_sort_key(r["P_FILL5"], r["U_RANK"], r["D_RANK"], r.get("symbol") or ""))
    for i, r in enumerate(scored, start=1):
        r["STAGE2_ORDER"] = i
        r["SELECTED_TOP3"] = i <= int(FINAL_SELECTION_N)
    return scored[: int(FINAL_SELECTION_N)], scored


def distinctness_example() -> dict[str, Any]:
    """Synthetic construction check only. Not a performance evaluation."""
    rows = [
        {"symbol": "A_HIGH_FILL_LOW_Q", "P_FILL5": 0.90, "pred_U": 0.10, "pred_D": 0.10},
        {"symbol": "B_HIGH_FILL_LOW_Q", "P_FILL5": 0.80, "pred_U": 0.20, "pred_D": 0.20},
        {"symbol": "C_MID_FILL_MID_Q", "P_FILL5": 0.70, "pred_U": 0.30, "pred_D": 0.30},
        {"symbol": "D_LOW_FILL_HIGH_Q", "P_FILL5": 0.60, "pred_U": 0.90, "pred_D": 0.90},
        {"symbol": "E_LOW_FILL_HIGH_Q", "P_FILL5": 0.50, "pred_U": 0.80, "pred_D": 0.80},
    ]
    fill_only = fill_only_select(rows)
    two_stage, short_scored = two_stage_select(rows)
    fo = [r["symbol"] for r in fill_only]
    ts = [r["symbol"] for r in two_stage]
    return {
        "FILL_ONLY_SYMBOLS": fo,
        "TWO_STAGE_SYMBOLS": ts,
        "SHORTLIST_SYMBOLS": [r["symbol"] for r in short_scored],
        "SETS_EQUAL": fo == ts,
        "DISTINCT_BY_CONSTRUCTION": fo != ts,
        "fill_only": fill_only,
        "two_stage_shortlist": short_scored,
        "two_stage_selected": two_stage,
        "note": (
            "If Stage1 P_FILL5 were the final Top3 key, TWO_STAGE would equal FILL_ONLY "
            "whenever P_FILL5 is untied. Shortlist Top5 then quality Top3 makes the arms distinct."
        ),
    }


def freeze_prior(preg: dict[str, Any], analysis_id: str) -> dict[str, Any]:
    obs = {
        "PRIOR_ANALYSIS_ID": analysis_id,
        "VERDICT": preg.get("VERDICT"),
        "SESSION": preg.get("SESSION"),
        "STAGE1_TARGET": preg.get("STAGE1_TARGET"),
        "STAGE2_U_TARGET": preg.get("STAGE2_U_TARGET"),
        "STAGE2_D_TARGET": preg.get("STAGE2_D_TARGET"),
        "QUALITY_COMBINATION": preg.get("QUALITY_COMBINATION"),
        "U_D_WEIGHT_SEARCH_ALLOWED": preg.get("U_D_WEIGHT_SEARCH_ALLOWED"),
        "STAGE1_STAGE2_SINGLE_SCALAR_ALLOWED": preg.get("STAGE1_STAGE2_SINGLE_SCALAR_ALLOWED"),
        "COMMON_AM_PM_MODEL_ALLOWED": preg.get("COMMON_AM_PM_MODEL_ALLOWED"),
    }
    checks = {
        "PRIOR_ANALYSIS_ID": str(obs["PRIOR_ANALYSIS_ID"] or "") == PRIOR_ANALYSIS_ID_REQUIRED,
        "VERDICT": obs["VERDICT"] == PRIOR_VERDICT_REQUIRED,
        "SESSION": obs["SESSION"] == "AM",
        "STAGE1_TARGET": obs["STAGE1_TARGET"] == STAGE1_TARGET,
        "STAGE2_U_TARGET": obs["STAGE2_U_TARGET"] == STAGE2_U_TARGET,
        "STAGE2_D_TARGET": obs["STAGE2_D_TARGET"] == STAGE2_D_TARGET,
        "QUALITY_COMBINATION": obs["QUALITY_COMBINATION"] == QUALITY_COMBINATION,
        "U_D_WEIGHT_SEARCH_ALLOWED": obs["U_D_WEIGHT_SEARCH_ALLOWED"] is False,
        "STAGE1_STAGE2_SINGLE_SCALAR_ALLOWED": obs["STAGE1_STAGE2_SINGLE_SCALAR_ALLOWED"] is False,
        "COMMON_AM_PM_MODEL_ALLOWED": obs["COMMON_AM_PM_MODEL_ALLOWED"] is False,
    }
    return {"ok": all(checks.values()), "checks": checks, "observed": obs}


def interface_contract() -> dict[str, Any]:
    return {
        "SUPERSEDED_INTERFACE": "P_FILL5 descending then JOINT_QUALITY_SCORE as direct Top3",
        "SUPERSEDE_REASON": (
            "If P_FILL5 is untied, that ordering makes TWO_STAGE identical to FILL_ONLY, "
            "so Stage2 quality has no incremental contribution to verify."
        ),
        "OBJECTIVE_RETAINED": True,
        "STAGE1_TARGET": STAGE1_TARGET,
        "STAGE1_SCORE": STAGE1_SCORE,
        "STAGE1_SHORTLIST_N": STAGE1_SHORTLIST_N,
        "STAGE1_SHORTLIST_RULE": STAGE1_SHORTLIST_RULE,
        "STAGE1_SHORTLIST_TIE": "symbol ASC",
        "STAGE1_SHORTLIST_IS_ARCHITECTURE_CONSTANT": True,
        "STAGE1_SHORTLIST_NOT_CLAIMED_OPTIMAL": True,
        "PERCENTILE_RANK_POPULATION": "Stage1 Top5 shortlist only",
        "U_RANK": "percentile rank(pred_U_FILL) inside shortlist; higher=better; midrank (i+0.5)/n",
        "D_RANK": "percentile rank(pred_D_FILL) inside shortlist; higher=better; midrank (i+0.5)/n",
        "JOINT_QUALITY_SCORE": "min(U_RANK, D_RANK)",
        "STAGE2_RANK_1": "JOINT_QUALITY_SCORE descending",
        "STAGE2_RANK_2": "mean(U_RANK, D_RANK) descending",
        "STAGE2_RANK_3": "P_FILL5 descending",
        "STAGE2_RANK_4": "symbol ASC",
        "STAGE2_SELECTION_RULE": STAGE2_SELECTION_RULE,
        "FINAL_SELECTION_N": FINAL_SELECTION_N,
        "QUALITY_COMBINATION": QUALITY_COMBINATION,
        "CONTROL_ARM": CONTROL_ARM,
        "FILL_ONLY_ARM": FILL_ONLY_ARM,
        "TWO_STAGE_ARM": TWO_STAGE_ARM,
        "ALLOWED_DEVELOPMENT_ARMS": list(ALLOWED_DEVELOPMENT_ARMS),
        "SHORTLIST_SEARCH_ALLOWED": SHORTLIST_SEARCH_ALLOWED,
        "PROBABILITY_THRESHOLD_ALLOWED": PROBABILITY_THRESHOLD_ALLOWED,
        "U_D_WEIGHT_SEARCH_ALLOWED": U_D_WEIGHT_SEARCH_ALLOWED,
        "U_ONLY_STRATEGY_ALLOWED": False,
        "D_ONLY_STRATEGY_ALLOWED": False,
        "WEIGHTED_UD_STRATEGY_ALLOWED": False,
        "P_FILL5_TIMES_QUALITY_ALLOWED": False,
        "PARETO_STRATEGY_ALLOWED": False,
        "BEST_REPRESENTATION_ADOPTED": False,
        "REPRESENTATION_N": REPRESENTATION_N,
        "REPRESENTATION_EVAL": "median / consensus. No cherry-pick.",
        "FILL_ONLY_AND_TWO_STAGE_DISTINCT_BY_CONSTRUCTION": (
            FILL_ONLY_AND_TWO_STAGE_DISTINCT_BY_CONSTRUCTION
        ),
    }


def decide(*, pop_ok: bool, prior_ok: bool, distinct_ok: bool) -> dict[str, Any]:
    if not pop_ok or not prior_ok or not distinct_ok:
        return {
            "CASE": "FAIL",
            "VERDICT": "AM_WAIT5_TWO_STAGE_INTERFACE_INTEGRITY_FAILED",
            "NEXT_RESEARCH": "NONE",
            "PRIMARY_FINDING": "Frozen AM population, prior objective, or distinct-arm construction failed.",
            "note": "STOP. Integrity failed. Interface not precommitted.",
        }
    return {
        "CASE": "A",
        "VERDICT": "AM_WAIT5_TWO_STAGE_INTERFACE_PRECOMMITTED",
        "NEXT_RESEARCH": NEXT_RESEARCH,
        "PRIMARY_FINDING": (
            "AM W5 two-stage interface is P_FILL5 Top5 shortlist then min-percentile-rank "
            "joint quality Top3 inside that shortlist. FILL_ONLY and TWO_STAGE are distinct "
            "by construction. Next development run may compare only CONTROL, FILL_ONLY, and "
            "TWO_STAGE. Runtime WAIT remains 1.0."
        ),
        "note": "STOP. No performance run. No W5 runtime adoption.",
    }
