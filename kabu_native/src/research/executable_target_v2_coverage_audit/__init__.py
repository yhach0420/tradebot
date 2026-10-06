"""EXECUTABLE TARGET V2 session + coverage integrity audit. Offline only. No C rebuild."""
from __future__ import annotations

ANALYSIS_ID = "EXECUTABLE_TARGET_V2_COVERAGE_AUDIT"
TASK_LABEL = "OFFLINE_AUDIT_ONLY"
C14_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14"
MAX_WORKERS = 2
WAIT_SEC = 1.0
HORIZON_SEC = 600.0  # 10 minutes, not 600 minutes
BOARD_FRESHNESS_SEC = 5.0
CAPTURE_GAP_LAG_SEC = 120.0
MIN_QTY = 100.0

# Precommitted before inspecting diagnostic extract. Not moved after seeing results.
SMD_BIAS_THRESHOLD = 0.25
SCORE_DECILE_COVERAGE_RANGE_MAX = 0.20
SYMBOL_COVERAGE_RANGE_MAX = 0.50
COHORT_MEDIAN_MIN = 25
COHORT_P10_MIN = 10
COHORT_MEDIAN_UNIVERSE_FRAC_MIN = 0.50

# Bias SoT slice: executable_at_t0 rows (non-executable t0 cannot enter PRIMARY by contract).
BIAS_SLICE = "executable_at_t0"

B0_STATUS = "historical_reference_only"
B1_STATUS = "B1_SCORE_THRESHOLD_NO_ROBUST_IMPROVEMENT"
PER_FEATURE_THRESHOLD_RESEARCH = False
C_REBUILD_THIS_RUN = False
