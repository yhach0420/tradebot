"""Freeze proven detector hashes. Do not retune."""
from __future__ import annotations

from typing import Any

from research.support_resistance_first_interaction_matched_causal_test_v1.freeze import (
    detector_sha256,
    freeze_record as _parent_freeze,
    state_machine_sha256,
)
from research.support_resistance_matched_separation_not_a_strategy_v1 import (
    EXPECTED_DETECTOR_SHA256,
    EXPECTED_STATE_MACHINE_SHA256,
    FROZEN_CONFIRM_ATR,
    FROZEN_ZONE_HALF_ATR,
)


def freeze_record() -> dict[str, Any]:
    fr = _parent_freeze()
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
            "retuned": False,
            "renamed_to_strategy": False,
            "renamed_to_causal_trading_edge": False,
        }
    )
    return fr
