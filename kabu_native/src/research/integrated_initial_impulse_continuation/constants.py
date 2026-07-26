"""IIC — Integrated Initial Impulse Continuation (scenario-integrated strategy)."""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CAPTURE_ROOT = REPO_ROOT / "data" / "market_capture"
OUT_ROOT = REPO_ROOT / "results" / "research" / "integrated_initial_impulse_continuation"

WARMUP = "20260721"
TRAIN = "20260722"
VALIDATION = "20260723"
HOLDOUT = "20260724"

STRIDE = 1  # mandatory — no event sampling
LOT = 100
COST_BPS = 5.0
SEED = 42
CAP = 5

# Interpretable fixed definitions (not PnL-tuned grids)
QUIET_LOOKBACK_SEC = 45.0
QUIET_RANGE_BPS_MAX = 18.0  # quiet base: recent range < 18bps
QUIET_RET_ABS_MAX = 0.004  # not after spike/crash in lookback
FLOW_BUY_RATIO_MIN = 0.58
FLOW_VOL_MULT = 1.35
BREAK_HOLD_EVENTS = 3
NO_FOLLOW_SEC = 45.0
NO_FOLLOW_MFE_MAX = 0.12  # % — no cost recovery
EXHAUST_STALL_SEC = 25.0
EXHAUST_MFE_MIN = 0.35
GIVEBACK_FRAC = 0.45
GIVEBACK_MFE_MIN = 0.40
HARD_SPREAD_BPS = 80.0
HORIZON_SEC = 180.0
DIAG_EXIT_SEC = 120.0  # A0 fixed diagnostic only
MIN_TRAIN_EPISODES = 100

SUBMIT = 0
CANCEL = 0
LIVE_ORDER = 0

ARMS = ("A0", "A1", "A2", "A3", "A4", "A5")
REQUIRED_ARTIFACTS = ("report.md", "report.json", "audit.xlsx")
REQUIRED_SHEETS = [
    "summary", "episodes", "state_transitions", "entries", "exits",
    "arms", "incremental", "daily", "symbols", "execution_audit",
    "integrity_audit", "tests",
]
