"""Fill-edge-preserving Stage2 interface contract. No training. No performance."""
from __future__ import annotations

from typing import Any

from research.am_wait5_stage2_fill_edge_precommit import (
    ALLOWED_DEVELOPMENT_ARMS,
    CONTROL_ARM,
    FILL_EDGE_PRESERVING_STRUCTURE,
    FILL_LOSS_ALLOWED_IN_PASS_GATE,
    FILL_ONLY_ARM,
    FILL_PRESERVING_BY_CONSTRUCTION,
    FINAL_SELECTION_N,
    LOCKED_FILL_SLOTS,
    MAX_MEMBERSHIP_SWAP_PER_COHORT,
    MINIMAL_SWAP_ARM,
    NEXT_RESEARCH,
    OLD_TWO_STAGE_ARM,
    OLD_TWO_STAGE_INTERFACE_ALLOWED,
    PARITY_ABS_TOL,
    PARITY_EXPECTED,
    PRIOR_DEV_ID_REQUIRED,
    PRIOR_DEV_VERDICT_REQUIRED,
    PRIOR_REASSESS_ID_REQUIRED,
    PRIOR_REASSESS_VERDICT_REQUIRED,
    PROBABILITY_THRESHOLD_ALLOWED,
    QUALITY_COMBINATION,
    QUALITY_SLOT_POOL,
    REPRESENTATION_N,
    SHORTLIST_SEARCH_ALLOWED,
    STAGE1_SCORE,
    STAGE1_SHORTLIST_N,
    STAGE2_D_TARGET,
    STAGE2_U_TARGET,
    U_D_WEIGHT_SEARCH_ALLOWED,
)
from research.am_wait5_two_stage_interface_precommit.analyze import stage2_sort_key, two_stage_select
from research.am_wait5_two_stage_objective_precommit.analyze import joint_quality_tuple, percentile_ranks
from research.canonical_entry_performance_rebase.analyze import rank_group
from research.passive_wait_policy_reassessment.analyze import _close


