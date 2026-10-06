"""Bind parent READY_V1 spec + correction RCA + frozen corrected SHA. Read-only."""
from __future__ import annotations

import json
from typing import Any

from research.pb1_v4_implementation_correction import V4_LEGACY_LEAKED_IMPLEMENTATION
from research.pb1_v4_implementation_correction.definitions import machine_sha256 as corrected_machine_sha256
from research.pb1_v4_implementation_correction_rca import CASE_SPEC as PARENT_RCA_VERDICT
from research.pb1_v4_implementation_correction_rca import V4_CORRECTED_MACHINE_SHA256
from research.pb1_v4_machine_implementation.definitions import machine_sha256 as leaked_machine_sha256
from research.pb1_v4_opening_drive_location_reaccel_spec import CASE_READY as PARENT_SPEC_VERDICT
from research.pb1_v4_semantic_spec_clarification.isolation import CORRECTED_OUT, PARENT_SPEC_OUT, RCA_OUT


def _load_json(path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def bind_prior() -> dict[str, Any]:
    spec_rep = _load_json(PARENT_SPEC_OUT / "report.json")
    rca_rep = _load_json(RCA_OUT / "report.json")
    corr_rep = _load_json(CORRECTED_OUT / "report.json")
    live_c = corrected_machine_sha256()
    live_l = leaked_machine_sha256()
    spec_v = str((spec_rep.get("decision") or {}).get("VERDICT") or "")
    rca_v = str((rca_rep.get("decision") or {}).get("VERDICT") or "")
    report_c = str((corr_rep.get("hashes") or {}).get("V4_CORRECTED_MACHINE_SHA256") or corr_rep.get("V4_CORRECTED_MACHINE_SHA256") or "")
    ok = (
        spec_v == PARENT_SPEC_VERDICT
        and rca_v == PARENT_RCA_VERDICT
        and live_c == V4_CORRECTED_MACHINE_SHA256
        and report_c == V4_CORRECTED_MACHINE_SHA256
        and live_l == V4_LEGACY_LEAKED_IMPLEMENTATION
        and spec_rep.get("v4_machine_implemented") is False
        and rca_rep.get("any_rule_changed") is False
        and corr_rep.get("future_economic_outcome_used") is False
        and rca_rep.get("future_economic_outcome_used") is False
        and rca_rep.get("prospective_event_consumed") is False
    )
    return {
        "ok": ok,
        "parent_spec_verdict": spec_v,
        "parent_rca_verdict": rca_v,
        "live_corrected_sha": live_c,
        "report_corrected_sha": report_c,
        "V4_CORRECTED_MACHINE_SHA256": V4_CORRECTED_MACHINE_SHA256,
        "live_leaked_sha": live_l,
        "V4_LEGACY_LEAKED_IMPLEMENTATION": V4_LEGACY_LEAKED_IMPLEMENTATION,
        "corrected_sha_unchanged": live_c == V4_CORRECTED_MACHINE_SHA256 == report_c,
        "leaked_preserved": live_l == V4_LEGACY_LEAKED_IMPLEMENTATION,
        "parent_spec_source_sha256": str((spec_rep.get("hashes") or {}).get("SOURCE_SHA256") or ""),
        "reason": None if ok else "parent_spec_rca_or_corrected_sha_mismatch",
        "any_rule_changed": False,
        "machine_implemented": False,
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
    }
