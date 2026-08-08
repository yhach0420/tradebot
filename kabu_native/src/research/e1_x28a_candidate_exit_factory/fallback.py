"""Fallback hierarchy (no X27/X28 performance)."""
from __future__ import annotations

from typing import Any, Optional

from . import (
    CONTROL_BY_HORIZON,
    FAMILY_ANY_EXIT,
    FAMILY_HORIZON_SEC,
    FAMILY_PROTECT_EXIT,
)


def choose_fallback(
    *,
    tags: list[str],
    candidate_horizon_sec: int,
    x26a_exits: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """
    1) family PROTECT for Discovery tags
    2) if multiple / no PROTECT: family EXIT closest to candidate horizon
    3) CONTROL_HOLD matching horizon
    """
    tags = [t for t in (tags or []) if t in FAMILY_ANY_EXIT]

    # Step 1: single PROTECT if exactly one protect-capable tag, or any protect tags
    protect_tags = [t for t in tags if t in FAMILY_PROTECT_EXIT]
    if len(protect_tags) == 1:
        eid = FAMILY_PROTECT_EXIT[protect_tags[0]]
        return _family_row(eid, "FAMILY_FALLBACK", "single_family_protect", protect_tags[0], x26a_exits)
    if len(protect_tags) > 1:
        # closest protect family horizon to candidate
        best = min(protect_tags, key=lambda t: abs(FAMILY_HORIZON_SEC[t] - candidate_horizon_sec))
        eid = FAMILY_PROTECT_EXIT[best]
        return _family_row(eid, "FAMILY_FALLBACK", "multi_tag_closest_protect_horizon", best, x26a_exits)

    # Step 2: any family EXIT closest horizon
    if tags:
        best = min(tags, key=lambda t: abs(FAMILY_HORIZON_SEC[t] - candidate_horizon_sec))
        eid = FAMILY_ANY_EXIT[best]
        return _family_row(eid, "FAMILY_FALLBACK", "closest_family_exit_no_protect", best, x26a_exits)

    # Step 3: common control by horizon
    # snap horizon to available control keys
    h = candidate_horizon_sec
    if h <= 300:
        cid = CONTROL_BY_HORIZON[300]
    elif h <= 900:
        cid = CONTROL_BY_HORIZON[900]
    else:
        cid = CONTROL_BY_HORIZON[1800]
    return {
        "ok": True,
        "exit_source": "COMMON_CONTROL_FALLBACK",
        "fallback_reason": f"no_family_tag_hold_{cid}",
        "primary_exit_id": cid,
        "canonical_exit_id": cid,
        "params_from": "common_control",
        "stop_bps": None,
        "target_bps": None,
        "trail_activation_bps": None,
        "giveback_bps": None,
        "giveback_mode": None,
        "no_progress_sec": None,
        "max_hold_sec": float(300 if cid.endswith("300") else 900 if cid.endswith("900") else 1800),
        "exit_mode": "CONTROL",
        "stop_risk_tag": None,
    }


def _family_row(
    eid: str,
    source: str,
    reason: str,
    tag: str,
    x26a_exits: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    p = x26a_exits.get(eid) or {}
    return {
        "ok": True,
        "exit_source": source,
        "fallback_reason": reason,
        "fallback_family_tag": tag,
        "primary_exit_id": eid,
        "canonical_exit_id": eid,
        "params_from": "x26a_family",
        "exit_mode": "TARGET" if p.get("target_bps") is not None else "TRAIL",
        "stop_bps": p.get("stop_bps"),
        "target_bps": p.get("target_bps"),
        "trail_activation_bps": p.get("trail_activation_bps"),
        "giveback_bps": p.get("giveback_bps"),
        "giveback_mode": p.get("giveback_mode"),
        "no_progress_sec": p.get("no_progress_sec"),
        "max_hold_sec": p.get("max_hold_sec"),
        "no_progress_mfe_bps": p.get("no_progress_mfe_bps", 5.0),
        "no_progress_abs_ret_bps": p.get("no_progress_abs_ret_bps", 5.0),
        "stop_risk_tag": None,
    }
