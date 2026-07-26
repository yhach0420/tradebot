"""EEC_v3 constants — frozen EC2 thresholds from EEC_v1; coarse noise grid only."""
from __future__ import annotations

from pathlib import Path

from research.entry_exit_contract.constants import DEFAULT_THRESHOLDS, NATIVE

SOT_EEC_INT = NATIVE / "results" / "research" / "entry_exit_contract_integrity" / "20260724_235031"
STUDY_VERSION = "EEC_v3_noise_hysteresis"
HARD_STOP_PCT = 1.20
ROUNDTRIP_COST_PCT = 0.05
SHARES = 100
CAP = 5
PATH_MAX_SEC = 20000.0

EC2_THR = dict(DEFAULT_THRESHOLDS["EC2"])  # frozen

TICK_MULTIPLIERS = (1.0, 2.0, 3.0)
RANGE_MULTIPLIERS = (0.20, 0.35, 0.50)
SPREAD_MULTIPLIERS = (1.0, 1.5)

# Default diagnostic band (mid of grid) used when train pick unavailable
DEFAULT_NOISE = {"tick_mult": 2.0, "range_mult": 0.35, "spread_mult": 1.0}

AM_FORCE_CLOSE_HM = (11, 25)
PM_FORCE_CLOSE_HM = (15, 23)
