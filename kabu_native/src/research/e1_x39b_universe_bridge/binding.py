"""Universe binding + prospective precommit artifacts (PASS only)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from . import (
    MODEL_ARTIFACT_SHA,
    PRECOMMIT_SHA,
    UNIVERSE_CONTRACT,
    V1R_SHA,
)

NATIVE = Path(__file__).resolve().parents[3]
X37 = NATIVE / "results" / "research" / "e1_x37_prospective"


def write_universe_binding(out: Path, *, warmup_semantic: dict, bridge_run_id: str) -> dict[str, Any]:
    body = {
        "manifest_id": "V1R_OPERATIONAL_UNIVERSE_BINDING_V1",
        "kind": "operational_universe_binding_not_strategy",
        "parent_v1r_sha": V1R_SHA,
        "model_artifact_sha": MODEL_ARTIFACT_SHA,
        "universe_contract": UNIVERSE_CONTRACT,
        "same_day_only": True,
        "all16_anchors_same_membership": True,
        "refresh_ignored_for_v1r_membership": True,
        "fail_closed_missing_am": True,
        "no_previous_day_fallback": True,
        "missing_data_handling": (
            "Keep AM membership; per-anchor score=-inf / ANCHOR_DATA_UNAVAILABLE; "
            "no day-level deletion; no future backfill."
        ),
        "warmup_semantic": warmup_semantic,
        "bridge_run_id": bridge_run_id,
        "does_not_overwrite_v1r": True,
        "research_paper_only": True,
    }
    raw = json.dumps(body, sort_keys=True, default=str).encode()
    body["sha256"] = hashlib.sha256(raw).hexdigest()
    path = out / "V1R_OPERATIONAL_UNIVERSE_BINDING_V1.json"
    path.write_text(json.dumps(body, indent=2, default=str), encoding="utf-8")
    return {"path": str(path), "sha256": body["sha256"], "body": body}


def write_new_precommit(
    out: Path,
    *,
    universe_binding_sha: str,
    bridge_run_id: str,
) -> dict[str, Any]:
    """Create PROSPECTIVE_PRECOMMIT_V1R_U1.json; mark old as superseded copy — do not overwrite."""
    old_path = X37 / "PROSPECTIVE_PRECOMMIT_V1.json"
    old = json.loads(old_path.read_text(encoding="utf-8"))
    assert old.get("sha256") == PRECOMMIT_SHA

    # Save superseded marker alongside X39B outputs (never mutate X37 file)
    superseded = {
        "status": "SUPERSEDED_BEFORE_PROSPECTIVE_START",
        "original_path": str(old_path),
        "original_sha256": PRECOMMIT_SHA,
        "superseded_by": "PROSPECTIVE_PRECOMMIT_V1R_U1",
        "reason": "Universe binding DAY_FIXED_AM_RUNTIME_UNIVERSE_V1 attached before prospective start",
        "original_body_sha_verified": True,
        "prospective_evidence_days": 0,
        "opened_20260810": False,
    }
    (out / "PROSPECTIVE_PRECOMMIT_V1_SUPERSEDED_BEFORE_PROSPECTIVE_START.json").write_text(
        json.dumps(superseded, indent=2, default=str), encoding="utf-8"
    )

    # Preserve original checkpoints from old precommit (field name in X37 artifact)
    checkpoints = old.get("evaluation_checkpoints") or old.get("checkpoints") or {
        "early_diagnostic_days": 5,
        "primary_days": 10,
        "extended_days": 20,
    }

    body = {
        "manifest_id": "PROSPECTIVE_PRECOMMIT_V1R_U1",
        "parent_precommit_sha": PRECOMMIT_SHA,
        "parent_precommit_status": "SUPERSEDED_BEFORE_PROSPECTIVE_START",
        "v1r_sha": V1R_SHA,
        "model_artifact_sha": MODEL_ARTIFACT_SHA,
        "universe_binding_sha": universe_binding_sha,
        "universe_contract": UNIVERSE_CONTRACT,
        "entry_sha": old.get("entry_sha"),
        "exit_sha": old.get("exit_sha"),
        "execution_sha": old.get("execution_sha"),
        "anchor_sha": old.get("anchor_sha"),
        "position_cap": (old.get("capacity") or {}).get("position_cap", 5),
        "wait_sec": (old.get("capacity") or {}).get("wait_sec", 1.0),
        "evaluation_checkpoints": checkpoints,
        "bridge_run_id": bridge_run_id,
        "prospective_evidence_days": 0,
        "opened_20260810": False,
        "does_not_overwrite_old_precommit_file": True,
        "research_paper_only": True,
    }
    raw = json.dumps(body, sort_keys=True, default=str).encode()
    body["sha256"] = hashlib.sha256(raw).hexdigest()
    path = out / "PROSPECTIVE_PRECOMMIT_V1R_U1.json"
    path.write_text(json.dumps(body, indent=2, default=str), encoding="utf-8")

    # Verify old file untouched
    old_after = json.loads(old_path.read_text(encoding="utf-8"))
    assert old_after.get("sha256") == PRECOMMIT_SHA

    return {"path": str(path), "sha256": body["sha256"], "old_precommit_unchanged": True}
