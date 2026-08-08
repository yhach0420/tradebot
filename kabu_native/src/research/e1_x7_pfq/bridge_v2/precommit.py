"""Bridge V2 precommit — sealed before outcomes / bootstrap / verdict."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from research.e1_x6_provisional.util import sha256_obj

from . import (
    ANALYSIS_ID,
    BOOTSTRAP_REPS,
    BOOTSTRAP_SEED,
    COST_BPS,
    FAILURE_PRIORITY,
    FRESHNESS_MAX_SEC,
    FROZEN_PAIRS,
    FROZEN_THRESHOLDS,
    HARD_EXITS,
    KNOWN_COUNTS,
    MAX_HOLD_SEC,
    SOFT_EXITS,
    SOURCE_RUN,
)

JST = ZoneInfo("Asia/Tokyo")


def build_precommit() -> dict[str, Any]:
    body = {
        "analysis_id": ANALYSIS_ID,
        "precommit_type": "PFQ_REALIZABILITY_BRIDGE_AUDIT_V2_PRECOMMIT",
        "precommit_at_jst": datetime.now(JST).isoformat(),
        "purpose": [
            "1. Realizable edge before stop-out vs",
            "2. Oracle edge from volatility / event-density rebound only",
        ],
        "source_run": SOURCE_RUN,
        "period": "20260721-20260731",
        "unused_data_forbidden": True,
        "prospective_status": "BLOCKED_PENDING_REALIZABILITY_BRIDGE_AUDIT",
        "frozen_thresholds": FROZEN_THRESHOLDS,
        "candidate_definitions": {
            "PFQ_UPDATE_Q70": "price_update_count_10s >= 8",
            "PFQ_FLOW_Q30": "uptick_volume_ratio_30s <= 0.7991666666666666 AND classified>=3 AND ratio_valid",
            "PFQ_JOINT": "UPDATE AND FLOW (design support insufficient at n=41; no economic pairs)",
        },
        "pfq_joint_label": "PFQ_DESIGN_SUPPORT_INSUFFICIENT",
        "pfq_joint_actual_support": 41,
        "pfq_joint_precommitted_minimum": 50,
        "matched_parents": {
            "UPDATE_ELIGIBLE_PARENT": "pu10 valid AND path evaluable",
            "FLOW_ELIGIBLE_PARENT": "ratio_valid AND classified>=3 AND path evaluable",
            "JOINT_ELIGIBLE_PARENT": "pu10 valid AND ratio_valid AND classified>=3 AND path evaluable",
            "ALL_PULLBACK": "reference_only",
        },
        "event_time_outcome": "all canonical same-symbol/day/session bids after entry within freshness",
        "fixed_grid_outcome": {
            "step_sec": 1.0,
            "max_hold_sec": MAX_HOLD_SEC,
            "price": "last canonical best_bid at or before grid time",
            "freshness_max_sec": FRESHNESS_MAX_SEC,
            "no_interpolation": True,
            "no_unlimited_locf": True,
        },
        "first_touch_definitions": [
            "PLUS5_vs_MINUS10",
            "PLUS5_vs_MINUS15",
            "PLUS10_vs_MINUS10",
            "PLUS10_vs_MINUS15",
        ],
        "plus5_vs_minus5_forbidden": True,
        "hard_exits": sorted(HARD_EXITS),
        "soft_exits": sorted(SOFT_EXITS),
        "counterfactual_rules": {
            "track_after_soft_exit_until_earliest_hard": True,
            "soft_exit_premature": "net+5 before hard invalidation after soft exit",
            "recovery_after_invalidation_not_exit_miss": True,
        },
        "failure_classification_priority": FAILURE_PRIORITY,
        "bootstrap": {
            "unit": "day_x_symbol",
            "reps": BOOTSTRAP_REPS,
            "seed": BOOTSTRAP_SEED,
        },
        "entry_path_support_requires": [
            "fixed_grid first-touch difference_ci95 lower > 0 for at least one metric",
            "day difference positive on >= 7/9 days",
        ],
        "best_net_improvement_alone_not_entry_support": True,
        "cost_bps_once": COST_BPS,
        "frozen_pairs_only": [f"{a}|{b}" for a, b in FROZEN_PAIRS],
        "known_counts": KNOWN_COUNTS,
        "no_candidate_or_exit_change": True,
        "no_285a_exclusion": True,
        "outcomes_opened_before_precommit": False,
    }
    body["precommit_sha256"] = sha256_obj(body)
    return body
