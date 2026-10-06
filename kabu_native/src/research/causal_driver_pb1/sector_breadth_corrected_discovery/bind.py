"""FAIL-CLOSED bind against sector-breadth precommit V1.2 only."""
from __future__ import annotations

import json
from typing import Any

import numpy as np

from research.causal_driver_pb1.identity.ids import sha256_bytes, sha256_obj
from research.causal_driver_pb1.phase2_precommit.sector_map import bind_sector_mapping
from research.causal_driver_pb1.phase2_precommit_v1_1.shuffle import generate_shuffle_permutations
from research.causal_driver_pb1.sector_breadth_corrected_discovery import (
    CONTROL_GATE_RULE_ID,
    EXPECTED_BOOTSTRAP_INDEX_SHA256,
    EXPECTED_ELIGIBLE_DAY_SHA256,
    EXPECTED_FAMILY_384_SHA256,
    EXPECTED_FOLD_BOUNDARY_SHA256,
    EXPECTED_PERMUTATION_SHA256,
    EXPECTED_PRECOMMIT_SHA256,
    EXPECTED_SECTOR_IDS,
    EXPECTED_SECTOR_MAPPING_SHA256,
    EXPECTED_UNIVERSE_SHA256,
    FORBIDDEN_V1_PRECOMMIT_SHA256,
    FORBIDDEN_V11_PRECOMMIT_SHA256,
    PRECOMMIT_ID,
)
from research.causal_driver_pb1.sector_breadth_corrected_discovery.isolation import PRECOMMIT_V12_OUT
from research.causal_driver_pb1.sector_breadth_discovery import (
    EXPECTED_C1_EARLY_LAST,
    EXPECTED_C1_EARLY_N,
    EXPECTED_C1_INDEX_SHA256,
    EXPECTED_C1_LATE_FIRST,
    EXPECTED_C1_LATE_N,
    EXPECTED_C1_MIDDLE_FIRST,
    EXPECTED_C1_MIDDLE_LAST,
    EXPECTED_C1_MIDDLE_N,
    EXPECTED_C1_N,
    EXPECTED_DEV_EARLY_LAST,
    EXPECTED_DEV_EARLY_N,
    EXPECTED_DEV_INDEX_SHA256,
    EXPECTED_DEV_LATE_FIRST,
    EXPECTED_DEV_LATE_N,
    EXPECTED_DEV_N,
)
from research.causal_driver_pb1.sector_breadth_precommit.family import family_identity
from research.causal_driver_pb1.sector_breadth_precommit_v1_1.inference import draw_date_block_indices, freeze_bootstrap_indices


