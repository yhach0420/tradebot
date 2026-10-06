"""Canonical C0 prospective challenger specification. Architecture only. No economics in digest."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.am_current_utility_augment import AUG_SCORE_THRESHOLD, AUGMENT_MAX_PER_COHORT, CURRENT_PRIORITY
from research.am_entry_information_expansion import (
    AVAILABLE_REP_MIN,
    CLASS_LOSS,
    CLASS_NEUTRAL,
    CLASS_WIN,
    RF_CLF_PARAMS,
    TARGET,
    X14_BUNDLE,
)
from research.am_entry_profit_improvement import C14_ID, DEV_WAIT_SEC, ELIGIBLE_DAYS, FINAL_SELECTION_N, SESSION
from research.am_entry_research_final_decision import (
    FORMAL_CANDIDATE,
    FROZEN_ARM,
    PAPER_STRATEGY_ADOPTION_ALLOWED,
    PROSPECTIVE_CHALLENGER_ID,
    PROSPECTIVE_CHALLENGER_NAME,
    PROSPECTIVE_STATUS,
    RUNTIME_ADOPTION_ALLOWED,
)
from research.am_entry_temporal_regime_information import (
    CORE_STATE_FEATURES,
    REGIME_FEATURES,
    TEMPORAL_FEATURES,
)
from research.am_entry_temporal_regime_information.precommit import extra_features_for
from research.direct_joint_objective.oof import representation_grid
from research.entry_objective_redesign_c3 import FEATURE_SETS, MIN_COHORT_N, NORMS
from research.wait5_session_target_learnability import POS_REP_DENOM, POS_REP_MIN
from small_paper.v1r_primary_runtime import POSITION_CAP, WAIT_SEC

A3 = "A3_X14_CORE_STATE"
A5 = "A5_X14_ALL_REGIME"


def _canon(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _canon(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_canon(v) for v in obj]
    if isinstance(obj, bool):
        return bool(obj)
    if isinstance(obj, int) and not isinstance(obj, bool):
        return int(obj)
    if isinstance(obj, float):
        return float(obj)
    if obj is None:
        return None
    return str(obj)


def canonical_c0_spec() -> dict[str, Any]:
    reps = []
    for rec in representation_grid():
        reps.append(
            {
                "representation_id": str(rec.get("representation_id")),
                "feature_set": str(rec.get("feature_set")),
                "normalization": str(rec.get("normalization")),
                "features": [str(x) for x in (rec.get("features") or [])],
                "n_features": int(rec.get("n_features") or 0),
            }
        )
    b0_extras = [str(x) for x in extra_features_for(A3)]
    b1_extras = [str(x) for x in extra_features_for(A5)]
    return _canon(
        {
            "PROSPECTIVE_CHALLENGER_ID": PROSPECTIVE_CHALLENGER_ID,
            "NAME": PROSPECTIVE_CHALLENGER_NAME,
            "STATUS": PROSPECTIVE_STATUS,
            "RUNTIME_ALLOWED": RUNTIME_ADOPTION_ALLOWED,
            "PAPER_STRATEGY_ADOPTION_ALLOWED": PAPER_STRATEGY_ADOPTION_ALLOWED,
            "FORMAL_CANDIDATE": FORMAL_CANDIDATE,
            "SESSION": SESSION,
            "target": TARGET,
            "classes": [CLASS_WIN, CLASS_LOSS, CLASS_NEUTRAL],
            "joint_score": "P_WIN - P_LOSS",
            "ensemble": "MEDIAN_JOINT_SCORE",
            "score_boundary": float(AUG_SCORE_THRESHOLD),
            "frozen_model": FROZEN_ARM,
            "rf_params": dict(RF_CLF_PARAMS),
            "x14_bundle": list(X14_BUNDLE),
            "append_after_representation": True,
            "b0": {
                "id": "B0_X14_CORE_STATE",
                "reuse_arm": A3,
                "extras": b0_extras,
            },
            "b1": {
                "id": "B1_X14_ALL_REGIME",
                "extras": b1_extras,
                "reuse_arm": A5,
                "temporal_features": list(TEMPORAL_FEATURES),
                "regime_features": list(REGIME_FEATURES),
                "core_state_features": list(CORE_STATE_FEATURES),
            },
            "feature_sets": {k: list(v) for k, v in FEATURE_SETS.items()},
            "normalizations": list(NORMS),
            "representations": reps,
            "representation_n": len(reps),
            "eligibility": {
                "outside_current": True,
                "available_rep_n_min": int(AVAILABLE_REP_MIN),
                "positive_rep_n_min": int(POS_REP_MIN),
                "positive_rep_denom": int(POS_REP_DENOM),
                "ensemble_joint_score_gt": float(AUG_SCORE_THRESHOLD),
            },
            "confirmation_rule": {
                "arm": PROSPECTIVE_CHALLENGER_ID,
                "primary": "B0_TOP1",
                "confirm": "B1_ELIGIBLE",
                "b1_ranking_required": False,
                "fallback": False,
                "score_blend": False,
                "score_threshold_search": False,
                "probability_threshold_search": False,
            },
            "ranking": {
                "current": ["current_score DESC", "symbol ASC"],
                "b0_top1": ["ENSEMBLE_JOINT_SCORE DESC", "POSITIVE_REP_N DESC", "symbol ASC"],
            },
            "current_priority": bool(CURRENT_PRIORITY),
            "current_topk": int(FINAL_SELECTION_N),
            "augment_max_per_cohort": int(AUGMENT_MAX_PER_COHORT),
            "min_cohort_n": int(MIN_COHORT_N),
            "dev_wait_sec": float(DEV_WAIT_SEC),
            "runtime_wait_sec": float(WAIT_SEC),
            "corrected_passive_fill": "DEV_WAIT_SEC_5_DIAGNOSTIC_ONLY",
            "w5_runtime_adopted": False,
            "exit": {
                "c14_id": C14_ID,
                "horizon_sec": 600.0,
                "exit_change_allowed": False,
            },
            "portfolio": {
                "position_cap": int(POSITION_CAP),
                "same_symbol": True,
                "occupancy": True,
                "reentry": True,
                "slot_release": True,
                "future_current_protection": False,
            },
            "development_days_burned": list(ELIGIBLE_DAYS),
            "development_day_n": len(list(ELIGIBLE_DAYS)),
            "future_oos_protocol": {
                "fit_b0_b1_on_development_days_only": True,
                "never_fit_on_eval_days": True,
                "no_inner_selection": True,
                "no_threshold_change_after_results": True,
                "compare_current_vs_c0_overlay_one_shot": True,
                "do_not_require_development_net_or_pf_reproduction": True,
            },
            "true_oos": False,
            "new_forward_n": 0,
        }
    )


def spec_sha256(spec: dict[str, Any]) -> str:
    blob = json.dumps(spec, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()
