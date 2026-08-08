"""Precommit for Failure Source Analysis V2 — must be sealed before economics."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from research.e1_x6_provisional.util import sha256_obj

from . import ANALYSIS_ID, CANONICAL_RUN, CANONICAL_VERDICT

JST = ZoneInfo("Asia/Tokyo")

LOCKED_P1 = "f7ef02ee7f8a2580765658cae1fe5e2fcabaf3d74cbf147142fa67cb83aa7db9"
LOCKED_P2 = "9a484c78b74c66d32be002b3bc9db9a0068b95ea62bbe49e4accf810c575894a"

# Feature schema (ENTRY decision time only). day/symbol/session are audit keys, not model features.
FEATURE_SCHEMA = [
    # price structure
    "setup_type_code",  # 0=PULLBACK_RECLAIM, 1=RANGE_BREAKOUT (encoded; setup_type string for audit)
    "anchor_type_code",  # 0=MICRO_HIGH, 1=RANGE_HIGH / other
    "range_width_atr",
    "range_duration_sec",
    "pullback_depth_atr",
    "pullback_duration_sec",
    "high_test_count",
    "cross_magnitude_bps",
    "distance_from_vwap_atr",
    "distance_from_session_high_atr",
    "pre_cross_return_10s",
    "pre_cross_return_30s",
    "pre_cross_return_60s",
    "pre_cross_slope_30s",
    "pre_cross_slope_60s",
    "pre_cross_acceleration",
    # volume / trade flow
    "volume_10s",
    "volume_30s",
    "volume_60s",
    "volume_impulse_ratio",
    "volume_persistence",
    "uptick_volume_ratio_10s",
    "uptick_volume_ratio_30s",
    "downtick_deceleration",
    "price_impact_efficiency",
    # board / updates
    "spread_bps",
    "spread_change",
    "bid_support",
    "best_bid_update_count",
    "ask_replenishment",
    "imbalance",
    "price_update_count_10s",
    "price_update_count_30s",
    "update_acceleration",
    # data quality
    "event_freshness",
    "board_freshness",
    "trade_side_quality_code",
    "missing_feature_count",
]

FORBIDDEN_FEATURES = [
    "calendar_date",
    "weekday",
    "symbol",
    "symbol_code",
    "am_pm_permit",
    "future_mfe",
    "future_mae",
    "scenario_id",
    "exit_reason",
    "future_price",
    "day",
    "session",
]

HORIZONS = (30.0, 60.0, 120.0, 300.0)
OPP_THRESHOLDS_BPS = (0.0, 5.0, 10.0)
ADVERSE_THRESHOLDS_BPS = (-10.0, -15.0, -20.0)
MAX_HOLD_SEC = 300.0
FRESHNESS_MAX_SEC = 30.0
COST_BPS = 5.0
LOT = 100
BOOTSTRAP_REPS = 1000
BOOTSTRAP_SEED = 20260804
TREE_MIN_LEAF = 20
LOGISTIC_C = 1.0
LABEL_QUALITY_MIN_CLUSTER_FRAC = 0.70


def build_precommit(*, episode_ids: list[str], path_ledger_n: int, usable_n: int, excluded_n: int) -> dict[str, Any]:
    body = {
        "analysis_id": ANALYSIS_ID,
        "precommit_type": "FSA_V2_ANALYSIS_PRECOMMIT",
        "precommit_at_jst": datetime.now(JST).isoformat(),
        "purpose": [
            "1. Did TAER ENTRY admit an executable profit opportunity?",
            "2. Can that opportunity be separated with ENTRY-time info across days?",
            "3. Close TAER family vs support a new independent family hypothesis?",
        ],
        "canonical_run": {
            "run_id": CANONICAL_RUN,
            "economic_integrity_status": "PASS",
            "final_verdict": CANONICAL_VERDICT,
            "family_status": "CLOSED_NO_ROBUST_PAIR",
            "study_revision": "E1_X6_TRIGGER_ANCHORED_ENTRY_EXIT_JOINT_V1",
            "period": "20260721-20260731",
            "period_status": "EXPLORATORY_FAILURE_ANALYSIS_ONLY",
            "locked_p1": LOCKED_P1,
            "locked_p2": LOCKED_P2,
        },
        "episode_set": {
            "source": "path_ledger of e1x6_taer_20260803_232514",
            "path_ledger_n": path_ledger_n,
            "usable_n": usable_n,
            "excluded_n": excluded_n,
            "episode_id_sha256": sha256_obj(sorted(episode_ids)),
            "setups_separated": ["PULLBACK_RECLAIM", "RANGE_BREAKOUT"],
            "s7_excluded_from_primary_supervised": True,
        },
        "continuous_outcome": {
            "primary_outcome": "best_net_pnl_bps_300s",
            "secondary_outcomes": [
                "best_net_pnl_bps_60s",
                "best_net_pnl_bps_120s",
                "adverse_before_best_bps",
                "time_to_net_positive_sec",
            ],
            "entry_price": "canonical best_ask at entry",
            "exit_candidate_price": "same symbol/day/session canonical best_bid",
            "cost": "5bps once (existing contract)",
            "lot": LOT,
            "oracle_only": True,
            "not_runtime_exit": True,
        },
        "secondary_label_grid": {
            "net_opportunity_thresholds_bps": list(OPP_THRESHOLDS_BPS),
            "adverse_before_opportunity_bps": list(ADVERSE_THRESHOLDS_BPS),
            "usage": "sensitivity_counts_only_do_not_cherry_pick",
        },
        "overlap_cluster": {
            "key": ["day", "session", "symbol"],
            "window_sec": MAX_HOLD_SEC,
            "rule": "union_find on overlapping [entry_t, entry_t+300]",
            "primary_weighting": "CLUSTER_FIRST_EPISODE",
            "tie_break": ["entry_t asc", "episode_id asc"],
            "raw_independent_rows_forbidden_as_primary": True,
        },
        "feature_schema": FEATURE_SCHEMA,
        "forbidden_features": FORBIDDEN_FEATURES,
        "stability_rules": {
            "evaluable_days_min": 7,
            "same_direction_rate_min": 0.80,
            "direction_reversal_count_max": 1,
            "missing_rate_max": 0.20,
            "positive_opportunity_days_min": 4,
            "non_opportunity_days_min": 4,
            "bootstrap_unit": "day_x_symbol",
            "bootstrap_reps": BOOTSTRAP_REPS,
            "bootstrap_seed": BOOTSTRAP_SEED,
            "bootstrap_ci_must_exclude_0_for_strong_stable": True,
        },
        "model_diagnostic_rules": {
            "gate_all_required": [
                "setup_usable_clusters_ge_100",
                "stable_univariate_features_ge_2",
                "positive_opp_days_ge_4",
                "negative_opp_days_ge_4",
            ],
            "models": {
                "logistic": {"penalty": "l2", "C": LOGISTIC_C, "no_hyperparam_search": True},
                "tree": {"max_depth": 2, "min_samples_leaf": TREE_MIN_LEAF},
            },
            "target": "net_plus_5bps_opportunity",
            "split": "Leave-One-Day-Out",
            "preprocess_fit": "build_days_only",
            "name": "cross_day_diagnostic",
            "not_oos_holdout": True,
        },
        "verdict_rules": {
            "A": "TAER_NO_EXECUTABLE_ENTRY_OPPORTUNITY",
            "B": "TAER_TRIGGER_ANCHORED_FAMILY_NO_STABLE_ENTRY_SIGNAL",
            "C_pullback": "TAER_PULLBACK_NEW_FAMILY_HYPOTHESIS_SUPPORTED",
            "C_range": "TAER_RANGE_NEW_FAMILY_HYPOTHESIS_SUPPORTED",
            "C_both": "TAER_SETUP_SPECIFIC_NEW_FAMILY_HYPOTHESES_SUPPORTED",
            "D": "TAER_FAILURE_ANALYSIS_INSUFFICIENT_LABEL_QUALITY",
            "stop_after_analysis": True,
            "no_new_family_in_this_analysis": True,
            "no_taer_v1_threshold_tune": True,
        },
        "economics_opened_before_precommit": False,
    }
    body["precommit_sha256"] = sha256_obj(body)
    return body
