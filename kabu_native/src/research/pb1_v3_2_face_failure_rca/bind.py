"""Bind frozen V3.2 + failed unseen face-verify. Do not mutate parents."""
from __future__ import annotations

import json
from typing import Any

from research.pb1_opening_range_continuation_face_valid_v2.definitions import machine_sha256 as v2_machine_sha256
from research.pb1_playbook_redesign_v3.definitions import machine_sha256 as v3_machine_sha256
from research.pb1_v3_1_face_validity_fix.definitions import machine_sha256 as v31_machine_sha256
from research.pb1_v3_2_face_failure_rca import (
    MANIFEST_FINAL_N,
    PARENT_UNSEEN_N,
    PARENT_UNSEEN_VERDICT,
    PARENT_V3_FACE_N,
    PARENT_V32_SHA,
    SEMANTIC_RCA_N,
)
from research.pb1_v3_2_face_failure_rca.isolation import UNSEEN_OUT, V3_CACHE, V3_OUT, V32_CACHE, V32_OUT
from research.pb1_v3_2_opening_drive_and_reacceleration_semantics import PARENT_V2_SHA, PARENT_V3_SHA, PARENT_V31_SHA
from research.pb1_v3_2_opening_drive_and_reacceleration_semantics.bind import bind_prior as bind_v32
from research.pb1_v3_2_opening_drive_and_reacceleration_semantics.definitions import machine_sha256 as v32_machine_sha256


def _load_json(path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def bind_prior() -> dict[str, Any]:
    base = bind_v32()
    v32 = _load_json(V32_OUT / "report.json")
    unseen = _load_json(UNSEEN_OUT / "report.json")
    v3 = _load_json(V3_OUT / "report.json")
    live = v32_machine_sha256()
    v32_sha = str((v32.get("hashes") or {}).get("PLAYBOOK_MACHINE_SHA256") or v32.get("MACHINE_SHA256") or "")
    unseen_v = str((unseen.get("decision") or {}).get("VERDICT") or "")
    man = dict(unseen.get("manifest_update") or {})
    v3_n = int((v3.get("human") or {}).get("reviewed_n") or 0)
    unseen_n = int((unseen.get("human") or {}).get("reviewed_n") or 0)
    ok = (
        bool(base.get("ok"))
        and live == PARENT_V32_SHA
        and v32_sha == PARENT_V32_SHA
        and unseen_v == PARENT_UNSEEN_VERDICT
        and unseen_n == int(PARENT_UNSEEN_N)
        and v3_n == int(PARENT_V3_FACE_N)
        and int(man.get("new_manifest_n") or 0) == int(MANIFEST_FINAL_N)
        and v31_machine_sha256() == PARENT_V31_SHA
        and v3_machine_sha256() == PARENT_V3_SHA
        and v2_machine_sha256() == PARENT_V2_SHA
        and (V32_CACHE / "walked.json").is_file()
        and (V3_CACHE / "walked.json").is_file()
        and v3_n + unseen_n == int(SEMANTIC_RCA_N)
    )
    return {
        **base,
        "ok": ok,
        "parent_v32_sha": v32_sha,
        "live_v32_machine_sha256": live,
        "parent_v32_setup_n": int(v32.get("setup_n") or 0),
        "parent_unseen_verdict": unseen_v,
        "parent_unseen_reviewed_n": unseen_n,
        "parent_v3_reviewed_n": v3_n,
        "semantic_rca_n": v3_n + unseen_n,
        "manifest_final_n": int(man.get("new_manifest_n") or 0),
        "v3_human": dict(v3.get("human") or {}),
        "unseen_human": dict(unseen.get("human") or {}),
        "reason": None if ok else "v32_unseen_v3_bind_mismatch",
        "v32_mutated": False,
        "holdout_consumed": True,
    }
