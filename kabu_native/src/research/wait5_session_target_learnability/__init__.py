"""WAIT5 session target learnability. Diagnostic LODO only. No deployable strategy."""
from __future__ import annotations

from research.direct_joint_objective import ELIGIBLE_DAYS, RF_PARAMS, RANDOM_STATE
from research.entry_objective_redesign_c3 import FEATURE_SETS, NORMS
from small_paper.v1r_primary_runtime import WAIT_SEC

ANALYSIS_ID = "WAIT5_SESSION_TARGET_LEARNABILITY_V1"
C14_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14"
MAX_WORKERS = 2
TRUE_OOS = False
NEW_FORWARD_N = 0
PARITY_ABS_TOL = 1e-10
DEV_WAIT_SEC = 5.0
POS_REP_MIN = 6
POS_REP_DENOM = 9
AM_TOPK = 3
PM_TOPK = 1
COMMON_AM_PM_MODEL_ALLOWED = False
COMMON_AM_PM_TARGET_ALLOWED = False

PRIOR_VERDICT_REQUIRED = "WAIT5_SESSION_SPLIT_ARCHITECTURE_PRECOMMITTED"

RF_CLF_PARAMS = dict(RF_PARAMS)
RF_REG_PARAMS = {
    "n_estimators": 500,
    "max_depth": 6,
    "min_samples_leaf": 20,
    "max_features": 1.0,
    "bootstrap": True,
    "random_state": RANDOM_STATE,
    "n_jobs": -1,
}

PARITY_EXPECTED = {
    "AM_LABELED_N": 6441,
    "AM_Y_FILL5_POS_N": 698,
    "AM_Y_FILL5_RATE": 0.10836826579723645,
    "PM_LABELED_N": 5243,
    "PM_Y_FILL5_POS_N": 154,
    "PM_Y_FILL5_RATE": 0.029372496662216287,
    "AM_CURRENT_TOP3_FILL5_RATE": 0.37962962962962965,
    "PM_CURRENT_TOP1_FILL_RATE": 0.1048951048951049,
    "AM_CONDITIONAL_FILL_N": 698,
}

FORBIDDEN_FEATURES = (
    "U_FILL",
    "D_FILL",
    "Y_FILL5",
    "POSTFILL_MFE_600",
    "POSTFILL_DOWNSIDE_AVOID_600",
    "TIME_TO_FILL_SEC",
    "fill_t",
    "fill_price",
    "T1",
    "T2",
    "joint_label",
)

assert abs(float(WAIT_SEC) - 1.0) < 1e-12
assert float(DEV_WAIT_SEC) == 5.0
assert len(FEATURE_SETS) * len(NORMS) == 9
assert int(POS_REP_MIN) == 6
assert int(POS_REP_DENOM) == 9
assert COMMON_AM_PM_MODEL_ALLOWED is False
assert RF_CLF_PARAMS.get("class_weight") is None
assert int(RF_REG_PARAMS["n_estimators"]) == 500
assert int(RF_REG_PARAMS["max_depth"]) == 6

RUNTIME_CHANGED = False
C14_CHANGED = False
PAPER_OPERATED = False
OPVAL_OPERATED = False
C4_STARTED = False
EXACT_RAN = False
NEW_MODEL_CREATED = False
FEATURE_SEARCH = False
PNL_USED = False
WAIT_POLICY_ADOPTED = False
BEST_REPRESENTATION_ADOPTED = False
HYPERPARAMETER_TUNING = False
CLASS_WEIGHT_TUNING = False
THRESHOLD_SEARCH = False
TOPK_SEARCH = False
QUALITY_SCALAR_WEIGHT = False
JOINT_BINARY_TARGET = False
PARETO_TARGET_ADOPTED = False
SESSION_INDICATOR_COMMON_MODEL = False
ENTRY_RETRAIN_STARTED = False
RUNTIME_CANDIDATE_CREATED = False
STRATEGY_CREATED = False
W10_RESELECTED = False
