"""AM ENTRY research final decision. Close 18-day search. Freeze C0 prospective challenger. Offline only."""
from __future__ import annotations

from research.am_entry_architecture_final_reassessment import C0
from research.am_entry_information_expansion import ARM_X14_RF, TARGET, X14_BUNDLE
from research.am_entry_profit_improvement import (
    COMMON_AM_PM_MODEL_ALLOWED,
    COMMON_AM_PM_TARGET_ALLOWED,
    DEV_WAIT_SEC,
    NEW_FORWARD_N,
    PAPER_OPERATED,
    RUNTIME_CHANGED,
    SESSION,
    TRUE_OOS,
    W5_RUNTIME_ADOPTED,
)
from research.am_entry_profit_improvement import CANCEL_N, LIVE_ORDER_N, SUBMIT_N
from small_paper.v1r_primary_runtime import POSITION_CAP, WAIT_SEC

ANALYSIS_ID = "AM_ENTRY_RESEARCH_FINAL_DECISION_V1"
PROSPECTIVE_CHALLENGER_ID = C0
PROSPECTIVE_CHALLENGER_NAME = "AM_ENTRY_PROSPECTIVE_CHALLENGER_C0"
PROSPECTIVE_STATUS = "FROZEN_FOR_FUTURE_OOS_ONLY"
VERDICT = "AM_ENTRY_DEVELOPMENT_CLOSED_C0_PROSPECTIVE_CHALLENGER_FROZEN"
NEXT = "AM_C0_FUTURE_OOS_WHEN_AVAILABLE"
FROZEN_ARM = ARM_X14_RF
RUNTIME_ADOPTION_ALLOWED = False
PAPER_STRATEGY_ADOPTION_ALLOWED = False
FORMAL_CANDIDATE = False
RUNTIME_CHANGE_N = 0
PAPER_OPERATION_N = 0

CURRENT_LOCKED = {
    "TRADE_N": 141,
    "NET": 30710.0,
    "PF": 1.0396263177589389,
    "DD": -317790.0,
}
C0_LOCKED = {
    "ARCHITECTURE_ID": C0,
    "NET": 141710.0,
    "PF": 1.1766979638150101,
    "MAX_DD": -309740.0,
    "DELTA_NET": 111000.0,
    "PAIRED_POS_DAYS": 7,
    "PAIRED_NEG_DAYS": 8,
    "PAIRED_ZERO_DAYS": 3,
    "PAIRED_MEDIAN": 0.0,
    "EX_BEST": 176.47058823529412,
    "EX_TOP3": -603.3333333333334,
    "CURRENT_PRESERVATION_PASS": True,
    "FULL_GATE_PASS": False,
}
BLOCKED_B0_COND_PNL = -5463.636363636364
RETAINED_B0_COND_PNL = 5000.0

CLOSED_LINES = (
    "27-spec replacement: CLOSED",
    "realized-yen utility replacement: CLOSED",
    "P_FILL-first augment: CLOSED",
    "utility-threshold line: CLOSED",
    "CURRENT veto: CLOSED",
    "RAW23: CLOSED",
    "event sequence / GRU: CLOSED",
    "snapshot threshold search: CLOSED",
    "B0/B1 post-hoc tuning: CLOSED",
)

assert abs(float(WAIT_SEC) - 1.0) < 1e-12
assert float(DEV_WAIT_SEC) == 5.0
assert SESSION == "AM"
assert int(POSITION_CAP) == 5
assert FROZEN_ARM == "EXPANDED_X14_RF"
assert TARGET == "PROFITABLE_FILL_CLASS"
assert len(X14_BUNDLE) == 6
assert PROSPECTIVE_CHALLENGER_ID == "C0_B0_PRIMARY_B1_CONFIRM"
assert RUNTIME_ADOPTION_ALLOWED is False
assert PAPER_STRATEGY_ADOPTION_ALLOWED is False
assert FORMAL_CANDIDATE is False
assert TRUE_OOS is False
assert int(NEW_FORWARD_N) == 0
assert int(RUNTIME_CHANGE_N) == 0
assert int(PAPER_OPERATION_N) == 0
assert COMMON_AM_PM_MODEL_ALLOWED is False
assert COMMON_AM_PM_TARGET_ALLOWED is False
assert W5_RUNTIME_ADOPTED is False
assert RUNTIME_CHANGED is False
assert PAPER_OPERATED is False
assert int(SUBMIT_N) == 0
assert int(CANCEL_N) == 0
assert int(LIVE_ORDER_N) == 0
assert C0_LOCKED["FULL_GATE_PASS"] is False
assert C0_LOCKED["NET"] > CURRENT_LOCKED["NET"]
assert C0_LOCKED["PF"] > CURRENT_LOCKED["PF"]
assert abs(C0_LOCKED["MAX_DD"]) < abs(CURRENT_LOCKED["DD"])
