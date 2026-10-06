"""AM two-stage objective contract. No training. No admission search."""
from __future__ import annotations

from typing import Any

from research.passive_wait_policy_reassessment.analyze import _close, _rate
from research.wait5_session_architecture_precommit.analyze import sess_rows
from research.wait5_session_target_learnability import AM_TOPK
from research.wait5_session_target_learnability.oof import admission_fill
from research.am_wait5_two_stage_objective_precommit import (
    ALLOWED_DEVELOPMENT_ARMS,
    ARCHITECTURE,
    CONTROL_ARM,
    FILL_ONLY_ARM,
    NEXT_RESEARCH,
    PARITY_ABS_TOL,
    PARITY_EXPECTED,
    PRIOR_AM_NEXT_REQUIRED,
    PRIOR_AM_TARGET_STATUS_REQUIRED,
    PRIOR_ANALYSIS_ID_REQUIRED,
    QUALITY_COMBINATION,
    REPRESENTATION_N,
    STAGE1_SCORE,
    STAGE1_STAGE2_SINGLE_SCALAR_ALLOWED,
    STAGE1_TARGET,
    STAGE2_D_TARGET,
    STAGE2_U_TARGET,
    TWO_STAGE_ARM,
    U_D_WEIGHT_SEARCH_ALLOWED,
)


def percentile_ranks(pairs: list[tuple[float, str]]) -> list[float]:
    """Higher value → higher percentile. Tie-break symbol ASC. Midrank (i+0.5)/n."""
    n = len(pairs)
    if n <= 0:
        return []
    order = sorted(range(n), key=lambda i: (float(pairs[i][0]), str(pairs[i][1])))
    out = [0.0] * n
    for rank, i in enumerate(order):
        out[i] = (rank + 0.5) / float(n)
    return out


def joint_quality_tuple(u_rank: float, d_rank: float) -> tuple[float, float]:
    """Primary min(U,D). Tie key mean(U,D). Higher is better."""
    return (min(float(u_rank), float(d_rank)), (float(u_rank) + float(d_rank)) / 2.0)


def two_stage_sort_key(p_fill5: float, u_rank: float, d_rank: float, symbol: str) -> tuple:
    joint, mean_ud = joint_quality_tuple(u_rank, d_rank)
    return (-float(p_fill5), -float(joint), -float(mean_ud), str(symbol or ""))


def maximin_example() -> list[dict[str, Any]]:
    """Synthetic illustration only. Not a strategy evaluation."""
    rows = [
        {"symbol": "STRONG_U", "pred_U": 0.90, "pred_D": 0.10, "P_FILL5": 0.50},
        {"symbol": "BALANCED", "pred_U": 0.55, "pred_D": 0.55, "P_FILL5": 0.50},
        {"symbol": "STRONG_D", "pred_U": 0.10, "pred_D": 0.90, "P_FILL5": 0.50},
    ]
    u_ranks = percentile_ranks([(float(r["pred_U"]), str(r["symbol"])) for r in rows])
    d_ranks = percentile_ranks([(float(r["pred_D"]), str(r["symbol"])) for r in rows])
    out = []
    for r, ur, dr in zip(rows, u_ranks, d_ranks):
        joint, mean_ud = joint_quality_tuple(ur, dr)
        out.append(
            {
                **r,
                "U_RANK": ur,
                "D_RANK": dr,
                "JOINT_QUALITY_SCORE": joint,
                "TIE_MEAN_UD_RANK": mean_ud,
            }
        )
    out.sort(key=lambda r: two_stage_sort_key(r["P_FILL5"], r["U_RANK"], r["D_RANK"], r["symbol"]))
    for i, r in enumerate(out, start=1):
        r["ORDER"] = i
    return out


