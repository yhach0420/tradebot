"""UNIFORM10 ENTRY rebuild under corrected Passive Fill. Research-only. No Runtime activation."""
from __future__ import annotations

ANALYSIS_ID = "UNIFORM10_ENTRY_REBUILD"
TASK_LABEL = "OFFLINE_RESEARCH_ONLY"
STRATEGY_RETUNED = False
NEW_FORWARD_N = 0
MAX_WORKERS = 2
PRIMARY_TARGET = "FORWARD_MID_RETURN_600S"
WAIT_SEC = 1.0
POSITION_CAP = 5

# User-specified 31 anchors. Not searched. Not Runtime CLOCK_GRID.
UNIFORM10_AM = (
    (9, 5), (9, 15), (9, 25), (9, 35), (9, 45), (9, 55),
    (10, 5), (10, 15), (10, 25), (10, 35), (10, 45), (10, 55),
    (11, 5), (11, 15),
)
UNIFORM10_PM = (
    (12, 40), (12, 50),
    (13, 0), (13, 10), (13, 20), (13, 30), (13, 40), (13, 50),
    (14, 0), (14, 10), (14, 20), (14, 30), (14, 40), (14, 50),
    (15, 0), (15, 10), (15, 20),
)
UNIFORM10 = UNIFORM10_AM + UNIFORM10_PM

SEARCH_SPACE = {
    "normalization": ["cross_sectional_z", "none"],
    "weight": ["equal", "abs_corr"],
    "topk": [3, 5],
    "threshold": [None, "p60"],
    "max_features": [6, 8],
    "direction": "sign(global_spearman_vs_FORWARD_MID_RETURN_600S)",
    "forbidden": [
        "symbol-specific",
        "day-specific",
        "weekday-specific",
        "individual-anchor-specific",
    ],
}

MIN_COVERAGE = 0.60
MIN_ABS_CORR = 0.02
