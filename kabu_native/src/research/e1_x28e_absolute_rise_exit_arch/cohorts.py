"""Frozen Specific49 / Family118 from X29 V2."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from research.e1_x28d_additional_stress.masks import (
    apply_mask,
    freeze_candidate_specs,
    verify_historical_mask_shas,
)

from . import (
    EXPECTED_FAMILY,
    EXPECTED_SPECIFIC,
    EXPECTED_TOTAL,
    X29_V2_PRECOMMIT_SHA,
)

NATIVE = Path(__file__).resolve().parents[3]
X29_DIR = NATIVE / "results" / "research" / "e1_x29_prospective"


def load_cohorts() -> dict[str, Any]:
    pc = json.loads((X29_DIR / "precommit_v2.json").read_text(encoding="utf-8"))
    if pc.get("precommit_sha") != X29_V2_PRECOMMIT_SHA:
        raise RuntimeError(f"X29 V2 sha mismatch got={pc.get('precommit_sha')}")
    specific = pc["specific_registry"]
    family = pc["family_registry"]
    if len(specific) != EXPECTED_SPECIFIC or len(family) != EXPECTED_FAMILY:
        raise RuntimeError("cohort size")
    ids_s = {r["candidate_id"] for r in specific}
    ids_f = {r["candidate_id"] for r in family}
    if ids_s & ids_f:
        raise RuntimeError("overlap")
    if len(ids_s) + len(ids_f) != EXPECTED_TOTAL:
        raise RuntimeError("total")
    return {
        "precommit": pc,
        "specific": specific,
        "family": family,
        "specific_ids": sorted(ids_s),
        "family_ids": sorted(ids_f),
    }


def build_masks(
    rows: list[dict[str, Any]],
    cohorts: dict[str, Any],
) -> tuple[dict[str, dict], dict[str, np.ndarray]]:
    _, cand_by = freeze_candidate_specs()
    # SHA verify on historical-only would need hist rows; verify against V2 registry
    # using apply on current rows is not SHA-stable; verify via freeze on checked pop:
    hist_check = verify_historical_mask_shas(
        {"specific": cohorts["specific"], "family": cohorts["family"]},
        cand_by,
    )
    if not hist_check["ok"]:
        raise RuntimeError(f"mask sha: {hist_check}")
    masks = {}
    for reg in cohorts["specific"] + cohorts["family"]:
        cid = reg["candidate_id"]
        masks[cid] = apply_mask(rows, cid, cand_by)
    return cand_by, masks
