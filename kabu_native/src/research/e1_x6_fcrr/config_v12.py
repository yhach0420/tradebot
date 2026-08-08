"""E1_X6_FCRR Spec 1.2 Phase B config — quantile thresholds, 6 candidates.

Old Spec 1.0 fixed thresholds remain untouched in config.py for the frozen reference.
"""
from __future__ import annotations

from typing import Any

DOCUMENT_ID = "E1_X6_FCRR_IMPLEMENTATION_SPEC"
DOCUMENT_VERSION = "1.2"
PLAN_DOCUMENT_ID = "E1_X6_VALIDATION_PLAN"
PLAN_VERSION = "1.3"
CANDIDATE_FAMILY = "E1_X6_FCRR"
STUDY_REVISION = "FCRR_V12_ENTRY_EXIT_JOINT"

CANDIDATE_IDS = (
    "FCRR_F1_R10", "FCRR_F1_R20", "FCRR_F1_R30",
    "FCRR_F2_R10", "FCRR_F2_R20", "FCRR_F2_R30",
)
FLOW_PROFILE: dict[str, str] = {
    "FCRR_F1_R10": "F1", "FCRR_F1_R20": "F1", "FCRR_F1_R30": "F1",
    "FCRR_F2_R10": "F2", "FCRR_F2_R20": "F2", "FCRR_F2_R30": "F2",
}
RETENTION_SEC: dict[str, float] = {
    "FCRR_F1_R10": 10.0, "FCRR_F1_R20": 20.0, "FCRR_F1_R30": 30.0,
    "FCRR_F2_R10": 10.0, "FCRR_F2_R20": 20.0, "FCRR_F2_R30": 30.0,
}
CANDIDATE_COUNT_LIMIT = 6
QUANTILE_GRID = (0.30, 0.50, 0.70)

# Precommitted selection method (no PnL): mid quantile with support preference.
SELECTION_METHOD = {
    "order": [
        "confirm_feature_direction_by_day",
        "drop_insufficient_support",
        "drop_direction_reversals",
        "pick_best_day_balanced_primary_proxy",
        "tie_break_looser_larger_support",
    ],
    "forbidden_in_threshold_choice": ["pnl", "pf", "win_rate", "exit_pnl"],
    "default_quantile_if_tie": 0.50,
}

REACHABILITY_GATE = {
    "CONTEXT_READY_episodes_min": 300,
    "PULLBACK_ACTIVE_episodes_min": 150,
    "SELLING_EXHAUSTED_episodes_min": 75,
    "RECLAIM_CROSSED_episodes_min": 45,
    "ENTRY_episodes_min": 30,
    "ENTRY_days_min": 3,
}

# Feature registry (direction + role). Threshold values filled at precommit fit.
FEATURE_CANDIDATES: dict[str, Any] = {
    "ret_180s": {"direction": "positive", "role": "price_context"},
    "linear_slope_180s": {"direction": "positive", "role": "price_context"},
    "distance_from_session_high": {"direction": "negative", "role": "price_context"},
    "spread_bps": {"direction": "negative", "role": "tradeability"},
    "active_volume_windows_120s": {"direction": "positive", "role": "tradeability"},
    "pullback_depth_atr": {"direction": "band", "role": "pullback_primary"},
    "pullback_duration_sec": {"direction": "band", "role": "pullback_guard"},
    "seconds_since_pullback_low": {"direction": "positive", "role": "exhaustion_primary"},
    "ret_15s_minus_ret_30s": {"direction": "positive", "role": "exhaustion_secondary"},
    "down_tick_deceleration": {"direction": "positive", "role": "exhaustion_secondary"},
    "vol10_over_med10": {"direction": "positive", "role": "flow"},
    "uptick_volume_ratio_30s": {"direction": "positive", "role": "flow"},
    "price_update_accel": {"direction": "positive", "role": "flow"},
}

