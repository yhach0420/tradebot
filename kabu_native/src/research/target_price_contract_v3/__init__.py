"""TARGET PRICE CONTRACT V3. Offline mark/coverage audit. No C rebuild. No Runtime write."""
from __future__ import annotations

ANALYSIS_ID = "TARGET_PRICE_CONTRACT_V3"
C14_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14"
MAX_WORKERS = 2
WAIT_SEC = 1.0
HORIZON_SEC = 600.0
PM_CONTINUOUS_END = "15:25"
PM_MARKET_CLOSE = "15:30"
PM_CLOSING_AUCTION = "15:25-15:30"

# Precommitted. Not expanded after seeing results. Not model search.
AGE_TOLERANCES_SEC: tuple[float, ...] = (5.0, 10.0, 15.0, 30.0, 60.0)
MARK_CANDIDATES: tuple[str, ...] = ("M1", "M2", "M3")  # M0 is reference only
TIE_BREAK_MARK_ORDER: tuple[str, ...] = ("M1", "M2", "M3")

COHORT_MEDIAN_MIN = 25
COHORT_P10_MIN = 10
COHORT_MEDIAN_UNIVERSE_FRAC_MIN = 0.50
SMD_BIAS_THRESHOLD = 0.25
SCORE_DECILE_COVERAGE_RANGE_GOAL = 0.20
SCORE_DECILE_COVERAGE_RANGE_HARD = 0.40
SYMBOL_COVERAGE_RANGE_GOAL = 0.50
EVENT_RATE_SMD_HARD = 1.0

B0_STATUS = "HISTORICAL_REFERENCE_ONLY"
B1_STATUS = "B1_SCORE_THRESHOLD_NO_ROBUST_IMPROVEMENT"
PER_FEATURE_THRESHOLD = "DO_NOT_START"
C_REBUILD_THIS_RUN = False

AGE_BUCKETS: tuple[tuple[float, float, str], ...] = (
    (0.0, 1.0, "0-1s"),
    (1.0, 5.0, "1-5s"),
    (5.0, 10.0, "5-10s"),
    (10.0, 15.0, "10-15s"),
    (15.0, 30.0, "15-30s"),
    (30.0, 60.0, "30-60s"),
    (60.0, 120.0, "60-120s"),
    (120.0, float("inf"), ">120s"),
)

QTY_SITES: tuple[dict[str, str], ...] = (
    {
        "file": "src/research/executable_target_v2_b_threshold/contract.py",
        "function": "last_executable_mid",
        "constraint": "bid_qty>=100 and ask_qty>=100",
        "class": "EXECUTION_CAPACITY_COPIED_INTO_V2_PRICE_MARK",
        "v3_action": "SEPARATE. Not used for M1/M2/M3 price marks.",
    },
    {
        "file": "src/research/e1_x28_executable_joint/__init__.py",
        "function": "MIN_QTY",
        "constraint": "100",
        "class": "EXECUTION_CAPACITY",
        "v3_action": "Leave. Fill/exit SoT. Not a V3 mark gate.",
    },
    {
        "file": "src/research/e1_x34a_execution_policy/arms.py",
        "function": "find_ask_cross_fill",
        "constraint": "qty>=MIN_QTY plus WAIT_SEC Ask-cross",
        "class": "EXECUTION_CONTRACT",
        "v3_action": "Runtime Fill unchanged.",
    },
    {
        "file": "src/research/v1r_exit_v2_asymmetric/states.py",
        "function": "exit quote checks",
        "constraint": "bid_qty/ask_qty>=MIN_QTY",
        "class": "EXECUTION_CONTRACT",
        "v3_action": "Runtime EXIT unchanged.",
    },
    {
        "file": "src/research/entry_exit_contract/execution.py",
        "function": "sellable",
        "constraint": "bid_qty>=100",
        "class": "EXECUTION_CAPACITY",
        "v3_action": "Not a V3 mark gate.",
    },
)
