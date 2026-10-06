"""AM temporal / regime information on frozen X14_RF. Offline only. Runtime WAIT unchanged."""
from __future__ import annotations

from research.am_entry_information_expansion import (
    ARM_X14_RF,
    AVAILABLE_REP_MIN,
    RF_CLF_PARAMS,
    TARGET,
    X14_BUNDLE,
)
from research.am_entry_profit_improvement import (
    COMMON_AM_PM_MODEL_ALLOWED,
    COMMON_AM_PM_TARGET_ALLOWED,
    DEV_WAIT_SEC,
    NEW_FORWARD_N,
    PAPER_OPERATED,
    REPRESENTATION_N,
    RUNTIME_CHANGED,
    SESSION,
    TRUE_OOS,
    W5_RUNTIME_ADOPTED,
)
from research.am_entry_profit_improvement import CANCEL_N, LIVE_ORDER_N, SUBMIT_N
from research.wait5_session_target_learnability import POS_REP_MIN
from small_paper.v1r_primary_runtime import CLOCK_GRID, POSITION_CAP, WAIT_SEC

ANALYSIS_ID = "AM_TEMPORAL_REGIME_INFORMATION_EXPANSION_V1"
FROZEN_ARM = ARM_X14_RF
AUGMENT_MAX_PER_COHORT = 1
CURRENT_PRIORITY = True
ARM_N = 6
A0 = "A0_X14_BASE"
A1 = "A1_X14_TEMPORAL"
A2 = "A2_X14_MARKET_REGIME"
A3 = "A3_X14_CORE_STATE"
A4 = "A4_X14_TEMPORAL_MARKET"
A5 = "A5_X14_ALL_REGIME"
ARM_IDS = (A0, A1, A2, A3, A4, A5)

AM_CLOCK = tuple((h, m) for h, m in CLOCK_GRID if int(h) < 12)

TEMPORAL_FEATURES = (
    "AM_SLOT_INDEX",
    "AM_MINUTES_FROM_OPEN",
)
REGIME_FEATURES = (
    "MARKET_MEDIAN_RET_60S",
    "MARKET_MEDIAN_RET_180S",
    "MARKET_BREADTH_UP_60S",
    "MARKET_BREADTH_UP_180S",
    "MARKET_IQR_RET_60S",
    "MARKET_IQR_RET_180S",
    "MARKET_MEDIAN_SPREAD_BPS",
    "MARKET_MEDIAN_IMBALANCE",
    "MARKET_MEDIAN_EVENT_RATE_60S",
    "MARKET_MEDIAN_VOLUME_RATE_60S",
    "MARKET_MEDIAN_VOLUME_PERCENTILE_60S",
    "MARKET_MEDIAN_TRADING_VALUE_PERCENTILE_180S",
    "MARKET_MEDIAN_DISTANCE_VWAP_BPS",
    "MARKET_MEDIAN_REBOUND_LOW_BPS",
)
CORE_STATE_FEATURES = (
    "CURRENT_REALIZED_PNL_BEFORE_COHORT",
    "CURRENT_CLOSED_TRADE_N_BEFORE_COHORT",
    "CURRENT_WIN_N_BEFORE_COHORT",
    "CURRENT_LOSS_N_BEFORE_COHORT",
    "CURRENT_ACTIVE_POSITION_N",
    "CURRENT_AVAILABLE_SLOT_N",
)

PRIOR_AUGMENT_TRADE_N = 46
PRIOR_AUGMENT_NET = 23620.0
PRIOR_AUGMENT_PF = 1.206324248777079
PRIOR_OVERLAY_NET = 54330.0
PRIOR_OVERLAY_PF = 1.0610813180882999
PRIOR_OVERLAY_MAX_DD = -356240.0
PRIOR_POS_DAYS = 5
PRIOR_NEG_DAYS = 12
PRIOR_PAIRED_MEDIAN = -1300.0
PRIOR_EX_BEST_AUGMENT_DAY = -38280.0
PRIOR_EX_TOP3_AUGMENT_DAYS = -58880.0

assert abs(float(WAIT_SEC) - 1.0) < 1e-12
assert float(DEV_WAIT_SEC) == 5.0
assert SESSION == "AM"
assert int(POSITION_CAP) == 5
assert FROZEN_ARM == "EXPANDED_X14_RF"
assert TARGET == "PROFITABLE_FILL_CLASS"
assert len(X14_BUNDLE) == 6
assert int(REPRESENTATION_N) == 9
assert int(POS_REP_MIN) == 6
assert int(AVAILABLE_REP_MIN) == 6
assert int(AUGMENT_MAX_PER_COHORT) == 1
assert CURRENT_PRIORITY is True
assert int(ARM_N) == 6
assert ARM_IDS == (A0, A1, A2, A3, A4, A5)
assert len(AM_CLOCK) == 8
assert AM_CLOCK[0] == (9, 5)
assert AM_CLOCK[-1] == (11, 0)
assert len(TEMPORAL_FEATURES) == 2
assert len(REGIME_FEATURES) == 14
assert len(CORE_STATE_FEATURES) == 6
assert int(RF_CLF_PARAMS["n_estimators"]) == 500
assert int(RF_CLF_PARAMS["max_depth"]) == 6
assert int(RF_CLF_PARAMS["min_samples_leaf"]) == 20
assert float(RF_CLF_PARAMS["max_features"]) == 1.0
assert RF_CLF_PARAMS["bootstrap"] is True
assert str(RF_CLF_PARAMS["class_weight"]) == "balanced_subsample"
assert int(RF_CLF_PARAMS["random_state"]) == 42
assert int(RF_CLF_PARAMS["n_jobs"]) == -1
assert TRUE_OOS is False
assert int(NEW_FORWARD_N) == 0
assert COMMON_AM_PM_MODEL_ALLOWED is False
assert COMMON_AM_PM_TARGET_ALLOWED is False
assert W5_RUNTIME_ADOPTED is False
assert RUNTIME_CHANGED is False
assert PAPER_OPERATED is False
assert int(SUBMIT_N) == 0
assert int(CANCEL_N) == 0
assert int(LIVE_ORDER_N) == 0
