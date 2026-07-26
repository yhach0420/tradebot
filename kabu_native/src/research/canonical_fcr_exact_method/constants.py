"""Canonical FCR exact-method constants — 5-stage reclaim, not VCIE."""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CAPTURE_ROOT = REPO_ROOT / "data" / "market_capture"
OUT_ROOT = REPO_ROOT / "results" / "research" / "canonical_fcr_exact_method"

SOT_REPAIR = REPO_ROOT / "results" / "research" / "canonical_quote_mainline_repair" / "20260725_080510"
SOT_AUDIT = REPO_ROOT / "results" / "research" / "global_quote_semantic_audit" / "20260725_065034"
SOT_EGC = REPO_ROOT / "results" / "research" / "execution_grade_confirmation" / "20260725_061724"
SOT_VCIE = REPO_ROOT / "results" / "research" / "canonical_vcie_exact_method" / "20260725_104742"

CAP = 5
LOT = 100
COST_BPS = 5.0
SEED = 42
SAMPLE_STRIDE = 6

# Coarse grids only
BUY_RATIO_GRID = (0.55, 0.60, 0.70)
FREQ_ACCEL_GRID = (1.2, 1.5, 2.0)
NEW_LOW_STOP_SEC = (10, 20, 30, 60)
RECLAIM_HOLD = (("cross", 0), ("events", 2), ("events", 3))
SPREAD_PERCENTILES = (0.50, 0.65, 0.80)
PULLBACK_FRAC = ((0.10, 0.30), (0.10, 0.50))
EXPIRY_EXH_TO_BUY = (10, 20, 30)
EXPIRY_BUY_TO_RECLAIM = (5, 10, 20)

SUBMIT = 0
CANCEL = 0
LIVE_ORDER = 0

REQUIRED_ARTIFACTS = ("report.md", "report.json", "audit.xlsx")
REQUIRED_SHEETS = [
    "README", "SOURCE_AUDIT", "DATA_SPLIT", "CANONICAL_COVERAGE", "VWAP_AUDIT",
    "TREND_CONTEXT", "INITIAL_IMPULSES", "PULLBACKS", "PULLBACK_QUALITY",
    "SELLING_EXHAUSTION", "BUY_FLOW_RESUMPTION", "BOARD_FLOW_CONFIRMATION",
    "RECLAIM_LEVELS", "RECLAIM_TRIGGERS", "STATE_TRANSITIONS", "EPISODES",
    "EXPIRED_EPISODES", "INVALIDATED_EPISODES", "ONE_IMPULSE_ONE_ENTRY",
    "F0_RECLAIM_ONLY", "F1_TREND_RECLAIM", "F2_PULLBACK_RECLAIM",
    "F3_SELLING_EXHAUSTED", "F4_BUY_FLOW_CONFIRMED", "F5_FULL_FCR",
    "D1_NO_EXHAUSTION", "D2_NO_BUY_FLOW", "INCREMENTAL_EFFECTS",
    "TRAIN_RESULTS", "VALIDATION_RESULTS", "FORENSIC_HOLDOUT",
    "OPPORTUNITY_PATHS", "PBV2_MATCHED", "EXECUTION_E0_E5", "ONE_TICK_ADVERSE",
    "REFERENCE_EXITS", "CAP5", "CAP_BLOCKED", "SYMBOL_REENTRY",
    "DAILY_RESULTS", "SYMBOL_RESULTS", "DEPENDENCY", "TESTS", "VERDICT",
]
