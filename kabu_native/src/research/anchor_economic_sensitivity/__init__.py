"""Decompose why ±5/10 min moves economics while ranks stay correlated.

Offline diagnostic only. No CLOCK_GRID / ENTRY / EXIT / Runtime / threshold change.
Does not search for a better clock. Does not adopt a shift.
"""
from __future__ import annotations

ANALYSIS_ID = "ANCHOR_ECONOMIC_SENSITIVITY_DECOMPOSITION"
DOCUMENT_ID = "WHY_RANK_ROBUST_ECONOMICS_MOVE"
TASK_LABEL = "OFFLINE_CAUSAL_DECOMPOSITION"
SOURCE_STUDY = "results/research/anchor_timing_robustness"

PRIMARY_SHIFTS = ("M5", "P5")
SECONDARY_SHIFTS = ("M10", "P10")
ALL_SHIFTS = ("M5", "P5", "M10", "P10")

X32_FORBIDDEN_FROM = "20260810"
X32_HISTORICAL_END = "20260807"

DEVELOPMENT_DAYS = (
    "20260722", "20260728", "20260729", "20260730", "20260731",
    "20260803", "20260804", "20260805", "20260806", "20260807",
)
HOLDOUT_DAYS = (
    "20260810", "20260817", "20260819", "20260820",
    "20260824", "20260825", "20260826",
)

# Precommitted interpretation — written before this run's numbers are computed.
# Portfolio M5 sign-flip from the prior study is an input fact, not a grid-search target.
REENTRY_SHRINK_PRIMARY = 0.50
COMPONENT_DOMINANT = 0.55
COMPONENT_MATERIAL = 0.30
RESIDUAL_OK = 0.05
RESIDUAL_FAIL = 0.20
BOUNDARY_HIGH = 0.60
BOUNDARY_MED = 0.35
LOO_STABLE_MIN = 5  # of 7 holdout days
TAIL_DAY_SHARE_HIGH = 0.50
SAME_PRICE_BPS = 1.0

EARLY_GUARD = frozenset({
    "IMBALANCE", "IMB_p5_t-10", "EARLY_GUARD", "GUARD",
})
EXIT600 = frozenset({
    "CONT_EXIT_600", "FIXED600", "FIXED_HOLD",
    "FIRST_VALID_BUY1_AT_OR_AFTER_TARGET", "TIME600",
})
EXTEND750 = frozenset({
    "CONT_EXTEND_750", "TIME750",
})

VERDICT_OK = "ANCHOR_ECONOMIC_SENSITIVITY_DECOMPOSED"
VERDICT_FAIL = "ANCHOR_ECONOMIC_SENSITIVITY_NOT_DECOMPOSED"
