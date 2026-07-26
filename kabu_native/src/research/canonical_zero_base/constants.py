from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CAPTURE_ROOT = REPO_ROOT / "data" / "market_capture"
OUT_ROOT = REPO_ROOT / "results" / "research" / "canonical_zero_base_strategy"

SOT_REPAIR = REPO_ROOT / "results" / "research" / "canonical_quote_mainline_repair" / "20260725_080510"
SOT_AUDIT = REPO_ROOT / "results" / "research" / "global_quote_semantic_audit" / "20260725_065034"
SOT_EGC = REPO_ROOT / "results" / "research" / "execution_grade_confirmation" / "20260725_061724"

CAP = 5
LOT = 100
COST_BPS = 5.0
HARD_STOP_PCT = 1.0
SAMPLE_STRIDE = 12
SEED = 42
MAX_PARALLEL = 4

RAW_COMBINATION_CAP = 2000
TRAIN_PASS_CAP = 100
VAL_PASS_CAP = 10
OOS_CARRY_CAP = 3

LEGACY_P0_PF = 0.9127
LEGACY_P3_PF = 0.0887

SUBMIT = 0
CANCEL = 0
LIVE_ORDER = 0

REQUIRED_ARTIFACTS = ("report.md", "report.json", "audit.xlsx")
REQUIRED_SHEETS = [
    "README",
    "SOURCE_AUDIT",
    "DATA_DISCOVERY",
    "DATA_SPLIT",
    "CANONICAL_COVERAGE",
    "FEATURE_DICTIONARY",
    "FEATURE_QUALITY",
    "EPISODES",
    "OPPORTUNITY_LABELS",
    "Z1_CONTRACT",
    "Z2_CONTRACT",
    "Z3_CONTRACT",
    "Z4_CONTRACT",
    "COMBINATION_COUNTS",
    "TRAIN_RESULTS",
    "VALIDATION_RESULTS",
    "STRICT_OOS_RESULTS",
    "ENTRY_RESULTS",
    "EXIT_RESULTS",
    "ENTRY_EXIT_PAIR",
    "EXECUTION_SCENARIOS",
    "ONE_EPISODE_ONE_ENTRY",
    "CAP5_Z1",
    "CAP5_Z2",
    "CAP5_Z3",
    "CAP5_Z4",
    "CAP5_INTEGRATED",
    "DAILY_RESULTS",
    "SYMBOL_RESULTS",
    "DEPENDENCY",
    "LEAVE_ONE_OUT",
    "OVERFIT_GATES",
    "LEGACY_REFERENCE",
    "CANDIDATE_SELECTION",
    "TESTS",
    "VERDICT",
]

# Coarse TRAIN quantile levels for threshold search (not fine grid)
QUANTILE_LEVELS = (0.20, 0.35, 0.50, 0.65, 0.80)
LARGE_RISE_LEVELS = (0.5, 1.0, 1.5, 2.0)  # pct; frozen after TRAIN definition
