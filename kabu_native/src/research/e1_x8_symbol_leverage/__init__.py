"""E1_X8 Threshold Symbol Leverage Audit — post-PFQ descriptive audit; no PFQ revival."""

ANALYSIS_ID = "THRESHOLD_SYMBOL_LEVERAGE_AUDIT"
DOCUMENT_ID = "E1_X8_THRESHOLD_SYMBOL_LEVERAGE"
SOURCE_PFQ_FINAL = "e1x7_pfq_exit_rev_20260805_000745_A"
SOURCE_BRIDGE = "e1x7_pfq_bridge_v2_20260804_232049_A"
PFQ_STATUS = "PFQ_CURRENT_LINE_CLOSED_REJECTED"

KNOWN = {
    "ALL_PULLBACK": 303,
    "update_valid": 301,
    "flow_valid": 284,
    "joint_eligible": 283,
    "PFQ_UPDATE_Q70": 92,
    "PFQ_FLOW_Q30": 85,
    "PFQ_JOINT": 41,
}

FROZEN = {
    "price_update_count_10s_q70": 8.0,
    "uptick_volume_ratio_30s_q30": 0.7991666666666666,
}

TARGET_SYMBOL = "285A"
RANDOM_REPS = 1000
RANDOM_SEED = 20260805
BOOTSTRAP_REPS = 1000
BOOTSTRAP_SEED = 20260804  # Bridge V2 seed
MIN_SYMBOL_SUPPORT = 5
