"""FAIL-CLOSED bind against leader-laggard precommit V1.1 only."""
from __future__ import annotations

import json
from typing import Any

from research.causal_driver_pb1.cross_sectional_discovery import (
    EXPECTED_C1_FOLD_N,
    EXPECTED_C1_N,
    EXPECTED_DEV_EARLY_N,
    EXPECTED_DEV_LATE_N,
    EXPECTED_DEV_N,
    EXPECTED_ELIGIBLE_DAY_SHA256,
    EXPECTED_FOLD_BOUNDARY_SHA256,
    EXPECTED_LEADER_PAIRS,
    EXPECTED_LEADER_SET_SHA256,
    EXPECTED_PERMUTATION_SHA256,
    EXPECTED_PRECOMMIT_SHA256,
    EXPECTED_TARGET_N,
    EXPECTED_TARGET_SET_SHA256,
    EXPECTED_UNIVERSE_SHA256,
    FORBIDDEN_SUPERSEDED_PRECOMMIT_SHA256,
    PRECOMMIT_ID,
)
from research.causal_driver_pb1.cross_sectional_discovery.isolation import PRECOMMIT_V11_OUT
from research.causal_driver_pb1.identity.ids import sha256_obj
from research.causal_driver_pb1.phase2_precommit.sector_map import bind_sector_mapping
from research.causal_driver_pb1.phase2_precommit_v1_1.shuffle import generate_shuffle_permutations


def bind_precommit() -> dict[str, Any]:
    blockers: list[str] = []
    path = PRECOMMIT_V11_OUT / "report.json"
    doc = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    a = doc.get("answers") or {}
    ev = doc.get("evaluation") or {}
    if a.get("precommit_id") != PRECOMMIT_ID:
        blockers.append("PRECOMMIT_ID_MISMATCH")
    if a.get("precommit_sha256") != EXPECTED_PRECOMMIT_SHA256:
        blockers.append("PRECOMMIT_SHA_MISMATCH")
    if a.get("precommit_sha256") == FORBIDDEN_SUPERSEDED_PRECOMMIT_SHA256:
        blockers.append("SUPERSEDED_V1_PRECOMMIT_USED")
    if a.get("leader_set_sha256") != EXPECTED_LEADER_SET_SHA256:
        blockers.append("LEADER_SET_SHA_MISMATCH")
    if a.get("target_set_sha256") != EXPECTED_TARGET_SET_SHA256:
        blockers.append("TARGET_SET_SHA_MISMATCH")
    if a.get("eligible_day_sha256") != EXPECTED_ELIGIBLE_DAY_SHA256:
        blockers.append("ELIGIBLE_DAY_SHA_MISMATCH")
    if a.get("fold_boundary_sha256") != EXPECTED_FOLD_BOUNDARY_SHA256:
        blockers.append("FOLD_SHA_MISMATCH")
    if a.get("permutation_sha256") != EXPECTED_PERMUTATION_SHA256:
        blockers.append("PERMUTATION_SHA_MISMATCH")
    if a.get("universe105_sha256") != EXPECTED_UNIVERSE_SHA256:
        blockers.append("UNIVERSE105_SHA_MISMATCH")
    sectors = bind_sector_mapping()
    if sectors.get("universe105_sha256") != EXPECTED_UNIVERSE_SHA256:
        blockers.append("UNIVERSE105_REBIND_MISMATCH")
    leaders = ev.get("leaders") or {}
    frozen = list(leaders.get("frozen_leaders") or [])
    pairs = tuple((str(r.get("sector_id")), str(r.get("leader_symbol"))) for r in frozen)
    if pairs != EXPECTED_LEADER_PAIRS:
        blockers.append("LEADER_PAIRS_CHANGED")
    targets = list(leaders.get("target_symbols") or [])
    if len(targets) != EXPECTED_TARGET_N:
        blockers.append("TARGET_N_MISMATCH")
    used = {r["leader_symbol"] for r in frozen}
    if not used.isdisjoint(set(targets)):
        blockers.append("LEADER_TARGET_NOT_DISJOINT")
    days = ev.get("days") or {}
    eligible = list(days.get("eligible_dates") or [])
    folds = days.get("folds") or {}
    if sha256_obj(eligible) != EXPECTED_ELIGIBLE_DAY_SHA256:
        blockers.append("ELIGIBLE_DAY_REHASH_MISMATCH")
    if folds.get("fold_boundary_sha256") != EXPECTED_FOLD_BOUNDARY_SHA256:
        blockers.append("FOLD_REHASH_MISMATCH")
    sh = generate_shuffle_permutations(eligible)
    if sh.get("permutation_sha256") != EXPECTED_PERMUTATION_SHA256:
        blockers.append("SHUFFLE_REHASH_MISMATCH")
    dev = list(folds.get("development_dates") or [])
    c1 = list(folds.get("c1_dates") or [])
    bdev = folds.get("development_fold_boundaries") or {}
    bc1 = folds.get("c1_fold_boundaries") or {}
    if len(dev) != EXPECTED_DEV_N or len(c1) != EXPECTED_C1_N:
        blockers.append("ELIGIBLE_N_MISMATCH")
    if (bdev.get("DEV_EARLY") or {}).get("n") != EXPECTED_DEV_EARLY_N:
        blockers.append("DEV_EARLY_N_MISMATCH")
    if (bdev.get("DEV_LATE") or {}).get("n") != EXPECTED_DEV_LATE_N:
        blockers.append("DEV_LATE_N_MISMATCH")
    if any((bc1.get(k) or {}).get("n") != EXPECTED_C1_FOLD_N for k in ("C1_EARLY", "C1_MIDDLE", "C1_LATE")):
        blockers.append("C1_FOLD_N_MISMATCH")
    listing = {str(r["symbol"]): r.get("listing_start") for r in (leaders.get("eligibility_rows") or [])}
    return {
        "pass": not blockers,
        "blockers": blockers,
        "precommit_id": a.get("precommit_id"),
        "precommit_sha256": a.get("precommit_sha256"),
        "sectors": sectors,
        "frozen_leaders": frozen,
        "leader_symbols": [r["leader_symbol"] for r in frozen],
        "target_symbols": targets,
        "listing_start": listing,
        "eligible_dates": eligible,
        "development_dates": dev,
        "c1_dates": c1,
        "folds": folds,
        "shuffle": {k: v for k, v in sh.items() if k != "maps"},
        "leader_set_sha256": EXPECTED_LEADER_SET_SHA256,
        "target_set_sha256": EXPECTED_TARGET_SET_SHA256,
        "eligible_day_sha256": EXPECTED_ELIGIBLE_DAY_SHA256,
        "fold_boundary_sha256": EXPECTED_FOLD_BOUNDARY_SHA256,
        "permutation_sha256": EXPECTED_PERMUTATION_SHA256,
        "universe105_sha256": EXPECTED_UNIVERSE_SHA256,
    }
