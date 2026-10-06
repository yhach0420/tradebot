"""NATIVE_CAUSAL_CONTEXT_STACK_DISCOVERY_V1.

Compact causal context stack around native price-displacement events.
D1 learns. D2-D4 locked replication. Not a strategy.
"""
from __future__ import annotations

from research.current_day1_information_close_v1 import FEATURE_MINING_CLOSED
from research.run_20260914_day2_futures_plus_first_live_breadth_v1 import TRADING_DATE as LIVE_TRADING_DATE
from research.support_resistance_face_valid_first_interaction_rebuild_v1 import CONFIRM_ATR, ZONE_HALF_ATR

PROGRAM_ID = "NATIVE_CAUSAL_CONTEXT_STACK_DISCOVERY_V1"
ANALYSIS_ID = "NATIVE_CAUSAL_CONTEXT_STACK_DISCOVERY_V1"
PARENT_ANALYSIS_ID = "NATIVE_PARTICIPATION_X_SR_CONTEXT_DISCOVERY_V1"
PARENT_VERDICT = "NO_STABLE_NATIVE_PARTICIPATION_SR_INTERACTION_V1"

EXPECTED_SPLIT_SHA256 = "2c9bd8f4ce7c86116833b54e1d41e4cbc4b59c3e504b769140fea435d375b3b4"
EXPECTED_BLOCK_SHA256 = "e7a16d860f73acc592c94ac7a717820f0dcea263fd60ab39b0cf7af980912718"
EXPECTED_DETECTOR_SHA256 = "6e3060b71f8a4787b09d6b3c0f3ba9fa84c87712bf0f11b0165bfbfd1e0bf47e"
EXPECTED_STATE_MACHINE_SHA256 = "3c065f3757083fa6da792f9450e07504e902ea93d48391467a6bb7dca6e6a5b8"

FROZEN_CONFIRM_ATR = 0.75
FROZEN_ZONE_HALF_ATR = 0.15
assert CONFIRM_ATR == FROZEN_CONFIRM_ATR
assert ZONE_HALF_ATR == FROZEN_ZONE_HALF_ATR

LUNCH_POLICY = "HOLD_THROUGH_LUNCH_RESUME_PM"
SESSION_FLAT = "15:20"
REFRACTORY_BARS = 5
TV_CLOCK_LOOKBACK_DAYS = 20
TV_CLOCK_MIN_OBS = 10

PRIMARY_METRIC = "p40_before_m20"
TRAIN_BLOCK = "D1"
EVAL_BLOCKS = ("D2", "D3", "D4")
DEV_BLOCKS = ("D1", "D2", "D3", "D4")

TREE_MAX_DEPTH = 3
TREE_MIN_LEAF_ABS = 500
TREE_MIN_LEAF_FRAC = 0.01
TREE_CRITERION = "gini"
TREE_RANDOM_STATE = 20260917
MAX_FAVORABLE_RULES = 3

BOOT_N = 400
BOOT_SEED = 20260917

FEATURE_NAMES = (
    "tv_clock_pctl",
    "dist_support_atr",
    "dist_resistance_atr",
    "inside_active_zone",
    "breaking_active_zone",
    "post_break_retest",
    "vwap_bps",
    "above_vwap",
    "r5",
    "r15",
    "prior5_range_rel",
    "prior5_ret",
    "pullback_from_extreme",
    "mkt_rel",
    "sec_rel",
)

FAMILIES = {
    "participation": ("tv_clock_pctl",),
    "sr": ("dist_support_atr", "dist_resistance_atr", "inside_active_zone", "breaking_active_zone", "post_break_retest"),
    "vwap": ("vwap_bps", "above_vwap"),
    "trend": ("r5", "r15"),
    "local": ("prior5_range_rel", "prior5_ret", "pullback_from_extreme"),
    "market_sector": ("mkt_rel", "sec_rel"),
}

CASE_FOUND = "NATIVE_CONTEXT_STACK_MECHANISM_FOUND_V1"
CASE_PARTIAL = "NATIVE_CONTEXT_STACK_PARTIAL_MECHANISM_V1"
CASE_NO_RULE = "NATIVE_CONTEXT_STACK_NO_STABLE_RULE_V1"
CASE_UNSTABLE = "CONTEXT_STACK_NOT_STABLE_EVEN_IN_DISCOVERY_V1"

NEXT_RCA = "NATIVE_CONTEXT_STACK_MECHANISM_RCA_V1"
NEXT_STOP = "STOP_NATIVE_CONTEXT_STACK_ARCHITECTURE_V1"

OLD_CONFIRMATION_OPENED = False
FROZEN_VALIDATION_OPENED = False
KABU50_APPLIED = False
DETECTOR_RETUNED = False
A2_REOPENED_AS_STRATEGY = False
C1_REOPENED = False
I2_REOPENED = False
PNL_OPTIMIZATION = False
FEATURE_MINING = FEATURE_MINING_CLOSED
LIVE_DATE = LIVE_TRADING_DATE
