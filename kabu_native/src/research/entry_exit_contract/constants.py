"""Entry–Exit Contract study constants (offline only)."""
from __future__ import annotations

from pathlib import Path

NATIVE = Path(__file__).resolve().parents[3]
SOT_PBV2 = NATIVE / "results" / "research" / "pbv2_zero_base_revalidation" / "20260723_235148"
SOT_RPFE = NATIVE / "results" / "research" / "realistic_price_flow_entry" / "20260724_010347"
SOT_VCIE = NATIVE / "results" / "research" / "volume_confirmed_impulse_entry" / "20260724_210951"
SOT_PFE = NATIVE / "results" / "research" / "price_flow_exit" / "20260724_214418"
SOT_PFE_INT = NATIVE / "results" / "research" / "price_flow_exit_integrity" / "20260724_221403"
PUSH_CACHE = NATIVE / "results" / "research" / "volume_confirmed_impulse_entry" / "_push_cache"

CONTRACT_VERSION = "EEC_v1"
HARD_STOP_PCT = 1.20
ROUNDTRIP_COST_PCT = 0.05
SHARES = 100
CAP = 5
PATH_MAX_SEC = 20000.0
MIN_OOS_DAYS = 10

AM_FORCE_CLOSE_HM = (11, 25)
PM_FORCE_CLOSE_HM = (15, 23)

# Predefined coarse thresholds (train-selectable only; defaults frozen for EEC_v1)
DEFAULT_THRESHOLDS = {
    "EC1": {
        "vol_impulse_10s": 1.5,
        "vol_impulse_30s": 1.3,
        "uptick_min": 0.55,
        "hold_sec": 5.0,
        "max_impulse_age_sec": 30.0,
        "max_spread_change_bps": 15.0,
        "chase_rise_max": 1.5,
    },
    "EC2": {
        "trend_rise_min_pct": 0.3,
        "pullback_min_pct": 0.15,
        "pullback_max_pct": 1.2,
        "uptick_min": 0.50,
        "hold_sec": 3.0,
        "rebound_horizon_sec": 180.0,
        "rebound_progress_atr_mult": 0.35,
    },
    "EC3": {
        "compress_ratio_max": 0.70,
        "vol_impulse_10s": 1.3,
        "uptick_min": 0.50,
        "hold_sec": 5.0,
        "range_lookback_sec": 120.0,
        "prior_lookback_sec": 300.0,
    },
}

# ExitParams for diagnostic X6 (frozen from SoT; no retune)
X6_PARAMS = {
    "fb_window_sec": 30.0,
    "nft_window_sec": 120.0,
    "nft_progress_pct": 0.1,
    "be_arm_pct": 0.1,
    "vol_decay_frac": 0.5,
    "uptick_min": 0.5,
    "giveback_frac": 0.5,
}
