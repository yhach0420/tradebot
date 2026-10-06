"""AM C0 indicator-state EXIT V1. Frozen C0 ENTRY. CURRENT keeps C14. Offline only."""
from __future__ import annotations

from research.am_entry_architecture_final_reassessment import C0
from research.am_entry_profit_improvement import (
    C14_ID,
    CANCEL_N,
    COMMON_AM_PM_MODEL_ALLOWED,
    COMMON_AM_PM_TARGET_ALLOWED,
    DEV_WAIT_SEC,
    LIVE_ORDER_N,
    NEW_FORWARD_N,
    PAPER_OPERATED,
    RUNTIME_CHANGED,
    SESSION,
    SUBMIT_N,
    TRUE_OOS,
    W5_RUNTIME_ADOPTED,
)
from research.am_entry_research_final_decision import PROSPECTIVE_CHALLENGER_NAME, PROSPECTIVE_STATUS
from research.am_exit_contribution_rca import FROZEN_C0_SPEC_SHA256
from small_paper.v1r_primary_runtime import POSITION_CAP, WAIT_SEC

ANALYSIS_ID = "AM_C0_INDICATOR_STATE_EXIT_V1"
ARCHITECTURE_ID = "C0_EXIT_INDICATOR_STATE_V1"
EXIT_MODEL = "L2_LOGISTIC_EXIT_V1"
EXIT_SCOPE = "C0_AUGMENT_ONLY"
ENTRY_PARENT_ID = C0
ENTRY_PARENT_SHA256 = FROZEN_C0_SPEC_SHA256
EXIT_REASON = "INDICATOR_STATE_EXIT"
TERMINAL_REASON = "AM_SESSION_END"
EXIT_THRESHOLD = 0.5
RESEARCH_PARALLELISM = 1
OUTER_FOLD_N = 18
TIME_FEATURE_N = 0
HOLDING_TIME_RULE_N = 0
MODEL_N = 1
TRUE_OOS = False
NEW_FORWARD_N = 0
RUNTIME_ADOPTION_ALLOWED = False
PAPER_STRATEGY_ADOPTION_ALLOWED = False
FORMAL_CANDIDATE = False
RUNTIME_CHANGE_N = 0
PAPER_OPERATION_N = 0
ENTRY_POLICY_CHANGE_N = 0
WAIT_CHANGE_N = 0
FEATURE_SEARCH_N = 0
MODEL_SEARCH_N = 0
HYPERPARAMETER_SEARCH_N = 0
EXIT_THRESHOLD_SEARCH_N = 0
EXIT_HORIZON_SEARCH_N = 0
PTL_REUSE_N = 0
CONTINUATION_REUSE_N = 0
CURRENT_EXIT_CHANGE_N = 0
ORACLE_EXIT_SELECTION_USE_N = 0

