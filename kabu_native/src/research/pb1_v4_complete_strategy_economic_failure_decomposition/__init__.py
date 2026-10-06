"""PB1 V4 Complete Strategy economic failure decomposition. V1 FAIL is permanent.

Does not retune ENTRY/EXIT. Does not rescore Confirmation 1.
Does not open Frozen Validation or prospective economics.
"""
from __future__ import annotations

from research.cause_first_mechanism_discovery_v1 import X1_TAX_BPS
from research.current_day1_information_close_v1 import FEATURE_MINING_CLOSED
from research.pb1_v4_complete_strategy_build_and_economic_validation.freeze import COMPLETE_STRATEGY_IDENTITY
from research.pb1_v4_complete_strategy_economic_confirmation1 import (
    CASE_FAIL as PARENT_FAIL,
    EVAL_FIRST,
    EVAL_LAST,
    EXPECTED_COMPLETE_STRATEGY_SHA256,
    FV_FIRST,
    FV_LAST,
    PROSPECTIVE_FROM,
)

PROGRAM_ID = "PB1_V4_COMPLETE_STRATEGY_ECONOMIC_FAILURE_DECOMPOSITION"
ANALYSIS_ID = "PB1_V4_COMPLETE_STRATEGY_ECONOMIC_FAILURE_DECOMPOSITION_V1"

EXPECTED_V1_VERDICT = PARENT_FAIL
EXPECTED_COMPLETE_STRATEGY_ID = COMPLETE_STRATEGY_IDENTITY
assert EXPECTED_COMPLETE_STRATEGY_ID == "PB1_V4_COMPLETE_STRATEGY_FROZEN_V1"
assert EXPECTED_COMPLETE_STRATEGY_SHA256 == "556542319d22dff40cc1758b24d44d2ecb1985b8595927ca8f80618126961bf8"
assert EXPECTED_V1_VERDICT == "PB1_V4_COMPLETE_STRATEGY_ECONOMIC_CONFIRMATION1_FAIL_V1"

BASELINE = {
    "signal_n": 150,
    "trade_n": 111,
    "gross_pnl_yen": -140610.0,
    "execution_cost_yen": 95061.0,
    "net_pnl_yen": -235671.0,
    "gross_pf": 0.71,
    "net_pf": 0.574,
    "mean_net_per_trade": -2123.0,
}

# A priori diagnostic thresholds. Frozen X1 tax width, not fitted on Confirmation 1 PnL.
X1_BPS = float(X1_TAX_BPS)
assert X1_BPS == 8.0
SMALL_EDGE_BPS = float(X1_BPS)
EXPAND_BPS = 2.0 * float(X1_BPS)
IMMEDIATE_MIN = 3
CAPTURE_HALF = 0.5
DEATH_LATE_MIN = 10

HORIZONS_MIN = (1, 3, 5, 10, 20, 30, 60)
NOTIONAL_BUCKETS = (
    ("lt_2000", 0.0, 2000.0),
    ("2000_5000", 2000.0, 5000.0),
    ("5000_10000", 5000.0, 10000.0),
    ("10000_20000", 10000.0, 20000.0),
    ("ge_20000", 20000.0, None),
)

CASE_READY = "PB1_V4_COMPLETE_STRATEGY_ECONOMIC_FAILURE_DECOMPOSITION_COMPLETE_V1"
CASE_FAIL = "PB1_V4_COMPLETE_STRATEGY_ECONOMIC_FAILURE_DECOMPOSITION_INCOMPLETE_V1"
NEXT_REPAIR = "PB1_V5_COMPLETE_STRATEGY_MECHANISM_REPAIR_DEVELOPMENT_V1"
NEXT_STOP = "STOP_NO_CAUSAL_REPAIR_YET"
NEXT_SEALED = "KEEP_FROZEN_VALIDATION_ECONOMIC_SEALED"

FEATURE_MINING = FEATURE_MINING_CLOSED
_ = (EVAL_FIRST, EVAL_LAST, FV_FIRST, FV_LAST, PROSPECTIVE_FROM, FEATURE_MINING)
