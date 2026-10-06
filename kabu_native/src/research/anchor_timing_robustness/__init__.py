"""Anchor timing robustness: exact clock vs cross-sectional selection.

Offline research only. Does not change Runtime / CLOCK_GRID / ENTRY / EXIT / Paper / OPVAL.
Does not search for a new grid. Does not adopt the best-PnL shift.
"""
from __future__ import annotations

ANALYSIS_ID = "ANCHOR_TIMING_ROBUSTNESS"
DOCUMENT_ID = "CLOCK_VS_CROSS_SECTION"
TASK_LABEL = "OFFLINE_CAUSAL_ROBUSTNESS_DIAGNOSTIC"
CONTAMINATION_LABEL = "NOT_A_GRID_SEARCH"

SHIFTS_MIN = (-10, -5, 0, 5, 10)
SHIFT_KEYS = {
    -10: "M10",
    -5: "M5",
    0: "REFERENCE",
    5: "P5",
    10: "P10",
}

# Existing Dual Lane / JPX continuous session. Not a new cutoff.
AM_START, AM_END = (9, 0), (11, 30)
PM_START, PM_END = (12, 30), (15, 0)

# Time-of-day buckets. 09:05±10 spans open; 10:20±5 does not. Do not pool them.
TOD_BUCKETS = {
    "OPEN_EARLY": ("09:05", "09:15"),
    "AM_MID": ("09:25", "09:40", "10:00", "10:20"),
    "AM_LATE": ("10:40", "11:00"),
    "PM_OPEN": ("12:40",),
    "PM_MID": ("13:00", "13:20", "13:40", "14:00", "14:20"),
    "PM_LATE": ("14:40", "15:00"),
}
NORMAL_SESSION_BUCKETS = ("AM_MID", "PM_MID")

# Precommitted interpretation thresholds — written before any result is seen.
# Headline metrics are isolated selection + isolated economics, never "best PnL shift".
SPEARMAN_ROBUST = 0.80
SPEARMAN_SENSITIVE = 0.50
TOP5_ROBUST = 0.60
TOP5_SENSITIVE = 0.30
ENTRY_OVERLAP_STABLE = 0.50
ENTRY_OVERLAP_UNSTABLE = 0.30
TRADE_COUNT_RATIO_LO = 0.60
TRADE_COUNT_RATIO_HI = 1.40
MIN_SPEARMAN_N = 5

VERDICT_ROBUST = "ANCHOR_TIME_ROBUST_CROSS_SECTIONAL"
VERDICT_SENSITIVE = "ANCHOR_TIME_SENSITIVE"
VERDICT_MIXED = "MIXED_TIME_OF_DAY_STRUCTURE"

X32_FORBIDDEN_FROM = "20260810"
X32_HISTORICAL_END = "20260807"

MAX_WORKERS = 2
LOT_QTY = 100
