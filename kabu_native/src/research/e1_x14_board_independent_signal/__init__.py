"""E1_X14 Board-Independent Entry Signal Audit — price/volume/RS only."""

ANALYSIS_ID = "E1_X14_BOARD_INDEPENDENT_ENTRY_SIGNAL_AUDIT"
DOCUMENT_ID = "E1_X14_PRICE_VOLUME_RELATIVE_STRENGTH"

TARGET_START = "20260615"
FORBIDDEN_EARLY = ("20260601", "20260602", "20260603", "20260604", "20260605",
                   "20260606", "20260607", "20260608", "20260609", "20260610",
                   "20260611", "20260612")
FORBIDDEN_ALPHA = ("20260803", "20260804")
# RISK_INFRASTRUCTURE_ONLY and later — never alpha-use
FORBIDDEN_RISK_ONLY_FROM = "20260805"

GRID_SEC = 10
PRICE_FRESH_MAX = 10.0
VOLUME_FRESH_MAX = 30.0
VALUE_FRESH_MAX = 30.0
VWAP_FRESH_MAX = 60.0
MIN_RS_UNIVERSE = 20
CLUSTER_WINDOW_SEC = 300
LABEL_HORIZONS = (30, 60, 180, 300)

AM_START, AM_END = (9, 0), (11, 30)
PM_START, PM_END = (12, 30), (15, 0)

# Fixed a priori feature directions (never flip after seeing results)
FEATURE_HYPOTHESIS = {
    "return_60s": "positive",
    "return_180s": "positive",
    "return_300s": "positive",
    "slope_60s": "positive",
    "slope_180s": "positive",
    "acceleration_30s_vs_prior30s": "positive",
    "distance_from_vwap_bps": "late_chase_risk_negative",
    "distance_from_session_high_bps": "negative",  # near high = chase risk when large negative distance? actually distance from high: 0=at high
    "drawdown_from_recent_high_bps": "pullback_positive",
    "rebound_from_recent_low_bps": "positive",
    "higher_low_180s": "positive",
    "lower_low_180s": "negative",
    "recent_high_break": "positive",
    "recent_low_break": "negative",
    "range_width_60s": "neutral",
    "volume_rate_60s": "positive",
    "volume_ratio_30s_vs_prior120s": "positive",
    "volume_persistence_180s": "positive",
    "volume_active_fraction_180s": "positive",
    "trading_value_delta_60s": "positive",
    "symbol_minus_median_return_60s": "positive",
    "symbol_minus_median_return_180s": "positive",
    "symbol_minus_median_return_300s": "positive",
    "return_percentile_60s": "positive",
    "return_percentile_180s": "positive",
    "volume_percentile_60s": "positive",
    "trading_value_percentile_180s": "positive",
}

FORBIDDEN_BOARD_COLUMNS = (
    "BidPrice", "AskPrice", "BidQty", "AskQty", "spread", "board_imbalance",
    "board_tier", "board_age", "board_update_count", "bid_refill",
    "ask_replenishment", "absorption", "special_quote", "best_bid", "best_ask",
)

VERDICT_NO_STABLE = "E1_X14_NO_STABLE_BOARD_INDEPENDENT_SIGNAL"
VERDICT_STABLE = "E1_X14_STABLE_COMPONENT_SIGNAL_FOUND"
VERDICT_BLOCKED = "E1_X14_SOURCE_POPULATION_BLOCKED"
VERDICT_INSUFFICIENT = "E1_X14_BOARD_INDEPENDENT_DATASET_READY_INSUFFICIENT_LABEL_SUPPORT"
