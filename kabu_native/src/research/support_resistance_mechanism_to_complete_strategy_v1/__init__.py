"""SUPPORT_RESISTANCE_MECHANISM_TO_COMPLETE_STRATEGY_V1.

Convert A2 and C1 into complete causal strategy candidates.
Not Confirmation. Not Frozen Validation. Not a live strategy.
"""
from __future__ import annotations

from research.current_day1_information_close_v1 import FEATURE_MINING_CLOSED
from research.run_20260914_day2_futures_plus_first_live_breadth_v1 import TRADING_DATE as LIVE_TRADING_DATE
from research.support_resistance_face_valid_first_interaction_rebuild_v1 import CONFIRM_ATR, ZONE_HALF_ATR

PROGRAM_ID = "SUPPORT_RESISTANCE_MECHANISM_TO_COMPLETE_STRATEGY_V1"
ANALYSIS_ID = "SUPPORT_RESISTANCE_MECHANISM_TO_COMPLETE_STRATEGY_V1"
PARENT_ANALYSIS_ID = "SUPPORT_RESISTANCE_MATCHED_SEPARATION_NOT_A_STRATEGY_V1"
PARENT_VERDICT = "SR_A_AND_C_EXECUTABLE_MECHANISMS_SUPPORTED_V1"

EXPECTED_SPLIT_SHA256 = "2c9bd8f4ce7c86116833b54e1d41e4cbc4b59c3e504b769140fea435d375b3b4"
EXPECTED_BLOCK_SHA256 = "e7a16d860f73acc592c94ac7a717820f0dcea263fd60ab39b0cf7af980912718"
EXPECTED_DETECTOR_SHA256 = "6e3060b71f8a4787b09d6b3c0f3ba9fa84c87712bf0f11b0165bfbfd1e0bf47e"
EXPECTED_STATE_MACHINE_SHA256 = "3c065f3757083fa6da792f9450e07504e902ea93d48391467a6bb7dca6e6a5b8"
EXPECTED_PRIMARY_FIRST_TEST_N = 2713

FROZEN_CONFIRM_ATR = 0.75
FROZEN_ZONE_HALF_ATR = 0.15
assert CONFIRM_ATR == FROZEN_CONFIRM_ATR
assert ZONE_HALF_ATR == FROZEN_ZONE_HALF_ATR

LUNCH_POLICY = "HOLD_THROUGH_LUNCH_RESUME_PM"
SESSION_FLAT = "15:20"
ENTRY_EXECUTION = "HISTORICAL_NEXT_BAR_OPEN_EXECUTION"
TARGET_FILL_CLASS = "HISTORICAL_STRUCTURAL_TARGET_TOUCH"
A1_CLASSIFICATION = "HISTORICAL_PASSIVE_EXECUTION_APPROXIMATION"
SHORT_LABEL = "HISTORICAL_SHORT_RESEARCH_ONLY"

CAP = 3
SHARES = 100
X1_STRESS_BPS = 8.0
TIE_SEED = "SR_A2_C1_SEEDED_HASH_ORDER_V1"
CAP_TIE_MATERIAL_REL = 0.25
CATASTROPHIC_MEDIAN_YEN = -20_000.0
EVAL_BLOCKS = ("D1", "D2", "D3", "D4")
COHERENCE_BLOCKS = ("D2", "D3", "D4")

STRATEGY_A2 = "SR_A2_COMPLETE"
STRATEGY_C1 = "SR_C1_COMPLETE"
STRATEGY_COMBINED = "SR_A2_C1_COMBINED"
STRATEGY_IDS = (STRATEGY_A2, STRATEGY_C1, STRATEGY_COMBINED)

MECH_RANK = {"A2": 0, "C1": 1}

CASE_A = "SR_A2_COMPLETE_STRATEGY_CANDIDATE_V1"
CASE_C = "SR_C1_COMPLETE_STRATEGY_CANDIDATE_V1"
CASE_BOTH = "SR_A2_AND_C1_COMPLETE_STRATEGY_CANDIDATES_V1"
CASE_COMBINED = "SR_A2_C1_COMBINED_COMPLETE_STRATEGY_CANDIDATE_V1"
CASE_NONE = "SR_PATH_SEPARATION_NOT_COMPLETE_STRATEGY_V1"

NEXT_CONFIRM = "SUPPORT_RESISTANCE_SECONDARY_CONFIRMATION_NOT_PRISTINE_V1"
NEXT_STOP = "STOP_SUPPORT_RESISTANCE_STRATEGY_PATH_V1"

EVIDENCE_A2 = "STRONGER_DISCOVERY_MECHANISM"
EVIDENCE_C1 = "SUPPORTED_DISCOVERY_MECHANISM_WITH_MULTIPLE_TESTING_CAVEAT"
C_HOLM_P = 0.07

OLD_CONFIRMATION_OPENED = False
FROZEN_VALIDATION_OPENED = False
KABU50_APPLIED = False
DETECTOR_RETUNED = False
MATCHABILITY_USED_AS_ENTRY_FILTER = False
PNL_BASED_RULE_CHANGE = False
FEATURE_MINING = FEATURE_MINING_CLOSED
LIVE_DATE = LIVE_TRADING_DATE

STRATEGY_SPEC = {
    "entry": ENTRY_EXECUTION,
    "same_bar_entry": False,
    "a2": "confirmed first-touch rejection; support LONG / resistance SHORT; next bar open",
    "c1": "BREAK CLEAR RETEST HOLD; bullish LONG / bearish SHORT; next bar open",
    "matchability_filter": False,
    "invalidation_a2_long": "completed close below support zone lower boundary; EXIT next open",
    "invalidation_a2_short": "completed close above resistance zone upper boundary; EXIT next open",
    "invalidation_c1_long": "completed close below former resistance lower boundary; EXIT next open",
    "invalidation_c1_short": "completed close above former support upper boundary; EXIT next open",
    "structural_target": "nearest pre-known opposing active zone near-boundary at ENTRY",
    "no_target": "remain until invalidation or 15:20; do not reject ENTRY",
    "target_fill": TARGET_FILL_CLASS,
    "target_not_on_entry_bar": True,
    "no_price_improvement": True,
    "ambiguity": "INVALIDATION_WINS",
    "lunch": LUNCH_POLICY,
    "session_close": SESSION_FLAT,
    "cap": CAP,
    "shares": SHARES,
    "same_symbol": 1,
    "reentry": "no same symbol x zone x day after fill; no resurrect blocked signals",
    "cap_blocked": "skip; no waiting list; no delayed fill",
    "tie_order": "entry_eligible_time, symbol ascending, A2 before C1",
    "cost": "X0_GROSS and X1_8BPS_EXECUTION_STRESS; 8bps is research friction not observed cost",
    "kabu50": False,
    "no_ema_vwap_time_stop": True,
    "no_atr_zone_center_far_boundary_grid": True,
}


__all__ = [
    "PROGRAM_ID",
    "ANALYSIS_ID",
    "PARENT_VERDICT",
    "EXPECTED_DETECTOR_SHA256",
    "EXPECTED_STATE_MACHINE_SHA256",
    "STRATEGY_IDS",
    "CASE_NONE",
    "NEXT_CONFIRM",
    "NEXT_STOP",
]
