"""C3 ENTRY objective redesign. Executable-at-t0 universe + Top-of-rank OOF. Offline only."""
from __future__ import annotations

ANALYSIS_ID = "ENTRY_OBJECTIVE_REDESIGN_C3"
C14_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14"
MAX_WORKERS = 2
WAIT_SEC = 1.0
HORIZON_SEC = 600.0
POSITION_CAP = 5
NEW_FORWARD_N = 0
TRUE_OOS = False
PRIMARY_TARGET = "EXECUTABLE_FORWARD_MID_RETURN_600S"
MIN_COHORT_N = 10

C2_STATUS_MAINTAINED = "C2_OOF_RANKING_EDGE_NOT_STABLE"
B2_FORMAL = {
    "B2_ROBUST_IMPROVEMENT": False,
    "RECOMMENDED_B_VARIANT": "B0",
    "VERDICT": "B_NO_ROBUST_IMPROVEMENT",
}
REENTRY_V2_FORMAL = "REENTRY_ORDINAL_IS_CLOCK_PROXY"

EXPECTED_A0_TRADES = 192
EXPECTED_A0_PNL = 245660.0
EXPECTED_A0_PF = 1.143435
EXPECTED_A0_MAXDD = -733300.0

EXPECTED_A2_TRADES = 233
EXPECTED_A2_PNL = 219610.0
EXPECTED_A2_PF = 1.113720
EXPECTED_A2_MAXDD = -778500.0

A0_TRADE_TOL = 0
A0_PNL_TOL = 1.0
A0_PF_TOL = 1e-4
A0_DD_TOL = 1.0
A2_TRADE_TOL = 0
A2_PNL_TOL = 1.0
A2_PF_TOL = 1e-4
A2_DD_TOL = 1.0

MIN_TRADE_RETENTION_VS_A2 = 0.70

F0_CURRENT6 = (
    "spread_bps",
    "imbalance",
    "mid_ret_60s",
    "mid_ret_180s",
    "event_rate_60s",
    "log_bid_qty",
)
F1_C2_6 = (
    "drawdown_180s",
    "mid_range_180s_bps",
    "vwap_dist_bps",
    "mid_abs_ret_60s",
    "mid_ret_180s",
    "xs_imbalance_z",
)
F2_UNION = tuple(dict.fromkeys((*F0_CURRENT6, *F1_C2_6)))

FEATURE_SETS = {
    "F0_CURRENT6": F0_CURRENT6,
    "F1_C2_6": F1_C2_6,
    "F2_UNION": F2_UNION,
}
NORMS = ("none", "cross_sectional_z", "robust_cross_sectional_rank")
RIDGE_ALPHAS = (0.1, 1.0, 10.0)

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

RUNTIME_CHANGED = False
C14_CHANGED = False
PAPER_OPERATED = False
OPVAL_OPERATED = False