def freeze_population(*, am: list[dict[str, Any]], am_top3: dict[str, Any]) -> dict[str, Any]:
    am_n = len(am)
    am_pos = sum(1 for r in am if int(r.get("Y_FILL5") or 0) == 1)
    obs = {
        "AM_LABELED_N": am_n,
        "AM_Y_FILL5_POS_N": am_pos,
        "AM_Y_FILL5_RATE": _rate(am_pos, am_n),
        "AM_CURRENT_TOP3_FILL5_RATE": am_top3.get("FILL_RATE"),
        "AM_CONDITIONAL_FILL_N": am_pos,
    }
    checks = {
        "AM_LABELED_N": int(obs["AM_LABELED_N"]) == int(PARITY_EXPECTED["AM_LABELED_N"]),
        "AM_Y_FILL5_POS_N": int(obs["AM_Y_FILL5_POS_N"]) == int(PARITY_EXPECTED["AM_Y_FILL5_POS_N"]),
        "AM_Y_FILL5_RATE": _close(obs["AM_Y_FILL5_RATE"], PARITY_EXPECTED["AM_Y_FILL5_RATE"], PARITY_ABS_TOL),
        "AM_CURRENT_TOP3_FILL5_RATE": _close(
            obs["AM_CURRENT_TOP3_FILL5_RATE"], PARITY_EXPECTED["AM_CURRENT_TOP3_FILL5_RATE"], PARITY_ABS_TOL
        ),
        "AM_CONDITIONAL_FILL_N": int(obs["AM_CONDITIONAL_FILL_N"])
        == int(PARITY_EXPECTED["AM_CONDITIONAL_FILL_N"]),
    }
    return {"ok": all(checks.values()), "checks": checks, "expected": {k: PARITY_EXPECTED[k] for k in obs}, "observed": obs}


def freeze_prior(preg: dict[str, Any], analysis_id: str) -> dict[str, Any]:
    obs = {
        "PRIOR_ANALYSIS_ID": analysis_id,
        "AM_TARGET_STATUS": preg.get("AM_TARGET_STATUS"),
        "AM_NEXT": preg.get("AM_NEXT"),
        "AM_FILLABILITY_LEARNABLE": preg.get("AM_FILLABILITY_LEARNABLE"),
        "AM_U_FILL_LEARNABLE": preg.get("AM_U_FILL_LEARNABLE"),
        "AM_D_FILL_LEARNABLE": preg.get("AM_D_FILL_LEARNABLE"),
        "AM_FILLABILITY_TOP3_DELTA": preg.get("AM_FILLABILITY_TOP3_DELTA"),
        "AM_U_FILL_SPEARMAN": preg.get("AM_U_FILL_SPEARMAN"),
        "AM_D_FILL_SPEARMAN": preg.get("AM_D_FILL_SPEARMAN"),
        "REPRESENTATION_N": preg.get("REPRESENTATION_N"),
        "COMMON_AM_PM_MODEL_ALLOWED": preg.get("COMMON_AM_PM_MODEL_ALLOWED"),
        "COMMON_AM_PM_TARGET_ALLOWED": preg.get("COMMON_AM_PM_TARGET_ALLOWED"),
    }
    checks = {
        "PRIOR_ANALYSIS_ID": str(obs["PRIOR_ANALYSIS_ID"] or "") == PRIOR_ANALYSIS_ID_REQUIRED,
        "AM_TARGET_STATUS": obs["AM_TARGET_STATUS"] == PRIOR_AM_TARGET_STATUS_REQUIRED,
        "AM_NEXT": obs["AM_NEXT"] == PRIOR_AM_NEXT_REQUIRED,
        "AM_FILLABILITY_LEARNABLE": obs["AM_FILLABILITY_LEARNABLE"] is True,
        "AM_U_FILL_LEARNABLE": obs["AM_U_FILL_LEARNABLE"] is True,
        "AM_D_FILL_LEARNABLE": obs["AM_D_FILL_LEARNABLE"] is True,
        "AM_FILLABILITY_TOP3_DELTA": _close(
            obs["AM_FILLABILITY_TOP3_DELTA"], PARITY_EXPECTED["AM_FILLABILITY_TOP3_DELTA"], PARITY_ABS_TOL
        ),
        "AM_U_FILL_SPEARMAN": _close(
            obs["AM_U_FILL_SPEARMAN"], PARITY_EXPECTED["AM_U_FILL_SPEARMAN"], PARITY_ABS_TOL
        ),
        "AM_D_FILL_SPEARMAN": _close(
            obs["AM_D_FILL_SPEARMAN"], PARITY_EXPECTED["AM_D_FILL_SPEARMAN"], PARITY_ABS_TOL
        ),
        "REPRESENTATION_N": int(obs["REPRESENTATION_N"] or -1) == int(REPRESENTATION_N),
        "COMMON_AM_PM_MODEL_ALLOWED": obs["COMMON_AM_PM_MODEL_ALLOWED"] is False,
        "COMMON_AM_PM_TARGET_ALLOWED": obs["COMMON_AM_PM_TARGET_ALLOWED"] is False,
    }
    return {"ok": all(checks.values()), "checks": checks, "observed": obs}