# Structural locks (not quantile-tuned)
STRUCTURAL = {
    "context_max_price_features": 2,
    "context_max_tradeability": 1,
    "pullback_require_real_decline": True,
    "exhaustion_require_low_stop": True,
    "exhaustion_max_secondary": 1,
    "reclaim_require_actual_cross": True,
    "f1_min_of_three": 2,
    "retention_hard_mid_hold": True,
    "one_advance_per_obs": True,
    "entry_not_same_event_as_cross": True,
    "entry_per_episode_max": 1,
    "cap": 5,
    "lot": 100,
    "cost_bps_once": 5.0,
}

FOLD_BUILDS = {
    "F1": ["20260721", "20260722", "20260723", "20260724"],
    "F2": ["20260721", "20260722", "20260723", "20260724", "20260727"],
    "F3": ["20260721", "20260722", "20260723", "20260724", "20260727", "20260728"],
    "F4": ["20260721", "20260722", "20260723", "20260724", "20260727", "20260728", "20260729"],
    "F5": ["20260721", "20260722", "20260723", "20260724", "20260727", "20260728", "20260729", "20260730"],
}
FOLD_CONFIRM = {
    "F1": "20260727",
    "F2": "20260728",
    "F3": "20260729",
    "F4": "20260730",
    "F5": "20260731",
}

DAYS = (
    "20260721", "20260722", "20260723", "20260724",
    "20260727", "20260728", "20260729", "20260730", "20260731",
)

PATH_LEDGER_SCHEMA = [
    "bid", "ask", "mid", "spread_bps", "entry_price", "reclaim_level", "pullback_low",
    "vwap", "elapsed_sec", "mfe_so_far", "mae_so_far", "current_pnl", "giveback_from_mfe",
    "new_high_count", "seconds_since_new_high", "reclaim_status",
    "volume_10s", "volume_30s", "volume_60s", "volume_persistence",
    "uptick_volume_ratio", "downtick_volume_ratio", "price_update_speed",
    "bid_support", "ask_replenishment", "imbalance_change", "freshness", "censor_reason",
]

SCENARIO_IDS = (
    "S1_IMMEDIATE_CONTINUATION",
    "S2_RETEST_THEN_CONTINUATION",
    "S3_FALSE_BREAKOUT",
    "S4_NO_PROGRESS",
    "S5_SPIKE_GIVEBACK",
    "S6_LATE_CONTINUATION",
    "S7_CENSORED_OR_OTHER",
)


def p1_entry_precommit_body() -> dict[str, Any]:
    return {
        "precommit_type": "P1_ENTRY_PRECOMMIT",
        "document_id": DOCUMENT_ID,
        "document_version": DOCUMENT_VERSION,
        "plan_document_id": PLAN_DOCUMENT_ID,
        "plan_version": PLAN_VERSION,
        "study_revision": STUDY_REVISION,
        "candidate_ids": list(CANDIDATE_IDS),
        "candidate_count_limit": CANDIDATE_COUNT_LIMIT,
        "feature_candidates": FEATURE_CANDIDATES,
        "feature_direction_policy": {
            "positive": [">=q30", ">=q50", ">=q70"],
            "negative": ["<=q70", "<=q50", "<=q30"],
            "band": ["q30-q70", "q30-q50", "q50-q70"],
        },
        "quantile_grid": list(QUANTILE_GRID),
        "selection_method": SELECTION_METHOD,
        "reachability_gate": REACHABILITY_GATE,
        "scenario_classification": list(SCENARIO_IDS),
        "path_ledger_schema": PATH_LEDGER_SCHEMA,
        "structural": STRUCTURAL,
        "flow_profiles": {"F1": "min_2_of_3_dynamic", "F2": "volume_and_uptick_and_spread"},
        "retention_sec": dict(RETENTION_SEC),
        "economics_opened_before_precommit": False,
        "note": "Threshold numeric values are fit per fold build days only after this body is SHA-frozen.",
    }
