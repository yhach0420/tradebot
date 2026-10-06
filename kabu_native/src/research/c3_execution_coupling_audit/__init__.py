"""C3 OOF → Exact execution coupling audit. Diagnosis only. No new model."""
from __future__ import annotations

ANALYSIS_ID = "C3_EXECUTION_COUPLING_AUDIT_V2"
C14_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14"
MAX_WORKERS = 2
WAIT_SEC = 1.0
HORIZON_SEC = 600.0
POSITION_CAP = 5
NEW_FORWARD_N = 0
TRUE_OOS = False
OOF_EXACT_KIND = "OOF_EXACT_DIAGNOSTIC_ONLY"

C3_VERDICT_MAINTAINED = "C3_TOP_EDGE_SUPPORTED_PORTFOLIO_FAIL"
C2_STATUS_MAINTAINED = "C2_OOF_RANKING_EDGE_NOT_STABLE"
B2_FORMAL = {
    "B2_ROBUST_IMPROVEMENT": False,
    "RECOMMENDED_B_VARIANT": "B0",
    "VERDICT": "B_NO_ROBUST_IMPROVEMENT",
}
REENTRY_V2_FORMAL = "REENTRY_ORDINAL_IS_CLOCK_PROXY"

EXPECTED_A2_TRADES = 233
EXPECTED_A2_PNL = 219610.0
EXPECTED_A2_PF = 1.113720
EXPECTED_A2_MAXDD = -778500.0

EXPECTED_C3_TRADES = 53
EXPECTED_C3_PNL = -158600.0
EXPECTED_C3_PF = 0.612225
EXPECTED_C3_MAXDD = -326600.0

TRADE_TOL = 0
PNL_TOL = 1.0
PF_TOL = 1e-4
DD_TOL = 1.0

FINAL_SPEC_ID = "F1_C2_6|cross_sectional_z|a10.0"
FINAL_SPEC = {
    "feature_set": "F1_C2_6",
    "features": [
        "drawdown_180s",
        "mid_range_180s_bps",
        "vwap_dist_bps",
        "mid_abs_ret_60s",
        "mid_ret_180s",
        "xs_imbalance_z",
    ],
    "n_features": 6,
    "normalization": "cross_sectional_z",
    "alpha": 10.0,
}

FIT_KEYS = (
    "feature_set",
    "features",
    "normalization",
    "alpha",
    "kind",
    "scaler_mean",
    "scaler_scale",
    "coef",
    "intercept",
    "train_n",
)

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
NEW_MODEL_CREATED = False
NEW_FEATURE_CREATED = False
NEW_THRESHOLD_CREATED = False
CLOCK_SEARCHED = False
REENTRY_SEARCHED = False
PNL_OPTIMIZED = False
C4_STARTED = False
C3_IMPLEMENTED = False
