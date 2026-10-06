"""Diagnostic only. Does not create a strategy and does not place orders."""
from __future__ import annotations

from research.causal_driver_pb1.phase1.dates import assert_ingest_date_allowed

_ = assert_ingest_date_allowed

ANALYSIS_ID = "M3_ALPHA_ACTIONABILITY_PB1_COMPATIBILITY_V1"
FAILED_STRATEGY_SHA256 = "808fa33bc80c784e67886a0886a2cf60c4e3a6d4a6a657de54a847e5f753ccaf"
FAILED_VERDICT = "SECTOR_STATE_ALPHA_COMPLETE_STRATEGY_ECONOMIC_FEASIBILITY_FAIL_V1"

CASE_BLOCKED = "DIAGNOSTIC_BLOCKED_IDENTITY_MISMATCH"
CASE_ALPHA = "M3_ALPHA_EXECUTABLE_ACTIONABILITY_NOT_ESTABLISHED_V1"
CASE_PB1 = "M3_ALPHA_ACTIONABLE_BUT_PB1_V4_STRUCTURALLY_INCOMPATIBLE_V1"
CASE_RCA = "M3_PB1_ADAPTER_SEMANTICS_REQUIRES_RCA_V1"
NEXT_STOP = "STOP_M3_ALPHA_CURRENT_ARCHITECTURE_V1"
NEXT_DESIGN = "DESIGN_M3_NATIVE_SYMBOL_ENTRY_CONFIRMATION_ARCHITECTURE_V1"
NEXT_RCA = "RCA_M3_PB1_CAUSAL_ADAPTER_V1"
NEXT_BLOCKED = "STOP"

PRIMARY_HORIZON_MIN = 3
SENSITIVITY_HORIZONS = (1, 5)
COST_BPS = 8.0
# Frozen before outcomes. Near-zero means at most 1% of target-episodes.
SPARSE_MAX_FRACTION = 0.01
# Frozen before outcomes. Independent in-episode executions above this, against a 6-event funnel, are an adapter discrepancy.
ADAPTER_DISCREPANCY_MIN_N = 50

EXPECTED_FUNNEL = {
    "eligible_day_n": 437,
    "alpha_episode_n": 9272,
    "target_alpha_opportunity_n": 46360,
    "PB1_confirmed_n": 1,
    "ALPHA_DIRECTION_MISMATCH_n": 5,
    "E0_qualified_n": 0,
    "E1_qualified_n": 1,
    "filled_trade_n": 1,
}
