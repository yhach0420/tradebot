"""E1_X36 ENTRY × FIXED600 EXIT × Pre-Fill Allocator × Hard-Cap Joint Replay."""

ANALYSIS_ID = "E1_X36_ENTRY_EXIT_ALLOCATOR_HARD_CAP_JOINT_REPLAY"
DOCUMENT_ID = "E1_X36_JOINT_ALLOCATOR"

SOURCE_X35R_RUN = "e1x35r_contract_20260808_202131_A"
SOURCE_X35_RUN = "e1x35_exit_20260808_195439_A"

ENTRY_SHA = "f2887bb2be539cc173aee438a43ee8afb8cfa2b8c31380937ecd843e90dd9b29"
ANCHOR_SHA = "4a2f176ef6f52458cb0e5b38764275e6ddafc01e1849693965b116089514eac2"
EXEC_SHA = "040fa4b061e575d3f6cdb2a11ffd3f862da5351b298567b31363de923a590869"
EXIT_SHA = "2c9fcc6e92971c252c8df93716066dda515fcbff0283d748b03293379c5eb62c"
BOARD_MAPPING_SHA = "6adf923a0fa850e5882d217b44030cb36a3287c967c4710c8951b305808a0a97"

FORBIDDEN_FROM = "20260810"

POSITION_CAP = 5
LOT_QTY = 100
WAIT_SEC = 1.0
HORIZON_SEC = 600.0
CANONICAL_LOOKUP = "FIRST_VALID_BUY1_AT_OR_AFTER_TARGET"

EXPECTED_SIGNALS = 3453
EXPECTED_FILLS = 330

OUTER_BLOCKS = {
    "A": ("20260721", "20260722", "20260723", "20260724"),
    "B": ("20260727", "20260728", "20260729", "20260730"),
    "C": ("20260731", "20260803", "20260804"),
    "D": ("20260805", "20260806", "20260807"),
}

ORDER_ASC = "symbol_ascending"
ORDER_DESC = "symbol_descending"
ORDER_HASH = "stable_hash_symbol"
HASH_SALT = "E1_X36_NEUTRAL_ADMISSION_V1"
HASH_SEEDS = (0, 1, 2, 3, 4)

MAX_ACTIVE_FEATURES = 8
LODO_MIN_POS_DAYS = 9
MAX_SYMBOL_CONTRIB = 0.50
MAX_DAY_CONTRIB = 0.50
MIN_INNER_DAYS = 3

VERDICT_PASS = "E1_X36_FULL_STRATEGY_HISTORICALLY_SUPPORTED"
VERDICT_NEUTRAL = "E1_X36_NEUTRAL_ADMISSION_FULL_STRATEGY_SUPPORTED"
VERDICT_ALLOC_FAIL = "E1_X36_ADMISSION_ALLOCATOR_NOT_ROBUST"
VERDICT_CAP_FAIL = "E1_X36_FULL_STRATEGY_NOT_SUPPORTED_UNDER_HARD_CAP"

NEXT_PASS = "PROSPECTIVE_FREEZE_READY"
NEXT_RESEARCH = "FURTHER_RESEARCH_REQUIRED"

# Precommitted feature families (no combinatorial explosion)
FEATURE_SETS: dict[str, tuple[str, ...]] = {
    "EXEC": ("spread_bps", "imbalance", "log_bid_qty", "log_ask_qty", "fresh_sec"),
    "PRICE": ("mid_ret_60s", "mid_ret_180s", "mid_range_180s_bps", "mid_abs_ret_60s"),
    "ACTIVITY_EXEC": ("event_rate_60s", "mid_abs_ret_60s", "spread_bps", "imbalance", "fresh_sec"),
    "BOARD_PRICE": ("spread_bps", "imbalance", "mid_ret_60s", "mid_ret_180s", "event_rate_60s", "log_bid_qty"),
    "COMPACT": (
        "spread_bps", "imbalance", "mid_ret_60s", "event_rate_60s",
        "univ_med_mid_ret_60s", "log_bid_qty", "mid_abs_ret_60s", "fresh_sec",
    ),
}

FAMILIES = ("A0_ASC", "A1_FILL", "A2_EDGE", "A3_EOV", "A4_DIRECT")
REG_GRID_LOG = (0.1, 1.0, 10.0)
REG_GRID_RIDGE = (0.1, 1.0, 10.0)
