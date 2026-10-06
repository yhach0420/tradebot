"""Freeze detector + event-generator + tree hyperparameters. No retune."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from research.native_causal_context_stack_discovery_v1 import (
    EXPECTED_DETECTOR_SHA256,
    EXPECTED_STATE_MACHINE_SHA256,
    FEATURE_NAMES,
    FROZEN_CONFIRM_ATR,
    FROZEN_ZONE_HALF_ATR,
    REFRACTORY_BARS,
    TREE_CRITERION,
    TREE_MAX_DEPTH,
    TREE_MIN_LEAF_ABS,
    TREE_MIN_LEAF_FRAC,
    TREE_RANDOM_STATE,
)
from research.support_resistance_first_interaction_matched_causal_test_v1.freeze import (
    detector_sha256,
    freeze_record as _matched_freeze,
    state_machine_sha256,
)


def event_generator_sha256() -> str:
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for name in ("__init__.py", "features.py", "walk.py"):
        h.update(name.encode("utf-8"))
        h.update((root / name).read_bytes())
    from research.native_participation_x_sr_context_discovery_v1.native import displacement

    h.update(displacement.__code__.co_code)
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
            "EVENT_GENERATOR_SHA256": event_generator_sha256(),
            "feature_names": list(FEATURE_NAMES),
            "tree_max_depth": TREE_MAX_DEPTH,
            "tree_min_leaf_abs": TREE_MIN_LEAF_ABS,
            "tree_min_leaf_frac": TREE_MIN_LEAF_FRAC,
            "tree_criterion": TREE_CRITERION,
            "tree_random_state": TREE_RANDOM_STATE,
            "refractory_bars": REFRACTORY_BARS,
            "tv_not_required": True,
            "retuned": False,
            "i2_not_reopened": True,
        }
    )
    return fr
