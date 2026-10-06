"""Joint entry feature-architecture redesign. Research only. No runtime adopt. No Exact."""
from __future__ import annotations

from research.direct_joint_objective import ELIGIBLE_DAYS, RANDOM_STATE
from research.entry_objective_redesign_c3 import F2_UNION

ANALYSIS_ID = "JOINT_ENTRY_FEATURE_ARCHITECTURE_REDESIGN_V1"
C14_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14"
MAX_WORKERS = 2
TRUE_OOS = False
NEW_FORWARD_N = 0
TOPK = 3
JOINT_RATE_MIN = 0.50
NORMALIZATION = "none"

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

PRIOR_VERDICT_REQUIRED = "DIRECT_JOINT_OBJECTIVE_STILL_UPSIDE_ONLY"

# B0 control = previous Direct Joint F2_UNION|none arm (the F2_UNION architecture).
A0_EXPECTED = {
    "TOP3_MFE_DELTA": 0.0006176389596935367,
    "TOP3_DOWNSIDE_DELTA": 6.729208305413287e-05,
    "JOINT_COHORT_SUCCESS_RATE": 0.33916083916083917,
}
A0_PARITY_ABS_TOL = 1e-10

# Previous probe required-output medians across 9 representations (not A0).
PREVIOUS_PROBE_MEDIAN = {
    "MEDIAN_DIRECT_MFE_DELTA": 0.0015119227607747153,
    "MEDIAN_DIRECT_DOWNSIDE_DELTA": -0.0005569130440506356,
    "MEDIAN_DIRECT_JOINT_COHORT_SUCCESS_RATE": 0.3240418118466899,
}

B0_EXISTING = tuple(F2_UNION)

BLOCK_P = (
    "return_30s",
    "return_60s",
    "return_180s",
    "realized_range_60s_bps",
    "realized_range_180s_bps",
    "drawdown_from_high_60s_bps",
    "drawdown_from_high_180s_bps",
    "rebound_from_low_60s_bps",
    "rebound_from_low_180s_bps",
    "distance_from_vwap_bps",
)

BLOCK_L = (
    "volume_percentile_60s",
    "trading_value_percentile_180s",
    "trading_value_delta_60s",
    "volume_rate_60s",
    "event_rate_60s",
    "spread_bps",
)

BLOCK_M = (
    "imbalance_t0",
    "imbalance_change_10s",
    "imbalance_change_30s",
    "imbalance_mean_30s",
    "imbalance_std_30s",
    "spread_change_30s",
    "best_bid_qty_change_30s",
    "best_ask_qty_change_30s",
    "bid_ask_depth_ratio_t0",
)

BLOCK_X = (
    "xs_return60_z",
    "xs_return180_z",
    "xs_range180_z",
    "xs_vwap_distance_z",
    "xs_rebound180_z",
    "xs_imbalance_z",
    "xs_volume_percentile60_rank",
)

# Frozen missing policy: insufficient lookback → None. No future fill. No 0-impute for changes.
MIN_MEAN_EVENTS = 1
MIN_STD_EVENTS = 3
MIN_PERCENTILE_SAMPLES = 10
MIN_Z_N = 3

HARVEST_FEATURE_KEYS = tuple(dict.fromkeys((*BLOCK_P, *BLOCK_L, *BLOCK_M, *BLOCK_X)))
# Never overwrite B0 columns on join (A0 parity).
JOIN_KEYS = tuple(k for k in HARVEST_FEATURE_KEYS if k not in B0_EXISTING)


def unique_feats(*blocks: tuple[str, ...]) -> tuple[str, ...]:
    seen: list[str] = []
    for block in blocks:
        for f in block:
            if f not in seen:
                seen.append(f)
    return tuple(seen)


ARCHITECTURES = (
    {"architecture_id": "A0", "name": "B0_EXISTING", "features": unique_feats(B0_EXISTING), "blocks": ("B0",)},
    {"architecture_id": "A1", "name": "B0_P", "features": unique_feats(B0_EXISTING, BLOCK_P), "blocks": ("B0", "P")},
    {"architecture_id": "A2", "name": "B0_L", "features": unique_feats(B0_EXISTING, BLOCK_L), "blocks": ("B0", "L")},
    {"architecture_id": "A3", "name": "B0_M", "features": unique_feats(B0_EXISTING, BLOCK_M), "blocks": ("B0", "M")},
    {"architecture_id": "A4", "name": "B0_X", "features": unique_feats(B0_EXISTING, BLOCK_X), "blocks": ("B0", "X")},
    {"architecture_id": "A5", "name": "B0_P_L_M_X", "features": unique_feats(B0_EXISTING, BLOCK_P, BLOCK_L, BLOCK_M, BLOCK_X), "blocks": ("B0", "P", "L", "M", "X")},
)

BLOCK_EFFECTS = (
    ("A1", "PRICE_PATH_BLOCK_EFFECT", "P"),
    ("A2", "LIQUIDITY_BLOCK_EFFECT", "L"),
    ("A3", "MICROSTRUCTURE_BLOCK_EFFECT", "M"),
    ("A4", "CROSS_SECTIONAL_BLOCK_EFFECT", "X"),
)

RUNTIME_CHANGED = False
C14_CHANGED = False
PAPER_OPERATED = False
OPVAL_OPERATED = False
C4_STARTED = False
EXACT_RAN = False
HYPERPARAMETER_TUNING = False
BEST_ARCHITECTURE_ADOPTED = False
INDIVIDUAL_FEATURE_SELECTION = False
WINDOW_SEARCH = False
THRESHOLD_SEARCH = False
TOPK_SEARCH = False
MODEL_CHANGED = False
PNL_USED = False
RUNTIME_CANDIDATE_CREATED = False
STRATEGY_CREATED = False
SEQUENCE_MODEL_STARTED = False
LABEL_CHANGED = False
