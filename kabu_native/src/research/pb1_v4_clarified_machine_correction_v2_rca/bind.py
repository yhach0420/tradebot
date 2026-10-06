"""Bind frozen Correction V2 + spec. Read-only. Hash change = FAIL."""
from __future__ import annotations

import json
from typing import Any

from research.pb1_v3_2_opening_drive_and_reacceleration_semantics.bind import bind_prior as bind_v32
from research.pb1_v4_clarified_machine_correction_v2.definitions import machine_sha256 as correction_v2_sha256
from research.pb1_v4_clarified_machine_correction_v2_rca import (
    EXPECTED_CORRECTION_V2_SHA256,
    EXPECTED_PARENT_MACHINE_SHA256,
    EXPECTED_SPEC_SHA256,
    PARENT_CLARIFIED_VERDICT,
    PARENT_PARITY_VERDICT,
    PARENT_READY_VERDICT,
)
from research.pb1_v4_clarified_machine_correction_v2_rca.isolation import (
    CLARIFIED_SPEC_OUT,
    CORRECTED_OUT,
    CORRECTION_OUT,
    LEAKED_OUT,
    PARENT_MACHINE_OUT,
    PARITY_AUDIT_OUT,
    READY_SPEC_OUT,
)
from research.pb1_v4_clarified_machine_implementation.definitions import machine_sha256 as parent_machine_sha256
from research.pb1_v4_implementation_correction import V4_LEGACY_LEAKED_IMPLEMENTATION
from research.pb1_v4_implementation_correction.definitions import machine_sha256 as corrected_machine_sha256
from research.pb1_v4_implementation_correction_rca import V4_CORRECTED_MACHINE_SHA256
from research.pb1_v4_machine_implementation.definitions import machine_sha256 as leaked_machine_sha256
from research.pb1_v4_semantic_spec_clarification.spec import spec_sha256


def _load_json(path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def bind_prior() -> dict[str, Any]:
    v32 = bind_v32()
    ready = _load_json(READY_SPEC_OUT / "report.json")
    clarified = _load_json(CLARIFIED_SPEC_OUT / "report.json")
    corr_v2 = _load_json(CORRECTION_OUT / "report.json")
    parent_rep = _load_json(PARENT_MACHINE_OUT / "report.json")
    corr = _load_json(CORRECTED_OUT / "report.json")
    leaked_rep = _load_json(LEAKED_OUT / "report.json")
    parity = _load_json(PARITY_AUDIT_OUT / "report.json")
    live_spec = spec_sha256()
    live_v2 = correction_v2_sha256()
    live_p = parent_machine_sha256()
    live_c = corrected_machine_sha256()
    live_l = leaked_machine_sha256()
    report_v2 = str(corr_v2.get("PB1_V4_CLARIFIED_MACHINE_CORRECTION_V2_SHA256") or "")
    report_spec = str((clarified.get("hashes") or {}).get("SPEC_SHA256") or "")
    report_c = str((corr.get("hashes") or {}).get("V4_CORRECTED_MACHINE_SHA256") or "")
    ready_v = str((ready.get("decision") or {}).get("VERDICT") or "")
    clar_v = str((clarified.get("decision") or {}).get("VERDICT") or "")
    v2_v = str((corr_v2.get("decision") or {}).get("VERDICT") or "")
    parity_v = str((parity.get("decision") or {}).get("VERDICT") or "")
    report_l = str((leaked_rep.get("hashes") or {}).get("V4_MACHINE_SHA256") or leaked_rep.get("V4_MACHINE_SHA256") or "")
    ok = (
        bool(v32.get("ok"))
        and live_spec == EXPECTED_SPEC_SHA256 == report_spec
        and live_v2 == EXPECTED_CORRECTION_V2_SHA256 == report_v2
        and live_p == EXPECTED_PARENT_MACHINE_SHA256
        and live_c == V4_CORRECTED_MACHINE_SHA256 == report_c
        and live_l == V4_LEGACY_LEAKED_IMPLEMENTATION
        and ready_v == PARENT_READY_VERDICT
        and clar_v == PARENT_CLARIFIED_VERDICT
        and v2_v == "PB1_V4_CLARIFIED_MACHINE_CORRECTION_V2_IMPLEMENTED_V1"
        and parity_v == PARENT_PARITY_VERDICT
        and ready.get("v4_machine_implemented") is False
        and clarified.get("machine_implemented") is False
        and corr_v2.get("future_economic_outcome_used") is False
        and (report_l == V4_LEGACY_LEAKED_IMPLEMENTATION or not report_l)
    )
    return {
        **{k: v for k, v in v32.items() if k != "by_symbol"},
        "ok": ok,
        "symbols": list(v32.get("symbols") or []),
        "by_symbol": dict(v32.get("by_symbol") or {}),
        "live_spec_sha256": live_spec,
        "live_correction_v2_sha256": live_v2,
        "correction_v2_sha_unchanged": live_v2 == EXPECTED_CORRECTION_V2_SHA256 == report_v2,
        "parent_machine_sha_unchanged": live_p == EXPECTED_PARENT_MACHINE_SHA256,
        "spec_sha_unchanged": live_spec == EXPECTED_SPEC_SHA256,
        "corrected_preserved": live_c == V4_CORRECTED_MACHINE_SHA256,
        "leaked_preserved": live_l == V4_LEGACY_LEAKED_IMPLEMENTATION,
        "parent_ready_verdict": ready_v,
        "parent_clarified_verdict": clar_v,
        "correction_v2_verdict": v2_v,
        "parent_parity_verdict": parity_v,
        "reason": None if ok else "frozen_hash_or_parent_mismatch",
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "any_code_changed": False,
        "DETECTOR_SHA256": v32.get("DETECTOR_SHA256"),
        "STATE_MACHINE_SHA256": v32.get("STATE_MACHINE_SHA256"),
    }
