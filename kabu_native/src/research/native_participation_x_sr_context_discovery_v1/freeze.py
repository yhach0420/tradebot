"""Freeze S/R detector hashes and participation definitions. Do not retune."""
from __future__ import annotations

from typing import Any

from research.support_resistance_first_interaction_matched_causal_test_v1.freeze import (
    detector_sha256,
    freeze_record as _matched_freeze,
    state_machine_sha256,
)
from research.native_participation_x_sr_context_discovery_v1 import (
    EXPECTED_DETECTOR_SHA256,
    EXPECTED_STATE_MACHINE_SHA256,
    FROZEN_CONFIRM_ATR,
    FROZEN_ZONE_HALF_ATR,
    REFRACTORY_BARS,
    TV_EXPAND_PCTL,
)


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
            "tv_expand_pctl": TV_EXPAND_PCTL,
            "refractory_bars": REFRACTORY_BARS,
            "retuned": False,
            "a2_reopened_as_strategy": False,
            "c1_reopened": False,
            "d1_d4_status": "ALL_DEVELOPMENT",
        }
    )
    return fr
