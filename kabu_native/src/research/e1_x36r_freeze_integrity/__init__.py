"""E1_X36R Full Strategy Freeze Integrity & Concentration Reconciliation."""

ANALYSIS_ID = "E1_X36R_FULL_STRATEGY_FREEZE_INTEGRITY"
DOCUMENT_ID = "E1_X36R_FREEZE_INTEGRITY"

SOURCE_X36_RUN = "e1x36_joint_20260808_203828_A"
V1_SHA = "51c138b612688f4b6474d3eef2e56ac9a5525dac715ec999ed0e9b49342f4412"

ENTRY_SHA = "f2887bb2be539cc173aee438a43ee8afb8cfa2b8c31380937ecd843e90dd9b29"
ANCHOR_SHA = "4a2f176ef6f52458cb0e5b38764275e6ddafc01e1849693965b116089514eac2"
EXEC_SHA = "040fa4b061e575d3f6cdb2a11ffd3f862da5351b298567b31363de923a590869"
EXIT_SHA = "2c9fcc6e92971c252c8df93716066dda515fcbff0283d748b03293379c5eb62c"
BOARD_MAPPING_SHA = "6adf923a0fa850e5882d217b44030cb36a3287c967c4710c8951b305808a0a97"

FORBIDDEN_FROM = "20260810"
EXPECTED_SIGNALS = 3453
EXPECTED_FILLS = 330

# Frozen architecture — no retune
FINAL_FAMILY = "A1_FILL"
FINAL_FEATURE_SET = "BOARD_PRICE"
FINAL_FEATURES = (
    "spread_bps",
    "imbalance",
    "mid_ret_60s",
    "mid_ret_180s",
    "event_rate_60s",
    "log_bid_qty",
)
FINAL_REG = 1.0
FINAL_KIND = "fill"

# X36 outer fold selected specs (frozen — no re-selection)
OUTER_SPECS = {
    "A": {"family": "A1_FILL", "feature_set": "BOARD_PRICE", "reg": 1.0},
    "B": {"family": "A1_FILL", "feature_set": "BOARD_PRICE", "reg": 1.0},
    "C": {"family": "A1_FILL", "feature_set": "COMPACT", "reg": 1.0},
    "D": {"family": "A1_FILL", "feature_set": "BOARD_PRICE", "reg": 0.1},
}

# Cross-fitted SoT targets from X36
X36_CROSS = {
    "admitted": 689,
    "fills": 148,
    "total_pnl_yen": 1821750.0000000023,
    "opp_bps": 3.0651710951898155,
    "pf": 2.387205317873527,
    "positive_days": 10,
    "hard_cap_violations": 0,
}

SYMBOL_OF_INTEREST = "285A"

VERDICT_PASS = "E1_X36R_FULL_STRATEGY_EXACTLY_FROZEN"
VERDICT_FREEZE_FAIL = "E1_X36R_MODEL_FREEZE_INCOMPLETE"
VERDICT_PROVENANCE_FAIL = "E1_X36R_FINAL_SELECTION_PROVENANCE_UNRESOLVED"
VERDICT_SYMBOL_DEP = "E1_X36R_HISTORICAL_SUPPORT_SYMBOL_DEPENDENT"

NEXT_PASS = "PROSPECTIVE_OBSERVATION_READY"
NEXT_STOP = "STOP"

# Material dependency thresholds (D1/D2 collapse → SYMBOL_DEPENDENT)
DEP_MIN_POS_DAYS = 9
DEP_MIN_OPP = 0.0
DEP_MIN_PNL_FRAC = 0.25  # remaining net PnL must be >= 25% of original
