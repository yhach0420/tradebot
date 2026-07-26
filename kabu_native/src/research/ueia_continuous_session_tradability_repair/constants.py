"""UEIA Continuous-Session Tradability Repair."""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
OUT_ROOT = REPO_ROOT / "results" / "research" / "ueia_continuous_session_tradability_repair"
SOURCE_UEIA = REPO_ROOT / "results" / "research" / "upward_edge_identification_audit" / "20260725_202310"
SOURCE_REPAIR = REPO_ROOT / "results" / "research" / "ueia_economic_gate_and_flow_delay" / "20260725_213512"
SAMPLE_CACHE = (
    REPO_ROOT / "results" / "research" / "ueia_economic_gate_and_flow_delay" / "_cache" / "samples_20260725_202310.pkl"
)
CACHE_DIR = OUT_ROOT / "_cache"

# Session source: small_paper.market_capture_sidecar.is_market_session_jst (TSE cash)
SESSION_SOURCE = "src/small_paper/market_capture_sidecar.py::is_market_session_jst"

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

SUBMIT = 0
CANCEL = 0
LIVE_ORDER = 0

REQUIRED_ARTIFACTS = ("report.md", "report.json", "audit.xlsx")
REQUIRED_SHEETS = [
    "summary", "source_reproduction", "session_calendar", "session_population",
    "preopen_audit", "lunch_audit", "session_boundary_audit", "tradability_audit",
    "feature_lifecycle", "session_feature_drift", "session_only_model",
    "samples_original", "samples_continuous", "barrier_labels", "all_12_candidates",
    "b4_h2", "b4_h3", "b4_h6", "train_selection", "validation", "holdout",
    "warmup_sensitivity", "am_pm_comparison", "delay_train", "delay_validation",
    "daily", "symbols", "execution_audit", "integrity_audit", "tests",
]
