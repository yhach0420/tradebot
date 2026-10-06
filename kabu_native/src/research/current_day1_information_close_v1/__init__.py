"""CURRENT_DAY1_INFORMATION_CLOSE_AND_NEW_TAPE_TRANSITION_V1.

Close 20260911 existing-raw ENTRY mining. Do not rewrite Day2 primary.
"""
from __future__ import annotations

from research.futures_reversal_precursor_day1_v1 import DAY2_PRIMARY, DAY2_PRIMARY_ID
from research.market_breadth_leadership_acquisition_v1 import (
    DEFAULT_CADENCE_SEC,
    FALLBACK_CADENCE_SEC,
    PRIMARY_WINDOW_END_HM,
    PRIMARY_WINDOW_START_HM,
    RANKING_TYPES,
)
from research.market_breadth_leadership_acquisition_v1.derive import (
    FORBIDDEN_PRIMARY_KEYS,
    PRIMARY_CONTEXT_CANDIDATES,
)
from research.day2_futures_plus_market_breadth_acquisition_v1 import CASE_LIVE_READY

ANALYSIS_ID = "CURRENT_DAY1_INFORMATION_CLOSE_AND_NEW_TAPE_TRANSITION_V1"
PARENT_ID = "FUTURES_REVERSAL_X_CASH_PARTICIPATION_DAY1_V1"
KIND = "NEW_INFO_DEV_CONSTRUCTION_ONLY"
TRADING_DATE = "20260911"

VERDICT = "CURRENT_DAY1_INFORMATION_INSUFFICIENT_FOR_ENTRY"
NEXT = "ACQUIRE_NEW_CAUSAL_MARKET_PARTICIPATION_TAPE_V1"
NEXT_SOURCE = "MARKET_BREADTH_LEADERSHIP"

DAY2_PRIMARY_UNCHANGED = True
FEATURE_MINING_CLOSED = True
ENTRY = False
EXIT = False
RUNTIME_CHANGED = False
PAPER_CHANGED = False
DAY2_SUBSTITUTIONS_ALLOWED = False

STOP_FORBIDDEN = (
    "new futures transforms",
    "new return windows",
    "new cash windows",
    "new activity thresholds",
    "new TopK",
    "new technical indicators",
    "new reversal definitions",
    "new AND/OR combinations",
    "ML / tree / ridge",
    "winner-vs-loser feature mining",
)

RECON_Q1 = "Does breadth / leadership provide incremental market-state information beyond futures?"
RECON_Q2 = "Does breadth state change the cross-sectional effect of OBSERVED_TRADE_N_180S / activity family?"

assert DAY2_PRIMARY_ID == "FUTURES_X_STOCK_STATE_DAY2_CONFIRMATION_V1"
assert DAY2_PRIMARY["substitutions_allowed"] is False
assert DAY2_PRIMARY["selector"] == "OBSERVED_TRADE_N_180S"
assert DAY2_PRIMARY["horizon"] == "10m"
assert DAY2_PRIMARY["direction"] == "LONG TOP16"
assert DAY2_PRIMARY_UNCHANGED is True
assert FEATURE_MINING_CLOSED is True
assert ENTRY is False and EXIT is False
assert RANKING_TYPES == (1, 2, 5, 6, 7, 14, 15)
assert DEFAULT_CADENCE_SEC == 60
assert FALLBACK_CADENCE_SEC == 120
assert PRIMARY_WINDOW_START_HM == (9, 5)
assert PRIMARY_WINDOW_END_HM == (11, 25)
assert CASE_LIVE_READY == "MARKET_BREADTH_LEADERSHIP_LIVE_READY_V2"
assert "VOLUME_SURGE_BREADTH" in FORBIDDEN_PRIMARY_KEYS
assert "LEADERSHIP_STRENGTH" in PRIMARY_CONTEXT_CANDIDATES
assert "LEADERSHIP_TURNOVER" in PRIMARY_CONTEXT_CANDIDATES
assert NEXT == "ACQUIRE_NEW_CAUSAL_MARKET_PARTICIPATION_TAPE_V1"
assert VERDICT == "CURRENT_DAY1_INFORMATION_INSUFFICIENT_FOR_ENTRY"
