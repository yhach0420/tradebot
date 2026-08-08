"""Precommit for E1_X8 — sealed before LOSO / random / signal outcomes."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from research.e1_x6_provisional.util import sha256_obj

from . import (
    ANALYSIS_ID,
    BOOTSTRAP_REPS,
    BOOTSTRAP_SEED,
    DOCUMENT_ID,
    FROZEN,
    PFQ_STATUS,
    RANDOM_REPS,
    RANDOM_SEED,
    SOURCE_BRIDGE,
    SOURCE_PFQ_FINAL,
    TARGET_SYMBOL,
)

JST = ZoneInfo("Asia/Tokyo")


def build_precommit(*, source_shas: dict[str, str]) -> dict[str, Any]:
    body = {
        "analysis_id": ANALYSIS_ID,
        "document_id": DOCUMENT_ID,
        "precommit_type": "E1_X8_THRESHOLD_SYMBOL_LEVERAGE_PRECOMMIT",
        "precommit_at_jst": datetime.now(JST).isoformat(),
        "purpose": [
            "A. q70/q30 pulled by 285A",
            "B. high-update/high-vol group identification",
            "C. thresholds not symbol-dependent",
            "UPDATE first-touch support ex-285A survive vs vanish",
        ],
        "pfq_current_line": PFQ_STATUS,
        "pfq_revival_forbidden": True,
        "source_pfq_final": SOURCE_PFQ_FINAL,
        "source_bridge": SOURCE_BRIDGE,
        "source_shas": source_shas,
        "period": "20260721-20260731",
        "unused_data_forbidden": True,
        "quantile_contract": {
            "method": "build_only_feature_quantile",
            "interpolation": "linear between order statistics: pos=q*(n-1)",
            "update_q": 0.70,
            "flow_q": 0.30,
            "update_missing": "exclude None price_update_count_10s",
            "flow_missing": "require ratio_valid and non-None uptick_volume_ratio_30s",
            "candidate_ops": {
                "UPDATE": "price_update_count_10s >= q70",
                "FLOW": "uptick_volume_ratio_30s <= q30 AND classified>=3 AND ratio_valid",
            },
            "frozen_expected": FROZEN,
        },
        "kioxia_threshold_leverage_rules": {
            "size_matched_percentile_ge": 0.95,
            "influence_rank_le": 3,
            "cross_symbol_flip_rate_ge": 0.05,
            "target_symbol": TARGET_SYMBOL,
        },
        "signal_support_rules": {
            "ci95_lower_gt_0": True,
            "positive_days_ge": 7,
            "bootstrap_reps": BOOTSTRAP_REPS,
            "bootstrap_seed": BOOTSTRAP_SEED,
            "unit": "day_x_symbol",
        },
        "random_deletion": {"reps": RANDOM_REPS, "seed": RANDOM_SEED, "stratify": "day_x_session"},
        "verdict_priority": [
            "E1_X8_SYMBOL_LEVERAGE_IDENTITY_MISMATCH",
            "E1_X8_QUANTILE_CONTRACT_UNRESOLVED",
            "E1_X8_BRIDGE_SIGNAL_IDENTITY_MISMATCH",
            "E1_X8_KIOXIA_DOMINANT_SIGNAL_DEPENDENCE",
            "E1_X8_KIOXIA_THRESHOLD_LEVERAGE_SIGNAL_SURVIVES",
            "E1_X8_BROAD_HIGH_UPDATE_REGIME_PROXY",
            "E1_X8_SYMBOL_THRESHOLD_STABLE",
            "E1_X8_SYMBOL_LEVERAGE_INSUFFICIENT_EVIDENCE",
        ],
        "outcomes_opened_before_precommit": False,
        "no_pfq_resurrection": True,
        "no_new_family": True,
    }
    body["precommit_sha256"] = sha256_obj(body)
    return body
