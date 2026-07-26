"""IDEES-CC — Fixed Candidate Concentration and OOS Closure."""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
OUT_ROOT = REPO_ROOT / "results" / "research" / "idees_fixed_candidate_concentration_oos"
SOURCE_IDEES = REPO_ROOT / "results" / "research" / "integrated_directional_entry_exit_strategy" / "20260725_233104"
ENRICHED_CACHE = (
    REPO_ROOT / "results" / "research" / "continuous_directional_vs_execution_edge" / "_cache" / "enriched_s1.pkl"
)

FIXED_STRATEGY = "E1_X5"
ENTRY_ARM = "E1"
EXIT_ARM = "X5"
COMPARE_EXIT = "X1"

FIXED_LABEL = "D-MID_D4"
FIXED_HID = "H6"
FIXED_THRESHOLD = 0.48256067040851486

TRAIN_DAYS = ["20260721", "20260722"]
VAL_DAYS = ["20260723"]
HOLD_DAYS = ["20260724"]

REPRO_ABS_TOL = 1e-9
REPRO_EXPECT = {
    "trades": 69,
    "total_pnl_yen_100": 546557.29,
    "avg_pnl_yen_100": 7921.120144927537,
    "profit_factor_yen_100": 20.517846869704798,
    "max_drawdown_yen": -18612.0,
    "top1_symbol_share": 0.4669108997781935,
    "top3_symbol_share": 0.8276537628987339,
    "daily_20260721": 17175.09,
    "daily_20260722": 529382.20,
}

SUBMIT = 0
CANCEL = 0
LIVE_ORDER = 0

REQUIRED_ARTIFACTS = ("report.md", "report.json", "audit.xlsx")
REQUIRED_SHEETS = [
    "summary", "candidate_spec", "reproduction", "train_full", "symbol_concentration",
    "yen_bps_concentration", "exclude_top1", "exclude_top3", "leave_one_symbol_out",
    "one_trade_per_symbol_session", "top_trade_removed", "daily", "time_bands",
    "validation", "holdout", "x1_x5_comparison", "exit_reasons", "cap5", "pbv2_overlap",
    "execution_audit", "integrity_audit", "tests",
]
