"""Canonical VCIE exact-method constants — yesterday's build order only."""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CAPTURE_ROOT = REPO_ROOT / "data" / "market_capture"
OUT_ROOT = REPO_ROOT / "results" / "research" / "canonical_vcie_exact_method"

SOT_REPAIR = REPO_ROOT / "results" / "research" / "canonical_quote_mainline_repair" / "20260725_080510"
SOT_AUDIT = REPO_ROOT / "results" / "research" / "global_quote_semantic_audit" / "20260725_065034"
SOT_EGC = REPO_ROOT / "results" / "research" / "execution_grade_confirmation" / "20260725_061724"
SOT_V2 = REPO_ROOT / "results" / "research" / "canonical_zero_base_v2" / "20260725_100757"

CAP = 5
LOT = 100
COST_BPS = 5.0
SEED = 42
SAMPLE_STRIDE = 4  # denser than v2; volume events are sparse

# Coarse grids only (no fine search)
VOL_RATIO_GRID = (1.3, 1.5, 2.0)
BUY_RATIO_GRID = (0.55, 0.60, 0.70)
HOLD_GRID = (("events", 2), ("events", 3), ("seconds", 5.0))
EXPIRY_GRID = (10, 20, 30, 60)
SPREAD_PERCENTILES = (0.50, 0.65, 0.80)

# Episode timing (yesterday)
CONTEXT_LOOKBACK_SEC = 120.0
BURST_TO_SIDE_MAX = 30.0
SIDE_TO_CROSS_MAX = 30.0
CROSS_TO_HOLD_MAX = 10.0
BURST_TO_ENTRY_MAX = 60.0

MIN_TRADE_DIR_CONFIDENCE = 0.55
MIN_DIR_CLASSIFIED_RATE = 0.50  # lineage gate

SUBMIT = 0
CANCEL = 0
LIVE_ORDER = 0

REQUIRED_ARTIFACTS = ("report.md", "report.json", "audit.xlsx")
REQUIRED_SHEETS = [
    "README",
    "SOURCE_AUDIT",
    "VOLUME_LINEAGE",
    "TRADE_DIRECTION_LINEAGE",
    "SESSION_TIME_LINEAGE",
    "CANONICAL_EXECUTION",
    "DATA_SPLIT",
    "CONTEXT_EVENTS",
    "VOLUME_BURSTS",
    "TRADE_SIDE_EVENTS",
    "PRICE_CROSSES",
    "BREAKOUT_HOLDS",
    "EPISODES",
    "EXPIRED_EPISODES",
    "FAILED_EPISODES",
    "V1_PRICE_CROSS",
    "V2_VOLUME",
    "V3_TRADE_SIDE",
    "V4_FULL_VCIE",
    "D1_TRADE_SIDE_NO_VOLUME",
    "INCREMENTAL_EFFECTS",
    "TRAIN_RESULTS",
    "VALIDATION_RESULTS",
    "STRICT_OOS",
    "OPPORTUNITY_PATHS",
    "EXECUTION_E0_E5",
    "ONE_TICK_ADVERSE",
    "CAP5",
    "CAP_BLOCKED",
    "DAILY_RESULTS",
    "SYMBOL_RESULTS",
    "DEPENDENCY",
    "TESTS",
    "VERDICT",
]
