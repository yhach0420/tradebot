"""Price-Flow EXIT integrity evaluation constants (offline only)."""
from __future__ import annotations

from pathlib import Path

NATIVE = Path(__file__).resolve().parents[3]
SOT_PFE = NATIVE / "results" / "research" / "price_flow_exit" / "20260724_214418"
SOT_PBV2 = NATIVE / "results" / "research" / "pbv2_zero_base_revalidation" / "20260723_235148"
SOT_VCIE = NATIVE / "results" / "research" / "volume_confirmed_impulse_entry" / "20260724_210951"
PUSH_CACHE = NATIVE / "results" / "research" / "volume_confirmed_impulse_entry" / "_push_cache"
SMALL_PAPER = NATIVE / "results" / "small_paper"

CAPTURE_DAYS = ("20260721", "20260722", "20260723", "20260724")
WARMUP_DAY = "20260721"
OOS_DAYS = ("20260722", "20260723", "20260724")
MIN_OOS_DAYS_FOR_EDGE = 10

PATH_MAX_SEC = 20000.0
CAP = 5
SHARES = 100

# X0 vocabulary used for utiliable-actual parity denominator
X0_REASONS = frozenset(
    {
        "stop_hit",
        "trailing_mfe_exit",
        "no_progress_exit",
        "morning_session_close",
        "afternoon_session_close",
    }
)

# Frozen ExitParams from SoT 20260724_214418 (no retune)
SOT_EXIT_PARAMS = {
    "fb_window_sec": 30.0,
    "nft_window_sec": 120.0,
    "nft_progress_pct": 0.1,
    "be_arm_pct": 0.1,
    "vol_decay_frac": 0.5,
    "uptick_min": 0.5,
    "giveback_frac": 0.5,
}
