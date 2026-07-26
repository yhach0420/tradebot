from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CAPTURE_ROOT = REPO_ROOT / "data" / "market_capture"
OUT_ROOT = REPO_ROOT / "results" / "research" / "canonical_strategy_root_cause"
SOT_REPAIR = REPO_ROOT / "results" / "research" / "canonical_quote_mainline_repair" / "20260725_080510"

AUDIT_DAYS = ("20260721", "20260722", "20260723", "20260724")
CAP = 5
LOT = 100
COST_BPS = 5.0
HARD_STOP_PCT = 1.0
BOARD_P33 = 0.437286
BOARD_P66 = 0.527869
MOMENTUM_P33 = 0.2546
BOARD_SPLIT_PERCENTILE = 47.62
LEGACY_FIXED_ACTIVATE_PCT = 0.80
LEGACY_FIXED_GIVEBACK_FRAC = 0.50
MAX_HOLD_SEC = 1800
SAMPLE_STRIDE = 5
SUBMIT = 0
CANCEL = 0
LIVE_ORDER = 0

REQUIRED_ARTIFACTS = ("report.md", "report.json", "audit.xlsx")
REQUIRED_SHEETS = [
    "README",
    "SOURCE_AUDIT",
    "PARITY_STATUS",
    "ENTRY_COHORTS",
    "PRE_EXIT_OPPORTUNITY",
    "BOARD_QUANTILES",
    "EXIT_CONTROLS",
    "EXIT_REASON_AUDIT",
    "IMMEDIATE_EXIT",
    "SPREAD_STOP",
    "EPISODES",
    "REENTRY",
    "C0_C8_RESULTS",
    "CAP5_EVENT_LOG",
    "DAILY_RESULTS",
    "SYMBOL_RESULTS",
    "ROOT_CAUSE_ATTRIBUTION",
    "NEXT_DECISION",
    "TESTS",
    "VERDICT",
]
