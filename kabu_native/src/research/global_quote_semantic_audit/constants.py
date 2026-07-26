"""Frozen constants for Global Quote Semantic Audit."""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CAPTURE_ROOT = REPO_ROOT / "data" / "market_capture"
OUT_ROOT = REPO_ROOT / "results" / "research" / "global_quote_semantic_audit"
EGC_SOT = REPO_ROOT / "results" / "research" / "execution_grade_confirmation" / "20260725_061724"

AUDIT_DAYS = ("20260721", "20260722", "20260723", "20260724")
SAMPLE_PER_DAY = 2500
TRACE_MIN = 30
SUBMIT = 0
CANCEL = 0
LIVE_ORDER = 0

# Board tertile cutoffs (frozen PBv2) — applied to imbalance value as currently computed.
BOARD_P33 = 0.437286
BOARD_P66 = 0.527869

SEARCH_TERMS = (
    "BidPrice",
    "AskPrice",
    "BidQty",
    "AskQty",
    "Buy1",
    "Sell1",
    "best_bid",
    "best_ask",
    "true_bid",
    "true_ask",
    "bid_qty",
    "ask_qty",
    "spread",
    "spread_bps",
    "imbalance",
    "bid_pressure",
    "ask_pressure",
    "board_mid",
    "board_high",
    "board_low",
)

REQUIRED_ARTIFACTS = ("report.md", "report.json", "audit.xlsx")

REQUIRED_SHEETS = [
    "README",
    "SEARCH_INVENTORY",
    "FIELD_SEMANTICS",
    "STATIC_REFERENCES",
    "RUNTIME_LINEAGE",
    "PAPER_LINEAGE",
    "RESEARCH_LINEAGE",
    "PBV2_IMPACT",
    "GUARD_IMPACT",
    "EXIT_IMPACT",
    "EXECUTION_IMPACT",
    "RESEARCH_IMPACT",
    "R0_CURRENT",
    "R1_CANONICAL",
    "DECISION_DIFF",
    "ENTRY_DIFF",
    "EXIT_DIFF",
    "PNL_DIFF",
    "INVALIDATED_STUDIES",
    "REPLAY_PRIORITY",
    "CANONICAL_SPEC",
    "TESTS",
    "VERDICT",
]

VERDICTS = (
    "QUOTE_SEMANTIC_MAINLINE_SAFE",
    "QUOTE_SEMANTIC_MAINLINE_AFFECTED",
    "QUOTE_SEMANTIC_RESEARCH_ONLY_AFFECTED",
    "QUOTE_SEMANTIC_GLOBAL_AFFECTED",
    "QUOTE_SEMANTIC_UNKNOWN",
)
