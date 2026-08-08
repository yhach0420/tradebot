"""E1_X6_FCRR frozen config — P1_STUDY_PRECOMMIT thresholds (no economics).

Retention is the ONLY variant axis. All other thresholds are shared and frozen
before any candidate PnL/PF/ranking is generated.
"""
from __future__ import annotations

from typing import Any

DOCUMENT_ID = "E1_X6_FCRR_IMPLEMENTATION_SPEC"
DOCUMENT_VERSION = "1.0"
PLAN_DOCUMENT_ID = "E1_X6_VALIDATION_PLAN"
PLAN_VERSION = "1.2"
CANDIDATE_FAMILY = "E1_X6_FCRR"
FAMILY_NAME = "Flow Confirmed Reclaim & Retention"

CANDIDATE_IDS = ("FCRR_R10", "FCRR_R20", "FCRR_R30")
RETENTION_SEC: dict[str, float] = {
    "FCRR_R10": 10.0,
    "FCRR_R20": 20.0,
    "FCRR_R30": 30.0,
}
CANDIDATE_COUNT_LIMIT = 3

# Shared thresholds (precommit — never tuned on results)
THRESHOLDS: dict[str, Any] = {
    "context": {
        "mid_gt_vwap": True,
        "ret_180s_gt_0": True,
        "linear_slope_180s_gt_0": True,
        "distance_from_session_high_atr_max": 1.0,
        "distance_above_vwap_atr_max": 1.5,
        "spread_bps_max": 5.0,
        "price_update_count_60s_min": 6,
        "active_volume_windows_120s_min": 4,
        "active_volume_windows_120s_denom": 6,
    },
    "pullback": {
        "depth_atr_min": 0.20,
        "depth_atr_max": 1.00,
        "duration_sec_min": 15.0,
        "duration_sec_max": 120.0,
        "pullback_low_vwap_atr_floor": -0.15,
        "spread_bps_max": 5.0,
    },
    "selling_exhausted": {
        "no_new_low_sec": 30.0,
        "spread_bps_max": 5.0,
    },
    "reclaim": {
        "vol10_ratio_min": 1.50,
        "vol30_ratio_min": 1.25,
        "uptick_volume_ratio_30s_min": 0.60,
        "active_10s_windows_120s_min": 4,
        "active_30s_windows_300s_min": 6,
        "spread_bps_max": 5.0,
        "volume_abs_floor_rule": "symbol_independent_q50_of_active_volume_30s_on_fit_period",
    },
    "retention": {
        "uptick_volume_ratio_10s_min": 0.50,
        "spread_bps_max": 5.0,
        "require_new_high_after_cross": True,
        "require_cross_return_gt_0": True,
        "volume_10s_must_be_nonzero": True,
    },
    "episode": {
        "max_episode_sec": 1800.0,
        "entry_per_episode_max": 1,
        "cap_blocked_counts_as_entry_emitted": True,
    },
    "quality": {
        "price_history_sec_min": 180.0,
        "volume_history_sec_min": 120.0,
        "spread_bps_spike_mult": 2.0,  # vs recent median => invalidate
        "freshness_max_sec": 30.0,
    },
    "execution": {
        "lot": 100,
        "cost_bps_once": 5.0,
        "cap": 5,
        "entry_price": "best_ask",
        "exit": "frozen_E1_X5",
    },
}

STATES = (
    "IDLE",
    "CONTEXT_READY",
    "PULLBACK_ACTIVE",
    "SELLING_EXHAUSTED",
    "RECLAIM_CROSSED",
    "RETENTION_CONFIRMED",
    "ENTRY_EMITTED",
    "EPISODE_LOCKED",
    "INVALIDATED",
)

TIMELINE_RULES = {
    "max_state_advances_per_observation": 1,
    "reclaim_and_entry_not_same_event": True,
    "retention_uses_elapsed_wall_time_only": True,
}

ABLATION_DIAGNOSTIC_ONLY = ("A0", "A1", "A2", "A3", "A4")
ABLATION_NOT_ADOPTABLE = True

