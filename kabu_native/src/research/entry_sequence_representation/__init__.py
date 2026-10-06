"""Causal entry sequence representation probe. Research only. No runtime adopt. No Exact."""
from __future__ import annotations

from research.direct_joint_objective import ELIGIBLE_DAYS, RANDOM_STATE
from research.entry_objective_redesign_c3 import F2_UNION

ANALYSIS_ID = "CAUSAL_ENTRY_SEQUENCE_REPRESENTATION_PROBE_V1"
C14_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14"
MAX_WORKERS = 2
TRUE_OOS = False
NEW_FORWARD_N = 0
TOPK = 3
JOINT_RATE_MIN = 0.50
NORMALIZATION = "none"
WINDOW_SEC = 180.0
STEP_SEC = 5.0
N_MARKS = 37
N_CHANNELS = 7
SEQUENCE_FEATURE_N = N_MARKS * N_CHANNELS  # 259

RF_PARAMS = {
    "n_estimators": 500,
    "max_depth": 6,
    "min_samples_leaf": 20,
    "max_features": 1.0,
    "bootstrap": True,
    "random_state": RANDOM_STATE,
    "n_jobs": -1,
    "class_weight": None,
}

PRIOR_VERDICT_REQUIRED = "ENGINEERED_FEATURE_ARCHITECTURE_INSUFFICIENT"

S0_EXPECTED = {
    "TOP3_MFE_DELTA": 0.0006176389596935367,
    "TOP3_DOWNSIDE_DELTA": 0.00006729208305413287,
    "JOINT_COHORT_SUCCESS_RATE": 0.33916083916083917,
}
S0_PARITY_ABS_TOL = 1e-10

B0_EXISTING = tuple(F2_UNION)
MARK_OFFSETS = tuple(range(180, -1, -5))  # 180, 175, ..., 0
CHANNELS = (
    "mid_rel_t0_bps",
    "spread_bps",
    "imbalance",
    "log_bid_qty",
    "log_ask_qty",
    "quote_age_sec",
    "valid_mask",
)


def sequence_feature_names() -> tuple[str, ...]:
    out: list[str] = []
    for off in MARK_OFFSETS:
        for ch in CHANNELS:
            out.append(f"seq_{off:03d}_{ch}")
    return tuple(out)


SEQ_FEATURES = sequence_feature_names()
S1_FEATURES = tuple(B0_EXISTING) + SEQ_FEATURES

assert len(MARK_OFFSETS) == N_MARKS
assert len(CHANNELS) == N_CHANNELS
assert len(SEQ_FEATURES) == SEQUENCE_FEATURE_N
assert SEQUENCE_FEATURE_N == 259

RUNTIME_CHANGED = False
C14_CHANGED = False
PAPER_OPERATED = False
OPVAL_OPERATED = False
C4_STARTED = False
EXACT_RAN = False
HYPERPARAMETER_TUNING = False
MODEL_CHANGED = False
LABEL_CHANGED = False
GRID_CHANGED = False
WINDOW_CHANGED = False
CHANNEL_CHANGED = False
TOPK_SEARCH = False
THRESHOLD_SEARCH = False
PNL_USED = False
RUNTIME_CANDIDATE_CREATED = False
STRATEGY_CREATED = False
TEMPORAL_DEEP_STARTED = False
INDIVIDUAL_LAG_SELECTION = False