def objective_contract() -> dict[str, Any]:
    return {
        "ARCHITECTURE": ARCHITECTURE,
        "STAGE1_TARGET": STAGE1_TARGET,
        "STAGE1_SCORE": STAGE1_SCORE,
        "STAGE1_ROLE": "Concentrate W5-fillable candidates into the quality ranking population.",
        "STAGE1_MIXES_U": False,
        "STAGE1_MIXES_D": False,
        "STAGE1_MIXES_PNL": False,
        "STAGE2_U_TARGET": STAGE2_U_TARGET,
        "STAGE2_D_TARGET": STAGE2_D_TARGET,
        "STAGE2_U_SOURCE": "POSTFILL_MFE_600",
        "STAGE2_D_SOURCE": "POSTFILL_DOWNSIDE_AVOID_600",
        "STAGE2_MODELS": "independent RandomForestRegressor U and D. No multi-output.",
        "U_RANK": "percentile rank(pred_U_FILL); higher=better; midrank (i+0.5)/n; tie symbol ASC",
        "D_RANK": "percentile rank(pred_D_FILL); higher=better; midrank (i+0.5)/n; tie symbol ASC",
        "JOINT_QUALITY_SCORE": "min(U_RANK, D_RANK)",
        "QUALITY_TIE_1": "mean(U_RANK, D_RANK)",
        "QUALITY_TIE_2": "symbol ASC",
        "QUALITY_COMBINATION": QUALITY_COMBINATION,
        "ORDERING_PRIMARY": "P_FILL5 descending",
        "ORDERING_SECONDARY": "JOINT_QUALITY_SCORE descending",
        "U_D_WEIGHT_SEARCH_ALLOWED": U_D_WEIGHT_SEARCH_ALLOWED,
        "STAGE1_STAGE2_SINGLE_SCALAR_ALLOWED": STAGE1_STAGE2_SINGLE_SCALAR_ALLOWED,
        "STAGE1_THRESHOLD_CREATED": False,
        "TOPN_SHORTLIST_CREATED": False,
        "PROBABILITY_CUTOFF_CREATED": False,
        "ALLOWED_DEVELOPMENT_ARMS": list(ALLOWED_DEVELOPMENT_ARMS),
        "CONTROL_ARM": CONTROL_ARM,
        "FILL_ONLY_ARM": FILL_ONLY_ARM,
        "TWO_STAGE_ARM": TWO_STAGE_ARM,
        "U_ONLY_STRATEGY_ALLOWED": False,
        "D_ONLY_STRATEGY_ALLOWED": False,
        "WEIGHTED_UD_STRATEGY_ALLOWED": False,
        "BEST_REPRESENTATION_ADOPTED": False,
        "REPRESENTATION_N": REPRESENTATION_N,
        "REPRESENTATION_EVAL": "median / consensus. No cherry-pick.",
    }


def decide(*, pop_ok: bool, prior_ok: bool) -> dict[str, Any]:
    if not pop_ok or not prior_ok:
        return {
            "CASE": "FAIL",
            "VERDICT": "AM_WAIT5_TWO_STAGE_OBJECTIVE_INTEGRITY_FAILED",
            "NEXT_RESEARCH": "NONE",
            "PRIMARY_FINDING": "Frozen AM W5 population or prior two-stage learnability did not reproduce.",
            "note": "STOP. Integrity failed. Objective not precommitted.",
        }
    return {
        "CASE": "A",
        "VERDICT": "AM_WAIT5_TWO_STAGE_OBJECTIVE_PRECOMMITTED",
        "NEXT_RESEARCH": NEXT_RESEARCH,
        "PRIMARY_FINDING": (
            "AM W5 ENTRY is precommitted as Stage1 P_FILL5 then Stage2 min-percentile-rank "
            "joint quality on independent U/D predictions. No weight search. No single scalar. "
            "Next development run may compare only CONTROL, FILL_ONLY, and TWO_STAGE. "
            "Runtime WAIT remains 1.0."
        ),
        "note": "STOP. No development performance run. No W5 runtime adoption.",
    }


def am_population(rows: list[dict[str, Any]]) -> dict[str, Any]:
    am = sess_rows(rows, "AM")
    return {"am": am, "am_top3": admission_fill(am, "current_score", int(AM_TOPK))}
