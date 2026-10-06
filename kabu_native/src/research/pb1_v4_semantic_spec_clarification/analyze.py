"""Assemble clarified spec report. No machine. No event_n. No returns."""
from __future__ import annotations

from typing import Any

from research.pb1_v4_implementation_correction import V4_LEGACY_LEAKED_IMPLEMENTATION
from research.pb1_v4_implementation_correction.definitions import machine_sha256 as corrected_machine_sha256
from research.pb1_v4_implementation_correction_rca import V4_CORRECTED_MACHINE_SHA256
from research.pb1_v4_machine_implementation.definitions import machine_sha256 as leaked_machine_sha256
from research.pb1_v4_semantic_spec_clarification import CASE_BIND, CASE_READY, NEXT_BIND, NEXT_MACHINE
from research.pb1_v4_semantic_spec_clarification.exemplars import build_exemplar_maps
from research.pb1_v4_semantic_spec_clarification.spec import spec_sha256
from research.pb1_v4_semantic_spec_clarification.specification import specification


def decide(bind_ok: bool) -> dict[str, Any]:
    live_c = corrected_machine_sha256()
    live_l = leaked_machine_sha256()
    if not bind_ok:
        return {
            "VERDICT": CASE_BIND,
            "NEXT": NEXT_BIND,
            "machine_implemented": False,
            "corrected_sha_unchanged": live_c == V4_CORRECTED_MACHINE_SHA256,
        }
    return {
        "VERDICT": CASE_READY,
        "NEXT": NEXT_MACHINE,
        "machine_implemented": False,
        "v4_1_created": False,
        "any_rule_changed": False,
        "any_threshold_optimized": False,
        "event_n_calculated": False,
        "returns_calculated": False,
        "corrected_sha_unchanged": live_c == V4_CORRECTED_MACHINE_SHA256,
        "leaked_preserved": live_l == V4_LEGACY_LEAKED_IMPLEMENTATION,
        "parent_spec_preserved": True,
        "timeframe_interpretation": "B",
        "SPEC_SHA256": spec_sha256(),
    }


def build_report_body(bind: dict[str, Any]) -> dict[str, Any]:
    spec = specification()
    ex = build_exemplar_maps()
    decision = decide(bool(bind.get("ok")))
    return {
        "V4_CORRECTED_MACHINE_SHA256": V4_CORRECTED_MACHINE_SHA256,
        "V4_LEGACY_LEAKED_IMPLEMENTATION": V4_LEGACY_LEAKED_IMPLEMENTATION,
        "SPEC_SHA256": spec_sha256(),
        "specification": spec,
        "exemplars": ex,
        "decision": decision,
        "future_outcome_used": False,
        "prospective_event_consumed": False,
        "machine_implemented": False,
        "event_n_calculated": False,
        "economic_test": False,
        "any_rule_changed": False,
    }
