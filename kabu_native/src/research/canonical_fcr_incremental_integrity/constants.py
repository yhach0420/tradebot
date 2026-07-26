"""Canonical FCR incremental integrity — frozen SoT, no retune."""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CAPTURE_ROOT = REPO_ROOT / "data" / "market_capture"
OUT_ROOT = REPO_ROOT / "results" / "research" / "canonical_fcr_incremental_integrity"
OLD_RUN = REPO_ROOT / "results" / "research" / "canonical_fcr_exact_method" / "20260725_120247"

# Frozen FCR thresholds from SoT 20260725_120247 — DO NOT RETUNE
FROZEN = {
    "slope_min": 0.0,
    "pb_lo": 0.10,
    "pb_hi": 0.30,
    "new_low_stop_sec": 10.0,
    "buy_ratio": 0.55,
    "freq_accel": 1.5,
    "reclaim_hold_events": 2,
    "expiry_exh_to_buy": 10.0,
    "expiry_buy_to_reclaim": 20.0,
    "spread_max_bps": None,  # absolute cap off in SoT; not inventing spread_not_widening
}

WARMUP_DAY = "20260721"
TRAIN_DAY = "20260722"
OLD_STRIDE = 6
EVAL_STRIDE = 1
LOT = 100
COST_BPS = 5.0
SEED = 42
SUBMIT = 0
CANCEL = 0
LIVE_ORDER = 0

REQUIRED_ARTIFACTS = ("report.md", "report.json", "audit.xlsx")
REQUIRED_SHEETS = [
    "README", "SOURCE_AUDIT", "OLD_RUN_BASELINE", "STRIDE_AUDIT",
    "EVENT_COUNT_RECONCILIATION", "EVENT_SEQUENCE_GAPS", "EPISODE_LINEAGE",
    "RECLAIM_CANDIDATES", "COMMON_ANCHORS", "PARENT_LINEAGE", "ARM_MEMBERSHIP",
    "ARM_NESTING", "STATE_STAGE_NESTING", "F5_SPEC_AUDIT", "SPREAD_GATE_AUDIT",
    "MATCHED_F0", "MATCHED_F1", "MATCHED_F2", "MATCHED_F3", "MATCHED_F4", "MATCHED_F5",
    "MATCHED_INCREMENTAL", "NATIVE_TIMING_DIAGNOSTIC", "TRAIN_RESULTS", "EXECUTION",
    "SYMBOL_DEPENDENCY", "OLD_VS_FIXED", "TESTS", "VERDICT",
]

ARMS = ("F0_RECLAIM_BASE", "F1_TREND", "F2_PULLBACK", "F3_EXHAUSTION", "F4_BUY_FLOW", "F5_FULL_FCR")
PARENT = {
    "F0_RECLAIM_BASE": None,
    "F1_TREND": "F0_RECLAIM_BASE",
    "F2_PULLBACK": "F1_TREND",
    "F3_EXHAUSTION": "F2_PULLBACK",
    "F4_BUY_FLOW": "F3_EXHAUSTION",
    "F5_FULL_FCR": "F4_BUY_FLOW",
}
