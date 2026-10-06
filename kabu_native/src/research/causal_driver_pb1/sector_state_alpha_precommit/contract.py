"""Frozen alpha-signal contract. Hash only after the runtime audit passes."""
from __future__ import annotations

from typing import Any

from research.causal_driver_pb1.identity.ids import sha256_obj
from research.causal_driver_pb1.sector_state_alpha_precommit import (
    CANDIDATE_LIST_SHA256,
    M1_TARGETS,
    M2_TARGETS,
    M3_TARGETS,
    PRECOMMIT_ID,
    VALIDATED_TRANSMISSION_SHA256,
)
from research.causal_driver_pb1.sector_state_alpha_precommit.bind import (
    GLOBAL_BOUNDARY_SHA,
    GLOBAL_Q,
    SECTOR_BOUNDARY_SHA,
    SECTOR_Q,
    THRESHOLD_ARTIFACT,
    THRESHOLD_SOURCE,
)


def contract(bound: dict[str, Any]) -> dict[str, Any]:
    ready = bool(bound.get("pass")) and bound.get("active_runtime_parent_set") == ["M3"]
    body = {
        "precommit_id": PRECOMMIT_ID,
        "validated_transmission_sha256": VALIDATED_TRANSMISSION_SHA256,
        "transmission_candidate_list_sha256": CANDIDATE_LIST_SHA256,
        "driver_substitution_used": False,
        "m1_runtime_binding_status": bound.get("m1_status"),
        "m2_runtime_binding_status": bound.get("m2_status"),
        "m3_runtime_binding_status": bound.get("m3_status"),
        "active_runtime_parent_set": bound.get("active_runtime_parent_set"),
        "m1_m2_role": "VALIDATED_BUT_NOT_RUNTIME_BOUND",
        "logical_families": {
            "GLOBAL_BREADTH_W1_UP": {"parents": ["M1", "M2"], "runtime_emits": False, "h1_support": "M1", "h3_support": "M2"},
            "SECTOR3650_BREADTH_W1_UP": {"parents": ["M3"], "runtime_emits": True},
        },
        "direction": "LONG",
        "targets": {"M1": list(M1_TARGETS), "M2": list(M2_TARGETS), "M3": list(M3_TARGETS)},
        "target_set_sha256": bound.get("target_shas"),
        "driver_identity_sha256": bound.get("driver_identity_sha256"),
        "thresholds": {
            "source": THRESHOLD_SOURCE,
            "artifact": THRESHOLD_ARTIFACT,
            "GLOBAL_105_w1": {**GLOBAL_Q, "boundary_sha256": GLOBAL_BOUNDARY_SHA, "shared_by": ["M1", "M2"]},
            "SECTOR_3650_w1": {**SECTOR_Q, "boundary_sha256": SECTOR_BOUNDARY_SHA, "shared_by": ["M3"]},
        },
        "alpha_on": "INACTIVE to ACTIVE when driver_value >= frozen Q80, including the first eligible session clock",
        "alpha_off": "ACTIVE to OFF when driver_value <= frozen Q60",
        "rearm": "after ALPHA_OFF, a new episode requires a later driver_value >= Q80",
        "clock_window_jst": f"{bound.get('clock_first')}-{bound.get('clock_last')}",
        "q60_is_episode_boundary_not_position_exit": True,
        "pb1_not_run": True,
        "register_limit": bound.get("register_limit"),
        "required_simultaneous_n": {"M1": 105, "M2": 105, "M3": 30},
    }
    out = dict(body)
    out["precommit_sha256"] = sha256_obj(body) if ready else None
    out["ready"] = ready
    return out
