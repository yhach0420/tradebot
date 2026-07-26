"""E1X5-FWD — Forward Shadow Implementation and Validation."""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
OUT_ROOT = REPO_ROOT / "results" / "research" / "e1_x5_forward_shadow"
SOURCE_CC = REPO_ROOT / "results" / "research" / "idees_fixed_candidate_concentration_oos" / "20260725_235043"
ENRICHED_CACHE = (
    REPO_ROOT / "results" / "research" / "continuous_directional_vs_execution_edge" / "_cache" / "enriched_s1.pkl"
)

FIXED_STRATEGY = "E1_X5"
ENV_KEY = "E1_X5_FORWARD_SHADOW"
THRESHOLD = 0.48256067040851486

TRAIN_DAYS = ["20260721", "20260722"]
VAL_DAYS = ["20260723"]
HOLD_DAYS = ["20260724"]
PARITY_DAYS = TRAIN_DAYS + VAL_DAYS + HOLD_DAYS

EXPECT = {
    "TRAIN": {"trades": 69, "total_pnl_yen_100": 546557.29, "profit_factor_yen_100": 20.517846869704798},
    "VAL": {"trades": 58, "total_pnl_yen_100": 72841.0, "profit_factor_yen_100": 4.997398763040485},
    "HOLD": {"trades": 16, "total_pnl_yen_100": 79969.4, "profit_factor_yen_100": 5.2975239477113645},
}

SUBMIT = 0
CANCEL = 0
LIVE_ORDER = 0

REQUIRED_ARTIFACTS = ("report.md", "report.json", "audit.xlsx")
REQUIRED_SHEETS = [
    "summary", "fixed_spec", "runtime_parity", "entry_candidates", "entries", "exits",
    "open_positions", "daily", "time_bands", "exit_reasons", "cap5", "pbv2_overlap",
    "top_trade_removed", "top_symbol_removed", "forward_gate", "execution_audit",
    "integrity_audit", "tests",
]
