"""Raw event information-loss audit. Diagnostic only. No model. No Exact."""
from __future__ import annotations

from research.direct_joint_objective import ELIGIBLE_DAYS
from research.entry_sequence_representation import CHANNELS, MARK_OFFSETS, N_MARKS
from research.temporal_model_probe import PARITY_ABS_TOL, S0_EXPECTED, S1_EXPECTED

ANALYSIS_ID = "RAW_EVENT_INFORMATION_LOSS_AUDIT_V1"
C14_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14"
MAX_WORKERS = 2
TRUE_OOS = False
NEW_FORWARD_N = 0
WINDOW_SEC = 180.0
STEP_SEC = 5.0
STALE_SEC = 5.0
DAILY_SIGN_MIN = 12
LOST_RATE_MIN = 0.25
MIN_DAY_POS = 5
MIN_DAY_NEG = 5
PRIOR_VERDICT_REQUIRED = "TEMPORAL_MODEL_STILL_SINGLE_OBJECTIVE"

assert N_MARKS == 37
assert MARK_OFFSETS[0] == 180 and MARK_OFFSETS[-1] == 0
assert CHANNELS[-1] == "valid_mask"

FAMILIES = (
    "EVENT_TIMING",
    "IMBALANCE_TRANSITION",
    "SPREAD_TRANSITION",
    "DEPTH_TRANSITION",
    "STATE_PERSISTENCE",
)

# Precommitted recoverability. Not fit on labels.
RECOVERABILITY = {
    "event_count_180s": "NOT_RECOVERABLE",
    "event_count_60s": "NOT_RECOVERABLE",
    "interarrival_median_ms": "NOT_RECOVERABLE",
    "interarrival_p10_ms": "NOT_RECOVERABLE",
    "interarrival_cv": "NOT_RECOVERABLE",
    "max_silence_ms": "NOT_RECOVERABLE",
    "imbalance_sign_flip_count": "NOT_RECOVERABLE",
    "imbalance_zero_cross_count": "NOT_RECOVERABLE",
    "imbalance_abs_change_sum": "NOT_RECOVERABLE",
    "imbalance_max": "NOT_RECOVERABLE",
    "imbalance_min": "NOT_RECOVERABLE",
    "imbalance_range": "NOT_RECOVERABLE",
    "spread_change_count": "NOT_RECOVERABLE",
    "spread_widen_count": "NOT_RECOVERABLE",
    "spread_narrow_count": "NOT_RECOVERABLE",
    "spread_max_bps": "NOT_RECOVERABLE",
    "spread_range_bps": "NOT_RECOVERABLE",
    "time_spread_above_median_share": "APPROXIMATELY_RECOVERABLE",
    "bid_qty_abs_change_sum": "NOT_RECOVERABLE",
    "ask_qty_abs_change_sum": "NOT_RECOVERABLE",
    "bid_qty_max_drop": "NOT_RECOVERABLE",
    "ask_qty_max_drop": "NOT_RECOVERABLE",
    "depth_ratio_sign_flip_count": "NOT_RECOVERABLE",
    "positive_imbalance_time_share": "APPROXIMATELY_RECOVERABLE",
    "negative_imbalance_time_share": "APPROXIMATELY_RECOVERABLE",
    "tight_spread_time_share": "APPROXIMATELY_RECOVERABLE",
    "stale_quote_time_share": "APPROXIMATELY_RECOVERABLE",
}

FAMILY_KEYS = {
    "EVENT_TIMING": (
        "event_count_180s",
        "event_count_60s",
        "interarrival_median_ms",
        "interarrival_p10_ms",
        "interarrival_cv",
        "max_silence_ms",
    ),
    "IMBALANCE_TRANSITION": (
        "imbalance_sign_flip_count",
        "imbalance_zero_cross_count",
        "imbalance_abs_change_sum",
        "imbalance_max",
        "imbalance_min",
        "imbalance_range",
    ),
    "SPREAD_TRANSITION": (
        "spread_change_count",
        "spread_widen_count",
        "spread_narrow_count",
        "spread_max_bps",
        "spread_range_bps",
        "time_spread_above_median_share",
    ),
    "DEPTH_TRANSITION": (
        "bid_qty_abs_change_sum",
        "ask_qty_abs_change_sum",
        "bid_qty_max_drop",
        "ask_qty_max_drop",
        "depth_ratio_sign_flip_count",
    ),
    "STATE_PERSISTENCE": (
        "positive_imbalance_time_share",
        "negative_imbalance_time_share",
        "tight_spread_time_share",
        "stale_quote_time_share",
    ),
}

