"""Exact frozen BG_CONT_VWAP definition. No parameter changes."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.behavior_group_sequence_mechanism_v1 import OCCUPANCY, SESSION_FLAT, X1_TAX_BPS
from research.behavior_group_sequence_mechanism_v1.replay import SPECS, _spec_sha
from research.freeze_group_mechanism_definitions_v1 import (
    EXPECTED_BLOCK_SHA256,
    EXPECTED_PARENT_SPEC_SHA256,
    EXPECTED_SPLIT_SHA256,
    FROZEN_MECHANISM_ID,
)
from research.freeze_group_mechanism_definitions_v1.spec import file_sha256

CAUSAL_FEATURE_DEFINITIONS = {
    "vwap_reclaim_at_T": "close[T] > session_vwap[T] AND close[T-1] <= session_vwap[T-1]",
    "vwap_loss_at_bar": "close[bar] <= session_vwap[bar] after having been above",
    "session_vwap": "session_vwap(high, low, close, volume, trading_value) from bar 0 through T",
    "behavior_group": "PREQUENTIAL CONTINUATION from prefix profiles strictly before the eval block",
    "group_training": "D1→D2; D1+D2→D3; D1+D2+D3→D4; D1 characterization only",
    "full_discovery_atlas_label_used": False,
    "available_at": "feature bar T usable no earlier than T+1m (BAR_START)",
    "entry_fill": "open of available_at / event_time bar",
    "x0": "exit_fill / entry_open - 1, in bps",
    "x1": "X0 - 8bps tax; tax not changed",
    "exit_thesis": "first VWAP_LOSS while in trade, else session flatten 15:20, else time_stop 20m",
    "CAP": 3,
    "same_symbol": "one live, no same-day reentry",
    "occupancy": "skip if 3 slots filled; slot release on exit",
    "parameter_changes": False,
}

CLOSED_PAIRS = (
    {
        "pair": "SECTOR_LAGGARD_CATCHUP x CATCHUP_RS_TURN",
        "status": "REJECTED_NON_MATERIAL",
        "reason": "prequential group vs global +0.57 bps < 2.0 bps material bar",
        "retuned": False,
        "subgroups_invented": False,
    },
    {
        "pair": "SECTOR_LAGGARD_CATCHUP x VWAP_RECLAIM",
        "status": "REJECTED_NON_MATERIAL",
        "reason": "laggard+VWAP worse than global VWAP (delta about -2.5 bps)",
        "retuned": False,
        "subgroups_invented": False,
    },
    {
        "pair": "CONTINUATION x IMPULSE_THEN_PAUSE",
        "status": "REJECTED_NON_MATERIAL",
        "reason": "delta about +0.96 bps, not material",
        "retuned": False,
        "subgroups_invented": False,
    },
    {
        "pair": "OPENING_MOMENTUM x OPENING_GAP_HOLD",
        "status": "REJECTED_NON_MATERIAL",
        "reason": "group worse than global (delta about -1.40 bps)",
        "retuned": False,
        "subgroups_invented": False,
    },
    {
        "pair": "SECTOR_LEADER x pullback continuation",
        "status": "REJECTED_NON_MATERIAL",
        "reason": "delta about +0.79 bps, not material",
        "retuned": False,
        "subgroups_invented": False,
    },
)


def frozen_spec() -> dict[str, Any]:
    raw = next(s for s in SPECS if s["candidate_id"] == FROZEN_MECHANISM_ID)
    spec = dict(raw)
    spec["occupancy"] = OCCUPANCY
    spec["CAP"] = "skip_if_occupancy_full"
    spec["slot_release"] = "on_exit"
    spec["reentry"] = "none_same_day"
    spec["execution"] = "X0=next_bar_open_after_available_at; X1=X0-8bps_tax_not_BidAsk"
    spec["hm1_retuned"] = False
    spec["uses_frozen_validation"] = False
    spec["uses_full_discovery_group"] = False
    spec["spec_sha256"] = _spec_sha(spec)
    if spec["spec_sha256"] != EXPECTED_PARENT_SPEC_SHA256:
        raise RuntimeError(f"frozen_spec_sha_drift {spec['spec_sha256']}")
    return spec


def mechanism_hashes(*, source_sha: str) -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    parent = root / "behavior_group_sequence_mechanism_v1"
    native_states = root / "one_minute_native_playbook_discovery_v1" / "states.py"
    spec = frozen_spec()
    group_sha = file_sha256(parent / "prequential.py")
    entry_sha = file_sha256(parent / "engine.py")
    exit_sha = file_sha256(parent / "replay.py")
    feature_sha = file_sha256(native_states)
    payload = {
        "mechanism_id": FROZEN_MECHANISM_ID,
        "spec_sha256": spec["spec_sha256"],
        "group_classification_code_sha256": group_sha,
        "entry_code_sha256": entry_sha,
        "exit_code_sha256": exit_sha,
        "vwap_feature_code_sha256": feature_sha,
        "split_sha256": EXPECTED_SPLIT_SHA256,
        "block_sha256": EXPECTED_BLOCK_SHA256,
        "source_sha256": source_sha,
        "causal_feature_definitions": CAUSAL_FEATURE_DEFINITIONS,
        "x1_tax_bps": X1_TAX_BPS,
        "occupancy": OCCUPANCY,
        "session_flat": SESSION_FLAT,
        "parameter_changed": False,
    }
    mech = hashlib.sha256(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str).encode("utf-8")).hexdigest()
    payload["mechanism_hash"] = mech
    return payload
