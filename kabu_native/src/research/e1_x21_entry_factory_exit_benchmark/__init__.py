"""E1_X21 broad ENTRY factory + neutral EXIT benchmark matrix."""

ANALYSIS_ID = "E1_X21_BROAD_ENTRY_FACTORY_NEUTRAL_EXIT_BENCHMARK"
DOCUMENT_ID = "E1_X21_MULTI_ENTRY_LOGIC_MATRIX"

SOURCE_X19 = "e1x19_prepath_20260806_075755_A"
EXPECTED_POP_N = 17688

DISCOVERY = ("20260721", "20260722", "20260723", "20260724", "20260727")
EVALUATION = ("20260728", "20260729", "20260730", "20260731")
STRESS_DAY = "20260803"
STRESS_ROLE = "CONSUMED_STRESS_DIAGNOSTIC"
FORBIDDEN_DAY = "20260804"
FORBIDDEN_RISK_FROM = "20260805"

RULE_TYPES = ("UPPER_REJECT", "LOWER_REJECT", "UPPER_SELECT", "LOWER_SELECT")

FEATURE_REGISTRY = {
    "TREND": (
        "return_30s", "return_60s", "return_180s", "return_300s",
        "slope_60s", "slope_180s", "acceleration_30s_vs_prior30s",
    ),
    "PRICE_POSITION": (
        "distance_from_vwap_bps",
        "distance_from_session_high_bps", "distance_from_session_low_bps",
        "drawdown_from_recent_high_bps", "rebound_from_recent_low_bps",
        "higher_low_180s", "lower_low_180s",
    ),
    "RANGE_VOLATILITY": (
        "range_width_60s", "range_width_180s", "cs_return_dispersion_60s",
        "range_width_300s",
    ),
    "ACTIVITY": (
        "volume_delta_30s", "volume_delta_60s", "volume_delta_180s",
        "volume_rate_60s", "volume_ratio_30s_vs_prior120s",
        "trading_value_delta_60s", "trading_value_delta_180s",
        "volume_percentile_60s", "trading_value_percentile_180s",
        "price_update_count_60s", "price_update_count_180s",
    ),
    "MARKET_STATE": (
        "advancing_symbol_fraction", "declining_symbol_fraction",
        "universe_median_return_60s", "universe_median_return_180s",
        "universe_median_return_300s",
        "cross_sectional_volume_dispersion",
    ),
    "RELATIVE_STRENGTH": (
        "symbol_minus_median_return_60s", "symbol_minus_median_return_180s",
    ),
    "PULLBACK_PATH": (
        "number_of_direction_changes_180s", "number_of_direction_changes_300s",
        "distance_from_session_open_bps",
    ),
}

FAMILY_BY_FEATURE = {}
for fam, feats in FEATURE_REGISTRY.items():
    for f in feats:
        FAMILY_BY_FEATURE[f] = fam

BENCHMARK_EXITS = ("BX_H60", "BX_H180", "BX_H300", "BX_TOUCH_10_10")

VERDICT_FAIL = "E1_X21_ENTRY_FACTORY_IMPLEMENTATION_FAILED"
VERDICT_NO_SIGNAL = "E1_X21_ENTRY_LOGICS_CREATED_NO_BENCHMARK_SIGNAL"
VERDICT_DIRECTIONAL = "E1_X21_MULTIPLE_DIRECTIONAL_ENTRY_LOGICS_CREATED"
VERDICT_ECONOMIC = "E1_X21_MULTIPLE_ENTRY_BENCHMARK_EXIT_SIGNALS_FOUND"

CREATE_MIN_SUPPORT = 30
CREATE_MIN_DAYS = 3
