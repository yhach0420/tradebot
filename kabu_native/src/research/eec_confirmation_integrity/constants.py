"""EEC confirmation causal integrity — frozen EC2/noise from SoT (no retune)."""
from __future__ import annotations

from pathlib import Path

from research.entry_exit_contract.constants import DEFAULT_THRESHOLDS, NATIVE
from research.eec_noise_hysteresis.constants import AM_FORCE_CLOSE_HM, PM_FORCE_CLOSE_HM

SOT_V3 = NATIVE / "results" / "research" / "eec_noise_hysteresis" / "20260725_003545"
SOT_V2 = NATIVE / "results" / "research" / "entry_exit_contract_integrity" / "20260724_235031"
STUDY_VERSION = "EEC_confirmation_causal_integrity"

EC2_THR = dict(DEFAULT_THRESHOLDS["EC2"])
# Frozen train-selected noise from EEC_v3 (do not retune)
FROZEN_NOISE = {"tick_mult": 3.0, "range_mult": 0.2, "spread_mult": 1.0}

HORIZON_SEC = float(EC2_THR["rebound_horizon_sec"])  # 180
MAX_CONFIRM_SEC = 180.0
DATA_GAP_SEC = 30.0
LONG_GAP_SEC = 120.0
CAP = 5
PATH_MAX_SEC = 20000.0
ASK_QTY_MIN = 100.0

DELAY_BUCKETS = (
    ("0_5", 0.0, 5.0),
    ("6_15", 5.0, 15.0),
    ("16_30", 15.0, 30.0),
    ("31_60", 30.0, 60.0),
    ("61_120", 60.0, 120.0),
    ("121_180", 120.0, 180.0),
    ("181_300", 180.0, 300.0),
    ("301_plus", 300.0, 1e12),
)

__all__ = [
    "SOT_V3",
    "SOT_V2",
    "STUDY_VERSION",
    "EC2_THR",
    "FROZEN_NOISE",
    "HORIZON_SEC",
    "MAX_CONFIRM_SEC",
    "DATA_GAP_SEC",
    "LONG_GAP_SEC",
    "CAP",
    "PATH_MAX_SEC",
    "ASK_QTY_MIN",
    "DELAY_BUCKETS",
    "AM_FORCE_CLOSE_HM",
    "PM_FORCE_CLOSE_HM",
    "NATIVE",
]
