"""AM utility-eligible / P_FILL-first augment. Offline only. Runtime WAIT unchanged."""
from __future__ import annotations

from research.am_current_utility_augment import (
    ARCHITECTURE,
    AUG_SCORE_THRESHOLD,
    AUGMENT_MAX_PER_COHORT,
    AVAILABLE_REP_MIN,
    CURRENT_PRIORITY,
    ENSEMBLE,
    TARGET,
)
from research.am_entry_profit_improvement import (
    COMMON_AM_PM_MODEL_ALLOWED,
    COMMON_AM_PM_TARGET_ALLOWED,
    DEV_WAIT_SEC,
    FILL_ONLY_REPRESENTATION_ID,
    NEW_FORWARD_N,
    PAPER_OPERATED,
    REPRESENTATION_N,
    RIDGE_ALPHA,
    RUNTIME_CHANGED,
    SESSION,
    TRUE_OOS,
    W5_RUNTIME_ADOPTED,
)
from research.am_entry_profit_improvement import CANCEL_N, LIVE_ORDER_N, SUBMIT_N
from research.wait5_session_target_learnability import POS_REP_MIN
from small_paper.v1r_primary_runtime import POSITION_CAP, WAIT_SEC

ANALYSIS_ID = "AM_UTILITY_AUGMENT_EXECUTION_RISK_V1"
AUGMENT_RANK = "P_FILL5"
PRIOR_ANALYSIS_ID = "AM_CURRENT_PRESERVING_UTILITY_AUGMENT_V1"

PRIOR_CANDIDATE_N = 144
PRIOR_FILL_N = 11
PRIOR_FILL_RATE = 0.0763888888888889
PRIOR_NET_PNL = 3500.0
PRIOR_PF = 1.7954545454545454
PRIOR_MAX_DD = -2500.0
PRIOR_POS_DAYS = 3
PRIOR_NEG_DAYS = 5
PRIOR_ZERO_DAYS = 10
PRIOR_OVERLAY_NET = 34210.0
PRIOR_OVERLAY_PF = 1.0438933011714289
PRIOR_OVERLAY_MAX_DD = -317890.0

assert abs(float(WAIT_SEC) - 1.0) < 1e-12
assert float(DEV_WAIT_SEC) == 5.0
assert SESSION == "AM"
assert int(POSITION_CAP) == 5
assert ARCHITECTURE == "RIDGE_UTILITY"
assert abs(float(RIDGE_ALPHA) - 1.0) < 1e-12
assert int(REPRESENTATION_N) == 9
assert int(POS_REP_MIN) == 6
assert int(AVAILABLE_REP_MIN) == 6
assert int(AUGMENT_MAX_PER_COHORT) == 1
assert float(AUG_SCORE_THRESHOLD) == 0.0
assert CURRENT_PRIORITY is True
assert ENSEMBLE == "MEDIAN_PRED_UTILITY"
assert AUGMENT_RANK == "P_FILL5"
assert FILL_ONLY_REPRESENTATION_ID == "F0_CURRENT6|none"
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
assert TARGET == "REALIZED_ENTRY_UTILITY"
