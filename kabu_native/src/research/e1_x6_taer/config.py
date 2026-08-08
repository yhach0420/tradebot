"""TAER — Trigger Anchored Entry & Retention (Study Revision V1).

Does not relax FCRR SELLING_EXHAUSTED thresholds. Changes ENTRY generation:
actual cross first, then lookback setup / exhaustion / dynamic evidence.
"""
from __future__ import annotations

from typing import Any

STUDY_REVISION = "E1_X6_TRIGGER_ANCHORED_ENTRY_EXIT_JOINT_V1"
CANDIDATE_FAMILY = "TAER"
FAMILY_NAME = "Trigger Anchored Entry & Retention"
DOCUMENT_ID = "E1_X6_FCRR_IMPLEMENTATION_SPEC"
DOCUMENT_VERSION = "1.2"
PLAN_DOCUMENT_ID = "E1_X6_VALIDATION_PLAN"
PLAN_VERSION = "1.3"

DAYS = (
    "20260721", "20260722", "20260723", "20260724",
    "20260727", "20260728", "20260729", "20260730", "20260731",
)

# Anchor reference-high windows (seconds) for range-high B
RANGE_HIGH_LOOKBACKS = (30.0, 60.0, 120.0, 180.0)

ANCHOR_SUPPORT = {
    "unique_anchor_episodes_min": 150,
    "anchor_days_min": 5,
}

ENTRY_PROFILE_SUPPORT = {
    "entry_observation_episodes_min": 50,
    "entry_days_min": 4,
    "max_day_share_max": 0.40,
}

FINAL_GATES = {
    "anchor_episodes_min": 150,
    "entry_observation_episodes_min": 50,
    "entry_days_min": 4,
    "max_day_share_max": 0.40,
    "path_complete_rate_min": 0.80,
}

PROFILES = ("TAER_P0", "TAER_P1", "TAER_P2", "TAER_P3")
PROFILE_STRICTNESS = {"TAER_P0": 0, "TAER_P1": 1, "TAER_P2": 2, "TAER_P3": 3}
# P0 diagnostic-only; not adoptable
ADOPTABLE_PROFILES = ("TAER_P1", "TAER_P2", "TAER_P3")

RETENTION_SEC = {"R0": 0.0, "R10": 10.0, "R20": 20.0}
# R0 diagnostic baseline only
ADOPTABLE_RETENTION = ("R10", "R20")

SETUP_TYPES = ("PULLBACK_RECLAIM", "RANGE_BREAKOUT", "NO_VALID_SETUP")

EXHAUSTION_EVIDENCE = ("E1", "E2", "E3", "E4", "E5", "E6")
DYNAMIC_EVIDENCE = ("volume_impulse", "uptick_improvement", "price_update_acceleration", "bid_support")

SCENARIO_IDS = (
    "S1_IMMEDIATE_CONTINUATION",
    "S2_RETEST_THEN_CONTINUATION",
    "S3_FALSE_BREAKOUT",
    "S4_NO_PROGRESS",
    "S5_SPIKE_GIVEBACK",
    "S6_LATE_CONTINUATION",
    "S7_CENSORED_OR_OTHER",
)

EXIT_CANDIDATES = ("X_STRUCTURAL", "X_CONTINUATION", "X_HYBRID")

# Hard cap on joint combinations (precommitted; no post-hoc expansion)
MAX_JOINT_COMBOS = 36  # e.g. 3 profiles × 2 setups × 2 retentions × 3 exits

FOLD_BUILDS = {
    "F1": ["20260721", "20260722", "20260723", "20260724"],
    "F2": ["20260721", "20260722", "20260723", "20260724", "20260727"],
    "F3": ["20260721", "20260722", "20260723", "20260724", "20260727", "20260728"],
    "F4": ["20260721", "20260722", "20260723", "20260724", "20260727", "20260728", "20260729"],
    "F5": ["20260721", "20260722", "20260723", "20260724", "20260727", "20260728", "20260729", "20260730"],
}
FOLD_CONFIRM = {
    "F1": "20260727", "F2": "20260728", "F3": "20260729",
    "F4": "20260730", "F5": "20260731",
}

STRUCTURAL = {
    "lot": 100,
    "cost_bps_once": 5.0,
    "cap": 5,
    "path_horizon_sec": 300.0,
    "spread_widen_mult_invalidate": 2.0,
}


def p1_taer_precommit_body() -> dict[str, Any]:
    return {
        "precommit_type": "P1_ENTRY_PRECOMMIT",
        "study_revision": STUDY_REVISION,
        "candidate_family": CANDIDATE_FAMILY,
        "plan_document_id": PLAN_DOCUMENT_ID,
        "plan_version": PLAN_VERSION,
        "document_id": DOCUMENT_ID,
        "document_version": DOCUMENT_VERSION,
        "profiles": list(PROFILES),
        "profile_strictness_order": list(PROFILES),
        "adoptable_profiles": list(ADOPTABLE_PROFILES),
        "p0_diagnostic_only": True,
        "retention_sec": dict(RETENTION_SEC),
        "adoptable_retention": list(ADOPTABLE_RETENTION),
        "r0_diagnostic_only": True,
        "setup_types": list(SETUP_TYPES),
        "exhaustion_evidence": list(EXHAUSTION_EVIDENCE),
        "dynamic_evidence": list(DYNAMIC_EVIDENCE),
        "scenario_ids": list(SCENARIO_IDS),
        "exit_candidates": list(EXIT_CANDIDATES),
        "max_joint_combos": MAX_JOINT_COMBOS,
        "anchor_support": ANCHOR_SUPPORT,
        "entry_profile_support": ENTRY_PROFILE_SUPPORT,
        "final_gates": FINAL_GATES,
        "selling_exhausted_state_required": False,
        "fcrr_se_thresholds_relaxed": False,
        "economics_opened_before_precommit": False,
        "selection_method": {
            "per_fold": "choose_strictest_adoptable_profile_meeting_support_on_build_days",
            "forbidden": ["pnl", "pf", "win_rate", "exit_pnl"],
        },
        "structural": STRUCTURAL,
        "frozen_prior": {
            "phase_a": "FCRR_SE_REACHABILITY_AUDIT_COMPLETE",
            "phase_b": "FCRR_SEQUENTIAL_ENTRY_FAMILY_UNREACHABLE",
        },
    }
