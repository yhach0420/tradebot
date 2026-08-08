"""FSA V3 precommit — sealed before feature effects / models."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from research.e1_x6_provisional.util import sha256_obj

from .precommit import (
    BOOTSTRAP_REPS,
    BOOTSTRAP_SEED,
    FEATURE_SCHEMA,
    FORBIDDEN_FEATURES,
    LOGISTIC_C,
    TREE_MIN_LEAF,
)
from .v3_identity import (
    ANALYSIS_ID_V3,
    LOCKED_CLUSTER_SHA,
    LOCKED_EPISODE_SHA,
    LOCKED_OPPORTUNITY_SHA,
    PURPOSE_V3,
    V2_RUN,
)

JST = ZoneInfo("Asia/Tokyo")

SETUP_SPECIFIC_FEATURES = {
    "range_width_atr": "RANGE_BREAKOUT",
    "range_duration_sec": "RANGE_BREAKOUT",
    "high_test_count": "RANGE_BREAKOUT",
    "pullback_depth_atr": "PULLBACK_RECLAIM",
    "pullback_duration_sec": "PULLBACK_RECLAIM",
}

# Always unavailable in this feed (no depth) — not row-killers; excluded from primary candidates
DEPTH_UNAVAILABLE = ("ask_replenishment", "imbalance")

TARGET_VALID_MIN_RATE = 0.70


def build_v3_precommit() -> dict[str, Any]:
    body = {
        "analysis_id": ANALYSIS_ID_V3,
        "purpose": PURPOSE_V3,
        "precommit_type": "FSA_V3_LABEL_CONTRACT_PRECOMMIT",
        "precommit_at_jst": datetime.now(JST).isoformat(),
        "frozen_v2": {
            "run_id": V2_RUN,
            "verdict_meaning": "FSA_V2_STOPPED_BY_SCENARIO_BASED_LABEL_QUALITY_GATE",
            "episode_identity_sha": LOCKED_EPISODE_SHA,
            "cluster_identity_sha": LOCKED_CLUSTER_SHA,
            "opportunity_table_sha": LOCKED_OPPORTUNITY_SHA,
            "primary_weighting": "CLUSTER_FIRST_EPISODE",
            "overlap_cluster_n": 399,
            "no_cluster_rebuild": True,
            "no_episode_add_delete": True,
        },
        "label_contract": {
            "fields": [
                "opportunity_target_valid",
                "scenario_label_valid",
                "feature_row_valid",
            ],
            "opportunity_target_ignores_scenario_id": True,
            "s7_does_not_invalidate_opportunity_target": True,
            "s7_not_excluded_from_feature_stability": True,
            "conflicting_scenario_invalidates_scenario_only": True,
            "stop_only_if_target_valid_rate_lt": TARGET_VALID_MIN_RATE,
            "scenario_valid_rate_does_not_stop": True,
        },
        "feature_schema": FEATURE_SCHEMA,
        "forbidden_features": FORBIDDEN_FEATURES,
        "setup_specific_features": SETUP_SPECIFIC_FEATURES,
        "depth_unavailable_features": list(DEPTH_UNAVAILABLE),
        "primary_outcome": "best_net_pnl_bps_300s",
        "secondary_outcomes": [
            "best_net_pnl_bps_60s",
            "best_net_pnl_bps_120s",
            "adverse_before_best_bps",
            "time_to_net_positive_sec",
            "net_plus_5bps",
        ],
        "oracle_vs_realizable": {
            "opportunity_envelope": "oracle_edge_only",
            "not_proof_of_realizable_exit_rule": True,
        },
        "stability_rules": {
            "evaluable_deletions_min": 7,
            "same_direction_rate_min": 0.80,
            "direction_reversal_count_max": 1,
            "setup_missing_rate_max": 0.20,
            "positive_opportunity_days_min": 4,
            "non_opportunity_days_min": 4,
            "bootstrap_reps": BOOTSTRAP_REPS,
            "bootstrap_seed": BOOTSTRAP_SEED,
        },
        "model_diagnostic_rules": {
            "target_valid_clusters_ge_100": True,
            "stable_univariate_ge_2": True,
            "logistic": {"penalty": "l2", "C": LOGISTIC_C, "no_search": True},
            "tree": {"max_depth": 2, "min_samples_leaf": TREE_MIN_LEAF},
            "name": "cross_day_diagnostic",
            "not_oos_holdout": True,
        },
        "verdict_rules": [
            "TAER_FAILURE_ANALYSIS_INSUFFICIENT_OPPORTUNITY_TARGET_QUALITY",
            "TAER_FAILURE_ANALYSIS_INSUFFICIENT_FEATURE_SCHEMA",
            "TAER_TRIGGER_ANCHORED_FAMILY_NO_STABLE_ENTRY_SIGNAL",
            "TAER_PULLBACK_NEW_FAMILY_HYPOTHESIS_SUPPORTED",
            "TAER_RANGE_NEW_FAMILY_HYPOTHESIS_SUPPORTED",
            "TAER_SETUP_SPECIFIC_NEW_FAMILY_HYPOTHESES_SUPPORTED",
        ],
        "no_new_family_in_this_analysis": True,
        "taer_v1_unchanged": True,
        "economics_or_feature_effects_opened_before_precommit": False,
    }
    body["precommit_sha256"] = sha256_obj(body)
    return body
