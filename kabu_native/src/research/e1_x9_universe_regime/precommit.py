"""Precommit for E1_X9 — sealed before regime outcome analysis."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from research.e1_x6_provisional.util import sha256_obj

from . import (
    ANALYSIS_ID,
    ASOF_CUTOFF,
    BOOTSTRAP_REPS,
    BOOTSTRAP_SEED,
    DOCUMENT_ID,
    FROZEN_UPDATE_THR,
    SOURCE_BRIDGE,
    SOURCE_PFQ,
    SOURCE_X8,
)

JST = ZoneInfo("Asia/Tokyo")


def build_precommit(*, source_shas: dict[str, str]) -> dict[str, Any]:
    body = {
        "analysis_id": ANALYSIS_ID,
        "document_id": DOCUMENT_ID,
        "precommit_type": "E1_X9_UNIVERSE_REGIME_PRECOMMIT",
        "precommit_at_jst": datetime.now(JST).isoformat(),
        "hypothesis": "lower institutional / large-capital participation regimes may show relatively stronger short-horizon ENTRY path quality",
        "pfq_policy": {
            "status": "PFQ_CURRENT_LINE_CLOSED_REJECTED",
            "pfq_revive": False,
            "prospective": False,
            "shadow": False,
        },
        "source_x8": SOURCE_X8,
        "source_pfq": SOURCE_PFQ,
        "source_bridge": SOURCE_BRIDGE,
        "source_shas": source_shas,
        "asof_cutoff": ASOF_CUTOFF,
        "asof_rules": {
            "effective_date_le": ASOF_CUTOFF,
            "publication_date_le": ASOF_CUTOFF,
            "no_retroactive_current_info": True,
        },
        "regime_axes_precommitted": [
            "MCAP tercile",
            "TURNOVER tercile",
            "INDEX status",
            "MARKET segment",
            "DIRECT ownership tercile (if evaluable)",
        ],
        "interactions_precommitted": [
            "MCAP tercile × TURNOVER tercile",
            "INDEX status × MCAP tercile",
        ],
        "no_composite_score": True,
        "frozen_update_threshold": FROZEN_UPDATE_THR,
        "no_regime_q70_rederivation": True,
        "bootstrap": {"unit": "day_x_symbol", "reps": BOOTSTRAP_REPS, "seed": BOOTSTRAP_SEED},
        "support_gate": {"n_symbols_min": 5, "n_episodes_min": 30, "n_days_min": 5},
        "coverage_gates": {
            "core_proxy_symbol_min": 0.90,
            "core_proxy_episode_min": 0.90,
            "direct_ownership_symbol_min": 0.70,
            "direct_ownership_episode_min": 0.70,
        },
        "verdict_set": [
            "E1_X9_ASOF_METADATA_INSUFFICIENT",
            "E1_X9_DIRECT_INSTITUTIONAL_DATA_EVALUABLE_NO_STABLE_RELATION",
            "E1_X9_DIRECT_LOW_INSTITUTIONAL_REGIME_SUPPORTED",
            "E1_X9_PROXY_LOW_PARTICIPATION_REGIME_SUPPORTED",
            "E1_X9_HIGH_UPDATE_REGIME_EXPLAINS_SIGNAL",
            "E1_X9_NO_STABLE_UNIVERSE_REGIME_SEPARATION",
        ],
        "outcomes_opened_before_precommit": False,
        "no_new_family": True,
        "no_pfq_revival": True,
    }
    body["precommit_sha256"] = sha256_obj(body)
    return body
