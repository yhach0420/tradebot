"""Bind frozen V3.2. Do not mutate parents. Do not re-walk Discovery."""
from __future__ import annotations

import json
from typing import Any

from research.pb1_opening_range_continuation_face_valid_v2.definitions import machine_sha256 as v2_machine_sha256
from research.pb1_playbook_redesign_v3.definitions import machine_sha256 as v3_machine_sha256
from research.pb1_v3_1_face_validity_fix.definitions import machine_sha256 as v31_machine_sha256
from research.pb1_v3_2_discovery_unseen_face_verify import (
    PARENT_V32_SETUP_N,
    PARENT_V32_SHA,
    PARENT_V32_VERDICT,
)
from research.pb1_v3_2_discovery_unseen_face_verify.isolation import V32_CACHE, V32_OUT
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
    live = v32_machine_sha256()
    v32_sha = str((v32.get("hashes") or {}).get("PLAYBOOK_MACHINE_SHA256") or v32.get("MACHINE_SHA256") or "")
    v32_v = str((v32.get("decision") or {}).get("VERDICT") or "")
    ok = (
        bool(base.get("ok"))
        and live == PARENT_V32_SHA
        and v32_sha == PARENT_V32_SHA
        and v32_v == PARENT_V32_VERDICT
        and int(v32.get("setup_n") or 0) == int(PARENT_V32_SETUP_N)
        and v31_machine_sha256() == PARENT_V31_SHA
        and v3_machine_sha256() == PARENT_V3_SHA
        and v2_machine_sha256() == PARENT_V2_SHA
        and (V32_CACHE / "walked.json").is_file()
    )
    return {
        **base,
        "ok": ok,
        "parent_v32_verdict": v32_v,
        "parent_v32_sha": v32_sha,
        "live_v32_machine_sha256": live,
        "parent_v32_setup_n": int(v32.get("setup_n") or 0),
        "v32_reported_unseen_n": int(((v32.get("unseen") or {}).get("discovery_unseen_event_n")) or 0),
        "v32_same_bar_entry_n": int(v32.get("same_bar_entry_n") or 0),
        "v32_failed_push_leak_n": int(v32.get("failed_push_leak_n") or 0),
        "v32_risk_invalid_n": int(v32.get("risk_invalid_n") or 0),
        "reason": None if ok else "v32_bind_sha_verdict_or_cache_mismatch",
        "v32_mutated": False,
    }
