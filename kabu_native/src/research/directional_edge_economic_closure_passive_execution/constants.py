"""DEECPA — Directional Edge Economic Closure + Passive Execution Audit."""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
OUT_ROOT = REPO_ROOT / "results" / "research" / "directional_edge_economic_closure_passive_execution"
SOURCE_CDEED = REPO_ROOT / "results" / "research" / "continuous_directional_vs_execution_edge" / "20260725_222851"
ENRICHED_CACHE = (
    REPO_ROOT / "results" / "research" / "continuous_directional_vs_execution_edge" / "_cache" / "enriched_s1.pkl"
)

STRIDE = 1
LOT = 100
COST_RATE = 0.0005  # 5bps roundtrip as fraction of entry notional
COST_BPS = 5.0
SEED = 42
REPRO_ABS_TOL = 1e-9

FIXED_CANDIDATE = "D-MID_D4_H6"
FIXED_LABEL = "D-MID_D4"
FIXED_HID = "H6"
FIXED_THRESHOLD = 0.48256067040851486
PRIMARY_HORIZON_SEC = 180.0
LIMIT_TIMEOUT_SEC = 5.0

TRAIN_DAYS = ["20260721", "20260722"]
VAL_DAYS = ["20260723"]
HOLD_DAYS = ["20260724"]

ARMS = ("E0", "E1", "E2", "E3", "E4")

SUBMIT = 0
CANCEL = 0
LIVE_ORDER = 0

REQUIRED_ARTIFACTS = ("report.md", "report.json", "audit.xlsx")
REQUIRED_SHEETS = [
    "summary", "source_reproduction", "economic_formula", "manual_yen_checks", "yen_vs_bps",
    "immediate_cross", "horizon_comparison", "fixed_candidate_spread_cohorts", "execution_arms",
    "orders", "fills", "partial_fills", "no_fills", "queue_audit", "train_arm_selection",
    "validation", "holdout", "daily", "symbols", "trade_dependence", "symbol_dependence",
    "price_band", "notional_band", "execution_audit", "integrity_audit", "tests",
]
