"""IOAR — Integrated Order Flow Absorption Reversal."""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CAPTURE_ROOT = REPO_ROOT / "data" / "market_capture"
OUT_ROOT = REPO_ROOT / "results" / "research" / "integrated_order_flow_absorption_reversal"

STRIDE = 1
LOT = 100
COST_BPS = 5.0
SEED = 42
CAP = 5
MIN_TRAIN_ENTRIES = 100
TARGET_TRAIN_DAYS = 5

# Interpretable fixed defs (not PnL grid search)
BALANCE_LOOKBACK_SEC = 30.0
SELL_PRESSURE_BUY_RATIO_MAX = 0.42  # sell-heavy
SELL_MIN_N = 3
SELL_MIN_V = 50.0
ABSORB_SELL_IMPACT_DECAY = 0.55  # impact after / impact before
ABSORB_MIN_SELL_QTY = 80.0
ABSORB_MIN_REPLENISH = 1
EXHAUST_SELL_FREQ_RATIO = 0.55
BUY_RATIO_MIN = 0.58
ACCEPT_HOLD_EVENTS = 3
NO_DEMAND_SEC = 40.0
NO_DEMAND_MFE_MAX = 0.05  # %
EXHAUST_MFE_MIN = 0.30
EXHAUST_STALL_SEC = 20.0
GIVEBACK_MFE_MIN = 0.35
GIVEBACK_FRAC = 0.45
HARD_SPREAD_BPS = 90.0
HORIZON_SEC = 180.0
DIAG_EXIT_SEC = 120.0
PRE_STAGE_MAX_SEC = 180.0
ZONE_COOLDOWN_SEC = 120.0

SUBMIT = 0
CANCEL = 0
LIVE_ORDER = 0

ARMS = ("A0", "A1", "A2", "A3", "A4", "A5")
REQUIRED_ARTIFACTS = ("report.md", "report.json", "audit.xlsx")
REQUIRED_SHEETS = [
    "summary", "dataset_scope", "feature_distribution", "episodes", "state_transitions",
    "sell_pressure", "absorption", "sell_exhaustion", "buy_reversal", "acceptance",
    "entries", "post_entry_states", "exits", "arms", "incremental", "outcome_classes",
    "success_failure_comparison", "daily", "symbols", "execution_audit", "integrity_audit", "tests",
]
