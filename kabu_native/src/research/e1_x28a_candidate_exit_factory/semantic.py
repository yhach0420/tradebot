"""Semantic EXIT key / SHA (X26A-compatible + mode)."""
from __future__ import annotations

from typing import Any, Optional

from research.e1_x6_provisional.util import sha256_obj

from . import EVENT_PRIORITY, TOUCH_EPS


def semantic_exit_key(p: dict[str, Any]) -> dict[str, Any]:
    mode = p.get("exit_mode")
    if mode is None:
        mode = "TARGET" if p.get("target_bps") is not None else (
            "TRAIL" if p.get("trail_activation_bps") is not None else "CONTROL"
        )
    return {
        "mode": mode,
        "stop_bps": p.get("stop_bps"),
        "target_bps": p.get("target_bps"),
        "trail_activation_bps": p.get("trail_activation_bps"),
        "giveback_bps": p.get("giveback_bps"),
        "giveback_mode": p.get("giveback_mode"),
        "no_progress_sec": p.get("no_progress_sec"),
        "no_progress_mfe_bps": p.get("no_progress_mfe_bps"),
        "no_progress_abs_ret_bps": p.get("no_progress_abs_ret_bps"),
        "max_hold_sec": p.get("max_hold_sec"),
        "event_priority": list(EVENT_PRIORITY),
        "TOUCH_EPS": TOUCH_EPS,
    }


def semantic_exit_sha(p: dict[str, Any]) -> str:
    return sha256_obj(semantic_exit_key(p))


def primary_exit_id_for_mask(decision_mask_sha: str) -> str:
    return f"ENTRY_SPECIFIC::{decision_mask_sha}::V1"
