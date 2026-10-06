"""AM EXIT contribution RCA. Frozen C0 / C14. Diagnostic only. Offline only."""
from __future__ import annotations

from research.am_entry_architecture_final_reassessment import C0
from research.am_entry_profit_improvement import (
    C14_ID,
    DEV_WAIT_SEC,
    NEW_FORWARD_N,
    PAPER_OPERATED,
    RUNTIME_CHANGED,
    SESSION,
    TRUE_OOS,
    W5_RUNTIME_ADOPTED,
)
from research.am_entry_profit_improvement import CANCEL_N, LIVE_ORDER_N, SUBMIT_N
from research.am_entry_research_final_decision import PROSPECTIVE_STATUS
from small_paper.v1r_primary_runtime import POSITION_CAP, WAIT_SEC

ANALYSIS_ID = "AM_EXIT_CONTRIBUTION_RCA_V1"
PROSPECTIVE_CHALLENGER_ID = C0
FROZEN_C0_SPEC_SHA256 = "c8ac25b5fb45de774b4bb776e7ed32d6823e23500719ab0772a08a0fca102f91"
C14_CANDIDATE_ID = C14_ID
RUNTIME_CHANGE_N = 0
PAPER_OPERATION_N = 0
ENTRY_POLICY_CHANGE_N = 0
EXIT_POLICY_CHANGE_N = 0
WAIT_CHANGE_N = 0
ORACLE_EXIT_SELECTION_USE_N = 0

CURRENT_LOCKED = {
    "TRADE_N": 141,
    "NET": 30710.0,
    "PF": 1.0396263177589389,
    "DD": -317790.0,
}
C0_AUGMENT_LOCKED = {
    "TRADE_N": 26,
    "WIN_N": 12,
    "LOSS_N": 13,
    "FLAT_N": 1,
    "NET": 111000.0,
}

assert abs(float(WAIT_SEC) - 1.0) < 1e-12
assert float(DEV_WAIT_SEC) == 5.0
assert SESSION == "AM"
assert int(POSITION_CAP) == 5
assert PROSPECTIVE_CHALLENGER_ID == "C0_B0_PRIMARY_B1_CONFIRM"
assert PROSPECTIVE_STATUS == "FROZEN_FOR_FUTURE_OOS_ONLY"
assert C14_CANDIDATE_ID == "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14"
assert TRUE_OOS is False
assert int(NEW_FORWARD_N) == 0
assert int(RUNTIME_CHANGE_N) == 0
assert int(PAPER_OPERATION_N) == 0
assert int(ENTRY_POLICY_CHANGE_N) == 0
assert int(EXIT_POLICY_CHANGE_N) == 0
assert int(WAIT_CHANGE_N) == 0
assert int(ORACLE_EXIT_SELECTION_USE_N) == 0
assert W5_RUNTIME_ADOPTED is False
assert RUNTIME_CHANGED is False
assert PAPER_OPERATED is False
assert int(SUBMIT_N) == 0
assert int(CANCEL_N) == 0
assert int(LIVE_ORDER_N) == 0
assert C0_AUGMENT_LOCKED["LOSS_N"] == 13
assert C0_AUGMENT_LOCKED["WIN_N"] == 12
assert C0_AUGMENT_LOCKED["FLAT_N"] == 1
