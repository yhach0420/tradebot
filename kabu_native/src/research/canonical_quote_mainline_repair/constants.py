from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CAPTURE_ROOT = REPO_ROOT / "data" / "market_capture"
OUT_ROOT = REPO_ROOT / "results" / "research" / "canonical_quote_mainline_repair"
AUDIT_SOT = REPO_ROOT / "results" / "research" / "global_quote_semantic_audit" / "20260725_065034"
EGC_SOT = REPO_ROOT / "results" / "research" / "execution_grade_confirmation" / "20260725_061724"

AUDIT_DAYS = ("20260721", "20260722", "20260723", "20260724")
CAP = 5
LOT = 100
COST_BPS = 5.0
HARD_STOP_PCT = 1.0
BOARD_P33 = 0.437286
BOARD_P66 = 0.527869
MOMENTUM_P33 = 0.2546
BOARD_SPLIT_PERCENTILE = 47.62
SAMPLE_STRIDE = 5
MAX_HOLD_SEC = 1800
SUBMIT = 0
CANCEL = 0
LIVE_ORDER = 0

REQUIRED_ARTIFACTS = ("report.md", "report.json", "audit.xlsx")
REQUIRED_SHEETS = [
    "README",
    "SOURCE_AUDIT",
    "CANONICAL_SPEC",
    "RAW_FIELD_PRESERVATION",
    "RUNTIME_REFERENCE_CLOSURE",
    "STAGE0_LINEAGE",
    "TOP_IMBALANCE",
    "DEPTH_IMBALANCE",
    "BOARD_CLASSIFICATION",
    "LEGACY_PARITY",
    "ENTRY_DECISION_DIFF",
    "EXIT_DECISION_DIFF",
    "ENTRY_TRACE",
    "EXIT_TRACE",
    "P0_LEGACY",
    "P1_CANONICAL_ENTRY",
    "P2_CANONICAL_EXIT",
    "P3_CANONICAL_FULL",
    "CAP5_EVENT_LOG",
    "PORTFOLIO_RESULTS",
    "DAILY_RESULTS",
    "SYMBOL_DEPENDENCY",
    "DAY_DEPENDENCY",
    "EXECUTION_PRICE",
    "OPERATIONAL_EXITS",
    "INVALIDATED_HISTORY",
    "PAPER_READINESS",
    "TESTS",
    "VERDICT",
]

OPERATIONAL_EXIT_REASONS = frozenset({
    "session_close",
    "reconnect",
    "recovery",
    "stale_data",
    "forced_close",
    "capture_end",
})
