"""Immutable strategy identity hashes. No live retune."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.native_1m_path_state_strategy_freeze_v1 import (
    DISCOVERY_STATUS,
    DIST_VWAP_THRESHOLD,
    ENTRY_CUTOFF,
    EPISODE_GAP_MIN,
    EXPECTED_BLOCK_SHA256,
    EXPECTED_SPLIT_SHA256,
    MINS_FROM_OPEN_THRESHOLD,
    OCCUPANCY,
    SESSION_FLAT,
    SOURCE_ANALYSIS_ID,
    STRATEGY_ID,
    TIME_STOP_MIN,
    VWAP_RECLAIM_THRESHOLD,
    X1_TAX_BPS,
)
from research.native_1m_path_state_strategy_freeze_v1.entry import entry_spec_sha256
from research.native_1m_path_state_strategy_freeze_v1.exit import exit_spec_sha256
from research.native_1m_path_state_strategy_freeze_v1.features import feature_spec_sha256
from research.native_1m_path_state_strategy_freeze_v1.generator import episode_generator_sha256
from research.native_1m_path_state_strategy_freeze_v1.r11 import predicate_sha256
from research.native_1m_path_state_strategy_freeze_v1.replay import portfolio_sha256
from research.native_1m_path_state_strategy_freeze_v1.spec import source_sha256


def _h(payload: Any) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def build_manifest(*, native: Path, d1_med: dict[str, float]) -> dict[str, Any]:
    gen = episode_generator_sha256(native=native)
    feat = feature_spec_sha256()
    pred = predicate_sha256()
    entry = entry_spec_sha256()
    ex = exit_spec_sha256()
    port = portfolio_sha256()
    src = source_sha256()
    complete = _h(
        {
            "strategy_id": STRATEGY_ID,
            "source_analysis_id": SOURCE_ANALYSIS_ID,
            "episode_generator_hash": gen,
            "feature_spec_hash": feat,
            "r11_predicate_hash": pred,
            "entry_implementation_hash": entry,
            "exit_implementation_hash": ex,
            "portfolio_state_hash": port,
            "split_hash": EXPECTED_SPLIT_SHA256,
            "discovery_block_hash": EXPECTED_BLOCK_SHA256,
            "cap": OCCUPANCY,
            "x1_tax_bps": X1_TAX_BPS,
            "entry_cutoff": ENTRY_CUTOFF,
            "session_flat": SESSION_FLAT,
            "time_stop_min": TIME_STOP_MIN,
            "episode_gap_min": EPISODE_GAP_MIN,
            "dist_vwap": repr(DIST_VWAP_THRESHOLD),
            "mins_from_open": repr(MINS_FROM_OPEN_THRESHOLD),
            "vwap_reclaim": repr(VWAP_RECLAIM_THRESHOLD),
            "d1_med_r11": {
                "dist_vwap": d1_med.get("dist_vwap"),
                "mins_from_open": d1_med.get("mins_from_open"),
                "vwap_reclaim": d1_med.get("vwap_reclaim"),
            },
            "discovery_status": DISCOVERY_STATUS,
            "freeze_source_sha256": src,
        }
    )
    return {
        "strategy_id": STRATEGY_ID,
        "source_analysis_id": SOURCE_ANALYSIS_ID,
        "episode_generator_hash": gen,
        "feature_spec_hash": feat,
        "r11_predicate_hash": pred,
        "ENTRY_implementation_hash": entry,
        "EXIT_implementation_hash": ex,
        "portfolio_state_hash": port,
        "split_hash": EXPECTED_SPLIT_SHA256,
        "discovery_block_hash": EXPECTED_BLOCK_SHA256,
        "freeze_package_source_hash": src,
        "complete_strategy_hash": complete,
        "d1_nan_med_frozen": {
            "dist_vwap": d1_med.get("dist_vwap"),
            "mins_from_open": d1_med.get("mins_from_open"),
            "vwap_reclaim": d1_med.get("vwap_reclaim"),
        },
    }
