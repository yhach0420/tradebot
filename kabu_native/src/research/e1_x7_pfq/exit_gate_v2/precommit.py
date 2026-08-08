"""Precommit for EXIT Gate Reconciliation V2."""
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
    REF_PROFITABLE_SOFT,
    SOURCE_BRIDGE_RUN,
    SOURCE_EXIT_GATE_RUN,
    SOURCE_VERDICT,
)

JST = ZoneInfo("Asia/Tokyo")


def build_precommit(
    *,
    source_run_sha: str,
    source_audit_sha: str,
    bridge_report_sha: str,
    bridge_audit_sha: str,
) -> dict[str, Any]:
    body = {
        "analysis_id": ANALYSIS_ID,
        "precommit_type": "PFQ_EXIT_GATE_RECONCILIATION_V2_PRECOMMIT",
        "precommit_at_jst": datetime.now(JST).isoformat(),
        "purpose": "Correct V1 Gate: repairable must be subset of ORACLE_PLUS5_REALIZED_LOSS denominator",
        "source_exit_gate_run": SOURCE_EXIT_GATE_RUN,
        "source_verdict": SOURCE_VERDICT,
        "source_run_sha256": source_run_sha,
        "source_audit_sha256": source_audit_sha,
        "bridge_run": SOURCE_BRIDGE_RUN,
        "bridge_report_sha256": bridge_report_sha,
        "bridge_audit_sha256": bridge_audit_sha,
        "overwrite_source_run": False,
        "corrected_subset_invariant": {
            "rule": "set(repairable_episode_ids)subseteq set(denominator_episode_ids)",
            "in_denominator_required": True,
            "out_of_denom_profitable_soft": REF_PROFITABLE_SOFT,
            "out_of_denom_forbidden_in_gate": True,
        },
        "denominator": {
            "name": "ORACLE_PLUS5_REALIZED_LOSS_EPISODES",
            "rule": "fixed_grid_best_net_pnl_bps_300s >= +5 AND realized_net_pnl_bps < 0",
        },
        "repairable_mechanisms": [MECH_SOFT, MECH_GIVEBACK],
        "candidate": CANDIDATE_ID,
        "pairs": list(PAIRS),
        "gate_thresholds": {
            "entry_path_support": True,
            "repairable_in_denominator_n_min": GATE_MIN_REPAIRABLE_N,
            "repairable_days_min": GATE_MIN_REPAIRABLE_DAYS,
            "repairable_fraction_min": GATE_MIN_REPAIRABLE_FRACTION,
            "top_mechanism_fraction_min": GATE_MIN_TOP_MECH_FRACTION,
        },
        "verdict_rules": {
            "progress_only": "E1_X7_PFQ_EXIT_REVISION_BASELINE_CONFIRMED",
            "none": "E1_X7_PFQ_NO_QUALIFIED_EXIT_REVISION_BASELINE",
            "two": "E1_X7_PFQ_MULTIPLE_EXIT_BASELINES_REVIEW_REQUIRED",
            "identity": "E1_X7_PFQ_EXIT_GATE_IDENTITY_MISMATCH",
        },
        "exit_revision_implemented_this_run": False,
        "outcomes_opened_before_precommit": False,
    }
    body["precommit_sha256"] = sha256_obj(body)
    return body
