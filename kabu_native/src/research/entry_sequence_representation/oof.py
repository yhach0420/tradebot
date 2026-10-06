"""S0 control and S1 flattened sequence. Same frozen RF classifier. No nested selection."""
from __future__ import annotations

from typing import Any

from research.direct_joint_objective import RF_PARAMS as DJ_RF_PARAMS
from research.direct_joint_objective.oof import process_representation
from research.entry_sequence_representation import (
    B0_EXISTING,
    NORMALIZATION,
    RF_PARAMS,
    S1_FEATURES,
    SEQ_FEATURES,
    SEQUENCE_FEATURE_N,
)


def _assert_frozen_rf() -> None:
    if dict(RF_PARAMS) != dict(DJ_RF_PARAMS):
        raise RuntimeError("RF_PARAMS drift vs Direct Joint classifier")


def s0_job(rows_path: str, days: list[str]) -> dict[str, Any]:
    _assert_frozen_rf()
    return {
        "spec": {
            "representation_id": "S0",
            "feature_set": "F2_UNION|none",
            "features": list(B0_EXISTING),
            "n_features": len(B0_EXISTING),
            "normalization": NORMALIZATION,
            "name": "A0_F2_UNION_none",
        },
        "representation_id": "S0",
        "rows_path": rows_path,
        "days": list(days),
    }


def s1_job(rows_path: str, days: list[str]) -> dict[str, Any]:
    _assert_frozen_rf()
    if len(SEQ_FEATURES) != int(SEQUENCE_FEATURE_N):
        raise RuntimeError("SEQUENCE_FEATURE_N drift")
    return {
        "spec": {
            "representation_id": "S1",
            "feature_set": "F2_UNION+SEQ_37x7",
            "features": list(S1_FEATURES),
            "n_features": len(S1_FEATURES),
            "normalization": NORMALIZATION,
            "name": "A0_PLUS_FLATTENED_SEQUENCE",
        },
        "representation_id": "S1",
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