def fill_only_select(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = rank_group(rows, "P_FILL5")
    return [dict(r) for r in ranked[: int(FINAL_SELECTION_N)]]


def _attach_quality(short: list[dict[str, Any]]) -> list[dict[str, Any]]:
    u_ranks = percentile_ranks([(float(r["pred_U"]), str(r.get("symbol") or "")) for r in short])
    d_ranks = percentile_ranks([(float(r["pred_D"]), str(r.get("symbol") or "")) for r in short])
    scored = []
    for i, (r, ur, dr) in enumerate(zip(short, u_ranks, d_ranks), start=1):
        joint, mean_ud = joint_quality_tuple(ur, dr)
        rec = dict(r)
        rec["P_FILL_RANK"] = i
        rec["U_RANK"] = ur
        rec["D_RANK"] = dr
        rec["JOINT_QUALITY_SCORE"] = joint
        rec["TIE_MEAN_UD_RANK"] = mean_ud
        scored.append(rec)
    return scored


def quality_winner(pool: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not pool:
        return None
    ordered = sorted(
        pool,
        key=lambda r: stage2_sort_key(
            float(r["P_FILL5"]),
            float(r["U_RANK"]),
            float(r["D_RANK"]),
            str(r.get("symbol") or ""),
        ),
    )
    return ordered[0]


def minimal_swap_select(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Lock P_FILL ranks 1-2. Quality winner among ranks 3-5. Ranks computed on full Top5."""
    short = [dict(r) for r in rank_group(rows, "P_FILL5")[: int(STAGE1_SHORTLIST_N)]]
    scored = _attach_quality(short)
    locked_n = min(int(LOCKED_FILL_SLOTS), len(scored))
    locked = [dict(r) for r in scored[:locked_n]]
    for r in locked:
        r["LOCKED_FILL_SLOT"] = True
        r["QUALITY_POOL"] = False
        r["SELECTED"] = True
    pool = [dict(r) for r in scored[locked_n:]]
    for r in pool:
        r["LOCKED_FILL_SLOT"] = False
        r["QUALITY_POOL"] = True
        r["SELECTED"] = False
    win = quality_winner(pool)
    selected = list(locked)
    if win is not None:
        rec = dict(win)
        rec["LOCKED_FILL_SLOT"] = False
        rec["QUALITY_POOL"] = True
        rec["SELECTED"] = True
        rec["QUALITY_WINNER"] = True
        selected.append(rec)
        for r in pool:
            r["QUALITY_WINNER"] = str(r.get("symbol") or "") == str(win.get("symbol") or "")
            r["SELECTED"] = bool(r.get("QUALITY_WINNER"))
    short_out = locked + pool
    return selected[: int(FINAL_SELECTION_N)], short_out


def construction_example() -> dict[str, Any]:
    """Synthetic construction check only. Not a performance evaluation."""
    rows = [
        {"symbol": "A_R1_HIGH_FILL_LOW_Q", "P_FILL5": 0.90, "pred_U": 0.10, "pred_D": 0.10},
        {"symbol": "B_R2_HIGH_FILL_LOW_Q", "P_FILL5": 0.80, "pred_U": 0.20, "pred_D": 0.20},
        {"symbol": "C_R3_MID_FILL_MID_Q", "P_FILL5": 0.70, "pred_U": 0.30, "pred_D": 0.30},
        {"symbol": "D_R4_LOW_FILL_HIGH_Q", "P_FILL5": 0.60, "pred_U": 0.90, "pred_D": 0.90},
        {"symbol": "E_R5_LOW_FILL_HIGH_Q", "P_FILL5": 0.50, "pred_U": 0.80, "pred_D": 0.80},
    ]
    fo = fill_only_select(rows)
    old_ts, old_short = two_stage_select(rows)
    ms, short = minimal_swap_select(rows)
    fo_set = {r["symbol"] for r in fo}
    ms_set = {r["symbol"] for r in ms}
    old_set = {r["symbol"] for r in old_ts}
    swapped_out = sorted(fo_set - ms_set)
    swapped_in = sorted(ms_set - fo_set)
    locked = [r["symbol"] for r in short if r.get("LOCKED_FILL_SLOT")]
    pool = [r["symbol"] for r in short if r.get("QUALITY_POOL")]
    return {
        "FILL_ONLY_SYMBOLS": [r["symbol"] for r in fo],
        "OLD_TWO_STAGE_SYMBOLS": [r["symbol"] for r in old_ts],
        "MINIMAL_SWAP_SYMBOLS": [r["symbol"] for r in ms],
        "LOCKED_SYMBOLS": locked,
        "QUALITY_POOL_SYMBOLS": pool,
        "SWAPPED_OUT": swapped_out,
        "SWAPPED_IN": swapped_in,
        "SWAP_N": len(swapped_in),
        "MAX_SWAP_RESPECTED": len(swapped_in) <= int(MAX_MEMBERSHIP_SWAP_PER_COHORT),
        "R1_R2_LOCKED": locked == [r["symbol"] for r in fo[: int(LOCKED_FILL_SLOTS)]],
        "DISTINCT_FROM_FILL_ONLY": fo_set != ms_set,
        "DISTINCT_FROM_OLD_TWO_STAGE": ms_set != old_set,
        "OLD_TWO_STAGE_NOT_CANDIDATE": OLD_TWO_STAGE_INTERFACE_ALLOWED is False,
        "fill_only": fo,
        "old_two_stage_shortlist": old_short,
        "old_two_stage_selected": old_ts,
        "minimal_swap_shortlist": short,
        "minimal_swap_selected": ms,
        "note": (
            "R1/R2 stay. At most one of R3/R4/R5 may replace R3. "
            "Old free Top5→Top3 quality reconstruction is closed."
        ),
    }


def freeze_parity(dev: dict[str, Any], reass: dict[str, Any]) -> dict[str, Any]:
    obs = {
        "FILL_ONLY_FILL_RATE": dev.get("FILL_ONLY_FILL_RATE"),
        "FILL_ONLY_EXEC_U": dev.get("FILL_ONLY_EXEC_U"),
        "FILL_ONLY_EXEC_D": dev.get("FILL_ONLY_EXEC_D"),
        "OLD_TWO_STAGE_FILL_RATE": dev.get("TWO_STAGE_FILL_RATE"),
        "OLD_TWO_STAGE_EXEC_U": dev.get("TWO_STAGE_EXEC_U"),
        "OLD_TWO_STAGE_EXEC_D": dev.get("TWO_STAGE_EXEC_D"),
        "PRED_U_D_SPEARMAN_MEDIAN": reass.get("PRED_U_D_SPEARMAN_MEDIAN"),
        "ACTUAL_U_D_SPEARMAN_MEDIAN": reass.get("ACTUAL_U_D_SPEARMAN_MEDIAN"),
    }
    checks = {k: _close(obs.get(k), exp, PARITY_ABS_TOL) for k, exp in PARITY_EXPECTED.items()}
    return {"ok": all(checks.values()), "checks": checks, "observed": obs, "expected": dict(PARITY_EXPECTED)}


def freeze_priors(*, dev_id: str, dev: dict[str, Any], reass_id: str, reass: dict[str, Any]) -> dict[str, Any]:
    obs = {
        "PRIOR_DEV_ANALYSIS_ID": dev_id,
        "PRIOR_DEV_VERDICT": dev.get("VERDICT"),
        "PRIOR_REASSESS_ANALYSIS_ID": reass_id,
        "PRIOR_REASSESS_VERDICT": reass.get("VERDICT"),
        "PRIOR_REASSESS_PRIMARY_MECHANISM": reass.get("PRIMARY_MECHANISM"),
        "OLD_TWO_STAGE_INTERFACE_ALLOWED": OLD_TWO_STAGE_INTERFACE_ALLOWED,
    }
    checks = {
        "PRIOR_DEV_ANALYSIS_ID": str(obs["PRIOR_DEV_ANALYSIS_ID"] or "") == PRIOR_DEV_ID_REQUIRED,
        "PRIOR_DEV_VERDICT": obs["PRIOR_DEV_VERDICT"] == PRIOR_DEV_VERDICT_REQUIRED,
        "PRIOR_REASSESS_ANALYSIS_ID": str(obs["PRIOR_REASSESS_ANALYSIS_ID"] or "") == PRIOR_REASSESS_ID_REQUIRED,
        "PRIOR_REASSESS_VERDICT": obs["PRIOR_REASSESS_VERDICT"] == PRIOR_REASSESS_VERDICT_REQUIRED,
        "PRIOR_REASSESS_PRIMARY_MECHANISM": obs["PRIOR_REASSESS_PRIMARY_MECHANISM"]
        == "D_GAIN_MAINLY_FROM_LOWER_FILL_EXPOSURE",
        "OLD_TWO_STAGE_INTERFACE_ALLOWED": obs["OLD_TWO_STAGE_INTERFACE_ALLOWED"] is False,
    }
    return {"ok": all(checks.values()), "checks": checks, "observed": obs}


def future_pass_gate() -> dict[str, Any]:
    return {
        "PRIMARY_REFERENCE": "FILL_ONLY",
        "FILL_LOSS_ALLOWED_IN_PASS_GATE": FILL_LOSS_ALLOWED_IN_PASS_GATE,
        "A_FILL_RATE_GE_FILL_ONLY": True,
        "B_EXEC_U_GT_FILL_ONLY": True,
        "C_EXEC_D_GT_FILL_ONLY": True,
        "D_FILL_NONNEG_REP_GE_6_9": True,
        "E_U_POS_REP_GE_6_9": True,
        "F_D_POS_REP_GE_6_9": True,
        "G_FILL_POS_OR_ZERO_DAYS_GT_NEG": True,
        "H_U_POS_DAYS_GT_NEG": True,
        "I_D_POS_DAYS_GT_NEG": True,
        "J_U_EX_BEST_GT_0": True,
        "K_U_EX_TOP3_GE_0": True,
        "L_D_EX_BEST_GT_0": True,
        "M_D_EX_TOP3_GE_0": True,
        "N_INTEGRITY_PASS": True,
        "D_MAY_NOT_COMPENSATE_FILL_LOSS": True,
        "A_IS_HARD_GATE": True,
        "COND_U_D_SANITY_SECONDARY": True,
        "PRIMARY_METRICS": ["EXEC_U", "EXEC_D"],
        "NOT_EVALUATED_IN_THIS_RUN": True,
    }


def interface_contract() -> dict[str, Any]:
    return {
        "CLOSED_INTERFACE": "P_FILL5 Top5 then free min-rank Top3",
        "OLD_TWO_STAGE_INTERFACE_ALLOWED": OLD_TWO_STAGE_INTERFACE_ALLOWED,
        "QUALITY_COMBINATION_UNCHANGED": QUALITY_COMBINATION,
        "STAGE1_SCORE": STAGE1_SCORE,
        "STAGE1_SHORTLIST_N": STAGE1_SHORTLIST_N,
        "STAGE1_SHORTLIST_TIE": "symbol ASC",
        "LOCKED_FILL_SLOTS": LOCKED_FILL_SLOTS,
        "QUALITY_SLOT_POOL": list(QUALITY_SLOT_POOL),
        "MAX_MEMBERSHIP_SWAP_PER_COHORT": MAX_MEMBERSHIP_SWAP_PER_COHORT,
        "FINAL_TOP3": "P_FILL Rank1 + Rank2 + QUALITY_WINNER(R3,R4,R5)",
        "PERCENTILE_RANK_POPULATION": "Stage1 Top5 shortlist (unchanged min-rank definition)",
        "U_RANK": "percentile rank(pred_U_FILL) inside Top5; higher=better; midrank (i+0.5)/n",
        "D_RANK": "percentile rank(pred_D_FILL) inside Top5; higher=better; midrank (i+0.5)/n",
        "JOINT_QUALITY_SCORE": "min(U_RANK, D_RANK)",
        "POOL_RANK_1": "JOINT_QUALITY_SCORE descending",
        "POOL_RANK_2": "mean(U_RANK, D_RANK) descending",
        "POOL_RANK_3": "P_FILL5 descending",
        "POOL_RANK_4": "symbol ASC",
        "FILL_PRESERVING_BY_CONSTRUCTION": FILL_PRESERVING_BY_CONSTRUCTION,
        "FILL_PRESERVING_BY_CONSTRUCTION_REASON": (
            "R4/R5 may replace R3, so predicted fill probability can fall."
        ),
        "FILL_EDGE_PRESERVING_STRUCTURE": FILL_EDGE_PRESERVING_STRUCTURE,
        "FILL_EDGE_PRESERVING_REASON": (
            "FILL_ONLY Rank1 and Rank2 are locked. Membership change is at most one candidate."
        ),
        "LOCKED_SLOTS_NOT_CLAIMED_OPTIMAL": True,
        "STAGE2_U_TARGET": STAGE2_U_TARGET,
        "STAGE2_D_TARGET": STAGE2_D_TARGET,
        "CONTROL_ARM": CONTROL_ARM,
        "FILL_ONLY_ARM": FILL_ONLY_ARM,
        "MINIMAL_SWAP_ARM": MINIMAL_SWAP_ARM,
        "OLD_TWO_STAGE_ARM": OLD_TWO_STAGE_ARM,
        "ALLOWED_DEVELOPMENT_ARMS": list(ALLOWED_DEVELOPMENT_ARMS),
        "SHORTLIST_SEARCH_ALLOWED": SHORTLIST_SEARCH_ALLOWED,
        "PROBABILITY_THRESHOLD_ALLOWED": PROBABILITY_THRESHOLD_ALLOWED,
        "U_D_WEIGHT_SEARCH_ALLOWED": U_D_WEIGHT_SEARCH_ALLOWED,
        "LOCK_SLOT_SEARCH": False,
        "ALL_THREE_QUALITY_SLOTS": False,
        "P_FILL_GAP_THRESHOLD": False,
        "P_FILL5_TIMES_QUALITY_ALLOWED": False,
        "U_ONLY_STRATEGY_ALLOWED": False,
        "D_ONLY_STRATEGY_ALLOWED": False,
        "WEIGHTED_UD_STRATEGY_ALLOWED": False,
        "PARETO_STRATEGY_ALLOWED": False,
        "BEST_REPRESENTATION_ADOPTED": False,
        "REPRESENTATION_N": REPRESENTATION_N,
        "REPRESENTATION_EVAL": "median / consensus. No cherry-pick.",
        "FILL_LOSS_ALLOWED_IN_PASS_GATE": FILL_LOSS_ALLOWED_IN_PASS_GATE,
        "NEW_MODEL_CREATED": False,
        "RETRAINING_FORBIDDEN": True,
    }


def decide(*, parity_ok: bool, prior_ok: bool, construct_ok: bool, pop_ok: bool) -> dict[str, Any]:
    if not parity_ok or not prior_ok or not construct_ok or not pop_ok:
        return {
            "CASE": "E",
            "VERDICT": "AM_WAIT5_MINIMAL_SWAP_INTEGRITY_FAILED",
            "NEXT_RESEARCH": "NONE",
            "PRIMARY_FINDING": "Parity, prior mechanism, construction, or AM isolation failed.",
            "note": "STOP. Integrity failed. Interface not precommitted.",
        }
    return {
        "CASE": "PRECOMMIT",
        "VERDICT": "AM_WAIT5_STAGE2_FILL_EDGE_OBJECTIVE_PRECOMMITTED",
        "NEXT_RESEARCH": NEXT_RESEARCH,
        "PRIMARY_FINDING": (
            "Old free Top5→Top3 TWO_STAGE is closed. Next development may compare only "
            "CONTROL, FILL_ONLY, and MINIMAL_SWAP_TWO_STAGE (lock P_FILL ranks 1-2, quality "
            "winner among ranks 3-5). Fill loss cannot pass. Runtime WAIT remains 1.0."
        ),
        "note": "STOP. No performance run. No new objective search. No W5 runtime adoption.",
    }
