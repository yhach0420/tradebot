"""Bind frozen V4 SHA + spec. Do not mutate. Do not re-walk."""
from __future__ import annotations

import json
from typing import Any

from research.pb1_v3_2_opening_drive_and_reacceleration_semantics.definitions import machine_sha256 as v32_machine_sha256
from research.pb1_v4_implementation_integrity_and_spec_parity_audit import FROZEN_V4_SHA, PARENT_SPEC_VERDICT
from research.pb1_v4_implementation_integrity_and_spec_parity_audit.isolation import SPEC_OUT, V4_CACHE, V4_OUT
from research.pb1_v4_machine_implementation import PARENT_V32_SHA
from research.pb1_v4_machine_implementation.definitions import machine_sha256 as v4_machine_sha256


def _load_json(path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def bind_prior() -> dict[str, Any]:
    live = v4_machine_sha256()
    v4 = _load_json(V4_OUT / "report.json")
    spec = _load_json(SPEC_OUT / "report.json")
    walked_path = V4_CACHE / "walked.json"
    v4_sha_rep = str((v4.get("hashes") or {}).get("V4_MACHINE_SHA256") or v4.get("V4_MACHINE_SHA256") or "")
    spec_v = str((spec.get("decision") or {}).get("VERDICT") or "")
    ok = (
        live == FROZEN_V4_SHA
        and v4_sha_rep == FROZEN_V4_SHA
        and spec_v == PARENT_SPEC_VERDICT
        and v32_machine_sha256() == PARENT_V32_SHA
        and walked_path.is_file()
        and int(v4.get("same_bar_entry_n") or 0) == 0
        and v4.get("future_economic_outcome_used") is False
    )
    return {
        "ok": ok,
        "live_v4_machine_sha256": live,
        "frozen_v4_sha": FROZEN_V4_SHA,
        "report_v4_sha": v4_sha_rep,
        "parent_spec_verdict": spec_v,
        "walked_path": str(walked_path),
        "walked_ok": walked_path.is_file(),
        "v4_setup_n": int(v4.get("setup_n") or 0),
        "v4_e0_n": int(v4.get("e0_n") or 0),
        "v4_e1_n": int(v4.get("e1_n") or 0),
        "reason": None if ok else "v4_sha_spec_or_walk_cache_mismatch",
        "v4_mutated": False,
        "rule_changed": False,
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
    }
