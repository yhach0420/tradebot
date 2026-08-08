"""E1_X7 PFQ config — exploratory design period only."""
from __future__ import annotations

from typing import Any

from . import DOCUMENT_ID, FAMILY_ID, STUDY_TYPE

DAYS = (
    "20260721", "20260722", "20260723", "20260724",
    "20260727", "20260728", "20260729", "20260730", "20260731",
)
PERIOD_STATUS = "EXPLORATORY_DESIGN_ONLY"

CANDIDATES = ("PFQ_UPDATE_Q70", "PFQ_FLOW_Q30", "PFQ_JOINT")
EXIT_CANDIDATES = ("PFQ_X_PROGRESS_STRUCT", "PFQ_X_PROTECT")

# Precommitted flow support (not tuned post-hoc)
MIN_CLASSIFIED_TRADES_30S = 3
UPDATE_Q = 0.70
FLOW_Q = 0.30

REACHABILITY = {
    "unique_overlap_clusters_min": 30,
    "entry_observation_episodes_min": 50,
    "entry_days_min": 5,
    "max_day_share_max": 0.40,
    "max_symbol_share_max": 0.30,
    "flow_ratio_valid_rate_min": 0.80,
    "path_complete_rate_min": 0.80,
}

STRUCTURAL = {
    "lot": 100,
    "cost_bps_once": 5.0,
    "cap": 5,
    "max_hold_sec": 300.0,
    "retention_sec": 10.0,
}

# P2 EXIT thresholds — path-distribution derived placeholders fixed at precommit
# (filled after path diagnosis from build-only percentiles; sealed before joint PnL)
EXIT_THRESHOLDS: dict[str, Any] = {
    "hard_stop_bps": -25.0,
    "max_hold_sec": 300.0,
    "progress_deadline_sec": 55.0,
    "progress_min_net_bps": 0.0,
    "update_deterioration_max": 1,  # price_update_count_10s at eval <= this vs entry activity
    "protect_giveback_frac": 0.55,
    "protect_min_net_bps_for_arm": 5.0,
    "level_break_ticks": 1.0,
}

PROSPECTIVE_MIN = {
    "unused_business_days_min": 5,
    "completed_trades_min": 30,
    "entry_days_min": 5,
    "max_day_share_max": 0.40,
    "max_symbol_share_max": 0.30,
}


def p1_body(*, feature_contract_sha: str, thresholds: dict, registry: list) -> dict[str, Any]:
    return {
        "precommit_type": "P1_ENTRY_PRECOMMIT",
        "document_id": DOCUMENT_ID,
        "family_id": FAMILY_ID,
        "study_type": STUDY_TYPE,
        "period": "20260721-20260731",
        "period_status": PERIOD_STATUS,
        "setup": "PULLBACK_RECLAIM",
        "anchor_base": "TAER_P3_compatible_pullback_reclaim_R10",
        "taer_v1_not_resurrected": True,
        "range_not_implemented": True,
        "feature_contract_sha256": feature_contract_sha,
        "candidates": registry,
        "threshold_derivation": {
            "method": "build_only_feature_quantile",
            "update_q": UPDATE_Q,
            "flow_q": FLOW_Q,
            "no_date_symbol_session_thresholds": True,
            "no_pnl_in_threshold": True,
            "thresholds": thresholds,
        },
        "reachability_gates": REACHABILITY,
        "candidate_count_ceiling": 3,
        "min_classified_trades_30s": MIN_CLASSIFIED_TRADES_30S,
        "structural": STRUCTURAL,
        "runtime_forbids": [
            "logistic_coefficients",
            "model_probability",
            "auc_threshold",
            "scenario_id",
        ],
        "economics_opened_before_precommit": False,
    }
