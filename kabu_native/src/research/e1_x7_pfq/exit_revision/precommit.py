"""Precommit for Single EXIT Revision — sealed before economics."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from research.e1_x6_provisional.util import sha256_obj

from . import (
    ANALYSIS_ID,
    ARM_NET_BPS,
    BASELINE_EXIT,
    BASELINE_PAIR,
    CANDIDATE_ID,
    FLOOR_NET_BPS,
    REVISION_ID,
    REVISION_PAIR,
    SOURCE_BRIDGE_RUN,
    SOURCE_EXIT_GATE_RUN,
    SOURCE_VERDICT,
)

JST = ZoneInfo("Asia/Tokyo")


def build_precommit(
    *,
    source_identity_sha: str,
    source_candidate_sha: str,
    source_path_sha: str,
) -> dict[str, Any]:
    body = {
        "analysis_id": ANALYSIS_ID,
        "precommit_type": "PFQ_SINGLE_EXIT_REVISION_PRECOMMIT",
        "precommit_at_jst": datetime.now(JST).isoformat(),
        "revision_id": REVISION_ID,
        "source_bridge_run": SOURCE_BRIDGE_RUN,
        "source_exit_gate_run": SOURCE_EXIT_GATE_RUN,
        "source_verdict": SOURCE_VERDICT,
        "source_identity_sha": source_identity_sha,
        "source_candidate_sha": source_candidate_sha,
        "source_path_sha": source_path_sha,
        "candidate": CANDIDATE_ID,
        "entry_rules_frozen": "price_update_count_10s >= 8",
        "baseline_exit_definition": {
            "exit": BASELINE_EXIT,
            "pair": BASELINE_PAIR,
            "unchanged": [
                "RECLAIM_LEVEL_BREAK", "PULLBACK_LOW_BREAK", "HARD_STOP",
                "NO_PROGRESS_UPDATE_DEAD", "MAX_HOLD", "SESSION_END",
            ],
        },
        "revision_exit_definition": {
            "exit": REVISION_ID,
            "pair": REVISION_PAIR,
            "arm": f"executable_net_pnl_bps >= +{ARM_NET_BPS}",
            "floor": f"after arm, executable_net_pnl_bps <= {FLOOR_NET_BPS} -> PLUS5_BREAKEVEN_FLOOR",
            "no_alt_thresholds": True,
        },
        "state_transition": [
            "ENTRY: profit_floor_armed=false",
            "first net>=+5: arm (sticky)",
            "after arm first net<=0: PLUS5_BREAKEVEN_FLOOR",
        ],
        "exit_priority": [
            "RECLAIM_LEVEL_BREAK",
            "PULLBACK_LOW_BREAK",
            "HARD_STOP",
            "MAX_HOLD",
            "SESSION_END",
            "PLUS5_BREAKEVEN_FLOOR",
            "NO_PROGRESS_UPDATE_DEAD",
        ],
        "price_contract": {
            "entry": "canonical best_ask at entry",
            "exit": "fresh canonical best_bid same symbol/day/session",
        },
        "cost_contract": {"bps_once": 5.0, "lot": 100},
        "economic_gates": {
            "total_pnl_yen_100_gt_0": True,
            "pf_ge_1_10": True,
            "positive_days_ge_5_of_9": True,
            "daily_median_gt_0": True,
            "lodo_all_remaining_ge_0": True,
            "ex_top1_trade_gt_0": True,
            "ex_top1_symbol_gt_0": True,
            "ex_top1_day_ge_0": True,
            "max_day_share_le_0_40": True,
            "max_symbol_share_le_0_30": True,
        },
        "mechanism_gates": {
            "giveback_n_31": True,
            "prevented_ge_16": True,
            "positive_to_nonpositive_eq_0": True,
            "baseline_identity": True,
            "revision_integrity": True,
            "ab_determinism": True,
        },
        "verdict_rules": {
            "identity": "E1_X7_PFQ_REVISION_BASELINE_IDENTITY_MISMATCH",
            "mechanism_fail": "E1_X7_PFQ_EXIT_REVISION_MECHANISM_FAILED",
            "econ_fail": "E1_X7_PFQ_REVISED_PAIR_NOT_ECONOMICALLY_ROBUST",
            "all_pass": "E1_X7_PFQ_REVISED_PAIR_DESIGN_ELIGIBLE",
        },
        "period_status": "DESIGN_DIAGNOSTIC_ONLY",
        "prospective": False,
        "shadow": False,
        "forward": False,
        "outcomes_opened_before_precommit": False,
        "no_threshold_search": True,
        "no_285a_special_case": True,
    }
    body["precommit_sha256"] = sha256_obj(body)
    return body