FEATURE_ORDER_ENTRY = (
    "spread_bps",
    "imbalance",
    "mid_ret_60s",
    "mid_ret_180s",
    "event_rate_60s",
    "log_bid_qty",
)
SYMBOL_FEATURES = (
    "spread_bps",
    "imbalance",
    "mid_ret_60s",
    "mid_ret_180s",
    "event_rate_60s",
    "log_bid_qty",
    "distance_from_vwap_bps",
    "rebound_from_recent_low_bps",
    "volume_rate_60s",
    "trading_value_delta_60s",
    "volume_percentile_60s",
    "trading_value_percentile_180s",
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
POSITION_FEATURES = (
    "UNREALIZED_RETURN_BPS",
    "POST_ENTRY_PEAK_RETURN_BPS",
    "DRAWDOWN_FROM_POST_ENTRY_PEAK_BPS",
)
EXIT_FEATURES = SYMBOL_FEATURES + REGIME_FEATURES + POSITION_FEATURES

LOGREG_PARAMS = {
    "penalty": "l2",
    "C": 1.0,
    "solver": "lbfgs",
    "class_weight": "balanced",
    "max_iter": 2000,
    "random_state": 42,
}

CURRENT_LOCKED = {
    "TRADE_N": 141,
    "NET": 30710.0,
    "PF": 1.0396263177589389,
    "DD": -317790.0,
}
C0_OVERLAY_LOCKED = {
    "NET": 141710.0,
    "PF": 1.1766979638150101,
    "DD": -309740.0,
    "PAIRED_POS_DAYS": 7,
    "PAIRED_NEG_DAYS": 8,
    "PAIRED_ZERO_DAYS": 3,
    "PAIRED_MEDIAN": 0.0,
    "EX_BEST": 176.47058823529412,
    "EX_TOP3": -603.3333333333334,
}
C0_AUGMENT_LOCKED = {
    "TRADE_N": 26,
    "WIN_N": 12,
    "LOSS_N": 13,
    "FLAT_N": 1,
    "NET": 111000.0,
    "PF": 5.111111111111111,
    "GROSS_LOSS": 27000.0,
    "L2_BASE_N": 10,
}

assert abs(float(WAIT_SEC) - 1.0) < 1e-12
assert float(DEV_WAIT_SEC) == 5.0
assert SESSION == "AM"
assert int(POSITION_CAP) == 5
assert ENTRY_PARENT_ID == "C0_B0_PRIMARY_B1_CONFIRM"
assert PROSPECTIVE_CHALLENGER_NAME == "AM_ENTRY_PROSPECTIVE_CHALLENGER_C0"
assert PROSPECTIVE_STATUS == "FROZEN_FOR_FUTURE_OOS_ONLY"
assert ENTRY_PARENT_SHA256 == "c8ac25b5fb45de774b4bb776e7ed32d6823e23500719ab0772a08a0fca102f91"
assert C14_ID == "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14"
assert ARCHITECTURE_ID == "C0_EXIT_INDICATOR_STATE_V1"
assert EXIT_MODEL == "L2_LOGISTIC_EXIT_V1"
assert EXIT_SCOPE == "C0_AUGMENT_ONLY"
assert abs(float(EXIT_THRESHOLD) - 0.5) < 1e-12
assert int(RESEARCH_PARALLELISM) == 1
assert int(OUTER_FOLD_N) == 18
assert int(TIME_FEATURE_N) == 0
assert int(HOLDING_TIME_RULE_N) == 0
assert int(MODEL_N) == 1
assert len(EXIT_FEATURES) == 29
assert RUNTIME_ADOPTION_ALLOWED is False
assert PAPER_STRATEGY_ADOPTION_ALLOWED is False
assert FORMAL_CANDIDATE is False
assert TRUE_OOS is False
assert int(NEW_FORWARD_N) == 0
assert int(RUNTIME_CHANGE_N) == 0
assert int(PAPER_OPERATION_N) == 0
assert int(ENTRY_POLICY_CHANGE_N) == 0
assert int(WAIT_CHANGE_N) == 0
assert int(FEATURE_SEARCH_N) == 0
assert int(MODEL_SEARCH_N) == 0
assert int(HYPERPARAMETER_SEARCH_N) == 0
assert int(EXIT_THRESHOLD_SEARCH_N) == 0
assert int(EXIT_HORIZON_SEARCH_N) == 0
assert int(PTL_REUSE_N) == 0
assert int(CONTINUATION_REUSE_N) == 0
assert int(CURRENT_EXIT_CHANGE_N) == 0
assert int(ORACLE_EXIT_SELECTION_USE_N) == 0
assert COMMON_AM_PM_MODEL_ALLOWED is False
assert COMMON_AM_PM_TARGET_ALLOWED is False
assert W5_RUNTIME_ADOPTED is False
assert RUNTIME_CHANGED is False
assert PAPER_OPERATED is False
assert int(SUBMIT_N) == 0
assert int(CANCEL_N) == 0
assert int(LIVE_ORDER_N) == 0
assert C0_AUGMENT_LOCKED["L2_BASE_N"] == 10
assert C0_AUGMENT_LOCKED["TRADE_N"] == 26
assert "AM_SLOT_INDEX" not in EXIT_FEATURES
assert "holding_sec" not in EXIT_FEATURES
assert "AM_MINUTES_FROM_OPEN" not in EXIT_FEATURES
