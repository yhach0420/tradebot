"""Bind frozen V1.1 identities. Do not regenerate days, folds, shuffle, or bootstrap draws."""
from __future__ import annotations

import json
from typing import Any

from research.causal_driver_pb1.identity.ids import sha256_bytes
from research.causal_driver_pb1.sector_breadth_precommit_v1_1.identity import load_v1_frozen_identities
from research.causal_driver_pb1.sector_breadth_precommit_v1_1.inference import freeze_bootstrap_indices
from research.causal_driver_pb1.sector_breadth_precommit_v1_2 import (
    SUPERSEDES_PRECOMMIT_ID,
    SUPERSEDES_PRECOMMIT_SHA256,
    V1_BOOTSTRAP_INDEX_SHA256,
    V1_ELIGIBLE_C1_N,
    V1_ELIGIBLE_DAY_SHA256,
    V1_ELIGIBLE_DEV_N,
    V1_FAMILY_384_SHA256,
    V1_FOLD_BOUNDARY_SHA256,
    V1_PERMUTATION_SHA256,
)
from research.causal_driver_pb1.sector_breadth_precommit_v1_2.isolation import DISC_OUT, V11_OUT


def bind_v11_identities() -> dict[str, Any]:
    blockers: list[str] = []
    path = V11_OUT / "report.json"
    raw = path.read_bytes() if path.is_file() else b""
    doc = json.loads(raw.decode("utf-8")) if raw else {}
    a = doc.get("answers") or {}
    if a.get("precommit_id") != SUPERSEDES_PRECOMMIT_ID:
        blockers.append("V1_1_PRECOMMIT_ID_MISMATCH")
    if a.get("precommit_sha256") != SUPERSEDES_PRECOMMIT_SHA256:
        blockers.append("V1_1_PRECOMMIT_SHA_MISMATCH")
    if a.get("eligible_day_sha256") != V1_ELIGIBLE_DAY_SHA256:
        blockers.append("ELIGIBLE_DAY_SHA_CHANGED")
    if a.get("fold_boundary_sha256") != V1_FOLD_BOUNDARY_SHA256:
        blockers.append("FOLD_SHA_CHANGED")
    if a.get("permutation_sha256") != V1_PERMUTATION_SHA256:
        blockers.append("PERMUTATION_SHA_CHANGED")
    if a.get("bootstrap_index_sha256") != V1_BOOTSTRAP_INDEX_SHA256:
        blockers.append("BOOTSTRAP_INDEX_SHA_CHANGED")
    if a.get("family_n") != 384 and a.get("family_n") is not None:
        blockers.append("FAMILY_N_CHANGED")
    if a.get("C1_outcomes_opened") is True:
        blockers.append("V1_1_C1_WAS_OPENED")
    v1 = load_v1_frozen_identities()
    blockers.extend(v1.get("blockers") or [])
    boot = {}
    if not (v1.get("blockers") or []) and v1.get("development_dates") and v1.get("c1_dates"):
        boot = freeze_bootstrap_indices(development_dates=list(v1["development_dates"]), c1_dates=list(v1["c1_dates"]))
        if boot.get("bootstrap_index_sha256") != V1_BOOTSTRAP_INDEX_SHA256:
            blockers.append("BOOTSTRAP_INDEX_REHASH_MISMATCH")
    disc_path = DISC_OUT / "report.json"
    ddoc = json.loads(disc_path.read_text(encoding="utf-8")) if disc_path.is_file() else {}
    da = ddoc.get("answers") or {}
    if bool(da.get("C1_opened")):
        blockers.append("C1_WAS_OPENED")
    if int(da.get("C1_rows_read_before_candidate_freeze") or 0) != 0:
        blockers.append("C1_ROWS_BEFORE_FREEZE_NOT_ZERO")
    return {
        "pass": not blockers,
        "blockers": blockers,
        "v11_precommit_sha256": a.get("precommit_sha256"),
        "v11_report_file_sha256": sha256_bytes(raw) if raw else None,
        "v1": v1,
        "bootstrap": boot,
        "eligible_dates": list(v1.get("eligible_dates") or []),
        "development_dates": list(v1.get("development_dates") or []),
        "c1_dates": list(v1.get("c1_dates") or []),
        "folds": v1.get("folds") or {},
        "shuffle": v1.get("shuffle") or {},
        "C1_opened": bool(da.get("C1_opened")),
        "C1_rows_read_before_candidate_freeze": int(da.get("C1_rows_read_before_candidate_freeze") or 0),
        "dates_regenerated": False,
        "folds_regenerated": False,
        "shuffle_regenerated": False,
        "bootstrap_draws_regenerated": False,
        "family_384_sha256": V1_FAMILY_384_SHA256,
        "eligible_day_sha256": V1_ELIGIBLE_DAY_SHA256,
        "fold_boundary_sha256": V1_FOLD_BOUNDARY_SHA256,
        "permutation_sha256": V1_PERMUTATION_SHA256,
        "bootstrap_index_sha256": V1_BOOTSTRAP_INDEX_SHA256,
        "eligible_dev_n": V1_ELIGIBLE_DEV_N,
        "eligible_c1_n": V1_ELIGIBLE_C1_N,
    }
