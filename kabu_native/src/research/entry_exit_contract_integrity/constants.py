"""EEC integrity constants — thresholds/ENTRY/EXIT frozen from EEC_v1."""
from __future__ import annotations

from pathlib import Path

from research.entry_exit_contract.constants import DEFAULT_THRESHOLDS, NATIVE, X6_PARAMS

SOT_EEC = NATIVE / "results" / "research" / "entry_exit_contract" / "20260724_231428"
INTEGRITY_VERSION = "EEC_v2_integrity"
ROUNDTRIP_COST_PCT = 0.05
SHARES = 100
CAP = 5
MIN_OOS_DAYS_FOR_EDGE = 10

# Episode merge tolerances (evaluation-only; does not change ENTRY rules)
EC1_LEVEL_TOL_PCT = 0.20  # same breakout wave if level within 0.20%
EC2_LEVEL_TOL_PCT = 0.25
EC3_LEVEL_TOL_PCT = 0.20
EPISODE_GAP_END_SEC = 300.0
SAME_WAVE_MAX_GAP_SEC = 600.0
