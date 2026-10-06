"""AM event-sequence architecture. Cross-fitted CAUSAL_GRU16. Offline only."""
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
from research.am_raw_event_incremental_selection import B0_EXPECTED, B1_EXPECTED
from research.raw_event_information_audit import WINDOW_SEC
from research.wait5_session_target_learnability import POS_REP_MIN
from small_paper.v1r_primary_runtime import POSITION_CAP, WAIT_SEC

ANALYSIS_ID = "AM_EVENT_SEQUENCE_ARCHITECTURE_V1"
FROZEN_ARM = ARM_X14_RF
AUGMENT_MAX_PER_COHORT = 1
CURRENT_PRIORITY = True
ARM_N = 4
B0 = "B0_X14_CORE_STATE"
S0 = "S0_X14_CORE_STATE_SEQ"
B1 = "B1_X14_ALL_REGIME"
S1 = "S1_X14_ALL_REGIME_SEQ"
ARM_IDS = (B0, S0, B1, S1)
A3_REUSE_ARM = "A3_X14_CORE_STATE"
A5_REUSE_ARM = "A5_X14_ALL_REGIME"

SEQUENCE_LEN = 180
SEQUENCE_CHANNEL_N = 11
SEQUENCE_MODEL = "CAUSAL_GRU16"
SEQUENCE_WINDOW_SEC = float(WINDOW_SEC)
GRU_HIDDEN = 16
GRU_LAYERS = 1
GRU_BIDIRECTIONAL = False
ADAMW_LR = 0.001
ADAMW_WD = 0.0001
GRU_EPOCHS = 25
GRU_BATCH = 128
GRAD_CLIP = 1.0
GRU_SEED = 42

SEQ_P_WIN = "SEQ_P_WIN"
SEQ_P_LOSS = "SEQ_P_LOSS"
SEQ_P_NEUTRAL = "SEQ_P_NEUTRAL"
SEQ_JOINT_SCORE = "SEQ_JOINT_SCORE"
SEQ_POSTERIOR3 = (SEQ_P_WIN, SEQ_P_LOSS, SEQ_P_NEUTRAL)

CHANNELS = (
    "EVENT_COUNT_1S",
    "MID_CHANGE_BPS_1S",
    "IMBALANCE_ABS_CHANGE_SUM_1S",
    "SPREAD_CHANGE_COUNT_1S",
    "LOG1P_BID_QTY_ABS_CHANGE_SUM_1S",
    "LOG1P_ASK_QTY_ABS_CHANGE_SUM_1S",
    "SPREAD_BPS_STATE",
    "IMBALANCE_STATE",
    "LOG1P_BID_QTY_STATE",
    "LOG1P_ASK_QTY_STATE",
    "STATE_AVAILABLE",
)
FLOW_IDX = (0, 1, 2, 3, 4, 5)
STATE_IDX = (6, 7, 8, 9)
AVAIL_IDX = 10

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
assert ARM_IDS == (B0, S0, B1, S1)
assert int(SEQUENCE_LEN) == 180
assert int(SEQUENCE_CHANNEL_N) == 11
assert len(CHANNELS) == 11
assert abs(float(SEQUENCE_WINDOW_SEC) - 180.0) < 1e-12
assert SEQUENCE_MODEL == "CAUSAL_GRU16"
assert int(GRU_HIDDEN) == 16
assert int(GRU_LAYERS) == 1
assert GRU_BIDIRECTIONAL is False
assert int(GRU_EPOCHS) == 25
assert int(GRU_BATCH) == 128
assert int(GRU_SEED) == 42
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
assert len(TEMPORAL_FEATURES) == 2
assert len(REGIME_FEATURES) == 14
assert len(CORE_STATE_FEATURES) == 6
assert B0_EXPECTED["OVERLAY_NET_PNL"] == 81610.0
assert B1_EXPECTED["OVERLAY_NET_PNL"] == 69010.0