def bind_precommit() -> dict[str, Any]:
    blockers: list[str] = []
    path = PRECOMMIT_V12_OUT / "report.json"
    doc = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    a = doc.get("answers") or {}
    ev = doc.get("evaluation") or {}
    if a.get("precommit_id") != PRECOMMIT_ID:
        blockers.append("PRECOMMIT_ID_MISMATCH")
    if a.get("precommit_sha256") != EXPECTED_PRECOMMIT_SHA256:
        blockers.append("PRECOMMIT_SHA_MISMATCH")
    if a.get("precommit_sha256") in (FORBIDDEN_V11_PRECOMMIT_SHA256, FORBIDDEN_V1_PRECOMMIT_SHA256):
        blockers.append("SUPERSEDED_PRECOMMIT_USED")
    gate = a.get("control_gate") or ev.get("control_gate") or {}
    if (gate.get("rule_id") or "") != CONTROL_GATE_RULE_ID:
        blockers.append("CONTROL_GATE_RULE_ID_MISMATCH")
    if a.get("eligible_day_sha256") != EXPECTED_ELIGIBLE_DAY_SHA256:
        blockers.append("ELIGIBLE_DAY_SHA_MISMATCH")
    if a.get("fold_boundary_sha256") != EXPECTED_FOLD_BOUNDARY_SHA256:
        blockers.append("FOLD_SHA_MISMATCH")
    if a.get("permutation_sha256") != EXPECTED_PERMUTATION_SHA256:
        blockers.append("PERMUTATION_SHA_MISMATCH")
    if a.get("bootstrap_index_sha256") != EXPECTED_BOOTSTRAP_INDEX_SHA256:
        blockers.append("BOOTSTRAP_INDEX_SHA_MISMATCH")
    if a.get("universe105_sha256") != EXPECTED_UNIVERSE_SHA256:
        blockers.append("UNIVERSE105_SHA_MISMATCH")
    if (a.get("family_384_sha256") or ((ev.get("contract") or {}).get("family") or {}).get("family_384_sha256")) != EXPECTED_FAMILY_384_SHA256:
        blockers.append("FAMILY_384_SHA_MISMATCH")
    sectors = bind_sector_mapping()
    if sectors.get("universe105_sha256") != EXPECTED_UNIVERSE_SHA256:
        blockers.append("UNIVERSE105_REBIND_MISMATCH")
    if sectors.get("sector_mapping_sha256") != EXPECTED_SECTOR_MAPPING_SHA256:
        blockers.append("SECTOR_MAPPING_SHA_MISMATCH")
    elig = list(sectors.get("eligible_sectors") or [])
    ids = tuple(sorted(str(s["sector_id"]) for s in elig))
    if ids != EXPECTED_SECTOR_IDS:
        blockers.append("ELIGIBLE_SECTOR_SET_MISMATCH")
    fam = family_identity(eligible_sectors=elig) if ids == EXPECTED_SECTOR_IDS else {}
    if fam.get("family_384_sha256") != EXPECTED_FAMILY_384_SHA256:
        blockers.append("FAMILY_384_REHASH_MISMATCH")
    days = ev.get("days") or {}
    folds = days.get("folds") or {}
    dev = list(folds.get("development_dates") or [])
    c1 = list(folds.get("c1_dates") or [])
    eligible = list(dev) + list(c1)
    if sha256_obj(eligible) != EXPECTED_ELIGIBLE_DAY_SHA256:
        blockers.append("ELIGIBLE_DAY_REHASH_MISMATCH")
    if folds.get("fold_boundary_SHA256") != EXPECTED_FOLD_BOUNDARY_SHA256 and folds.get("fold_boundary_sha256") != EXPECTED_FOLD_BOUNDARY_SHA256:
        blockers.append("FOLD_REHASH_MISMATCH")
    sh = generate_shuffle_permutations(eligible)
    if sh.get("permutation_sha256") != EXPECTED_PERMUTATION_SHA256:
        blockers.append("SHUFFLE_REHASH_MISMATCH")
    bdev = folds.get("development_fold_boundaries") or {}
    bc1 = folds.get("c1_fold_boundaries") or {}
    if len(dev) != EXPECTED_DEV_N or len(c1) != EXPECTED_C1_N:
        blockers.append("ELIGIBLE_N_MISMATCH")
    if (bdev.get("DEV_EARLY") or {}).get("n") != EXPECTED_DEV_EARLY_N or (bdev.get("DEV_EARLY") or {}).get("last") != EXPECTED_DEV_EARLY_LAST:
        blockers.append("DEV_EARLY_MISMATCH")
    if (bdev.get("DEV_LATE") or {}).get("n") != EXPECTED_DEV_LATE_N or (bdev.get("DEV_LATE") or {}).get("first") != EXPECTED_DEV_LATE_FIRST:
        blockers.append("DEV_LATE_MISMATCH")
    if (bc1.get("C1_EARLY") or {}).get("n") != EXPECTED_C1_EARLY_N or (bc1.get("C1_EARLY") or {}).get("last") != EXPECTED_C1_EARLY_LAST:
        blockers.append("C1_EARLY_MISMATCH")
    if (bc1.get("C1_MIDDLE") or {}).get("n") != EXPECTED_C1_MIDDLE_N or (bc1.get("C1_MIDDLE") or {}).get("first") != EXPECTED_C1_MIDDLE_FIRST:
        blockers.append("C1_MIDDLE_MISMATCH")
    if (bc1.get("C1_MIDDLE") or {}).get("last") != EXPECTED_C1_MIDDLE_LAST:
        blockers.append("C1_MIDDLE_LAST_MISMATCH")
    if (bc1.get("C1_LATE") or {}).get("n") != EXPECTED_C1_LATE_N or (bc1.get("C1_LATE") or {}).get("first") != EXPECTED_C1_LATE_FIRST:
        blockers.append("C1_LATE_MISMATCH")
    boot = freeze_bootstrap_indices(development_dates=dev, c1_dates=c1) if dev and c1 else {}
    if boot.get("bootstrap_index_sha256") != EXPECTED_BOOTSTRAP_INDEX_SHA256:
        blockers.append("BOOTSTRAP_INDEX_REHASH_MISMATCH")
    periods = {p["period"]: p for p in (boot.get("periods") or [])}
    if (periods.get("DEVELOPMENT") or {}).get("index_sha256") != EXPECTED_DEV_INDEX_SHA256:
        blockers.append("DEV_BOOTSTRAP_INDEX_SHA_MISMATCH")
    if (periods.get("C1") or {}).get("index_sha256") != EXPECTED_C1_INDEX_SHA256:
        blockers.append("C1_BOOTSTRAP_INDEX_SHA_MISMATCH")
    dev_idx = draw_date_block_indices(n_day=len(dev)) if dev else np.zeros((0, 0), dtype=np.int64)
    c1_idx = draw_date_block_indices(n_day=len(c1)) if c1 else np.zeros((0, 0), dtype=np.int64)
    if dev_idx.size and sha256_bytes(np.ascontiguousarray(dev_idx, dtype="<i8").tobytes()) != EXPECTED_DEV_INDEX_SHA256:
        blockers.append("DEV_INDEX_BYTES_MISMATCH")
    if c1_idx.size and sha256_bytes(np.ascontiguousarray(c1_idx, dtype="<i8").tobytes()) != EXPECTED_C1_INDEX_SHA256:
        blockers.append("C1_INDEX_BYTES_MISMATCH")
    if a.get("C1_outcomes_opened") is True or a.get("C1_opened") is True:
        blockers.append("PRECOMMIT_C1_WAS_OPENED")
    return {
        "pass": not blockers,
        "blockers": blockers,
        "precommit_id": a.get("precommit_id"),
        "precommit_sha256": a.get("precommit_sha256"),
        "control_gate_rule_id": gate.get("rule_id"),
        "sectors": sectors,
        "scopes": fam.get("scopes") or [],
        "tests": fam.get("tests") or [],
        "family_384_sha256": fam.get("family_384_sha256"),
        "eligible_dates": eligible,
        "development_dates": dev,
        "c1_dates": c1,
        "folds": folds,
        "eligible_day_sha256": EXPECTED_ELIGIBLE_DAY_SHA256,
        "fold_boundary_sha256": EXPECTED_FOLD_BOUNDARY_SHA256,
        "permutation_sha256": EXPECTED_PERMUTATION_SHA256,
        "bootstrap_index_sha256": EXPECTED_BOOTSTRAP_INDEX_SHA256,
        "universe105_sha256": EXPECTED_UNIVERSE_SHA256,
        "sector_mapping_sha256": EXPECTED_SECTOR_MAPPING_SHA256,
        "dev_boot_index": dev_idx,
        "c1_boot_index": c1_idx,
        "shuffle": {k: v for k, v in sh.items() if k != "maps"},
        "precommit_sha_verified": a.get("precommit_sha256") == EXPECTED_PRECOMMIT_SHA256 and not blockers,
    }
