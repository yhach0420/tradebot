"""UNIFORM10 ENTRY REBUILD V2. TARGET V4 M4 + nested OOF. Offline only. No Runtime write."""
from __future__ import annotations

ANALYSIS_ID = "UNIFORM10_ENTRY_REBUILD_V2"
C14_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14"
C_V1_STATUS = "C_REBUILD_V1_INVALID_TARGET_CONTAMINATED"
C_V1_SUPERSEDED = True
MAX_WORKERS = 2
WAIT_SEC = 1.0
HORIZON_SEC = 600.0
POSITION_CAP = 5
NEW_FORWARD_N = 0
TRUE_OOS = False
PRIMARY_TARGET = "EXECUTABLE_FORWARD_MID_RETURN_600S"
B0_STATUS = "HISTORICAL_REFERENCE_ONLY"
B1_STATUS = "B1_SCORE_THRESHOLD_NO_ROBUST_IMPROVEMENT"
PER_FEATURE_THRESHOLD = "DO_NOT_START"
EXPECTED_B0_TRADES = 278
EXPECTED_B0_PNL = 441350.0
EXPECTED_B0_PF = 1.315622
EXPECTED_B0_MAXDD = -357050.0
B0_TRADE_TOL = 0
B0_PNL_TOL = 1.0
B0_PF_TOL = 1e-5
B0_DD_TOL = 1.0

# Frozen before search. Not expanded after seeing results.
MANIFEST = {
    "target_soT": "TARGET_PRICE_CONTRACT_V4 M4_PERSISTENT",
    "max_mark_age_sec": None,
    "execution_freshness_sec": 5.0,
    "execution_freshness_used_as_label": False,
    "families": ("BASE_CURRENT_SCORE", "LINEAR_REGULARIZED", "RF_EXISTING"),
    "linear": {
        "class": "sklearn.linear_model.Ridge",
        "source": "research.e1_x36_joint_allocator.models A2/A4",
        "alpha": (0.1, 1.0, 10.0),
        "why": "Existing low-complexity regularized linear ranking of a continuous target.",
    },
    "nonlinear": {
        "class": "sklearn.ensemble.RandomForestRegressor",
        "source": "research.winner_multiclass.models RandomForestClassifier hyperparams, regression form",
        "n_estimators": 80,
        "max_depth": 4,
        "min_samples_leaf": 5,
        "random_state": 0,
        "why": "Single existing-repo nonlinear family. Frozen HPs. Not expanded mid-run.",
    },
    "max_selected_features": (4, 6, 8),
    "normalization": ("cross_sectional_z", "robust_cross_sectional_rank", "none"),
    "primary_metric": "MEAN_DAILY_OOF_SPEARMAN",
    "no_score_admission_threshold": True,
    "no_rank_pass_gate": True,
    "no_per_feature_hard_threshold": True,
    "no_topk_as_architecture": True,
    "no_pnl_in_model_selection": True,
    "baseline_current_score_refit": False,
    "forbidden_predictors": (
        "symbol identity",
        "day identity",
        "weekday",
        "individual anchor ID",
        "executable_flag",
        "OPENS_WITHIN_1S",
        "future Fill",
        "future spread",
        "future volume",
        "future CurrentPriceStatus",
    ),
    "min_feature_coverage": 0.60,
    "redundancy_abs_corr": 0.85,
    "min_cohort_n_spearman": 5,
}

FEATURE_COVERAGE_MIN = 0.60
REDUNDANCY_ABS_CORR = 0.85
MIN_COHORT_N = 5
MAX_SELECTED_FEATURES = MANIFEST["max_selected_features"]
NORMS = MANIFEST["normalization"]
RIDGE_ALPHAS = MANIFEST["linear"]["alpha"]
RF_HP = MANIFEST["nonlinear"]