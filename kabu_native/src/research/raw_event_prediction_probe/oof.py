"""E0 = frozen S1. E1 = S1 + all 23 precommitted raw descriptors. Same RF. No subset."""
from __future__ import annotations

from typing import Any

from research.direct_joint_objective import RF_PARAMS as DJ_RF_PARAMS
from research.direct_joint_objective.oof import process_representation
from research.raw_event_prediction_probe import (
    E0_FEATURES,
    E1_FEATURES,
    NORMALIZATION,
    RAW_DESCRIPTOR_N,
    RAW_DESCRIPTORS,
    RF_PARAMS,
)


def _assert_frozen_rf() -> None:
    if dict(RF_PARAMS) != dict(DJ_RF_PARAMS):
        raise RuntimeError("RF_PARAMS drift vs Direct Joint / S1 classifier")
    if len(RAW_DESCRIPTORS) != int(RAW_DESCRIPTOR_N):
        raise RuntimeError("RAW_DESCRIPTOR_N drift")
    if len(E1_FEATURES) != len(E0_FEATURES) + int(RAW_DESCRIPTOR_N):
        raise RuntimeError("E1 feature width drift")


def e0_job(rows_path: str, days: list[str]) -> dict[str, Any]:
    _assert_frozen_rf()
    return {
        "spec": {
            "representation_id": "E0",
            "feature_set": "F2_UNION+SEQ_37x7",
            "features": list(E0_FEATURES),
            "n_features": len(E0_FEATURES),
            "normalization": NORMALIZATION,
            "name": "S1_FLATTENED_5S_CONTROL",
        },
        "representation_id": "E0",
        "rows_path": rows_path,
        "days": list(days),
    }


def e1_job(rows_path: str, days: list[str]) -> dict[str, Any]:
    _assert_frozen_rf()
    return {
        "spec": {
            "representation_id": "E1",
            "feature_set": "S1+RAW_R1R4_23",
            "features": list(E1_FEATURES),
            "n_features": len(E1_FEATURES),
            "normalization": NORMALIZATION,
            "name": "E0_PLUS_RAW_DESCRIPTORS_23",
        },
        "representation_id": "E1",
        "rows_path": rows_path,
        "days": list(days),
    }


def process_architecture(payload: dict[str, Any]) -> dict[str, Any]:
    body = process_representation(payload)
    spec = dict(payload.get("spec") or {})
    body["architecture_id"] = spec.get("representation_id") or payload.get("representation_id")
    body["name"] = spec.get("name") or spec.get("feature_set")
    body["features"] = list(spec.get("features") or [])
    return body
