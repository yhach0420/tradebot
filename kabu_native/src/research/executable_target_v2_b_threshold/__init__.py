"""EXECUTABLE TARGET CONTRACT V2 + UNIFORM10 current-ENTRY score threshold. Offline only."""
from __future__ import annotations

ANALYSIS_ID = "EXECUTABLE_TARGET_V2_B_THRESHOLD"
TASK_LABEL = "OFFLINE_RESEARCH_ONLY"
WAIT_SEC = 1.0
NEW_FORWARD_N = 0
MAX_WORKERS = 2
PRIMARY_TARGET = "EXECUTABLE_FORWARD_MID_RETURN_600S"
C14_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14"
HORIZON_SEC = 600.0
STRATEGY_RETUNED = False

# Precommitted before looking at results. Not expanded mid-run.
# None = NO_THRESHOLD (B0). Integers are within-anchor cross-sectional percentiles.
# higher score = preferred (predict_proba class 1). p80 is stricter than p20.
B1_PERCENTILES: tuple = (None, 20, 30, 40, 50, 60, 70, 80)
B1_GATE = "within_anchor_cross_sectional_score_percentile"
HIGHER_SCORE_PREFERRED = True
AM_PM_SPLIT_THRESHOLD = False
SYMBOL_SPLIT_THRESHOLD = False
WEEKDAY_SPLIT_THRESHOLD = False
ANCHOR_SPLIT_THRESHOLD = False
MAX_HARD_GATES = 0  # B1 is score-only; per-feature gates not started this run

# B0 known headline from UNIFORM10 + CURRENT ENTRY + CORRECTED FILL (cache).
EXPECTED_B0_TRADES = 278
EXPECTED_B0_PNL = 441350.0
EXPECTED_B0_PF = 1.315622
EXPECTED_B0_MAXDD = -357050.0

# Occupancy vs cache: STOP B1 if exceeded (precommitted).
OCC_TRADE_N_TOL = 5
OCC_PNL_TOL = 50000.0
OCC_JACCARD_MIN = 0.95

# Tail "大幅改善": cross to >=0, or recover at least half the B0 hole.
TAIL_HALF_GAP = 0.50

MIN_COHORT_FOR_PERCENTILE = 3
