"""AM CURRENT-preserving selective Ridge utility augment. Offline only. Runtime WAIT unchanged."""
from __future__ import annotations

from research.am_entry_profit_improvement import (
    ARCH_RIDGE,
    COMMON_AM_PM_MODEL_ALLOWED,
    COMMON_AM_PM_TARGET_ALLOWED,
    DEV_WAIT_SEC,
    FINAL_SELECTION_N,
    NEW_FORWARD_N,
    PAPER_OPERATED,
    REPRESENTATION_N,
    RIDGE_ALPHA,
    RUNTIME_CHANGED,
    SESSION,
    TRUE_OOS,
    UTILITY_KEY,
    W5_RUNTIME_ADOPTED,
)
from research.am_entry_profit_improvement import CANCEL_N, LIVE_ORDER_N, SUBMIT_N
from research.wait5_session_target_learnability import POS_REP_DENOM, POS_REP_MIN
from small_paper.v1r_primary_runtime import POSITION_CAP, WAIT_SEC

ANALYSIS_ID = "AM_CURRENT_PRESERVING_UTILITY_AUGMENT_V1"
ARCHITECTURE = ARCH_RIDGE
ENSEMBLE = "MEDIAN_PRED_UTILITY"
AUG_SCORE_THRESHOLD = 0.0
AVAILABLE_REP_MIN = int(POS_REP_MIN)
AUGMENT_MAX_PER_COHORT = 1
CURRENT_PRIORITY = True
TARGET = UTILITY_KEY

assert abs(float(WAIT_SEC) - 1.0) < 1e-12
assert float(DEV_WAIT_SEC) == 5.0
assert SESSION == "AM"
assert int(POSITION_CAP) == 5
assert int(FINAL_SELECTION_N) == 3
assert ARCHITECTURE == "RIDGE_UTILITY"
assert abs(float(RIDGE_ALPHA) - 1.0) < 1e-12
assert int(REPRESENTATION_N) == 9
assert int(POS_REP_MIN) == 6
assert int(POS_REP_DENOM) == 9
assert int(AVAILABLE_REP_MIN) == 6
assert int(AUGMENT_MAX_PER_COHORT) == 1
assert float(AUG_SCORE_THRESHOLD) == 0.0
assert CURRENT_PRIORITY is True
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
