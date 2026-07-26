"""Canonical FCR EXIT episode — ENTRY frozen, EXIT built from post-entry states."""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CAPTURE_ROOT = REPO_ROOT / "data" / "market_capture"
OUT_ROOT = REPO_ROOT / "results" / "research" / "canonical_fcr_exit_episode"
ENTRY_SOT = REPO_ROOT / "results" / "research" / "canonical_fcr_exact_method" / "20260725_120247"
INTEGRITY_SOT = REPO_ROOT / "results" / "research" / "canonical_fcr_incremental_integrity" / "20260725_123804"

# Frozen ENTRY thresholds — DO NOT CHANGE
FROZEN_ENTRY = {
    "slope_min": 0.0,
    "pb_lo": 0.10,
    "pb_hi": 0.30,
    "new_low_stop_sec": 10.0,
    "buy_ratio": 0.55,
    "freq_accel": 1.5,
    "reclaim_hold_events": 2,
    "expiry_exh_to_buy": 10.0,
    "expiry_buy_to_reclaim": 20.0,
    "spread_max_bps": None,
}

WARMUP = "20260721"
TRAIN = "20260722"
VALIDATION = "20260723"
HOLDOUT = "20260724"

STRIDE = 1
LOT = 100
COST_BPS = 5.0
SEED = 42
HORIZON_SEC = 180.0
NO_PROGRESS_SEC = 60.0
WINNER_MFE_PCT = 0.35
GIVEBACK_FRAC = 0.40  # give back 40% of MFE
NOISE_ADVERSE_PCT = 0.25

SUBMIT = 0
CANCEL = 0
LIVE_ORDER = 0

EXIT_ARMS = ("X0", "X1", "X2", "X3", "X4", "X5")
REQUIRED_ARTIFACTS = ("report.md", "report.json", "audit.xlsx")
REQUIRED_SHEETS = [
    "README", "SOURCE_AUDIT", "FROZEN_ENTRY", "POST_ENTRY_STATES",
    "HEALTHY_ADVANCE", "TEMPORARY_NOISE", "FALSE_RECLAIM", "NO_PROGRESS", "WINNER_GIVEBACK",
    "X0", "X1", "X2", "X3", "X4", "X5",
    "INCREMENTAL_EXIT", "TRAIN_RESULTS", "VALIDATION_RESULTS", "CAP5",
    "STRATEGY_EVAL", "TESTS", "VERDICT",
]
