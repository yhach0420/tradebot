"""FSA V4 precommit — sealed before bootstrap / models / final verdict."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from research.e1_x6_provisional.util import sha256_obj
from research.e1_x6_taer.failure_source.precommit import BOOTSTRAP_REPS, BOOTSTRAP_SEED, FEATURE_SCHEMA, LOGISTIC_C, TREE_MIN_LEAF
from research.e1_x6_taer.failure_source.v4_identity import (
    ANALYSIS_ID_V4,
    LOCKED_CLUSTER_SHA,
    LOCKED_EPISODE_SHA,
    LOCKED_OPPORTUNITY_SHA,
    LOCKED_TARGET_VALIDITY_SHA,
    PURPOSE_V4,
    V3_RUN,
)

JST = ZoneInfo("Asia/Tokyo")

MIN_EFFECT_BPS = {
    "PULLBACK_RECLAIM": 2.0,
    "RANGE_BREAKOUT": 3.0,
}

ENTRY_FEATURE_COLUMNS = [
    "cluster_id", "episode_id", "setup_type", "anchor_type", "day", "session", "symbol",
    "decision_time", "feature_asof_time",
    "range_width_atr", "range_duration_sec", "pullback_depth_atr", "pullback_duration_sec",
    "high_test_count", "cross_magnitude_bps", "distance_from_vwap_atr",
    "distance_from_session_high_atr",
    "pre_cross_return_10s", "pre_cross_return_30s", "pre_cross_return_60s",
    "pre_cross_slope_30s", "pre_cross_slope_60s", "pre_cross_acceleration",
    "volume_10s", "volume_30s", "volume_60s", "volume_impulse_ratio", "volume_persistence",
    "uptick_volume_ratio_10s", "uptick_volume_ratio_30s", "downtick_deceleration",
    "price_impact_efficiency",
    "spread_bps", "spread_change", "bid_support", "best_bid_update_count",
    "ask_replenishment", "imbalance", "price_update_count_10s", "price_update_count_30s",
    "update_acceleration",
    "event_freshness", "board_freshness", "trade_side_quality", "missing_feature_count",
]


def build_v4_precommit() -> dict[str, Any]:
    body = {
        "analysis_id": ANALYSIS_ID_V4,
        "purpose": PURPOSE_V4,
        "precommit_type": "FSA_V4_STABILITY_GATE_PRECOMMIT",
        "precommit_at_jst": datetime.now(JST).isoformat(),
        "frozen_v3": {
            "run_id": V3_RUN,
            "meaning": "FSA_V3_STOPPED_BY_INVALID_DAY_CLASS_SUPPORT_GATE",
            "episode_identity_sha": LOCKED_EPISODE_SHA,
            "cluster_identity_sha": LOCKED_CLUSTER_SHA,
            "opportunity_table_sha": LOCKED_OPPORTUNITY_SHA,
            "target_validity_sha": LOCKED_TARGET_VALIDITY_SHA,
            "cluster_n": 399,
            "pullback_n": 303,
            "range_n": 96,
            "no_cluster_rebuild": True,
            "no_target_change": True,
            "no_feature_value_change": True,
            "s7_handling_unchanged": True,
        },
        "target_definition": {
            "primary_continuous": "best_net_pnl_bps_300s",
            "binary_diagnostic": "net_plus_5bps = best_net_pnl_bps_300s >= 5",
            "forbidden_as_target_or_feature": ["scenario_id", "exit_reason", "final_pnl"],
        },
        "class_support_definition": {
            "positive_n": "count net_plus_5bps true",
            "negative_n": "count net_plus_5bps false",
            "forbidden": "daily median sign of best_net_pnl_bps_300s",
            "descriptive_two_class_day": {
                "cluster_n_ge": 4, "positive_n_ge": 1, "negative_n_ge": 1,
            },
            "model_confirm_eligible_day": {
                "cluster_n_ge": 6, "positive_n_ge": 2, "negative_n_ge": 2,
            },
        },
        "continuous_outcome_stability": {
            "does_not_use_positive_opportunity_days_ge_4": True,
            "does_not_use_non_opportunity_days_ge_4": True,
            "requires": [
                "primary_candidate_eligible",
                "setup_missing_rate_le_0.20",
                "zero_variance_false",
                "evaluable_day_deletions_ge_7",
                "same_direction_rate_ge_0.80",
                "direction_reversal_count_le_1",
                "lodo_min_max_effect_not_cross_0",
                "minimum_lodo_cluster_support_ge_60",
            ],
        },
        "bootstrap_definition": {
            "unit": "day_x_symbol",
            "reps": BOOTSTRAP_REPS,
            "seed": BOOTSTRAP_SEED,
            "primary_stat": "median_split_effect_on_best_net_pnl_bps_300s",
            "secondary_stat": "spearman",
            "min_effect_bps": MIN_EFFECT_BPS,
            "strong_requires_ci_excludes_0": True,
            "strong_requires_sign_matches_full_period": True,
            "strong_requires_abs_effect_ge_min": True,
        },
        "model_execution_gate": {
            "pullback": {
                "target_valid_clusters_ge": 100,
                "strong_stable_features_ge": 2,
                "model_eligible_days_ge": 5,
                "each_lodo_build_positive_n_ge": 20,
                "each_lodo_build_negative_n_ge": 20,
            },
            "range": {
                "target_valid_clusters_ge": 100,
                "not_relaxed_posthoc": True,
                "current_n": 96,
                "if_lt_100": "NOT_EVALUABLE_SUPPORT_LT_100",
                "not_evidence_of_no_entry_signal": True,
            },
            "models": {
                "logistic": {"penalty": "l2", "C": LOGISTIC_C, "no_search": True},
                "tree": {"max_depth": 2, "min_samples_leaf": TREE_MIN_LEAF},
                "name": "cross_day_diagnostic",
            },
        },
        "verdict_rules": [
            "TAER_TRIGGER_ANCHORED_FAMILY_NO_STABLE_ENTRY_SIGNAL",
            "TAER_PULLBACK_ENTRY_FEATURES_STABLE_MODEL_NOT_SUPPORTED",
            "TAER_PULLBACK_NEW_FAMILY_HYPOTHESIS_SUPPORTED",
            "TAER_RANGE_STABLE_FEATURES_MODEL_SUPPORT_INSUFFICIENT",
            "TAER_RANGE_NO_STABLE_ENTRY_FEATURE",
            "TAER_FAILURE_ANALYSIS_AUDIT_SCHEMA_INCOMPLETE",
        ],
        "feature_schema_ref": FEATURE_SCHEMA,
        "entry_features_columns": ENTRY_FEATURE_COLUMNS,
        "no_new_family": True,
        "bootstrap_models_verdict_opened_before_precommit": False,
    }
    body["precommit_sha256"] = sha256_obj(body)
    return body
