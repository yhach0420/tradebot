"""Shared constants for PBv2 zero-base revalidation."""
from __future__ import annotations

from pathlib import Path

NATIVE = Path(__file__).resolve().parents[3]
JST_NAME = "Asia/Tokyo"
COST_BPS = 0.05  # round-trip 5bps expressed as percent of notional for yen calc
SHARES = 100
CAP = 5

# Suspect static-board days (prior audit); sensitivity only, not primary.
SUSPECT_BOARD_DAYS = frozenset({"20260615", "20260616", "20260617", "20260618", "20260619"})

# Dynamic-board SHADOW_READY gate (pre-fixed).
MIN_DYNAMIC_OOS_DAYS = 5

# Panel thinning: one evaluation state per symbol per this many seconds.
EVAL_BUCKET_SEC = 120
# Price path downsample for forward labels (seconds).
PRICE_PATH_BUCKET_SEC = 10
# Max sessions per day used for panel (prefer richest events file).
MAX_SESSIONS_PER_DAY = 1

# Counterfactual EXIT (mainline-like, no production change).
CF_STOP_PCT = -1.0
CF_TRAIL_ACTIVATE_PCT = 1.0
CF_TRAIL_GIVEBACK = 0.40
CF_NO_PROGRESS_SEC = 300.0
CF_NO_PROGRESS_MFE_PCT = 0.35
CF_MAX_HOLD_SEC = 900.0

# Large-rise episode proxies (multiple horizons; not a single arbitrary cut).
LARGE_RISE_MFE_5M_PCT = 1.0
LARGE_RISE_MFE_10M_PCT = 1.5
LARGE_RISE_MFE_15M_PCT = 2.0

# Forbidden feature name fragments (time-of-day / symbol / date).
TIME_FEATURE_BLOCKLIST = (
    "minutes_from_open",
    "minutes_to_refresh",
    "near_refresh",
    "session_am",
    "session_pm",
    "day_high_from_open",
    "minutes_since_day_high",
    "mkt_minutes",
    "mkt_session",
    "time_of_day",
    "hour_of_day",
    "am_pm",
)

LANE_A_FEATURES = (
    "f_rise5",
    "f_rise10",
    "f_rise15",
    "f_mom",
    "f_near_high",
    "f_vwap",
    "f_atr",
    "f_tv",
    "f_bounce",
    "f_fall",
    "f_slope5",
    "f_chase",
    "f_pbv2",  # comparison only; not required input
    "f_spread",  # treated as Lane C when used as dynamic; stored here if dense
)

LANE_B_FEATURES = (
    "f_imb",
    "f_imb_pct",
    "f_board_age",
)

LANE_C_FEATURES = (
    "f_np_ret_10",
    "f_np_ret_30",
    "f_np_ret_60",
    "f_np_ret_120",
    "f_np_ret_300",
    "f_np_slope_60",
    "f_np_accel_60",
    "f_np_imb_chg_10",
    "f_np_imb_chg_30",
    "f_np_imb_chg_60",
    "f_np_imb_chg_120",
    "f_np_imb_chg_300",
    "f_np_imb_persist_60",
    "f_np_bid_chg_60",
    "f_np_ask_chg_60",
    "f_np_tv_chg_pct_60",
    "f_np_vol_price_sync_60",
    "f_np_ticks_60",
)

# Required set for "lane_c_complete_required" (candidate rules that need full window).
LANE_C_REQUIRED = (
    "f_np_imb_chg_60",
    "f_np_bid_chg_60",
    "f_np_ask_chg_60",
    "f_np_tv_chg_pct_60",
    "f_np_imb_persist_60",
)

EVENT_FEATURE_MAP = {
    "f_rise5": "entry_rise_5min_pct",
    "f_rise10": "entry_rise_10min_pct",
    "f_rise15": "entry_rise_15min_pct",
    "f_mom": "entry_momentum_continuation_score",
    "f_mom_alt": "momentum_continuation_score",
    "f_near_high": "entry_near_day_high_pct",
    "f_vwap": "entry_vwap_dev_pct",
    "f_atr": "atr_pct",
    "f_tv": "trading_value",
    "f_bounce": "microseq_bounce_from_recent_low",
    "f_fall": "microseq_fall_from_recent_high",
    "f_slope5": "microseq_slope_5min",
    "f_chase": "late_chase_flag",
    "f_pbv2": "entry_expectancy_score_v2",
    "f_spread": "spread_bps",
    "f_imb": "entry_order_book_imbalance",
    "f_imb_pct": "entry_imbalance_percentile",
    "f_board_age": "board_age_sec",
    "f_price_age": "price_age_sec",
    "f_r30": "r30_sec",
    "f_r60": "r60_sec",
    "f_r120": "r120_sec",
}

NP_STEMS = (
    "np_ret",
    "np_accel",
    "np_slope",
    "np_imb_chg",
    "np_imb_persist",
    "np_bid_chg",
    "np_ask_chg",
    "np_tv_chg_pct",
    "np_vol_price_sync",
    "np_ticks",
)
NP_WINDOWS = (10, 30, 60, 120, 300)
