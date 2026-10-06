"""AM ENTRY fixed-spec 18-day OOF matrix. Offline only. Runtime WAIT unchanged."""
from __future__ import annotations

from research.am_entry_profit_improvement import (
    ARCHITECTURE_N,
    COMMON_AM_PM_MODEL_ALLOWED,
    COMMON_AM_PM_TARGET_ALLOWED,
    DEV_WAIT_SEC,
    FINAL_SELECTION_N,
    NEW_FORWARD_N,
    PAPER_OPERATED,
    POSTHOC_SPEC_ADDITION_N,
    REPRESENTATION_N,
    RUNTIME_CHANGED,
    SESSION,
    SLOTS,
    SPEC_N,
    TRUE_OOS,
    W5_RUNTIME_ADOPTED,
)
from research.am_entry_profit_improvement import CANCEL_N, LIVE_ORDER_N, SUBMIT_N
from small_paper.v1r_primary_runtime import POSITION_CAP, WAIT_SEC

ANALYSIS_ID = "AM_ENTRY_FIXED_SPEC_OOF_MATRIX_V1"
V2_ANALYSIS_ID = "AM_ENTRY_PROFIT_IMPROVEMENT_V2"

# Frozen V2 replay contract. If CURRENT/FILL_ONLY drift, STOP.
V2_CURRENT_TRADE_N = 141
V2_CURRENT_NET_PNL = 30710.0
V2_CURRENT_PF = 1.0396263177589389
V2_CURRENT_MAX_DD = -317790.0
V2_FILL_ONLY_TRADE_N = 182
V2_FILL_ONLY_NET_PNL = -166760.0

assert abs(float(WAIT_SEC) - 1.0) < 1e-12
assert float(DEV_WAIT_SEC) == 5.0
assert SESSION == "AM"
assert int(POSITION_CAP) == 5
assert int(FINAL_SELECTION_N) == 3
assert int(SLOTS) == 3
assert int(ARCHITECTURE_N) * int(REPRESENTATION_N) == int(SPEC_N)
assert int(SPEC_N) == 27
assert TRUE_OOS is False
assert int(NEW_FORWARD_N) == 0
assert COMMON_AM_PM_MODEL_ALLOWED is False
assert COMMON_AM_PM_TARGET_ALLOWED is False
assert W5_RUNTIME_ADOPTED is False
assert RUNTIME_CHANGED is False
assert int(SUBMIT_N) == 0
assert int(CANCEL_N) == 0
assert int(LIVE_ORDER_N) == 0
assert int(POSTHOC_SPEC_ADDITION_N) == 0
assert PAPER_OPERATED is False
