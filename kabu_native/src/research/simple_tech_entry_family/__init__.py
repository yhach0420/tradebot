"""SIMPLE_TECH_PULLBACK_V1. Offline AM ENTRY family baseline + deficiency RCA. Runtime/Capture untouched."""
from __future__ import annotations

from research.am_c0_indicator_exit import C0_OVERLAY_LOCKED, CURRENT_LOCKED
from research.am_entry_profit_improvement import (
    C14_ID,
    CANCEL_N,
    DEV_WAIT_SEC,
    ELIGIBLE_DAYS,
    LIVE_ORDER_N,
    NEW_FORWARD_N,
    PAPER_OPERATED,
    RUNTIME_CHANGED,
    SESSION,
    SUBMIT_N,
    TRUE_OOS,
    W5_RUNTIME_ADOPTED,
)
from research.e1_x28_executable_joint import BOARD_FRESHNESS_SEC, MIN_QTY
from small_paper.v1r_primary_runtime import POSITION_CAP, WAIT_SEC

ANALYSIS_ID = "SIMPLE_TECH_ENTRY_FAMILY_V1"
STRATEGY_ID = "SIMPLE_TECH_PULLBACK_V1"
FAMILY_ID = "SIMPLE_TECH_FAMILY"
ARCHITECTURE_ID = "MA_BB_RCI_VOLUME_PRICE_ACTION_BOARD_SUPPORT"
RESEARCH_PARALLELISM = 1
TRUE_OOS = False
NEW_FORWARD_N = 0
RUNTIME_ADOPTION_ALLOWED = False
PAPER_STRATEGY_ADOPTION_ALLOWED = False
FORMAL_CANDIDATE = False
V2_IMPLEMENTED = False
ML_USED = False
SCORE_USED = False
TOPK_RANKING_USED = False
MACD_ADDED = False
ADX_ADDED = False
RSI_ADDED = False
PM_USED = False

EMA_SHORT = 9
EMA_LONG = 21
EMA_SLOPE_BARS = 3
BB_PERIOD = 20
BB_SIGMA = 2.0
RCI_PERIOD = 9
RCI_CROSS_LEVEL = -80.0
VOLUME_MEDIAN_BARS = 5
VOLUME_MULT = 1.5
PULLBACK_LOOKBACK = 3
BOARD_ASK_BID_QTY_MAX_RATIO = 2.0

# EMA21 defined at index 20; slope uses [t-3] → first evaluable index 23 (24 bars).
WARMUP_BARS = int(EMA_LONG) + int(EMA_SLOPE_BARS)

GOOD_FWD_BARS = 3
GOOD_MFE_BARS = 5
COST_FWD_BARS = 1

assert abs(float(WAIT_SEC) - 1.0) < 1e-12
assert float(DEV_WAIT_SEC) == 5.0
assert SESSION == "AM"
assert int(POSITION_CAP) == 5
assert C14_ID == "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14"
assert int(RESEARCH_PARALLELISM) == 1
assert STRATEGY_ID == "SIMPLE_TECH_PULLBACK_V1"
assert int(EMA_SHORT) == 9
assert int(EMA_LONG) == 21
assert int(BB_PERIOD) == 20
assert abs(float(BB_SIGMA) - 2.0) < 1e-12
assert int(RCI_PERIOD) == 9
assert abs(float(RCI_CROSS_LEVEL) + 80.0) < 1e-12
assert abs(float(VOLUME_MULT) - 1.5) < 1e-12
assert int(VOLUME_MEDIAN_BARS) == 5
assert abs(float(BOARD_ASK_BID_QTY_MAX_RATIO) - 2.0) < 1e-12
assert abs(float(MIN_QTY) - 100.0) < 1e-12
assert abs(float(BOARD_FRESHNESS_SEC) - 5.0) < 1e-12
assert int(WARMUP_BARS) == 24
assert TRUE_OOS is False
assert int(NEW_FORWARD_N) == 0
assert RUNTIME_ADOPTION_ALLOWED is False
assert PAPER_STRATEGY_ADOPTION_ALLOWED is False
assert FORMAL_CANDIDATE is False
assert V2_IMPLEMENTED is False
assert ML_USED is False
assert SCORE_USED is False
assert TOPK_RANKING_USED is False
assert MACD_ADDED is False
assert ADX_ADDED is False
assert RSI_ADDED is False
assert PM_USED is False
assert W5_RUNTIME_ADOPTED is False
assert RUNTIME_CHANGED is False
assert PAPER_OPERATED is False
assert int(SUBMIT_N) == 0
assert int(CANCEL_N) == 0
assert int(LIVE_ORDER_N) == 0
assert len(list(ELIGIBLE_DAYS)) == 18
assert CURRENT_LOCKED["TRADE_N"] == 141
assert C0_OVERLAY_LOCKED["NET"] > CURRENT_LOCKED["NET"]
