"""RPFE constants."""
from __future__ import annotations

from pathlib import Path

NATIVE = Path(__file__).resolve().parents[3]
SOT_RUN = "20260723_235148"
SOT_DIR = NATIVE / "results" / "research" / "pbv2_zero_base_revalidation" / SOT_RUN

QUANTILES = (0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8)
MAX_FEATURES_PER_PATTERN = 8
MAX_THRESHOLDS_PER_STATE = 2

# Dynamic coverage gates (provisional)
MIN_OOS_DAYS = 10
MIN_DYNAMIC_COMPLETE_ROWS = 2000
MIN_AM_COMPLETE_DAYS = 5
MIN_PM_COMPLETE_DAYS = 5

TIME_FEATURE_BLOCKLIST = (
    "minutes_from_open",
    "minutes_to_refresh",
    "near_refresh",
    "session_am",
    "session_pm",
    "day_high_from_open",
    "minutes_since_day_high",
    "mkt_minutes",
    "hour_of_day",
    "am_pm",
    "weekday",
)

STATES = (
    "IDLE",
    "CONTEXT_READY",
    "SETUP_DETECTED",
    "SELL_PRESSURE_WEAKENED",
    "BUY_PRESSURE_CONFIRMED",
    "PRICE_TRIGGERED",
    "ENTRY",
    "INVALIDATED",
)
