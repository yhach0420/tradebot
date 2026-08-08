"""E1_X14 Historical Holdout Reconciliation — gate Design-fixed thresholds on Holdout."""

ANALYSIS_ID = "E1_X14_HISTORICAL_HOLDOUT_RECONCILIATION"
DOCUMENT_ID = "E1_X14_COMPONENT_SIGNAL_HOLDOUT_GATE"

SOURCE_RUN = "e1x14_bisig_20260805_205150_A"
SOURCE_VERDICT = "E1_X14_STABLE_COMPONENT_SIGNAL_FOUND"
SOURCE_DECISION_STATUS = "SUPERSEDED_FOR_DECISION_BY_HOLDOUT_RECONCILIATION"

DESIGN = ("20260721", "20260722", "20260723", "20260724", "20260727")
VALIDATION = ("20260728", "20260729")
HOLDOUT = ("20260730", "20260731")

FORBIDDEN_ALPHA = ("20260803", "20260804")
FORBIDDEN_RISK_FROM = "20260805"

LABEL = "forward_return_180s"
TOUCH = "plus5_before_minus5"
HOLDOUT_SUPPORT_MIN = 100

# Expected classifications (tests); numbers from source must not be fabricated
KNOWN_REVERSALS = (
    "return_180s", "slope_180s", "acceleration_30s_vs_prior30s",
    "drawdown_from_recent_high_bps", "lower_low_180s", "recent_low_break",
    "volume_ratio_30s_vs_prior120s",
    "symbol_minus_median_return_180s", "symbol_minus_median_return_300s",
    "return_percentile_180s",
)
KNOWN_MAINTAINED = (
    "distance_from_vwap_bps", "rebound_from_recent_low_bps",
    "volume_rate_60s", "trading_value_delta_60s",
    "volume_percentile_60s", "trading_value_percentile_180s",
)

VERDICT_NONE = "E1_X14_NO_HOLDOUT_MAINTAINED_COMPONENT"
VERDICT_MIXED = "E1_X14_HOLDOUT_MIXED_COMPONENT_CANDIDATES_FOUND"
VERDICT_SUPPORTED = "E1_X14_HOLDOUT_COMPONENT_SIGNAL_SUPPORTED"

PRICE_PATH_CORE = ("rebound_from_recent_low_bps", "distance_from_vwap_bps")
PRICE_PATH_OTHER = (
    "return_60s", "return_180s", "return_300s", "slope_60s", "slope_180s",
    "acceleration_30s_vs_prior30s", "drawdown_from_recent_high_bps",
    "higher_low_180s", "lower_low_180s", "recent_high_break", "recent_low_break",
    "range_width_60s", "range_width_180s",
)
PRICE_RS = (
    "symbol_minus_median_return_60s", "symbol_minus_median_return_180s",
    "symbol_minus_median_return_300s", "return_percentile_60s", "return_percentile_180s",
)
ABS_ACTIVITY = (
    "volume_rate_60s", "volume_ratio_30s_vs_prior120s", "trading_value_delta_60s",
    "volume_persistence_180s", "volume_active_fraction_180s", "volume_rate_30s",
)
XS_ACTIVITY = ("volume_percentile_60s", "trading_value_percentile_180s")
