"""SHA-lock architecture contract before seeing feature-to-PnL results."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.pb1_entry_architecture_reassessment import (
    FOLDS,
    MIN_BUCKET_N,
    MIN_FOLD_N,
    MIN_FOLDS_SAME_SIGN,
    MIN_OOF_TRADES,
    MIN_SPEARMAN_N,
    PRECOMMIT_ID,
    PRICE_PROXY_ABS_RHO,
    QUANTILE_CUTS,
    WINNER_CONC_MAX,
)

FEATURES = (
    # A opening drive
    "opening_disp_atr",
    "or_width_atr",
    "gap_atr",
    "dominant_5m_frac",
    "max_counter_body_atr",
    "followthrough_or_bps",
    "same_dir_run_5m",
    "minutes_since_or_leave",
    "dist_session_extreme_atr",
    "retrace_from_extreme_frac",
    "dist_vwap_bps",
    "dist_pdc_bps",
    "tv_pace_vs_or",
    # B location
    "dist_location_bps",
    "dist_location_atr",
    "location_age_min",
    "level_touch_n",
    "approach_3m_bps",
    # C thesis age / quality
    "minutes_from_open",
    "failed_ext_n",
    "counter_committed_n",
    "range_compression",
    "dir_persist_frac",
    # D execution quality (not E0/E1 label)
    "confirm_body_frac",
    "confirm_close_loc",
    "confirm_range_atr",
    "confirm_vol_ratio",
)

FAMILIES = {
    "A_OPENING_DRIVE": FEATURES[:13],
    "B_LOCATION": FEATURES[13:18],
    "C_THESIS_AGE": FEATURES[18:23],
    "D_EXECUTION_QUALITY": FEATURES[23:],
    "E_MARKET_CONTEXT": (),
}


def contract() -> dict[str, Any]:
    return {
        "precommit_id": PRECOMMIT_ID,
        "purpose": "identify_economically_valid_PB1_entry_from_causal_entry_time_information_inside_complete_strategy",
        "not_entry_only_profit_study": True,
        "V1_VERDICT_CHANGED": False,
        "V4_CHANGED": False,
        "V5_CREATED": False,
        "ml_grid_forbidden": True,
        "location_subtype_deletion_forbidden": True,
        "symbol_blacklist_forbidden": True,
        "time_blacklist_forbidden": True,
        "price_as_signal_filter_forbidden": True,
        "future_mfe_as_feature_forbidden": True,
        "feature_available_at_le_entry_allowed_at": True,
        "e0_e1_label_not_keep_drop": True,
        "joint_grid_max_features_if_univariate_ok": 4,
        "folds": list(FOLDS),
        "quantile_cuts": list(QUANTILE_CUTS),
        "features": list(FEATURES),
        "families": {k: list(v) for k, v in FAMILIES.items()},
        "market_context": "external_index_futures_fx_only_if_existing_aligned_data_no_interpolation",
        "univariate_acceptance": {
            "min_fold_n": int(MIN_FOLD_N),
            "min_spearman_n": int(MIN_SPEARMAN_N),
            "min_bucket_n": int(MIN_BUCKET_N),
            "min_folds_same_sign_q80_minus_q20_net_bps": int(MIN_FOLDS_SAME_SIGN),
            "require_at_least_one_dev_and_one_c1": True,
            "price_proxy_abs_spearman_entry_px": float(PRICE_PROXY_ABS_RHO),
            "mfe_alone_insufficient": True,
        },
        "walk_forward": {
            "direction_and_quantile_locked_on_DEV_EARLY_only": True,
            "threshold_then_tested_chronologically": True,
            "no_retune_on_test_fold": True,
        },
        "complete_strategy_oof_acceptance": {
            "unit": "occupancy_replay_CAP5_same_symbol_thesis_lost_exit_8bps",
            "confirmation1_net_pnl_gt_0": True,
            "confirmation1_pf_gt_1": True,
            "confirmation1_mean_net_gt_0": True,
            "min_oof_trades": int(MIN_OOF_TRADES),
            "max_top_symbol_net_share": float(WINNER_CONC_MAX),
            "slightly_better_than_v1_insufficient": True,
            "positive_economic_edge_required": True,
        },
    }


def precommit_sha256() -> str:
    blob = json.dumps(contract(), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()
