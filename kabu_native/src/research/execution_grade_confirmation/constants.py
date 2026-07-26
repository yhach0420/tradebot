"""Execution-grade quote reconstruction — frozen confirmation/noise/EC2."""
from __future__ import annotations

from pathlib import Path

from research.entry_exit_contract.constants import NATIVE
from research.eec_confirmation_integrity.constants import FROZEN_NOISE, HORIZON_SEC
from research.eec_noise_hysteresis.constants import AM_FORCE_CLOSE_HM, PM_FORCE_CLOSE_HM, EC2_THR

SOT_CAUSAL = NATIVE / "results" / "research" / "eec_confirmation_integrity" / "20260725_052459"
SOT_V3 = NATIVE / "results" / "research" / "eec_noise_hysteresis" / "20260725_003545"
SOT_V2 = NATIVE / "results" / "research" / "entry_exit_contract_integrity" / "20260724_235031"
CAPTURE_ROOT = NATIVE / "data" / "market_capture"
STUDY_VERSION = "execution_grade_confirmation"

CAP = 5
SHARES = 100
QUOTE_FRESHNESS_MS = 5000.0
LATENCY_MS = (0, 100, 250, 500, 1000)
ENTRY_LABELS = ("E0", "E1", "E2", "E3", "E4", "E5")
EXIT_LABELS = ("X0", "X1", "X2", "X3", "X4", "X5")

# Coverage gates for historical reconstruction
MIN_ASK_COVERAGE = 0.80
MIN_BID_COVERAGE = 0.80
MAX_CROSSED_RATE = 0.05

__all__ = [
    "NATIVE",
    "SOT_CAUSAL",
    "SOT_V3",
    "SOT_V2",
    "CAPTURE_ROOT",
    "STUDY_VERSION",
    "FROZEN_NOISE",
    "HORIZON_SEC",
    "EC2_THR",
    "AM_FORCE_CLOSE_HM",
    "PM_FORCE_CLOSE_HM",
    "CAP",
    "SHARES",
    "QUOTE_FRESHNESS_MS",
    "LATENCY_MS",
    "ENTRY_LABELS",
    "EXIT_LABELS",
    "MIN_ASK_COVERAGE",
    "MIN_BID_COVERAGE",
    "MAX_CROSSED_RATE",
]
