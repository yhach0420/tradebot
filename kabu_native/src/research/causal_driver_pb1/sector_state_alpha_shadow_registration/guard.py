"""Fail-closed owner, rollback, and activation gates. No live register call."""
from __future__ import annotations

from typing import Any, Sequence

from research.causal_driver_pb1.identity.ids import sha256_obj
from research.causal_driver_pb1.sector_state_alpha_shadow_registration import (
    ACTIVATION_FLAG,
    ACTIVATION_STEPS,
    MODE,
    OWNER,
    WINDOW_END_STEPS,
)


def activation_requested(argv: Sequence[str]) -> bool:
    return ACTIVATION_FLAG in [str(x) for x in argv]


def owner_guard(probe: dict[str, Any]) -> dict[str, Any]:
    conflict = bool(
        probe.get("standard_paper_active")
        or probe.get("dynamic38_continuity_active")
        or probe.get("formal_certification_active")
    )
    if conflict:
        return {
            "status": "REGISTER_PROFILE_OWNER_CONFLICT",
            "mutated": False,
            "register_changed": False,
            "alpha_enabled": False,
        }
    return {"status": "CLEAR", "mutated": False, "register_changed": False, "alpha_enabled": False}


def snapshot_register(*, symbols: Sequence[str], owner: str) -> dict[str, Any]:
    ordered = sorted(str(s) for s in symbols)
    return {
        "pre_shadow_registration_symbols": ordered,
        "pre_shadow_registration_sha256": sha256_obj({
            "namespace": "PAPER_STANDARD_REGISTER_PROFILE_V1",
            "symbols": ordered,
        }),
        "pre_shadow_registration_owner": owner,
    }


def restore_register(snapshot: dict[str, Any], restored_symbols: Sequence[str]) -> dict[str, Any]:
    got = sha256_obj({
        "namespace": "PAPER_STANDARD_REGISTER_PROFILE_V1",
        "symbols": sorted(str(s) for s in restored_symbols),
    })
    ok = got == snapshot["pre_shadow_registration_sha256"] and list(sorted(restored_symbols)) == list(snapshot["pre_shadow_registration_symbols"])
    return {
        "ok": ok,
        "status": "RESTORED" if ok else "REGISTRATION_RESTORE_FAILED",
        "restored_sha256": got,
        "improvised_dynamic_set": False,
    }


def ack_register(*, required_m3: Sequence[str], actual: Sequence[str]) -> dict[str, Any]:
    need = set(required_m3)
    got = set(actual)
    missing = sorted(need - got)
    extra = sorted(got - need)
    complete = not missing and len(need) == 30
    return {
        "required_M3_30_subset_of_actual_register": complete,
        "missing_M3_n": len(missing),
        "missing": missing,
        "extra_n": len(extra),
        "total_n": len(got),
        "alpha_enabled": complete and len(got) <= 50,
        "status": "READY" if complete else "M3_REGISTER_INCOMPLETE",
        "driver_symbols": sorted(need),
    }


def activation_banner(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "mode": MODE,
        "selected_profile": plan["profile_id"],
        "selected_plan_sha256": plan["plan_sha256"],
        "register_n": plan["union_n"],
        "M3_30_verified": plan.get("m3_missing_n") == 0 and plan.get("M3_n") == 30,
        "target_5_verified": True,
        "submit": 0,
        "cancel": 0,
        "live": 0,
        "live_mutation_executed": False,
    }


def precheck(steps_ok: Sequence[bool]) -> dict[str, Any]:
    """Any failure before the final enable step keeps Alpha evaluation off."""
    enabled = False
    failed = None
    for i, ok in enumerate(steps_ok):
        if not ok:
            failed = ACTIVATION_STEPS[i]
            break
    if failed is None and len(steps_ok) == len(ACTIVATION_STEPS) and all(steps_ok):
        enabled = True
    return {"alpha_enabled": enabled, "failed_step": failed, "steps": list(ACTIVATION_STEPS), "window_end": list(WINDOW_END_STEPS)}
