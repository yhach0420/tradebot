"""Frozen Discovery masks applied to stress-day population (no retune)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from research.e1_x21_entry_factory_exit_benchmark.factory import decision_mask
from research.e1_x22_actual_exit_factory.registry import (
    build_alias_groups,
    load_population_checked,
    mask_sha256,
    rebuild_candidates_and_masks,
)

from . import (
    EXPECTED_EMERGENT,
    EXPECTED_FAMILY,
    EXPECTED_SPECIFIC,
    EXPECTED_SURVIVOR,
    EXPECTED_TOTAL,
    OLD_X29_PRECOMMIT_SHA,
)

NATIVE = Path(__file__).resolve().parents[3]
X29_DIR = NATIVE / "results" / "research" / "e1_x29_prospective"


def load_x29_cohorts() -> dict[str, Any]:
    pc = json.loads((X29_DIR / "precommit.json").read_text(encoding="utf-8"))
    if pc.get("precommit_sha") != OLD_X29_PRECOMMIT_SHA:
        raise RuntimeError(f"old X29 sha mismatch got={pc.get('precommit_sha')}")
    specific = pc["specific_registry"]
    family = pc["family_registry"]
    if len(specific) != EXPECTED_SPECIFIC or len(family) != EXPECTED_FAMILY:
        raise RuntimeError("cohort size")
    surv = sum(1 for r in specific if r.get("historical_origin_tag") == "REFERENCE_SURVIVOR")
    emerg = sum(1 for r in specific if r.get("historical_origin_tag") == "EXECUTION_EMERGENT")
    if surv != EXPECTED_SURVIVOR or emerg != EXPECTED_EMERGENT:
        raise RuntimeError("survivor/emergent counts")
    ids_s = {r["candidate_id"] for r in specific}
    ids_f = {r["candidate_id"] for r in family}
    if ids_s & ids_f:
        raise RuntimeError("overlap nonzero")
    if len(ids_s) + len(ids_f) != EXPECTED_TOTAL:
        raise RuntimeError("total != 167")
    return {
        "precommit": pc,
        "specific": specific,
        "family": family,
        "specific_ids": sorted(ids_s),
        "family_ids": sorted(ids_f),
    }


def freeze_candidate_specs() -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Rebuild from historical population only — Discovery thresholds frozen."""
    rows = load_population_checked()
    cands, masks = rebuild_candidates_and_masks(rows)
    alias_rows, _, unique_masks = build_alias_groups(cands, masks)
    by_id = {c["candidate_id"]: c for c in cands}
    return cands, by_id


def apply_mask(
    stress_rows: list[dict[str, Any]],
    cand_id: str,
    cand_by: dict[str, dict[str, Any]],
) -> np.ndarray:
    c = cand_by[cand_id]
    if int(c.get("n_features") or 1) == 1:
        return decision_mask(stress_rows, c)
    parents = c.get("parents") or []
    m = np.ones(len(stress_rows), dtype=bool)
    for pid in parents:
        m &= decision_mask(stress_rows, cand_by[pid])
    return m


def verify_historical_mask_shas(
    cohorts: dict[str, Any],
    cand_by: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Confirm frozen Discovery mask SHAs match X29 registry (on historical pop)."""
    rows = load_population_checked()
    mismatches = []
    for reg in cohorts["specific"] + cohorts["family"]:
        cid = reg["candidate_id"]
        m = apply_mask(rows, cid, cand_by)
        sha = mask_sha256(m)
        exp = reg.get("decision_mask_sha256")
        if exp and sha != exp:
            mismatches.append({"candidate_id": cid, "got": sha, "exp": exp})
    return {"ok": len(mismatches) == 0, "mismatch_n": len(mismatches), "mismatches": mismatches[:10]}
