"""Precommit for EXIT Gate Reconciliation — sealed before pair gates."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from research.e1_x6_provisional.util import sha256_obj

from . import (
    ANALYSIS_ID,
    CANDIDATE_ID,
    GATE_MIN_REPAIRABLE_DAYS,
    GATE_MIN_REPAIRABLE_FRACTION,
    GATE_MIN_REPAIRABLE_N,
    GATE_MIN_TOP_MECH_FRACTION,
    MECH_GIVEBACK,
    MECH_SOFT,
    PAIRS,
    SOURCE_BRIDGE_RUN,
    SOURCE_VERDICT,
)

JST = ZoneInfo("Asia/Tokyo")


def build_precommit(*, source_report_sha: str, source_audit_sha: str) -> dict[str, Any]:
    body = {
        "analysis_id": ANALYSIS_ID,
        "precommit_type": "PFQ_EXIT_GATE_RECONCILIATION_PRECOMMIT",
        "precommit_at_jst": datetime.now(JST).isoformat(),
        "purpose": "Re-evaluate EXIT revision eligibility per pair / unique episode; no EXIT logic change",
        "source_bridge_run": SOURCE_BRIDGE_RUN,
        "source_verdict": SOURCE_VERDICT,
        "source_report_sha256": source_report_sha,
        "source_audit_sha256": source_audit_sha,
        "entry_support_preserved": True,
        "candidate_in_scope": CANDIDATE_ID,
        "candidates_out_of_scope": ["PFQ_FLOW_Q30", "PFQ_JOINT"],
        "pairs": list(PAIRS),
        "pair_specific_denominator": {
            "name": "ORACLE_PLUS5_REALIZED_LOSS_EPISODES",
            "rule": "fixed_grid_best_net_pnl_bps_300s >= +5 AND realized_net_pnl_bps < 0",
            "unique_within_pair": True,
        },
        "pair_specific_repairable_definition": {
            "classes": [MECH_SOFT, MECH_GIVEBACK],
            "soft_premature": "soft exit then first +5 before earliest hard invalidation",
            "giveback": "+5 reached before actual exit then non-positive realized",
            "hard_invalidation_recovery_excluded": True,
            "unique_episode_within_pair": True,
        },
        "unique_episode_rule": "within each pair, episode_id counted at most once; pairs not mixed for Gate",
        "mechanism_priority": [
            "If +5 before exit and non-positive end -> GIVEBACK",
            "Elif soft exit then +5 before hard -> SOFT_EXIT_PREMATURE",
            "Never both",
        ],
        "verdict_rules": {
            "none_pass": "E1_X7_PFQ_NO_QUALIFIED_EXIT_REVISION_BASELINE",
            "one_pass": "E1_X7_PFQ_EXIT_REVISION_BASELINE_CONFIRMED",
            "two_pass": "E1_X7_PFQ_MULTIPLE_EXIT_BASELINES_REVIEW_REQUIRED",
            "identity_fail": "E1_X7_PFQ_EXIT_GATE_IDENTITY_MISMATCH",
        },
        "gate_thresholds": {
            "entry_path_support": True,
            "repairable_n_min": GATE_MIN_REPAIRABLE_N,
            "repairable_days_min": GATE_MIN_REPAIRABLE_DAYS,
            "repairable_fraction_min": GATE_MIN_REPAIRABLE_FRACTION,
            "top_mechanism_fraction_min": GATE_MIN_TOP_MECH_FRACTION,
            "identity_integrity_pass": True,
            "ab_determinism_pass": True,
        },
        "combined_mixed_counting_for_gate": False,
        "exit_revision_implemented_this_run": False,
        "no_exit_threshold_search": True,
        "outcomes_opened_before_precommit": False,
    }
    body["precommit_sha256"] = sha256_obj(body)
    return body
