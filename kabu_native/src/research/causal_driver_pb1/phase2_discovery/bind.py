"""FAIL-CLOSED identity bind against precommit V1.1 only."""
from __future__ import annotations

import json
from typing import Any

from research.causal_driver_pb1.identity.ids import sha256_obj
from research.causal_driver_pb1.phase2_precommit.sector_map import bind_sector_mapping
from research.causal_driver_pb1.phase2_precommit_v1_1.shuffle import generate_shuffle_permutations
from research.causal_driver_pb1.phase2_discovery import (
    EXPECTED_ELIGIBLE_DAY_SHA256,
    EXPECTED_FOLD_BOUNDARY_SHA256,
    EXPECTED_PHASE1_INVENTORY_SHA256,
    EXPECTED_PHASE1_SOURCE_FINGERPRINT,
    EXPECTED_PRECOMMIT_ID,
    EXPECTED_PRECOMMIT_SHA256,
    EXPECTED_SECTOR_MAP_SHA256,
    EXPECTED_SHUFFLE_SHA256,
    EXPECTED_UNIVERSE_SHA256,
    FORBIDDEN_SUPERSEDED_PRECOMMIT_SHA256,
    TARGET_SCOPES,
)
from research.causal_driver_pb1.phase2_discovery.isolation import PHASE1_OUT, PRECOMMIT_V11_OUT


def _load_json(path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def bind_precommit() -> dict[str, Any]:
    blockers: list[str] = []
    doc = _load_json(PRECOMMIT_V11_OUT / "report.json")
    answers = doc.get("answers") or {}
    ev = doc.get("evaluation") or {}
    if answers.get("precommit_id") != EXPECTED_PRECOMMIT_ID:
        blockers.append("PRECOMMIT_ID_MISMATCH")
    if answers.get("precommit_sha256") != EXPECTED_PRECOMMIT_SHA256:
        blockers.append("PRECOMMIT_SHA_MISMATCH")
    if answers.get("precommit_sha256") == FORBIDDEN_SUPERSEDED_PRECOMMIT_SHA256:
        blockers.append("SUPERSEDED_V1_PRECOMMIT_USED")
    parent = _load_json(PHASE1_OUT / "report.json")
    p_ans = parent.get("answers") or {}
    if p_ans.get("inventory_sha256") != EXPECTED_PHASE1_INVENTORY_SHA256:
        blockers.append("PHASE1_INVENTORY_SHA_MISMATCH")
    fp = (parent.get("evaluation") or {}).get("source_fingerprint") or p_ans.get("source_fingerprint") or answers.get(
        "USDJPY_source_fingerprint"
    )
    if fp != EXPECTED_PHASE1_SOURCE_FINGERPRINT:
        blockers.append("USDJPY_SOURCE_FINGERPRINT_MISMATCH")
    sectors = bind_sector_mapping()
    if sectors.get("universe105_sha256") != EXPECTED_UNIVERSE_SHA256:
        blockers.append("UNIVERSE105_SHA_MISMATCH")
    if sectors.get("sector_mapping_sha256") != EXPECTED_SECTOR_MAP_SHA256:
        blockers.append("SECTOR_MAP_SHA_MISMATCH")
    if list(sectors.get("target_scopes") or []) != list(TARGET_SCOPES):
        blockers.append("TARGET_SCOPE_DRIFT")
    folds = ev.get("folds") or {}
    if folds.get("fold_boundary_sha256") != EXPECTED_FOLD_BOUNDARY_SHA256:
        blockers.append("FOLD_SHA_MISMATCH")
    if answers.get("eligible_day_sha256") != EXPECTED_ELIGIBLE_DAY_SHA256:
        blockers.append("ELIGIBLE_DAY_SHA_MISMATCH")
    dev = list(folds.get("development_dates") or [])
    c1 = list(folds.get("c1_dates") or [])
    eligible = sorted(dev + c1)
    if sha256_obj(eligible) != EXPECTED_ELIGIBLE_DAY_SHA256:
        blockers.append("ELIGIBLE_DAY_REHASH_MISMATCH")
    sh = generate_shuffle_permutations(eligible)
    if sh.get("permutation_sha256") != EXPECTED_SHUFFLE_SHA256:
        blockers.append("SHUFFLE_SHA_MISMATCH")
    if sh.get("unshufflable") != ["20240930"]:
        blockers.append("UNSHUFFLABLE_DATE_DRIFT")
    bdev = folds.get("development_fold_boundaries") or {}
    bc1 = folds.get("c1_fold_boundaries") or {}
    if (bdev.get("DEV_EARLY") or {}).get("n") != 143 or (bdev.get("DEV_LATE") or {}).get("n") != 144:
        blockers.append("DEV_FOLD_N_MISMATCH")
    if any((bc1.get(k) or {}).get("n") != 32 for k in ("C1_EARLY", "C1_MIDDLE", "C1_LATE")):
        blockers.append("C1_FOLD_N_MISMATCH")
    if len(dev) != 287 or len(c1) != 96:
        blockers.append("ELIGIBLE_N_MISMATCH")
    return {
        "pass": not blockers,
        "blockers": blockers,
        "precommit_sha256": answers.get("precommit_sha256"),
        "sectors": sectors,
        "folds": folds,
        "development_dates": dev,
        "c1_dates": c1,
        "eligible_dates": eligible,
        "shuffle": {k: v for k, v in sh.items() if k != "maps"},
        "source_fingerprint": fp,
        "inventory_sha256": p_ans.get("inventory_sha256"),
    }
