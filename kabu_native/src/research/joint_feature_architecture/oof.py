"""6 frozen architectures. Direct joint RF classifier. No nested selection."""
from __future__ import annotations

from typing import Any

from research.direct_joint_objective.oof import process_representation
from research.joint_feature_architecture import ARCHITECTURES, NORMALIZATION


def architecture_jobs(rows_path: str, days: list[str]) -> list[dict[str, Any]]:
    jobs = []
    for spec in ARCHITECTURES:
        jobs.append(
            {
                "spec": {
                    "representation_id": spec["architecture_id"],
                    "feature_set": spec["name"],
                    "features": list(spec["features"]),
                    "n_features": len(spec["features"]),
                    "normalization": NORMALIZATION,
                    "blocks": list(spec["blocks"]),
                    "name": spec["name"],
                },
                "representation_id": spec["architecture_id"],
                "rows_path": rows_path,
                "days": list(days),
            }
        )
    return jobs


def process_architecture(payload: dict[str, Any]) -> dict[str, Any]:
    body = process_representation(payload)
    spec = dict(payload.get("spec") or {})
    body["architecture_id"] = spec.get("representation_id") or payload.get("representation_id")
    body["name"] = spec.get("name") or spec.get("feature_set")
    body["blocks"] = list(spec.get("blocks") or [])
    body["features"] = list(spec.get("features") or [])
    return body
