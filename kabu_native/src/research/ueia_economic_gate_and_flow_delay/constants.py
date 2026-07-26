"""UEIA Economic Gate Repair + Flow Delay Root Cause Audit."""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
OUT_ROOT = REPO_ROOT / "results" / "research" / "ueia_economic_gate_and_flow_delay"
SOURCE_RUN = REPO_ROOT / "results" / "research" / "upward_edge_identification_audit" / "20260725_202310"
CACHE_DIR = OUT_ROOT / "_cache"

STRIDE = 1
LOT = 100
COST_BPS = 5.0
SEED = 42
REPRO_ABS_TOL = 1e-9
MIN_SELECTED = 20

TRAIN_DAYS = ["20260721", "20260722"]
VAL_DAYS = ["20260723"]
HOLD_DAYS = ["20260724"]

HYPOTHESES = {
    "H1": ["G2", "G3"],
    "H2": ["G3", "G4"],
    "H3": ["G2", "G3", "G4"],
    "H4": ["G2", "G3", "G4", "G5"],
    "H5": ["G2", "G3", "G4", "G5", "G6"],
    "H6": ["G1", "G2", "G3", "G4", "G5", "G6"],
}
CANDIDATE_KEYS = [f"{b}_{h}" for b in ("B2", "B4") for h in HYPOTHESES]

DELAYS_SEC = (0.0, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0)

SUBMIT = 0
CANCEL = 0
LIVE_ORDER = 0

REQUIRED_ARTIFACTS = ("report.md", "report.json", "audit.xlsx")
REQUIRED_SHEETS = [
    "summary", "source_run_reproduction", "candidate_selection_logic", "all_12_candidates",
    "split_local_vs_fixed", "fixed_threshold", "cost_formula_audit", "manual_path_checks",
    "train_candidate_selection", "validation", "holdout", "flow_timestamps", "delay_comparison",
    "edge_consumption", "daily", "symbols", "duplicate_overlap", "execution_audit",
    "integrity_audit", "tests",
]
