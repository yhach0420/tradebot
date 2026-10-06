"""AM EXPANDED_X14_RF risk integration. Frozen model. No score tuning. Offline only."""
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
from small_paper.v1r_primary_runtime import POSITION_CAP, WAIT_SEC

ANALYSIS_ID = "AM_EXPANDED_ENTRY_RISK_INTEGRATION_V1"
FROZEN_ARM = ARM_X14_RF
AUGMENT_MAX_PER_COHORT = 1
CURRENT_PRIORITY = True
RISK_ARM_N = 3
R1 = "R1_WIN_DOMINANT"
R2 = "R2_CORE_CONFIRMED"
R3 = "R3_WIN_DOMINANT_CORE_CONFIRMED"
RISK_ARM_IDS = (R1, R2, R3)

PRIOR_AUGMENT_NET = 23620.0
PRIOR_AUGMENT_TRADE_N = 46
PRIOR_OVERLAY_NET = 54330.0
PRIOR_OVERLAY_PF = 1.0610813180882999

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
assert int(RISK_ARM_N) == 3
assert int(RF_CLF_PARAMS["n_estimators"]) == 500
assert str(RF_CLF_PARAMS["class_weight"]) == "balanced_subsample"
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
