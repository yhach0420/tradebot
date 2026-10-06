"""AM raw-event incremental selection on frozen X14/regime bases. Offline only."""
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
from research.am_entry_temporal_regime_information import (
    CORE_STATE_FEATURES,
    REGIME_FEATURES,
    TEMPORAL_FEATURES,
)
from research.raw_event_information_audit import FAMILY_KEYS, WINDOW_SEC
from research.raw_event_prediction_probe import RAW_DESCRIPTOR_N, RAW_DESCRIPTORS
from research.wait5_session_target_learnability import POS_REP_MIN
from small_paper.v1r_primary_runtime import POSITION_CAP, WAIT_SEC

ANALYSIS_ID = "AM_RAW_EVENT_INCREMENTAL_SELECTION_V2"
FROZEN_ARM = ARM_X14_RF
AUGMENT_MAX_PER_COHORT = 1
CURRENT_PRIORITY = True
ARM_N = 4
B0 = "B0_X14_CORE_STATE"
R0 = "R0_X14_CORE_STATE_RAW23"
B1 = "B1_X14_ALL_REGIME"
R1 = "R1_X14_ALL_REGIME_RAW23"
ARM_IDS = (B0, R0, B1, R1)
RAW_EVENT_AVAILABLE = "RAW_EVENT_AVAILABLE"
RAW_WINDOW_SEC = float(WINDOW_SEC)
A3_REUSE_ARM = "A3_X14_CORE_STATE"
A5_REUSE_ARM = "A5_X14_ALL_REGIME"

B0_EXPECTED = {
    "OVERLAY_NET_PNL": 81610.0,
    "OVERLAY_PF": 1.0924664906695067,
    "OVERLAY_MAX_DD": -338640.0,
    "PAIRED_MEDIAN_DAILY_DELTA": 950.0,
    "PAIRED_POS_DAYS": 9,
    "PAIRED_NEG_DAYS": 8,
    "EX_BEST_DAY_PNL_DELTA": -711.7647058823529,
    "EX_TOP3_DAYS_PNL_DELTA": -2133.3333333333335,
}
B1_EXPECTED = {
    "OVERLAY_NET_PNL": 69010.0,
    "OVERLAY_PF": 1.0848424495014692,
    "OVERLAY_MAX_DD": -275440.0,
    "PAIRED_MEDIAN_DAILY_DELTA": -200.0,
    "PAIRED_POS_DAYS": 7,
    "PAIRED_NEG_DAYS": 10,
    "EX_BEST_DAY_PNL_DELTA": 782.3529411764706,
    "EX_TOP3_DAYS_PNL_DELTA": -766.6666666666666,
}

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
assert int(ARM_N) == 4
assert ARM_IDS == (B0, R0, B1, R1)
assert int(RAW_DESCRIPTOR_N) == 23
assert len(RAW_DESCRIPTORS) == 23
assert abs(float(RAW_WINDOW_SEC) - 180.0) < 1e-12
assert list(RAW_DESCRIPTORS) == list(
    FAMILY_KEYS["EVENT_TIMING"]
    + FAMILY_KEYS["IMBALANCE_TRANSITION"]
    + FAMILY_KEYS["SPREAD_TRANSITION"]
    + FAMILY_KEYS["DEPTH_TRANSITION"]
)
assert "positive_imbalance_time_share" not in RAW_DESCRIPTORS
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
assert len(TEMPORAL_FEATURES) == 2
assert len(REGIME_FEATURES) == 14
assert len(CORE_STATE_FEATURES) == 6
