"""Dry-run planner. Never calls a live registration API."""
from __future__ import annotations

from typing import Any

from research.causal_driver_pb1.sector_state_alpha_shadow_registration import (
    OWNER,
    PROFILE_MODE,
    REGISTER_LIMIT,
    SECTOR_STATE_ALPHA_SHADOW_ENABLED,
    SECTOR_STATE_ALPHA_SHADOW_ORDERS_ENABLED,
    SECTOR_STATE_ALPHA_SHADOW_REGISTER_PROFILE_ENABLED,
    STANDARD_MODE,
)
from research.causal_driver_pb1.sector_state_alpha_shadow_registration.guard import activation_banner, owner_guard
from research.causal_driver_pb1.sector_state_alpha_shadow_registration.identities import load_identities
from research.causal_driver_pb1.sector_state_alpha_shadow_registration.profiles import build_candidates, plan_sha, reject_rotation, select_profile

LIVE_REGISTER_CALLS = 0


def dry_run(*, probe: dict[str, Any] | None = None) -> dict[str, Any]:
    if LIVE_REGISTER_CALLS != 0:
        raise RuntimeError("live_register_call_forbidden")
    ident = load_identities()
    if not ident["pass"]:
        return {"pass": False, "blockers": ident["blockers"], "would_mutate_live_register": False}
    candidates = build_candidates(m3=ident["m3"], core=ident["core"], futures=ident["futures"])
    selected = select_profile(candidates)
    reject_rotation(selected)
    selected_sha = plan_sha(selected)
    selected = {**selected, "plan_sha256": selected_sha}
    guard = owner_guard(probe or {"standard_paper_active": False, "dynamic38_continuity_active": False, "formal_certification_active": False})
    core_set = set(ident["core"])
    dyn_set = set(ident["dynamic"])
    fut_set = set(ident["futures"])
    chosen = set(selected["symbols"])
    dynamic_block_added = dyn_set <= chosen and len(dyn_set) == 38 and not dyn_set <= set(ident["m3"])
    banner = activation_banner({**selected, "plan_sha256": selected_sha})
    return {
        "pass": bool(selected["valid"]) and ident["targets_subset"] and not dynamic_block_added,
        "would_mutate_live_register": False,
        "live_register_calls": LIVE_REGISTER_CALLS,
        "selected_profile": selected["profile_id"],
        "selected_symbols": list(selected["symbols"]),
        "selected_plan_sha256": selected_sha,
        "union_n": selected["union_n"],
        "remaining_slots": selected["remaining_slots"],
        "M3_required_n": 30,
        "M3_missing_n": 0,
        "core_preserved_n": len(core_set & chosen),
        "futures_preserved_n": len(fut_set & chosen),
        "dynamic_preserved_n": len(dyn_set & chosen),
        "dynamic38_block_included": False,
        "dynamic38_policy": "NOT_REQUIRED_FOR_M3_ALPHA",
        "standard_paper_simultaneous_allowed": False,
        "profile_mode": PROFILE_MODE,
        "excluded_mode": STANDARD_MODE,
        "registration_owner": OWNER,
        "previous_registration_owner": ident["previous_owner"],
        "previous_registration_plan_sha256": ident["previous_plan_sha256"],
        "flags": {
            "SECTOR_STATE_ALPHA_SHADOW_ENABLED": SECTOR_STATE_ALPHA_SHADOW_ENABLED,
            "SECTOR_STATE_ALPHA_SHADOW_REGISTER_PROFILE_ENABLED": SECTOR_STATE_ALPHA_SHADOW_REGISTER_PROFILE_ENABLED,
            "SECTOR_STATE_ALPHA_SHADOW_ORDERS_ENABLED": SECTOR_STATE_ALPHA_SHADOW_ORDERS_ENABLED,
        },
        "owner_guard": guard,
        "banner": banner,
        "candidates": candidates,
        "identities": ident,
        "register_limit": REGISTER_LIMIT,
        "driver_read_n": 30,
        "driver_read_sha256": ident["m3_sha256"],
    }
