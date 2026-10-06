"""Bind frozen spec + leaked V4 SHA (immutable) + V3.2 Discovery panel. Do not mutate leaked files."""
from __future__ import annotations

import json
from typing import Any

from research.pb1_v3_2_opening_drive_and_reacceleration_semantics.bind import bind_prior as bind_v32
from research.pb1_v3_2_opening_drive_and_reacceleration_semantics.definitions import machine_sha256 as v32_machine_sha256
from research.pb1_v4_implementation_correction import PARENT_SPEC_VERDICT, PARENT_V32_SHA, V4_LEGACY_LEAKED_IMPLEMENTATION
from research.pb1_v4_implementation_correction.isolation import LEAKED_OUT, SPEC_OUT
from research.pb1_v4_machine_implementation.definitions import machine_sha256 as leaked_machine_sha256
from research.pb1_v4_opening_drive_location_reaccel_spec.bind import bind_prior as bind_spec


def _load_json(path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def bind_prior() -> dict[str, Any]:
    v32 = bind_v32()
    spec = bind_spec()
    spec_rep = _load_json(SPEC_OUT / "report.json")
    leaked_rep = _load_json(LEAKED_OUT / "report.json")
    live_leaked = leaked_machine_sha256()
    leaked_sha = str((leaked_rep.get("hashes") or {}).get("V4_MACHINE_SHA256") or leaked_rep.get("V4_MACHINE_SHA256") or "")
    spec_v = str((spec_rep.get("decision") or {}).get("VERDICT") or "")
    ok = (
        bool(v32.get("ok"))
        and bool(spec.get("ok"))
        and spec_v == PARENT_SPEC_VERDICT
        and live_leaked == V4_LEGACY_LEAKED_IMPLEMENTATION
        and leaked_sha == V4_LEGACY_LEAKED_IMPLEMENTATION
        and v32_machine_sha256() == PARENT_V32_SHA
        and spec_rep.get("v4_machine_implemented") is False
        and leaked_rep.get("future_economic_outcome_used") is False
    )
    return {
        **{k: v for k, v in v32.items() if k != "by_symbol"},
        "ok": ok,
        "symbols": list(v32.get("symbols") or []),
        "by_symbol": dict(v32.get("by_symbol") or {}),
        "exemplars": dict(spec.get("exemplars") or {}),
        "parent_spec_verdict": spec_v,
        "live_leaked_v4_sha": live_leaked,
        "report_leaked_v4_sha": leaked_sha,
        "V4_LEGACY_LEAKED_IMPLEMENTATION": V4_LEGACY_LEAKED_IMPLEMENTATION,
        "leaked_preserved": live_leaked == V4_LEGACY_LEAKED_IMPLEMENTATION == leaked_sha,
        "live_v32_machine_sha256": v32_machine_sha256(),
        "reason": None if ok else "spec_or_leaked_sha_mismatch",
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "contaminated_symbol_dates_n": 352,
        "v4_1_created": False,
        "spec_changed": False,
        "DETECTOR_SHA256": v32.get("DETECTOR_SHA256"),
        "STATE_MACHINE_SHA256": v32.get("STATE_MACHINE_SHA256"),
    }
