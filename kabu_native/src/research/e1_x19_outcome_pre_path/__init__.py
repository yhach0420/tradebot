"""E1_X19 outcome pre-path audit — no ENTRY candidate."""

ANALYSIS_ID = "E1_X19_OUTCOME_PRE_PATH_AUDIT"
DOCUMENT_ID = "E1_X19_WINNER_STOP_NOPROGRESS"

DISCOVERY = ("20260721", "20260722", "20260723", "20260724", "20260727")
CONFIRMATION = ("20260728", "20260729", "20260730", "20260731")
STRESS_DAY = "20260803"
STRESS_ROLE = "CONSUMED_PROSPECTIVE_FAILURE_ANALYSIS_ONLY"
ALL_DAYS = DISCOVERY + CONFIRMATION + (STRESS_DAY,)
FORBIDDEN_DAY = "20260804"
FORBIDDEN_RISK_FROM = "20260805"

MIN_RS_UNIVERSE = 20
NO_PROGRESS_BPS = 5.0 / 10000.0
TWO_SIDED_BPS = 10.0 / 10000.0
MATCHED_MIN_PER_CLASS = 10
GATE_SUPPORT = 100
GATE_DAYS = 7
GATE_MAX_DAY = 0.30
GATE_MAX_SYM = 0.10

TIME_BUCKETS = {
    "AM_OPEN": (9 * 60, 9 * 60 + 30),
    "AM_MID": (9 * 60 + 30, 11 * 60 + 30),
    "PM_OPEN": (12 * 60 + 30, 13 * 60),
    "PM_MID": (13 * 60, 15 * 60 + 30),
}

# Features available from X14 cluster contract (no future, pre-anchor)
PRICE_FEATURES = (
    "return_30s", "return_60s", "return_180s", "return_300s",
    "slope_60s", "slope_180s", "acceleration_30s_vs_prior30s",
    "range_width_60s", "range_width_180s",
    "distance_from_vwap_bps",
    "distance_from_session_high_bps", "distance_from_session_low_bps",
    "drawdown_from_recent_high_bps", "rebound_from_recent_low_bps",
    "higher_low_180s", "lower_low_180s",
)
ACTIVITY_FEATURES = (
    "volume_delta_30s", "volume_delta_60s", "volume_delta_180s",
    "volume_rate_60s", "volume_ratio_30s_vs_prior120s",
    "trading_value_delta_60s", "trading_value_delta_180s",
    "volume_percentile_60s", "trading_value_percentile_180s",
)
MARKET_FEATURES = (
    "advancing_symbol_fraction", "declining_symbol_fraction",
    "universe_median_return_60s", "universe_median_return_180s", "universe_median_return_300s",
    "symbol_minus_median_return_60s", "symbol_minus_median_return_180s",
    "cs_return_dispersion_60s",  # derived same-grid
)
ALL_FEATURES = PRICE_FEATURES + ACTIVITY_FEATURES + MARKET_FEATURES

MECHANISM_MAP = {
    "PRE_ANCHOR_TREND": ("return_60s", "return_180s", "return_300s", "slope_60s", "slope_180s", "acceleration_30s_vs_prior30s"),
    "PULLBACK_RECOVERY": ("drawdown_from_recent_high_bps", "rebound_from_recent_low_bps", "higher_low_180s", "lower_low_180s"),
    "RANGE_COMPRESSION": ("range_width_60s", "range_width_180s"),
    "ACTIVITY_EXPANSION": (
        "volume_delta_60s", "volume_rate_60s", "volume_ratio_30s_vs_prior120s",
        "trading_value_delta_60s", "volume_percentile_60s", "trading_value_percentile_180s",
    ),
    "MARKET_RELATIVE_STATE": (
        "advancing_symbol_fraction", "universe_median_return_60s",
        "symbol_minus_median_return_60s", "symbol_minus_median_return_180s", "cs_return_dispersion_60s",
    ),
    "VOLATILITY_STATE": ("range_width_180s", "cs_return_dispersion_60s"),
    "TIME_OF_DAY_STATE": (),  # strata only
}

# Unavailable in X14 cache (documented, not invented)
UNAVAILABLE_FEATURES = (
    "range_width_300s", "distance_from_session_open_bps",
    "number_of_direction_changes_180s", "number_of_direction_changes_300s",
    "price_update_count_60s", "price_update_count_180s",
    "cross_sectional_volume_dispersion",
)

VERDICT_NONE = "E1_X19_NO_STABLE_PRE_PATH_DISCRIMINATOR"
VERDICT_PARTIAL = "E1_X19_PARTIAL_PRE_PATH_DISCRIMINATORS_FOUND"
VERDICT_FOUND = "E1_X19_STABLE_PRE_PATH_MECHANISMS_FOUND"

CLASSES = ("WINNER", "STOP", "NOPROGRESS", "TWO_SIDED_VOLATILE", "UNCLASSIFIED")
