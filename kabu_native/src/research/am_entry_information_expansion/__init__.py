"""AM CURRENT-preserving profitable-fill classification. Offline only. Runtime WAIT unchanged."""
from __future__ import annotations

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
from research.direct_joint_objective import RANDOM_STATE
from research.entry_objective_redesign_c3 import F0_CURRENT6, F1_C2_6, F2_UNION, FEATURE_SETS, NORMS
from research.wait5_session_target_learnability import POS_REP_MIN
from small_paper.v1r_primary_runtime import POSITION_CAP, WAIT_SEC

ANALYSIS_ID = "AM_ENTRY_INFORMATION_EXPANSION_V1"
TARGET = "PROFITABLE_FILL_CLASS"
CLASS_WIN = "WIN"
CLASS_LOSS = "LOSS"
CLASS_NEUTRAL = "NEUTRAL"
CLASSES = (CLASS_WIN, CLASS_LOSS, CLASS_NEUTRAL)
JOINT_SCORE_KEY = "JOINT_SCORE"
AUGMENT_MAX_PER_COHORT = 1
AVAILABLE_REP_MIN = int(POS_REP_MIN)
SCORE_BOUNDARY = 0.0
ARM_N = 4
ARM_BASE_LOGIT = "BASE_LOGIT"
ARM_BASE_RF = "BASE_RF"
ARM_X14_LOGIT = "EXPANDED_X14_LOGIT"
ARM_X14_RF = "EXPANDED_X14_RF"
ARM_IDS = (ARM_BASE_LOGIT, ARM_BASE_RF, ARM_X14_LOGIT, ARM_X14_RF)
FAMILY_LOGIT = "LOGIT"
FAMILY_RF = "RF"
INFO_BASE = "BASE"
INFO_X14 = "EXPANDED_X14"

X14_BUNDLE = (
    "distance_from_vwap_bps",
    "rebound_from_recent_low_bps",
    "volume_rate_60s",
    "trading_value_delta_60s",
    "volume_percentile_60s",
    "trading_value_percentile_180s",
)

LOGIT_PARAMS = {
    "penalty": "l2",
    "C": 1.0,
    "solver": "lbfgs",
    "max_iter": 2000,
    "class_weight": "balanced",
    "random_state": int(RANDOM_STATE),
}
RF_CLF_PARAMS = {
    "n_estimators": 500,
    "max_depth": 6,
    "min_samples_leaf": 20,
    "max_features": 1.0,
    "bootstrap": True,
    "class_weight": "balanced_subsample",
    "random_state": int(RANDOM_STATE),
    "n_jobs": -1,
}

MAX_WORKERS = 4

assert abs(float(WAIT_SEC) - 1.0) < 1e-12
assert float(DEV_WAIT_SEC) == 5.0
assert SESSION == "AM"
assert int(POSITION_CAP) == 5
assert TARGET == "PROFITABLE_FILL_CLASS"
assert int(REPRESENTATION_N) == 9
assert len(FEATURE_SETS) * len(NORMS) == int(REPRESENTATION_N)
assert len(X14_BUNDLE) == 6
assert int(POS_REP_MIN) == 6
assert int(AVAILABLE_REP_MIN) == 6
assert int(AUGMENT_MAX_PER_COHORT) == 1
assert float(SCORE_BOUNDARY) == 0.0
assert int(ARM_N) == 4
assert ARM_IDS == (ARM_BASE_LOGIT, ARM_BASE_RF, ARM_X14_LOGIT, ARM_X14_RF)
assert float(LOGIT_PARAMS["C"]) == 1.0
assert str(LOGIT_PARAMS["solver"]) == "lbfgs"
assert str(LOGIT_PARAMS["class_weight"]) == "balanced"
assert int(RF_CLF_PARAMS["n_estimators"]) == 500
assert int(RF_CLF_PARAMS["max_depth"]) == 6
assert int(RF_CLF_PARAMS["min_samples_leaf"]) == 20
assert float(RF_CLF_PARAMS["max_features"]) == 1.0
assert RF_CLF_PARAMS["bootstrap"] is True
assert str(RF_CLF_PARAMS["class_weight"]) == "balanced_subsample"
assert int(RF_CLF_PARAMS["random_state"]) == 42
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
assert F0_CURRENT6[0] == "spread_bps"
assert "vwap_dist_bps" in F1_C2_6
assert len(F2_UNION) >= 6
