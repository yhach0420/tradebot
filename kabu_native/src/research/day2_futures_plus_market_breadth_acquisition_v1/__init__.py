"""DAY2_FUTURES_PLUS_MARKET_BREADTH_ACQUISITION_V1. Live-ready derive V2. No strategy."""
from __future__ import annotations

from research.futures_context_day1_effect_check_v1 import FEATURE_NAMES, PLACEBO_SHIFT_SEC
from research.market_breadth_leadership_acquisition_v1 import (
    FORBIDDEN_RESEARCH_OUTCOME_DAYS,
    RANKING_TYPES,
)

ANALYSIS_ID = "DAY2_FUTURES_PLUS_MARKET_BREADTH_ACQUISITION_V1"
PARENT_ID = "MARKET_BREADTH_LEADERSHIP_ACQUISITION_V1"
KIND = "NEW_INFO_DEV_CONSTRUCTION_ONLY"

CASE_LIVE_READY = "MARKET_BREADTH_LEADERSHIP_LIVE_READY_V2"
NEXT_RUN = "RUN_DAY2_FUTURES_PLUS_FIRST_LIVE_BREADTH_V1"

FUTURES_LIVE_COMMAND = "python kabu_native\\scripts\\run_futures_market_context_capture.py --live"
BREADTH_LIVE_COMMAND = "python kabu_native\\scripts\\run_market_breadth_leadership_capture.py --live"
FUTURES_PID_REL = "data/market_context_capture/YYYYMMDD/new_info.pid"
BREADTH_PID_REL = "data/market_breadth_capture/YYYYMMDD/breadth_collector.pid"

ENTRY = False
EXIT = False
STRATEGY_BUILT = False
DAY2_FROZEN_PRICE_RETURN_TEST_CHANGED = False
TRUE_OOS = False
CERTIFIED = False

assert RANKING_TYPES == (1, 2, 5, 6, 7, 14, 15)
assert FEATURE_NAMES[0] == "NK_RET_30S"
assert PLACEBO_SHIFT_SEC == 300
assert "20260912" in FORBIDDEN_RESEARCH_OUTCOME_DAYS
assert ENTRY is False and EXIT is False and STRATEGY_BUILT is False
assert DAY2_FROZEN_PRICE_RETURN_TEST_CHANGED is False
