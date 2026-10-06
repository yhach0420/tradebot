"""AM ENTRY profit failure decomposition V2. Diagnostic only. No new model or policy."""
from __future__ import annotations

from research.am_entry_fixed_spec_oof import (
    V2_CURRENT_MAX_DD,
    V2_CURRENT_NET_PNL,
    V2_CURRENT_PF,
    V2_CURRENT_TRADE_N,
    V2_FILL_ONLY_NET_PNL,
    V2_FILL_ONLY_TRADE_N,
)
from research.am_entry_profit_improvement import (
    ARCHITECTURE_N,
    COMMON_AM_PM_MODEL_ALLOWED,
    COMMON_AM_PM_TARGET_ALLOWED,
    DEV_WAIT_SEC,
    NEW_FORWARD_N,
    PAPER_OPERATED,
    REPRESENTATION_N,
    RUNTIME_CHANGED,
    SESSION,
    SPEC_N,
    TRUE_OOS,
    W5_RUNTIME_ADOPTED,
)
from research.am_entry_profit_improvement import CANCEL_N, LIVE_ORDER_N, SUBMIT_N
from research.wait5_session_target_learnability import POS_REP_DENOM, POS_REP_MIN
from small_paper.v1r_primary_runtime import POSITION_CAP, WAIT_SEC

ANALYSIS_ID = "AM_ENTRY_PROFIT_FAILURE_DECOMPOSITION_V2"
FIXED_SPEC_ANALYSIS_ID = "AM_ENTRY_FIXED_SPEC_OOF_MATRIX_V1"

BEST_FIXED_NET_EXPECTED = 22600.0
PASS_SPEC_N_EXPECTED = 0
PARTIAL_SPEC_N_EXPECTED = 0
INNER_OUTER_SPEARMAN_EXPECTED = -0.564499484004128
INNER_OUTER_PEARSON_EXPECTED = -0.7917394710705267
NESTED_FILL_RATE_EXPECTED = 0.11267605633802817
CURRENT_FILL_RATE_EXPECTED = 0.34987593052109184
TOP2_LOSS_SHARE_EXPECTED = 0.7655502392344498
SPEARMAN_ABS_UTILITY_VS_FILL_PRICE_EXPECTED = 0.5491944495027511

ORACLE_INELIGIBLE_SCORE = -1e30
EQUAL_SHARE = 1.0 / 3.0

assert abs(float(WAIT_SEC) - 1.0) < 1e-12
assert float(DEV_WAIT_SEC) == 5.0
assert SESSION == "AM"
assert int(POSITION_CAP) == 5
assert int(SPEC_N) == 27
assert int(ARCHITECTURE_N) * int(REPRESENTATION_N) == int(SPEC_N)
assert int(POS_REP_MIN) == 6
assert int(POS_REP_DENOM) == 9
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
assert abs(float(V2_CURRENT_NET_PNL) - 30710.0) < 1e-9
assert int(V2_CURRENT_TRADE_N) == 141
assert abs(float(V2_FILL_ONLY_NET_PNL) + 166760.0) < 1e-9
assert int(V2_FILL_ONLY_TRADE_N) == 182
assert abs(float(V2_CURRENT_PF) - 1.0396263177589389) < 1e-12
assert abs(float(V2_CURRENT_MAX_DD) + 317790.0) < 1e-9