ROLLING_ORIGIN_5FOLD = {
    "F1": {"build": ["20260721", "20260722", "20260723", "20260724"], "confirm": "20260727"},
    "F2": {"build": ["20260721", "20260722", "20260723", "20260724", "20260727"], "confirm": "20260728"},
    "F3": {
        "build": ["20260721", "20260722", "20260723", "20260724", "20260727", "20260728"],
        "confirm": "20260729",
    },
    "F4": {
        "build": [
            "20260721", "20260722", "20260723", "20260724",
            "20260727", "20260728", "20260729",
        ],
        "confirm": "20260730",
    },
    "F5": {
        "build": [
            "20260721", "20260722", "20260723", "20260724",
            "20260727", "20260728", "20260729", "20260730",
        ],
        "confirm": "20260731",
    },
}

DAYS = (
    "20260721", "20260722", "20260723", "20260724", "20260727",
    "20260728", "20260729", "20260730", "20260731",
)

PASS_GATES_NOTE = (
    "CORE_VALID / ALL_USABLE / ex-20260722 / Rolling-origin / FIXED_SPEC_DAY_DELETION / "
    "concentration / BASE compare / Safety — per VALIDATION_PLAN 1.2 §11.1; "
    "never relax thresholds on failure"
)


def candidate_spec(candidate_id: str) -> dict[str, Any]:
    if candidate_id not in RETENTION_SEC:
        raise KeyError(candidate_id)
    return {
        "candidate_id": candidate_id,
        "family": CANDIDATE_FAMILY,
        "retention_sec": RETENTION_SEC[candidate_id],
        "thresholds": THRESHOLDS,
        "timeline_rules": TIMELINE_RULES,
        "states": list(STATES),
    }


def precommit_body() -> dict[str, Any]:
    """Pure precommit payload — no economics fields allowed."""
    return {
        "kind": "P1_STUDY_PRECOMMIT",
        "document_id": DOCUMENT_ID,
        "document_version": DOCUMENT_VERSION,
        "plan_document_id": PLAN_DOCUMENT_ID,
        "plan_version": PLAN_VERSION,
        "candidate_family": CANDIDATE_FAMILY,
        "family_name": FAMILY_NAME,
        "candidate_ids": list(CANDIDATE_IDS),
        "candidate_count_limit": CANDIDATE_COUNT_LIMIT,
        "retention_variants": dict(RETENTION_SEC),
        "thresholds": THRESHOLDS,
        "states": list(STATES),
        "timeline_rules": TIMELINE_RULES,
        "feature_schema": [
            "mid", "vwap", "bid", "ask", "spread_bps",
            "ret_15s", "ret_30s", "ret_180s", "linear_slope_180s", "atr_180s",
            "volume_10s", "volume_30s",
            "median_active_volume_10s_120s", "median_active_volume_30s_300s",
            "active_volume_windows_120s", "active_volume_windows_300s",
            "uptick_volume_ratio_10s", "uptick_volume_ratio_30s",
            "down_tick_volume_ratio_15s", "down_tick_volume_ratio_60s",
            "price_update_count_10s", "price_update_count_60s",
            "median_price_update_count_10s_120s",
            "session_high", "distance_from_session_high", "distance_above_vwap",
            "trade_side_quality",
        ],
        "label_schema": [
            "completed_net_pnl_yen_100", "exit_reason", "mfe_bps", "mae_bps",
            "is_winner", "missed_winner_audit", "x5_keep_removed_added",
        ],
        "fold": ROLLING_ORIGIN_5FOLD,
        "seed": 0,
        "pass_conditions": PASS_GATES_NOTE,
        "ablation_diagnostic_only": list(ABLATION_DIAGNOSTIC_ONLY),
        "ablation_not_adoptable": ABLATION_NOT_ADOPTABLE,
        "days": list(DAYS),
        "forbidden_in_entry": [
            "calendar_date", "weekday", "symbol_code_rules", "day_specific_thresholds",
            "am_pm_permit", "future_mfe_mae_stop", "post_hoc_regime",
        ],
        "economics_opened_before_precommit": False,
    }
