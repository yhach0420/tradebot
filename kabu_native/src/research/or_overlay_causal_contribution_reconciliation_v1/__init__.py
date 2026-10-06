"""OR_OVERLAY_CAUSAL_CONTRIBUTION_RECONCILIATION_V1. Exact Production overlay only."""
from __future__ import annotations

ANALYSIS_ID = "OR_OVERLAY_CAUSAL_CONTRIBUTION_RECONCILIATION_V1"

# Frozen from Phase687W27 or_era (paper journals). Do not expand.
OR_ERA_DAYS = (
    "20260625",
    "20260629",
    "20260630",
    "20260701",
    "20260706",
    "20260708",
    "20260709",
    "20260710",
    "20260714",
)

# Exact W27 session index rows with or_overlay=True on OR-era days.
W27_OR_ERA_SESSIONS = (
    ("20260625", "live_session_080340", "am"),
    ("20260625", "live_session_122535", "pm"),
    ("20260629", "live_session_080236", "am"),
    ("20260629", "live_session_122526", "pm"),
    ("20260630", "live_session_091118", "am"),
    ("20260701", "live_session_080616", "am"),
    ("20260706", "live_session_080937", "am"),
    ("20260708", "live_session_081852", "am"),
    ("20260708", "live_session_122537", "pm"),
    ("20260709", "live_session_082103", "am"),
    ("20260709", "live_session_122530", "pm"),
    ("20260710", "live_session_084821", "am"),
    ("20260710", "live_session_122525", "pm"),
    ("20260714", "live_session_082256", "am"),
    ("20260714", "live_session_122532", "pm"),
)

CASE_A = "PRODUCTION_OR_OVERLAY_CAUSAL_CONTRIBUTION_SUPPORTED"
CASE_B = "PRODUCTION_OR_OVERLAY_CAUSAL_CONTRIBUTION_NOT_SUPPORTED"
CASE_C = "OR_OVERLAY_CAUSAL_RECONCILIATION_COVERAGE_FAILED"
CASE_D = "OR_OVERLAY_CAUSAL_RECONCILIATION_SELECTION_UNSTABLE"
CASE_E1 = "OR_OVERLAY_CAUSAL_RECONCILIATION_RECOVERABLE_INTEGRITY_GAP"
CASE_E2 = "OR_OVERLAY_CAUSAL_RECONCILIATION_UNRESOLVABLE"

NEXT_IF_E2 = "E1_X7_PFQ_FULL_CAUSAL_RECONCILIATION_V1"
OR_CLOSED_STATUS = "PREEXISTING_PARTIALLY_TESTED_UNRESOLVABLE"

STRESS_DAYS = ("20260828", "20260831", "20260901", "20260902")
FORBIDDEN_FUTURE_PREFIX = "20260903"

REQUIRED_STREAM_FIELDS = (
    "stage2_pbv2_evaluation",
    "pbv2_accept",
    "pbv2_reject",
    "reject_reason",
    "or_overlay_evaluation_eligibility",
    "or_predicate_inputs",
    "or_accept_reject",
    "event_timestamp",
    "symbol",
    "same_symbol_state",
    "pbv2_lane_occupancy",
    "or_lane_occupancy",
    "fill_event",
    "exit_event",
    "slot_release_event",
)

assert ANALYSIS_ID == "OR_OVERLAY_CAUSAL_CONTRIBUTION_RECONCILIATION_V1"
assert len(OR_ERA_DAYS) == 9
assert len(W27_OR_ERA_SESSIONS) == 15
assert CASE_E1 != CASE_E2
assert NEXT_IF_E2 != "OPEN_STRENGTH_LEADER_FULL_STRATEGY_V1"
assert STRESS_DAYS[0] == "20260828"
