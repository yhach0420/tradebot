"""Canonical Zero-Base v2 constants — no v1 template/threshold reuse."""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CAPTURE_ROOT = REPO_ROOT / "data" / "market_capture"
OUT_ROOT = REPO_ROOT / "results" / "research" / "canonical_zero_base_v2"

SOT_V1 = REPO_ROOT / "results" / "research" / "canonical_zero_base_strategy" / "20260725_092756"
SOT_ROOT = REPO_ROOT / "results" / "research" / "canonical_strategy_root_cause" / "20260725_083727"
SOT_REPAIR = REPO_ROOT / "results" / "research" / "canonical_quote_mainline_repair" / "20260725_080510"
SOT_AUDIT = REPO_ROOT / "results" / "research" / "global_quote_semantic_audit" / "20260725_065034"
SOT_EGC = REPO_ROOT / "results" / "research" / "execution_grade_confirmation" / "20260725_061724"

CAP = 5
LOT = 100
COST_BPS = 5.0
HARD_STOP_PCT = 1.0
SEED = 42
MAX_PARALLEL = 4
SAMPLE_STRIDE = 12

# Interaction / candidate caps (not fixed-50 collapse)
INTER_2_CAP = 2000
INTER_3_CAP = 2000
INTER_4_CAP = 1000
ENTRY_CAND_CAP_PER_STRAT = 200
JOINT_ENTRY_CAP = 50
JOINT_EXIT_CAP = 30
JOINT_PAIR_CAP = 1500

SUBMIT = 0
CANCEL = 0
LIVE_ORDER = 0

HORIZONS_SEC = (5, 10, 15, 30, 60, 120, 180, 300)
PRICE_WINDOWS_SEC = (1, 2, 3, 5, 10, 15, 20, 30, 45, 60, 120, 180, 300)

REQUIRED_ARTIFACTS = ("report.md", "report.json", "audit.xlsx")
REQUIRED_SHEETS = [
    "README",
    "SOURCE_AUDIT",
    "DATA_DISCOVERY",
    "DATA_SPLIT",
    "CANONICAL_COVERAGE",
    "ANCHOR_INVENTORY",
    "ANCHOR_SAMPLES",
    "OUTCOME_LABEL_SPEC",
    "OUTCOME_COUNTS",
    "ENTRY_FEATURE_INVENTORY",
    "ENTRY_FEATURE_FORMULAS",
    "ENTRY_FEATURE_QUALITY",
    "ENTRY_FEATURE_SEPARATION",
    "ENTRY_FEATURE_STABILITY",
    "ENTRY_INTERACTIONS",
    "ENTRY_REJECTED_FEATURES",
    "Z1_EPISODES",
    "Z2_EPISODES",
    "Z3_EPISODES",
    "Z4_EPISODES",
    "EPISODE_QUALITY",
    "Z1_ENTRY_CANDIDATES",
    "Z2_ENTRY_CANDIDATES",
    "Z3_ENTRY_CANDIDATES",
    "Z4_ENTRY_CANDIDATES",
    "TRAIN_ENTRY_GATE",
    "VALIDATION_ENTRY_GATE",
    "POST_ENTRY_PATHS",
    "EXIT_FEATURE_INVENTORY",
    "EXIT_FEATURE_FORMULAS",
    "EXIT_FEATURE_SEPARATION",
    "EXIT_FEATURE_LEADTIME",
    "EXIT_FEATURE_STABILITY",
    "FALSE_WARNING",
    "TRUE_INVALIDATION",
    "WINNER_RETENTION",
    "Z1_EXIT_CANDIDATES",
    "Z2_EXIT_CANDIDATES",
    "Z3_EXIT_CANDIDATES",
    "Z4_EXIT_CANDIDATES",
    "ENTRY_EXIT_PAIRS",
    "TRAIN_PAIR_GATE",
    "VALIDATION_PAIR_GATE",
    "STRICT_OOS",
    "EXECUTION_E0_E5",
    "EXECUTION_S0_S5",
    "LATENCY_SENSITIVITY",
    "ONE_TICK_ADVERSE",
    "CAP5_Z1",
    "CAP5_Z2",
    "CAP5_Z3",
    "CAP5_Z4",
    "CAP5_INTEGRATED",
    "CAP_BLOCKED",
    "SLOT_RECYCLING",
    "DAILY_RESULTS",
    "SYMBOL_RESULTS",
    "DEPENDENCY",
    "LEAVE_ONE_OUT",
    "OVERFIT_GATES",
    "TESTS",
    "VERDICT",
]
