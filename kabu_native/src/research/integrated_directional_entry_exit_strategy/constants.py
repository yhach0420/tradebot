"""IDEES — Integrated Directional ENTRY-EXIT Strategy Construction."""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
OUT_ROOT = REPO_ROOT / "results" / "research" / "integrated_directional_entry_exit_strategy"
SOURCE_CDEED = REPO_ROOT / "results" / "research" / "continuous_directional_vs_execution_edge" / "20260725_222851"
ENRICHED_CACHE = (
    REPO_ROOT / "results" / "research" / "continuous_directional_vs_execution_edge" / "_cache" / "enriched_s1.pkl"
)

STRIDE = 1
LOT = 100
COST_RATE = 0.0005  # 5bps roundtrip once on entry notional
COST_BPS = 5.0
CAP = 5
SEED = 42

FIXED_LABEL = "D-MID_D4"
FIXED_HID = "H6"
FIXED_CANDIDATE = "D-MID_D4_H6"
FIXED_THRESHOLD = 0.48256067040851486

CONFIRM_SEC = 5.0
TRAIN_DAYS = ["20260721", "20260722"]
VAL_DAYS = ["20260723"]
HOLD_DAYS = ["20260724"]

ENTRIES = ("E1", "E2", "E3", "E4")
EXITS = ("X1", "X2", "X3", "X4", "X5")
STRATEGIES = tuple(f"{e}_{x}" for e in ENTRIES for x in EXITS)

SUBMIT = 0
CANCEL = 0
LIVE_ORDER = 0

REQUIRED_ARTIFACTS = ("report.md", "report.json", "audit.xlsx")
REQUIRED_SHEETS = [
    "summary", "strategy_specs", "all_20_strategies", "entry_comparison", "exit_comparison",
    "interaction_matrix", "trades", "train", "validation", "holdout", "daily", "symbols",
    "exit_reasons", "holding_time", "mfe_mae", "cap5", "pbv2_overlap", "execution_audit",
    "integrity_audit", "tests",
]
