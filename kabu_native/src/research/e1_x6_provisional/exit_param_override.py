"""Research-only EXIT param injection for JointStrategy EXIT family variants.

Does not modify Runtime / Paper / Live modules permanently — patches during replay only.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Mapping


@contextmanager
def exit_param_override(exit_spec: Mapping[str, Any]) -> Iterator[None]:
    """Temporarily override frozen E1_X5 exit constants for research replay."""
    import small_paper.e1_x5_forward_shadow as fs

    keys = {
        "STOP_BPS": float(exit_spec.get("initial_stop_bps", fs.STOP_BPS)),
        "TARGET_BPS": float(exit_spec.get("target_bps", fs.TARGET_BPS)),
        "MAX_HOLD_SEC": int(exit_spec.get("max_hold_sec", fs.MAX_HOLD_SEC)),
    }
    trail = exit_spec.get("trailing") or {}
    if "arm_bps" in trail:
        keys["TRAIL_ARM_BPS"] = float(trail["arm_bps"])
    if "giveback" in trail:
        keys["GIVEBACK"] = float(trail["giveback"])

    saved = {k: getattr(fs, k) for k in keys}
    try:
        for k, v in keys.items():
            setattr(fs, k, v)
        yield
    finally:
        for k, v in saved.items():
            setattr(fs, k, v)
