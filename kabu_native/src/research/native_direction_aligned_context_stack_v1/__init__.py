"""NATIVE_DIRECTION_ALIGNED_CONTEXT_STACK_V1.

New development. Direction-normalized context stack. Not a strategy.
Closes unsigned R14/T15 permanently. Does not retune their thresholds.
"""
from __future__ import annotations

from research.current_day1_information_close_v1 import FEATURE_MINING_CLOSED
from research.run_20260914_day2_futures_plus_first_live_breadth_v1 import TRADING_DATE as LIVE_TRADING_DATE
from research.support_resistance_face_valid_first_interaction_rebuild_v1 import CONFIRM_ATR, ZONE_HALF_ATR

PROGRAM_ID = "NATIVE_DIRECTION_ALIGNED_CONTEXT_STACK_V1"
ANALYSIS_ID = "NATIVE_DIRECTION_ALIGNED_CONTEXT_STACK_V1"
PARENT_ANALYSIS_ID = "R14_TREND_INCREMENTALITY_RCA_V1"
PARENT_VERDICT = "R14_DIRECTION_SEMANTICS_INVALID_V1"

EXPECTED_SPLIT_SHA256 = "2c9bd8f4ce7c86116833b54e1d41e4cbc4b59c3e504b769140fea435d375b3b4"
EXPECTED_BLOCK_SHA256 = "e7a16d860f73acc592c94ac7a717820f0dcea263fd60ab39b0cf7af980912718"
EXPECTED_EVENT_GENERATOR_SHA256 = "b6e0ebd51956505883ffdac46c442e3c9d9311284d69ad3f89bd41fab0d1a829"
EXPECTED_DETECTOR_SHA256 = "6e3060b71f8a4787b09d6b3c0f3ba9fa84c87712bf0f11b0165bfbfd1e0bf47e"
EXPECTED_STATE_MACHINE_SHA256 = "3c065f3757083fa6da792f9450e07504e902ea93d48391467a6bb7dca6e6a5b8"
EXPECTED_TREE_R14_SHA256 = "f6abfa7c2f34363a706ec67e70d9a5b0fb9988e6217abd7fd265be63161f318e"

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

TREE_MAX_DEPTH = 3
TREE_MIN_LEAF_ABS = 500
TREE_MIN_LEAF_FRAC = 0.01
TREE_CRITERION = "gini"
TREE_RANDOM_STATE = 20260917
MAX_FAVORABLE_RULES = 3
BOOT_N = 400
BOOT_SEED = 20260917
RNG_REL_TOL = 0.50

FEATURE_NAMES = (
    "aligned_r5",
    "aligned_r15",
    "aligned_prior5_ret",
    "tv_clock_pctl",
    "event_range_rel",
    "break_distance_bps",
    "close_location_strength",
    "prior5_range_rel",
    "aligned_vwap_bps",
    "ahead_sr_distance_atr",
    "ahead_sr_missing",
    "behind_sr_distance_atr",
    "behind_sr_missing",
    "breaking_ahead_sr",
    "post_break_retest_aligned",
    "aligned_market_rel",
    "aligned_sector_rel",
)

RAW_DIRECTIONAL_FORBIDDEN = ("r5", "r15", "prior5_ret", "vwap_bps", "mkt_rel", "sec_rel")

FAMILIES = {
    "trend": ("aligned_r5", "aligned_r15", "aligned_prior5_ret"),
    "participation": ("tv_clock_pctl",),
    "event_strength": ("event_range_rel", "break_distance_bps", "close_location_strength"),
    "sr": (
        "ahead_sr_distance_atr",
        "ahead_sr_missing",
        "behind_sr_distance_atr",
        "behind_sr_missing",
        "breaking_ahead_sr",
        "post_break_retest_aligned",
    ),
    "vwap": ("aligned_vwap_bps",),
    "market_sector": ("aligned_market_rel", "aligned_sector_rel"),
    "local": ("prior5_range_rel",),
}

CASE_FOUND = "DIRECTION_ALIGNED_CONTEXT_MECHANISM_FOUND_V1"
CASE_PARTIAL = "DIRECTION_ALIGNED_CONTEXT_PARTIAL_MECHANISM_V1"
CASE_NONE = "DIRECTION_ALIGNED_CONTEXT_NO_INCREMENTAL_RULE_V1"
CASE_UNSTABLE = "DIRECTION_ALIGNED_CONTEXT_DISCOVERY_UNSTABLE_V1"

NEXT_RCA = "DIRECTION_ALIGNED_CONTEXT_MECHANISM_RCA_V1"
NEXT_STOP = "STOP_NATIVE_DIRECTION_ALIGNED_CONTEXT_STACK_V1"

OLD_CONFIRMATION_OPENED = False
FROZEN_VALIDATION_OPENED = False
KABU50_APPLIED = False
PNL_OPTIMIZATION = False
THRESHOLD_RETUNED = False
R14_REPAIRED = False
FEATURE_MINING = FEATURE_MINING_CLOSED
LIVE_DATE = LIVE_TRADING_DATE
