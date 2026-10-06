"""Bind frozen hashes. Read-only. Fingerprint drift = FAIL."""
from __future__ import annotations

import json
from typing import Any

from research.pb1_v3_2_opening_drive_and_reacceleration_semantics.bind import bind_prior as bind_v32
from research.pb1_v4_clarified_machine_implementation.definitions import machine_sha256 as clarified_machine_sha256
from research.pb1_v4_clarified_machine_parity_rca import EXPECTED_MACHINE_SHA256, EXPECTED_SPEC_SHA256, PARENT_CLARIFIED_VERDICT, PARENT_READY_VERDICT
from research.pb1_v4_clarified_machine_parity_rca.isolation import AUDIT_OUT, CLARIFIED_SPEC_OUT, CORRECTED_OUT, LEAKED_OUT, MACHINE_OUT, READY_SPEC_OUT
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
    mach = _load_json(MACHINE_OUT / "report.json")
    corr = _load_json(CORRECTED_OUT / "report.json")
    audit = _load_json(AUDIT_OUT / "report.json")
    live_spec = spec_sha256()
    live_m = clarified_machine_sha256()
    live_c = corrected_machine_sha256()
    live_l = leaked_machine_sha256()
    report_m = str((mach.get("hashes") or {}).get("PB1_V4_CLARIFIED_MACHINE_SHA256") or "")
    report_spec = str((clarified.get("hashes") or {}).get("SPEC_SHA256") or "")
    report_c = str((corr.get("hashes") or {}).get("V4_CORRECTED_MACHINE_SHA256") or "")
    audit_v = str((audit.get("decision") or {}).get("VERDICT") or "")
    ok = (
        bool(v32.get("ok"))
        and live_spec == EXPECTED_SPEC_SHA256 == report_spec
        and live_m == EXPECTED_MACHINE_SHA256 == report_m
        and live_c == V4_CORRECTED_MACHINE_SHA256 == report_c
        and live_l == V4_LEGACY_LEAKED_IMPLEMENTATION
        and str((ready.get("decision") or {}).get("VERDICT") or "") == PARENT_READY_VERDICT
        and str((clarified.get("decision") or {}).get("VERDICT") or "") == PARENT_CLARIFIED_VERDICT
        and str((mach.get("decision") or {}).get("VERDICT") or "") == "PB1_V4_CLARIFIED_MACHINE_IMPLEMENTED_V1"
        and audit_v == "PB1_V4_CLARIFIED_MACHINE_SPEC_PARITY_FAIL_V1"
        and ready.get("v4_machine_implemented") is False
        and clarified.get("machine_implemented") is False
        and audit.get("any_code_changed") is False
        and audit.get("prospective_event_consumed") is False
        and audit.get("future_economic_outcome_used") is False
    )
    return {
        **{k: v for k, v in v32.items() if k != "by_symbol"},
        "ok": ok,
        "symbols": list(v32.get("symbols") or []),
        "by_symbol": dict(v32.get("by_symbol") or {}),
        "live_spec_sha256": live_spec,
        "live_machine_sha256": live_m,
        "report_machine_sha256": report_m,
        "EXPECTED_MACHINE_SHA256": EXPECTED_MACHINE_SHA256,
        "EXPECTED_SPEC_SHA256": EXPECTED_SPEC_SHA256,
        "live_corrected_sha": live_c,
        "live_leaked_sha": live_l,
        "machine_sha_unchanged": live_m == EXPECTED_MACHINE_SHA256 == report_m,
        "spec_sha_unchanged": live_spec == EXPECTED_SPEC_SHA256,
        "corrected_preserved": live_c == V4_CORRECTED_MACHINE_SHA256,
        "leaked_preserved": live_l == V4_LEGACY_LEAKED_IMPLEMENTATION,
        "parent_parity_verdict": audit_v,
        "reason": None if ok else "frozen_hash_or_parent_mismatch",
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "any_code_changed": False,
    }
