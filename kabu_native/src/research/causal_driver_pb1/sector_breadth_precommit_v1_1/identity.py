"""Load V1 frozen day/fold/shuffle identities. Do not regenerate."""
from __future__ import annotations

import json
from typing import Any

from research.causal_driver_pb1.identity.ids import sha256_bytes, sha256_obj
from research.causal_driver_pb1.sector_breadth_precommit_v1_1 import (
    SUPERSEDES_PRECOMMIT_SHA256,
    V1_ELIGIBLE_C1_N,
    V1_ELIGIBLE_DAY_SHA256,
    V1_ELIGIBLE_DEV_N,
    V1_FAMILY_384_SHA256,
    V1_FOLD_BOUNDARY_SHA256,
    V1_PERMUTATION_SHA256,
)
from research.causal_driver_pb1.sector_breadth_precommit_v1_1.isolation import V1_OUT


def load_v1_frozen_identities() -> dict[str, Any]:
    path = V1_OUT / "report.json"
    raw = path.read_bytes() if path.is_file() else b""
    doc = json.loads(raw.decode("utf-8")) if raw else {}
    a = doc.get("answers") or {}
    ev = doc.get("evaluation") or {}
    days = ev.get("days") or {}
    folds = days.get("folds") or {}
    shuffle = days.get("shuffle") or {}
    contract = ev.get("contract") or {}
    family = contract.get("family") or {}
    blockers: list[str] = []
    if a.get("precommit_sha256") != SUPERSEDES_PRECOMMIT_SHA256:
        blockers.append("V1_PRECOMMIT_SHA_MISMATCH")
    if a.get("eligible_day_sha256") != V1_ELIGIBLE_DAY_SHA256:
        blockers.append("V1_ELIGIBLE_DAY_SHA_CHANGED")
    if a.get("fold_boundary_sha256") != V1_FOLD_BOUNDARY_SHA256:
        blockers.append("V1_FOLD_SHA_CHANGED")
    if a.get("permutation_sha256") != V1_PERMUTATION_SHA256:
        blockers.append("V1_PERMUTATION_SHA_CHANGED")
    if a.get("eligible_dev_n") != V1_ELIGIBLE_DEV_N:
        blockers.append("V1_ELIGIBLE_DEV_N_CHANGED")
    if a.get("eligible_c1_n") != V1_ELIGIBLE_C1_N:
        blockers.append("V1_ELIGIBLE_C1_N_CHANGED")
    if a.get("family_n") != 384:
        blockers.append("V1_FAMILY_N_CHANGED")
    if family.get("family_384_sha256") != V1_FAMILY_384_SHA256:
        blockers.append("V1_FAMILY_384_SHA_CHANGED")
    if a.get("SECTOR_BREADTH_DISPERSION_OUTCOMES_OPENED") is True:
        blockers.append("V1_OUTCOMES_WERE_OPENED")
    if a.get("C1_outcomes_opened") is True:
        blockers.append("V1_C1_WAS_OPENED")
    if a.get("candidate_list_sha256") not in (None,):
        blockers.append("V1_CANDIDATE_LIST_NOT_NULL")
    eligible_dates = list(days.get("eligible_dates") or [])
    if eligible_dates and sha256_obj(eligible_dates) != V1_ELIGIBLE_DAY_SHA256:
        blockers.append("V1_ELIGIBLE_DATES_REHASH_MISMATCH")
    dev = list(folds.get("development_dates") or [])
    c1 = list(folds.get("c1_dates") or [])
    if len(dev) != V1_ELIGIBLE_DEV_N:
        blockers.append("V1_DEV_DATES_N_MISMATCH")
    if len(c1) != V1_ELIGIBLE_C1_N:
        blockers.append("V1_C1_DATES_N_MISMATCH")
    if folds.get("fold_boundary_sha256") != V1_FOLD_BOUNDARY_SHA256:
        blockers.append("V1_FOLD_PAYLOAD_SHA_MISMATCH")
    if shuffle.get("permutation_sha256") != V1_PERMUTATION_SHA256:
        blockers.append("V1_SHUFFLE_PAYLOAD_SHA_MISMATCH")
    return {
        "pass": not blockers,
        "blockers": blockers,
        "v1_precommit_sha256": a.get("precommit_sha256"),
        "v1_report_file_sha256": sha256_bytes(raw) if raw else None,
        "eligible_dates": eligible_dates,
        "eligible_day_sha256": V1_ELIGIBLE_DAY_SHA256,
        "development_dates": dev,
        "c1_dates": c1,
        "eligible_dev_n": len(dev),
        "eligible_c1_n": len(c1),
        "folds": folds,
        "shuffle": shuffle,
        "fold_boundary_sha256": V1_FOLD_BOUNDARY_SHA256,
        "permutation_sha256": V1_PERMUTATION_SHA256,
        "family_384_sha256": V1_FAMILY_384_SHA256,
        "listing_start_sha256": days.get("listing_start_sha256"),
        "coverage_summary": days.get("coverage_summary") or {},
        "days": days,
        "v1_answers": {
            "precommit_sha256": a.get("precommit_sha256"),
            "eligible_day_sha256": a.get("eligible_day_sha256"),
            "fold_boundary_sha256": a.get("fold_boundary_sha256"),
            "permutation_sha256": a.get("permutation_sha256"),
            "eligible_dev_n": a.get("eligible_dev_n"),
            "eligible_c1_n": a.get("eligible_c1_n"),
            "family_n": a.get("family_n"),
            "SECTOR_BREADTH_DISPERSION_OUTCOMES_OPENED": a.get("SECTOR_BREADTH_DISPERSION_OUTCOMES_OPENED"),
            "C1_outcomes_opened": a.get("C1_outcomes_opened"),
            "candidate_list_sha256": a.get("candidate_list_sha256"),
        },
        "dates_regenerated": False,
        "folds_regenerated": False,
        "shuffle_regenerated": False,
        "future_return_used": False,
    }