RECOVERABILITY_REASON = {
    "event_count_180s": "Event counts are not stored on the 37 snapshot grid.",
    "event_count_60s": "Event counts are not stored on the 37 snapshot grid.",
    "interarrival_median_ms": "Inter-arrival times require the raw event clock.",
    "interarrival_p10_ms": "Inter-arrival times require the raw event clock.",
    "interarrival_cv": "Inter-arrival dispersion requires the raw event clock.",
    "max_silence_ms": "True max gap can sit between 5s marks; grid quote_age is mark-local only.",
    "imbalance_sign_flip_count": "Within-5s sign flips can net out before the next mark.",
    "imbalance_zero_cross_count": "Zero-crosses inside a 5s bin are invisible on the grid.",
    "imbalance_abs_change_sum": "Path length of imbalance is not the 37-point polyline.",
    "imbalance_max": "Instantaneous extrema can occur between marks.",
    "imbalance_min": "Instantaneous extrema can occur between marks.",
    "imbalance_range": "Intra-bin range is not the range of 37 snapshots.",
    "spread_change_count": "Spread transitions inside a 5s bin are not counted on the grid.",
    "spread_widen_count": "Widen/narrow pulses can revert before the next mark.",
    "spread_narrow_count": "Widen/narrow pulses can revert before the next mark.",
    "spread_max_bps": "Instantaneous spread max can occur between marks.",
    "spread_range_bps": "Intra-bin spread range is not the snapshot range.",
    "time_spread_above_median_share": "5s piecewise hold approximates event-time persistence.",
    "bid_qty_abs_change_sum": "Depth path length is not recoverable from 37 qty snapshots.",
    "ask_qty_abs_change_sum": "Depth path length is not recoverable from 37 qty snapshots.",
    "bid_qty_max_drop": "Largest drop can occur and recover inside a 5s bin.",
    "ask_qty_max_drop": "Largest drop can occur and recover inside a 5s bin.",
    "depth_ratio_sign_flip_count": "Depth-ratio flips inside a 5s bin are aliased.",
    "positive_imbalance_time_share": "Time shares can be approximated by 5s piecewise-constant grid states.",
    "negative_imbalance_time_share": "Time shares can be approximated by 5s piecewise-constant grid states.",
    "tight_spread_time_share": "Time shares can be approximated by 5s piecewise-constant grid states.",
    "stale_quote_time_share": "Grid quote_age_sec approximates staleness at marks, not continuously.",
}

# Joint+ minus joint-. Precommitted economic stories. Not estimated.
STABILITY_SIGN = {
    "event_count_180s": -1,
    "event_count_60s": -1,
    "interarrival_median_ms": 1,
    "interarrival_p10_ms": 1,
    "interarrival_cv": -1,
    "max_silence_ms": -1,
    "imbalance_sign_flip_count": -1,
    "imbalance_zero_cross_count": -1,
    "imbalance_abs_change_sum": -1,
    "imbalance_range": -1,
    "spread_change_count": -1,
    "spread_widen_count": -1,
    "spread_range_bps": -1,
    "bid_qty_abs_change_sum": -1,
    "ask_qty_abs_change_sum": -1,
    "bid_qty_max_drop": -1,
    "ask_qty_max_drop": -1,
    "depth_ratio_sign_flip_count": -1,
    "tight_spread_time_share": 1,
    "stale_quote_time_share": -1,
}
ALIASING_SIGN = {
    "event_count_180s": 1,
    "event_count_60s": 1,
    "interarrival_median_ms": -1,
    "interarrival_p10_ms": -1,
    "interarrival_cv": 1,
    "max_silence_ms": -1,
    "imbalance_sign_flip_count": 1,
    "imbalance_zero_cross_count": 1,
    "imbalance_abs_change_sum": 1,
    "imbalance_range": 1,
    "spread_change_count": 1,
    "spread_widen_count": 1,
    "spread_range_bps": 1,
    "bid_qty_abs_change_sum": 1,
    "ask_qty_abs_change_sum": 1,
    "bid_qty_max_drop": 1,
    "ask_qty_max_drop": 1,
    "depth_ratio_sign_flip_count": 1,
    "stale_quote_time_share": -1,
}

RUNTIME_CHANGED = False
C14_CHANGED = False
PAPER_OPERATED = False
OPVAL_OPERATED = False
C4_STARTED = False
EXACT_RAN = False
NEW_MODEL = False
FEATURE_SEARCH = False
THRESHOLD_SEARCH = False
WINDOW_CHANGED = False
GRID_RERUN = False
DESCRIPTOR_ADDED = False
PNL_USED = False
RUNTIME_CANDIDATE_CREATED = False
STRATEGY_CREATED = False
EVENT_MODEL_STARTED = False
LABEL_CHANGED = False
