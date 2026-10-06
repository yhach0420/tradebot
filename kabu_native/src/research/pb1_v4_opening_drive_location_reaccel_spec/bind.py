"""Bind parent RCA + frozen V3.2. Do not mutate. Do not walk V4."""
from __future__ import annotations

import json
from typing import Any

from research.pb1_opening_range_continuation_face_valid_v2.definitions import machine_sha256 as v2_machine_sha256
from research.pb1_playbook_redesign_v3.definitions import machine_sha256 as v3_machine_sha256
from research.pb1_v3_1_face_validity_fix.definitions import machine_sha256 as v31_machine_sha256
from research.pb1_v3_2_face_failure_rca import PARENT_V32_SHA, SEMANTIC_RCA_N
from research.pb1_v3_2_opening_drive_and_reacceleration_semantics import PARENT_V2_SHA, PARENT_V3_SHA, PARENT_V31_SHA
from research.pb1_v3_2_opening_drive_and_reacceleration_semantics.definitions import machine_sha256 as v32_machine_sha256
from research.pb1_v4_opening_drive_location_reaccel_spec import PARENT_CLEAR_N, PARENT_RCA_VERDICT
from research.pb1_v4_opening_drive_location_reaccel_spec.isolation import RCA_OUT, V32_OUT


def _load_json(path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def bind_prior() -> dict[str, Any]:
    rca = _load_json(RCA_OUT / "report.json")
    v32 = _load_json(V32_OUT / "report.json")
    live = v32_machine_sha256()
    v32_sha = str((v32.get("hashes") or {}).get("PLAYBOOK_MACHINE_SHA256") or v32.get("MACHINE_SHA256") or "")
    rca_v = str((rca.get("decision") or {}).get("VERDICT") or "")
    rca_n = int(rca.get("semantic_rca_n") or 0)
    human_clear = int(((rca.get("exemplars") or {}).get("positive_n")) or 0)
    ok = (
        rca_v == PARENT_RCA_VERDICT
        and live == PARENT_V32_SHA
        and v32_sha == PARENT_V32_SHA
        and rca_n == int(SEMANTIC_RCA_N)
        and human_clear == int(PARENT_CLEAR_N)
        and v31_machine_sha256() == PARENT_V31_SHA
        and v3_machine_sha256() == PARENT_V3_SHA
        and v2_machine_sha256() == PARENT_V2_SHA
        and bool(rca.get("v32_unchanged"))
        and rca.get("future_outcome_used") is False
    )
    return {
        "ok": ok,
        "parent_rca_verdict": rca_v,
        "parent_rca_n": rca_n,
        "parent_clear_n": human_clear,
        "parent_v32_sha": v32_sha,
        "live_v32_machine_sha256": live,
        "rca": {
            "PRIMARY_CAUSE": (rca.get("decision") or {}).get("PRIMARY_CAUSE"),
            "SECONDARY_CAUSE": (rca.get("decision") or {}).get("SECONDARY_CAUSE"),
            "CONTRIBUTING_CAUSE": (rca.get("decision") or {}).get("CONTRIBUTING_CAUSE"),
            "pb1_salvageable": (rca.get("decision") or {}).get("pb1_salvageable"),
        },
        "exemplars": dict(rca.get("exemplars") or {}),
        "reason": None if ok else "rca_verdict_n_or_v32_sha_mismatch",
        "v32_mutated": False,
        "v4_machine_not_run": True,
    }
