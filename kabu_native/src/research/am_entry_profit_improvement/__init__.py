"""AM ENTRY profit improvement nested program. Offline only. Runtime WAIT unchanged."""
from __future__ import annotations

from research.direct_joint_objective import ELIGIBLE_DAYS, RANDOM_STATE
from research.entry_objective_redesign_c3 import FEATURE_SETS, MIN_COHORT_N, NORMS, RIDGE_ALPHAS
from research.wait5_session_target_learnability import AM_TOPK, FORBIDDEN_FEATURES, RF_REG_PARAMS
from small_paper.v1r_primary_runtime import POSITION_CAP, WAIT_SEC

ANALYSIS_ID = "AM_ENTRY_PROFIT_IMPROVEMENT_V2"
C14_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14"
MAX_WORKERS = 4
TRUE_OOS = False
NEW_FORWARD_N = 0
PARITY_ABS_TOL = 1e-10
DEV_WAIT_SEC = 5.0
SESSION = "AM"
ARCHITECTURE_N = 3
REPRESENTATION_N = 9
SPEC_N = 27
COMMON_AM_PM_MODEL_ALLOWED = False
COMMON_AM_PM_TARGET_ALLOWED = False

ARCH_RIDGE = "RIDGE_UTILITY"
ARCH_RF = "RF_UTILITY"
ARCH_PAIR = "PAIRWISE_UTILITY_RANK"
ARCHITECTURE_IDS = (ARCH_RIDGE, ARCH_RF, ARCH_PAIR)

# Precommitted from existing RIDGE_ALPHAS=(0.1, 1.0, 10.0). No run-time search.
RIDGE_ALPHA = 1.0

# Precommitted LogisticRegression. No run-time search.
LOGREG_PARAMS = {
    "penalty": "l2",
    "C": 1.0,
    "solver": "lbfgs",
    "max_iter": 2000,
    "random_state": int(RANDOM_STATE),
    "fit_intercept": True,
}

FILL_ONLY_REPRESENTATION_ID = "F0_CURRENT6|none"
SCORE_KEY = "pred_utility"
UTILITY_KEY = "REALIZED_ENTRY_UTILITY"
FINAL_SELECTION_N = int(AM_TOPK)
SLOTS = int(AM_TOPK)

UTILITY_FORBIDDEN = FORBIDDEN_FEATURES + (
    UTILITY_KEY,
    "pnl_yen_100",
    "realized_pnl_yen_100",
    "fill_t",
    "fill_price",
    "exit_t",
    "exit_price",
    "exit_reason",
    "fill_score",
    "pred_U",
    "pred_D",
    "pred_EXEC_U",
    "pred_utility",
    "TARGET_EXEC_U",
    "current_score",
)

PARITY_EXPECTED = {
    "AM_LABELED_N": 6441,
    "AM_Y_FILL5_POS_N": 698,
    "AM_CURRENT_TOP3_FILL5_RATE": 0.37962962962962965,
}

assert abs(float(WAIT_SEC) - 1.0) < 1e-12
assert float(DEV_WAIT_SEC) == 5.0
assert SESSION == "AM"
assert int(POSITION_CAP) == 5
assert int(FINAL_SELECTION_N) == 3
assert int(SLOTS) == 3
assert len(FEATURE_SETS) * len(NORMS) == int(REPRESENTATION_N)
assert int(ARCHITECTURE_N) * int(REPRESENTATION_N) == int(SPEC_N)
assert float(RIDGE_ALPHA) in {float(a) for a in RIDGE_ALPHAS}
assert int(RF_REG_PARAMS["n_estimators"]) == 500
assert int(RF_REG_PARAMS["max_depth"]) == 6
assert int(RF_REG_PARAMS["min_samples_leaf"]) == 20
assert float(RF_REG_PARAMS["max_features"]) == 1.0
assert RF_REG_PARAMS["bootstrap"] is True
assert int(RF_REG_PARAMS["random_state"]) == 42
assert int(MIN_COHORT_N) == 10
assert COMMON_AM_PM_MODEL_ALLOWED is False
assert COMMON_AM_PM_TARGET_ALLOWED is False
assert FILL_ONLY_REPRESENTATION_ID == "F0_CURRENT6|none"
assert float(LOGREG_PARAMS["C"]) == 1.0
assert str(LOGREG_PARAMS["solver"]) == "lbfgs"

RUNTIME_CHANGED = False
C14_CHANGED = False
PAPER_OPERATED = False
OPVAL_OPERATED = False
SUBMIT_N = 0
CANCEL_N = 0
LIVE_ORDER_N = 0
W5_RUNTIME_ADOPTED = False
POSTHOC_SPEC_ADDITION_N = 0
OUTER_RESELECTION_N = 0
FEATURE_SEARCH_N = 0
NEW_FEATURE_N = 0
THRESHOLD_SEARCH_N = 0
WAIT_SEARCH_N = 0
