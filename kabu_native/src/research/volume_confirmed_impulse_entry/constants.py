"""VCIE constants — offline research only."""
from __future__ import annotations

from pathlib import Path

NATIVE = Path(__file__).resolve().parents[3]
SOT_PBV2_RUN = "20260723_235148"
SOT_RPFE_RUN = "20260724_010347"
SOT_PBV2_DIR = NATIVE / "results" / "research" / "pbv2_zero_base_revalidation" / SOT_PBV2_RUN
SOT_RPFE_DIR = NATIVE / "results" / "research" / "realistic_price_flow_entry" / SOT_RPFE_RUN

# Coverage gates (adoption blocked if unmet)
MIN_OOS_DAYS = 10
MIN_COMPLETE_TRIGGERS = 200
MIN_AM_DAYS = 5
MIN_PM_DAYS = 5
MAX_DAY_TRIGGER_SHARE = 0.40
MAX_DAY_PNL_SHARE = 0.50

# Impulse → ENTRY latency (primary)
MAX_IMPULSE_TO_ENTRY_SEC = 30.0
MAX_CONTEXT_AGE_SEC = 300.0

# Predefined threshold grids (train-only selection; no open search)
VOL_IMPULSE_10S_GRID = (1.25, 1.5, 2.0, 3.0)
VOL_IMPULSE_30S_GRID = (1.2, 1.3, 1.5, 2.0)
UPTICK_RATIO_GRID = (0.55, 0.60, 0.65, 0.70)
ASK_EXEC_RATIO_GRID = (0.55, 0.60, 0.65, 0.70)
HOLD_SPECS = (
    {"mode": "ticks", "n": 2},
    {"mode": "sec", "n": 5},
    {"mode": "sec", "n": 10},
)
CONTEXT_AGE_GRID = (60.0, 180.0, 300.0)

TIME_FEATURE_BLOCKLIST = (
    "minutes_from_open",
    "hour_of_day",
    "weekday",
    "session_am",
    "session_pm",
)

METHODS = (
    "V0_PBv2",
    "V1_CROSS",
    "V2_VOLUME",
    "V3_TRADE_SIDE",
    "V4_FULL_VCIE",
    "V5_PBV2_OR",
    "V6_PBV2_AND",
    "V7_INDEPENDENT",
)
