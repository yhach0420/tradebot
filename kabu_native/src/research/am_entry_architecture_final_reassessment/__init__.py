"""AM dual-confirmation final reassessment. No new model. Offline only."""
from __future__ import annotations

from research.am_entry_information_expansion import ARM_X14_RF, AVAILABLE_REP_MIN, TARGET, X14_BUNDLE
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
from research.am_raw_event_incremental_selection import B0_EXPECTED, B1_EXPECTED
from research.wait5_session_target_learnability import POS_REP_MIN
from small_paper.v1r_primary_runtime import POSITION_CAP, WAIT_SEC

ANALYSIS_ID = "AM_ENTRY_ARCHITECTURE_FINAL_REASSESSMENT_V1"
FROZEN_ARM = ARM_X14_RF
AUGMENT_MAX_PER_COHORT = 1
CURRENT_PRIORITY = True
CONSENSUS_ARM_N = 3
B0 = "B0_X14_CORE_STATE"
B1 = "B1_X14_ALL_REGIME"
C0 = "C0_B0_PRIMARY_B1_CONFIRM"
C1 = "C1_B1_PRIMARY_B0_CONFIRM"
C2 = "C2_DUAL_TOP1_AGREEMENT"
ARM_IDS = (B0, B1, C0, C1, C2)
A3_REUSE_ARM = "A3_X14_CORE_STATE"
A5_REUSE_ARM = "A5_X14_ALL_REGIME"

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
assert int(CONSENSUS_ARM_N) == 3
assert ARM_IDS == (B0, B1, C0, C1, C2)
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
assert B0_EXPECTED["OVERLAY_NET_PNL"] == 81610.0
assert B1_EXPECTED["OVERLAY_NET_PNL"] == 69010.0
