"""Price-Flow EXIT study constants."""
from __future__ import annotations

from pathlib import Path

NATIVE = Path(__file__).resolve().parents[3]
SOT_PBV2 = NATIVE / "results" / "research" / "pbv2_zero_base_revalidation" / "20260723_235148"
SOT_RPFE = NATIVE / "results" / "research" / "realistic_price_flow_entry" / "20260724_010347"
SOT_VCIE = NATIVE / "results" / "research" / "volume_confirmed_impulse_entry" / "20260724_210951"
PUSH_CACHE = NATIVE / "results" / "research" / "volume_confirmed_impulse_entry" / "_push_cache"

CAPTURE_DAYS = ("20260721", "20260722", "20260723", "20260724")
WARMUP_DAY = "20260721"
OOS_DAYS = ("20260722", "20260723", "20260724")

HARD_STOP_PCT = 1.20
ROUNDTRIP_COST_PCT = 0.05  # 5bps
SHARES = 100

# Session force-close (JST)
AM_FORCE_CLOSE_HM = (11, 25)
PM_FORCE_CLOSE_HM = (15, 23)

# No-progress (runtime Phase442 simplified for research path)
NP_START_SEC = 900.0
NP_REQUIRED_MFE_PCT = 0.6
NP_CURRENT_PNL_MAX = 0.3

MIN_OOS_DAYS_FOR_CANDIDATE = 10
EPSILON = 1e-9
