"""Bind frozen V4 spec + V3.2. Do not mutate parents."""
from __future__ import annotations

import json
from typing import Any

from research.pb1_opening_range_continuation_face_valid_v2.definitions import machine_sha256 as v2_machine_sha256
from research.pb1_playbook_redesign_v3.definitions import machine_sha256 as v3_machine_sha256
from research.pb1_v3_1_face_validity_fix.definitions import machine_sha256 as v31_machine_sha256
from research.pb1_v3_2_opening_drive_and_reacceleration_semantics.bind import bind_prior as bind_v32
from research.pb1_v3_2_opening_drive_and_reacceleration_semantics.definitions import machine_sha256 as v32_machine_sha256
from research.pb1_v4_machine_implementation import PARENT_SPEC_VERDICT, PARENT_V2_SHA, PARENT_V3_SHA, PARENT_V31_SHA
from research.pb1_v4_machine_implementation.isolation import RCA_OUT, SPEC_OUT, V32_OUT
from research.pb1_v4_opening_drive_location_reaccel_spec import PARENT_V32_SHA
from research.pb1_v4_opening_drive_location_reaccel_spec.bind import bind_prior as bind_spec


def _load_json(path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def bind_prior() -> dict[str, Any]:
    v32 = bind_v32()
    spec = bind_spec()
    spec_rep = _load_json(SPEC_OUT / "report.json")
    v32_rep = _load_json(V32_OUT / "report.json")
    rca_rep = _load_json(RCA_OUT / "report.json")
    live_v32 = v32_machine_sha256()
    spec_v = str((spec_rep.get("decision") or {}).get("VERDICT") or "")
    ok = (
        bool(v32.get("ok"))
        and bool(spec.get("ok"))
        and spec_v == PARENT_SPEC_VERDICT
        and live_v32 == PARENT_V32_SHA
        and str((v32_rep.get("hashes") or {}).get("PLAYBOOK_MACHINE_SHA256") or "") == PARENT_V32_SHA
        and v31_machine_sha256() == PARENT_V31_SHA
        and v3_machine_sha256() == PARENT_V3_SHA
        and v2_machine_sha256() == PARENT_V2_SHA
        and rca_rep.get("future_outcome_used") is False
        and spec_rep.get("v4_machine_implemented") is False
    )
    return {
        **{k: v for k, v in v32.items() if k != "by_symbol"},
        "ok": ok,
        "symbols": list(v32.get("symbols") or []),
        "by_symbol": dict(v32.get("by_symbol") or {}),
        "exemplars": dict(spec.get("exemplars") or {}),
        "spec_bind": {k: spec[k] for k in ("ok", "parent_v32_sha", "live_v32_machine_sha256", "parent_rca_n", "parent_clear_n") if k in spec},
        "parent_spec_verdict": spec_v,
        "live_v32_machine_sha256": live_v32,
        "live_v31_machine_sha256": v31_machine_sha256(),
        "live_v3_machine_sha256": v3_machine_sha256(),
        "live_v2_machine_sha256": v2_machine_sha256(),
        "reason": None if ok else "spec_v32_rca_or_parent_hash_mismatch",
        "v32_mutated": False,
        "v31_mutated": False,
        "v3_mutated": False,
        "v2_mutated": False,
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "contaminated_symbol_dates_n": 352,
    }
