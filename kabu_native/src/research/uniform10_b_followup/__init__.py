"""UNIFORM10 B follow-up: executability rank audit + Exact Dual-Lane B1. Offline only."""
from __future__ import annotations

ANALYSIS_ID = "UNIFORM10_B_FOLLOWUP"
C14_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14"
MAX_WORKERS = 2
WAIT_SEC = 1.0
POSITION_CAP = 5

EXPECTED_B0_TRADES = 278
EXPECTED_B0_PNL = 441350.0
EXPECTED_B0_PF = 1.315622
EXPECTED_B0_MAXDD = -357050.0
B0_TRADE_TOL = 0
B0_PNL_TOL = 1.0
B0_PF_TOL = 1e-4
B0_DD_TOL = 1.0

# Precommitted. Not expanded mid-run. None = NO_THRESHOLD.
B1_PERCENTILES: tuple = (None, 20, 30, 40, 50, 60, 70, 80)
MIN_COHORT_FOR_PERCENTILE = 3
MIN_TRADE_RETENTION = 0.50

# Rank-bias flag. Frozen before seeing rates. Not tuned after.
# Material excess: Top3 nonexec rate / population rate >= 1.50,
# or absolute gap >= 0.10.
BIAS_ENRICHMENT_MIN = 1.50
BIAS_ABS_GAP_MIN = 0.10

C_REBUILD_THIS_RUN = False
PER_FEATURE_THRESHOLD = "DO_NOT_START"

# Frozen 18 FULL development days. Do not silently add later captures.
ELIGIBLE_DAYS = (
    "20260722",
    "20260728",
    "20260729",
    "20260730",
    "20260731",
    "20260803",
    "20260804",
    "20260805",
    "20260806",
    "20260807",
    "20260810",
    "20260817",
    "20260819",
    "20260820",
    "20260824",
    "20260825",
    "20260826",
    "20260827",
)
