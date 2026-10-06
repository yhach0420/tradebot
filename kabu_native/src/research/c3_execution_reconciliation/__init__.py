"""C3 execution coupling reconciliation. Diagnosis only. No new model."""
from __future__ import annotations

ANALYSIS_ID = "C3_EXECUTION_COUPLING_RECONCILIATION"
C14_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14"
MAX_WORKERS = 2
WAIT_SEC = 1.0
POSITION_CAP = 5
NEW_FORWARD_N = 0
TRUE_OOS = False
EXPECTED_EXECUTABLE_T0 = 11390

C3_VERDICT_MAINTAINED = "C3_TOP_EDGE_SUPPORTED_PORTFOLIO_FAIL"
C3_AUDIT_VERDICT_MAINTAINED = "C3_MULTIFACTOR_EXACT_FAILURE"
C2_STATUS_MAINTAINED = "C2_OOF_RANKING_EDGE_NOT_STABLE"
B2_FORMAL = {
    "B2_ROBUST_IMPROVEMENT": False,
    "RECOMMENDED_B_VARIANT": "B0",
    "VERDICT": "B_NO_ROBUST_IMPROVEMENT",
}
REENTRY_V2_FORMAL = "REENTRY_ORDINAL_IS_CLOCK_PROXY"

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

F1_RAW = (
    "drawdown_180s",
    "mid_range_180s_bps",
    "vwap_dist_bps",
    "mid_abs_ret_60s",
    "mid_ret_180s",
)
F1_LOOKBACK = (
    "drawdown_180s",
    "mid_range_180s_bps",
    "mid_abs_ret_60s",
    "mid_ret_180s",
)
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
C4_STARTED = False
C3_IMPLEMENTED = False
NEW_MODEL_CREATED = False
EXECUTION_AWARE_MODEL_CREATED = False
