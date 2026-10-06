"""Assemble V4 spec report. No machine. No event_n. No returns."""
from __future__ import annotations

from typing import Any

from research.pb1_v3_2_opening_drive_and_reacceleration_semantics.definitions import machine_sha256 as v32_machine_sha256
from research.pb1_v4_opening_drive_location_reaccel_spec import (
    CASE_BIND,
    CASE_READY,
    NEXT_BIND,
    NEXT_MACHINE,
    PARENT_V32_SHA,
)
from research.pb1_v4_opening_drive_location_reaccel_spec.exemplars import build_exemplar_maps
from research.pb1_v4_opening_drive_location_reaccel_spec.specification import specification


def decide(bind_ok: bool) -> dict[str, Any]:
    live = v32_machine_sha256()
    identity = live == PARENT_V32_SHA
    if not bind_ok:
        return {
            "VERDICT": CASE_BIND,
            "NEXT": NEXT_BIND,
            "v32_unchanged": identity,
            "v4_machine_implemented": False,
        }
    return {
        "VERDICT": CASE_READY,
        "NEXT": NEXT_MACHINE,
        "v32_unchanged": identity,
        "v4_machine_implemented": False,
        "any_rule_changed": False,
        "any_threshold_optimized": False,
        "event_n_calculated": False,
        "returns_calculated": False,
        "PRIMARY_CAUSE_inherited": "OPENING_DRIVE",
        "SECONDARY_CAUSE_inherited": "LOCATION+REACCELERATION",
        "CONTRIBUTING_CAUSE_inherited": "STOCK_SELECTION+HTF_CONTEXT+THESIS_LOST",
    }


def build_report_body(bind: dict[str, Any]) -> dict[str, Any]:
    spec = specification()
    ex = build_exemplar_maps(bind)
    live = v32_machine_sha256()
    decision = decide(bool(bind.get("ok")))
    return {
        "MACHINE_SHA256": live,
        "PARENT_V32_SHA": PARENT_V32_SHA,
        "v32_unchanged": live == PARENT_V32_SHA,
        "specification": spec,
        "exemplars": ex,
        "decision": decision,
        "future_outcome_used": False,
        "v4_machine_implemented": False,
        "event_n_calculated": False,
        "economic_test": False,
    }
