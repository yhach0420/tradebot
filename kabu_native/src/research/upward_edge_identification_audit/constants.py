"""UEIA — Upward Edge Identification Audit."""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CAPTURE_ROOT = REPO_ROOT / "data" / "market_capture"
OUT_ROOT = REPO_ROOT / "results" / "research" / "upward_edge_identification_audit"

STRIDE = 1
LOT = 100
COST_BPS = 5.0
SEED = 42
TARGET_TRAIN_DAYS = 5

# Sample thinning (features update every event; output only)
REGULAR_SAMPLE_SEC = 5.0
STATE_SAMPLE_MIN_GAP_SEC = 0.5
MAX_REGULAR_PER_STREAM = 250
MAX_STATE_PER_STREAM = 250
WARMUP_SEC = 60.0

BARRIERS = {
    "B1": {"up_bps": 10.0, "down_bps": 10.0, "horizon_sec": 30.0},
    "B2": {"up_bps": 20.0, "down_bps": 10.0, "horizon_sec": 60.0},
    "B3": {"up_bps": 20.0, "down_bps": 20.0, "horizon_sec": 60.0},
    "B4": {"up_bps": 30.0, "down_bps": 15.0, "horizon_sec": 180.0},
    "B5": {"up_bps": 30.0, "down_bps": 30.0, "horizon_sec": 300.0},
    "B6": {"up_bps": 50.0, "down_bps": 20.0, "horizon_sec": 300.0},
}
PRIMARY_BARRIERS = ("B2", "B4")
MAX_HORIZON_SEC = 300.0

SUBMIT = 0
CANCEL = 0
LIVE_ORDER = 0

FEATURE_GROUPS = {
    "G1": "PRICE_STATE",
    "G2": "AGGRESSIVE_FLOW",
    "G3": "FLOW_EFFICIENCY",
    "G4": "PERSISTENCE",
    "G5": "MARKET_CONTEXT",
    "G6": "REMAINING_UPSIDE",
}

MODELS = {
    "M0": [],
    "M1": ["G1"],
    "M2": ["G2"],
    "M3": ["G3"],
    "M4": ["G4"],
    "M5": ["G5"],
    "M6": ["G6"],
    "M7": ["G1", "G2"],
    "M8": ["G2", "G3"],
    "M9": ["G2", "G3", "G4"],
    "M10": ["G2", "G3", "G4", "G5"],
    "M11": ["G1", "G2", "G3", "G4", "G5", "G6"],
}

HYPOTHESES = {
    "H1": ["G2", "G3"],
    "H2": ["G3", "G4"],
    "H3": ["G2", "G3", "G4"],
    "H4": ["G2", "G3", "G4", "G5"],
    "H5": ["G2", "G3", "G4", "G5", "G6"],
    "H6": ["G1", "G2", "G3", "G4", "G5", "G6"],
}

REQUIRED_ARTIFACTS = ("report.md", "report.json", "audit.xlsx")
REQUIRED_SHEETS = [
    "summary", "dataset_scope", "data_quality", "sample_population", "barrier_definitions",
    "labels", "feature_dictionary", "feature_distribution", "price_state", "aggressive_flow",
    "flow_efficiency", "persistence", "market_context", "remaining_upside", "univariate_bins",
    "hypothesis_comparison", "model_metrics_train", "model_metrics_validation", "model_metrics_holdout",
    "first_passage_summary", "daily_metrics", "symbol_metrics", "winner_vs_down", "high_buy_no_rise",
    "high_replenish_no_rise", "pbv2_comparison", "duplicate_overlap_audit", "execution_audit",
    "integrity_audit", "tests",
]
