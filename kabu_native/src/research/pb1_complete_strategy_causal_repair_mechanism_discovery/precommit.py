"""SHA-lock acceptance and forbidden search before candidate-repair PnL."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.cause_first_mechanism_discovery_v1 import X1_TAX_BPS
from research.pb1_complete_strategy_causal_repair_mechanism_discovery import (
    EQUAL_NOTIONAL_YEN,
    MAX_NOTIONAL_YEN,
    MIN_CONF_FOLDS_POSITIVE,
    MIN_FOLD_TRADES,
    MIN_TOTAL_TRADES,
    PRECOMMIT_ID,
    REQUIRE_DEV_POSITIVE,
    WINNER_DAMAGE_FLOOR_BPS,
)
from research.fixed_universe_historical_foundation_v1 import EMA_PERIODS, SMA_PERIODS
from research.pb1_v4_clarified_machine_correction_v4 import OR_RECROSS_CLOSES, UNWIND_FRAC
from research.pb1_v4_clarified_machine_correction_v4.encoding import RCA_COMPARABLE_OPPOSITE_BODY
from research.pb1_v4_complete_strategy_economic_failure_decomposition import EXPAND_BPS, SMALL_EDGE_BPS

# Frozen technical periods. Do not invent EMA5.
assert 9 in EMA_PERIODS
assert 5 in SMA_PERIODS

EXIT_CANDIDATES = (
    "FIRST_OR_RECROSS",
    "SECOND_OR_RECROSS",
    "OPPOSITE_COMMITTED_5M",
    "STALE_RANGE_RESOLUTION",
    "FAILED_EXTENSION_TWO_5M",
    "DISPLACEMENT_UNWIND",
    "VWAP_ADVERSE_1M",
    "EMA9_1M_LOSS_PERSIST_2",
    "EMA9_3M_LOSS_PERSIST_2",
    "EMA9_5M_LOSS_PERSIST_2",
    "SMA5_1M_LOSS_PERSIST_2",
    "TWO_BAR_WEAKNESS",
    "ASF_FIRST_STRUCTURAL_KILL",
)

SIZING_CANDIDATES = ("S0_FIXED_100", "S1_EQUAL_NOTIONAL", "S2_ATR_RISK", "S3_EQUAL_NOTIONAL_MAX_NOTIONAL")
FOLDS = ("DEV", "C1_EARLY", "C1_MIDDLE", "C1_LATE")


def contract() -> dict[str, Any]:
    return {
        "precommit_id": PRECOMMIT_ID,
        "purpose": "existence_of_reproducible_causal_repair_not_profit_search",
        "V1_VERDICT_CHANGED": False,
        "CONFIRMATION1_RESCORED_AS_V1": False,
        "V4_ENTRY_CHANGED": False,
        "V5_CREATED": False,
        "joint_search_forbidden": True,
        "mfe_trailing_forbidden": True,
        "loser_only_oracle_forbidden": True,
        "price_as_signal_filter_forbidden": True,
        "full_sample_best_threshold_forbidden": True,
        "frozen_validation_economic_open": False,
        "prospective_economic_open": False,
        "CAP": 5,
        "same_symbol_block": "preserved",
        "X1_TAX_BPS": float(X1_TAX_BPS),
        "path_class_expand_bps": float(EXPAND_BPS),
        "path_class_small_edge_bps": float(SMALL_EDGE_BPS),
        "or_recross_closes_v1": int(OR_RECROSS_CLOSES),
        "unwind_frac": float(UNWIND_FRAC),
        "opposite_body_frac": float(RCA_COMPARABLE_OPPOSITE_BODY),
        "ema_period": 9,
        "sma_period": 5,
        "structure_loss_persist_bars": 2,
        "exit_candidates": list(EXIT_CANDIDATES),
        "sizing_candidates": list(SIZING_CANDIDATES),
        "folds": list(FOLDS),
        "fold_split": "DEV=20240917-20251126; Confirmation unique dates equal-count tertiles",
        "s1_equal_notional_yen": float(EQUAL_NOTIONAL_YEN),
        "s3_max_notional_yen": float(MAX_NOTIONAL_YEN),
        "s2_atr_ref": "median ATR20 of development trades only; missing ATR weight=1",
        "acceptance": {
            "causal": True,
            "future_information_free": True,
            "same_rule_winners_and_losers": True,
            "require_dev_fold_positive_delta_net_bps": bool(REQUIRE_DEV_POSITIVE),
            "min_confirmation_folds_positive": int(MIN_CONF_FOLDS_POSITIVE),
            "min_fold_trades": int(MIN_FOLD_TRADES),
            "min_total_trades": int(MIN_TOTAL_TRADES),
            "winner_damage_mean_bps_floor": float(WINNER_DAMAGE_FLOOR_BPS),
            "pooled_delta_net_bps_gt_0": True,
            "drop_top1_symbol_still_positive": True,
            "drop_top1_day_still_positive": True,
            "survives_8bps_tax": True,
            "gross_only_positive_insufficient": True,
        },
        "workstreams_standalone": ("A_EXIT_PROFIT_RETENTION", "B_EARLY_FAILURE_ABORT", "C_POSITION_SIZING"),
    }


def precommit_sha256() -> str:
    blob = json.dumps(contract(), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()
