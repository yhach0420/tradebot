"""Freeze detector hashes. Do not retune. Strategy SHA over eligibility+entry+exit."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.support_resistance_first_interaction_matched_causal_test_v1.freeze import (
    detector_sha256,
    freeze_record as _matched_freeze,
    state_machine_sha256,
)
from research.sr_a2_bracketed_structure_complete_strategy_v1 import (
    EXPECTED_DETECTOR_SHA256,
    EXPECTED_STATE_MACHINE_SHA256,
    FROZEN_CONFIRM_ATR,
    FROZEN_ZONE_HALF_ATR,
    STRATEGY_SPEC,
)


def strategy_sha256() -> str:
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    h.update(json.dumps(STRATEGY_SPEC, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    for name in ("__init__.py", "eligibility.py"):
        h.update(name.encode("utf-8"))
        h.update((root / name).read_bytes())
    from research.support_resistance_mechanism_to_complete_strategy_v1.exits import simulate_path
    from research.support_resistance_mechanism_to_complete_strategy_v1.portfolio import replay

    h.update(simulate_path.__code__.co_code)
    h.update(replay.__code__.co_code)
    return h.hexdigest()


def freeze_record() -> dict[str, Any]:
    fr = _matched_freeze()
    det = detector_sha256()
    mach = state_machine_sha256()
    fr.update(
        {
            "expected_DETECTOR_SHA256": EXPECTED_DETECTOR_SHA256,
            "expected_STATE_MACHINE_SHA256": EXPECTED_STATE_MACHINE_SHA256,
            "detector_hash_match": det == EXPECTED_DETECTOR_SHA256,
            "state_machine_hash_match": mach == EXPECTED_STATE_MACHINE_SHA256,
            "confirm_atr_frozen": fr.get("confirm_atr") == FROZEN_CONFIRM_ATR,
            "zone_half_atr_frozen": fr.get("zone_half_atr") == FROZEN_ZONE_HALF_ATR,
            "STRATEGY_SHA256": strategy_sha256(),
            "STRATEGY_SPEC": dict(STRATEGY_SPEC),
            "retuned": False,
            "c1_reopened": False,
            "post_hoc_development_architecture": True,
        }
    )
    return fr
