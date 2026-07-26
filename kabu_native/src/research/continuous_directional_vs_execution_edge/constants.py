"""CDEED — Continuous Directional Edge vs Execution Edge Decomposition."""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
OUT_ROOT = REPO_ROOT / "results" / "research" / "continuous_directional_vs_execution_edge"
SOURCE_CS = REPO_ROOT / "results" / "research" / "ueia_continuous_session_tradability_repair" / "20260725_220242"
S1_CACHE = (
    REPO_ROOT / "results" / "research" / "ueia_continuous_session_tradability_repair" / "_cache" / "continuous_s1.pkl"
)
CACHE_DIR = OUT_ROOT / "_cache"

STRIDE = 1
LOT = 100
COST_BPS = 5.0
SEED = 42
MIN_SELECTED = 20
REPRO_ABS_TOL = 1e-9

TRAIN_DAYS = ["20260721", "20260722"]
VAL_DAYS = ["20260723"]
HOLD_DAYS = ["20260724"]

# Directional barriers (no cost / no spread deduction)
D_BARRIERS = {
    "D1": {"up_bps": 10.0, "down_bps": 10.0, "horizon_sec": 30.0},
    "D2": {"up_bps": 20.0, "down_bps": 10.0, "horizon_sec": 60.0},
    "D3": {"up_bps": 20.0, "down_bps": 20.0, "horizon_sec": 60.0},
    "D4": {"up_bps": 30.0, "down_bps": 15.0, "horizon_sec": 180.0},
    "D5": {"up_bps": 30.0, "down_bps": 30.0, "horizon_sec": 300.0},
}
PRIMARY_D = ("D2", "D4")  # align with B2/B4 horizons

EXEC_HORIZONS = (30.0, 60.0, 180.0, 300.0)

HYPOTHESES = {
    "H1": ["G2", "G3"],
    "H2": ["G3", "G4"],
    "H3": ["G2", "G3", "G4"],
    "H4": ["G2", "G3", "G4", "G5"],
    "H5": ["G2", "G3", "G4", "G5", "G6"],
    "H6": ["G1", "G2", "G3", "G4", "G5", "G6"],
}

SUBMIT = 0
CANCEL = 0
LIVE_ORDER = 0

REQUIRED_ARTIFACTS = ("report.md", "report.json", "audit.xlsx")
REQUIRED_SHEETS = [
    "summary", "source_reproduction", "quote_integrity", "spread_distribution",
    "mechanical_down", "mechanical_down_examples", "directional_labels", "execution_labels",
    "mid_vs_bid_vs_ask", "spread_cohorts", "feature_groups", "all_candidates",
    "train_selection", "validation_direction", "validation_execution", "am_pm",
    "daily", "symbols", "holdout", "execution_audit", "integrity_audit", "tests",
]
