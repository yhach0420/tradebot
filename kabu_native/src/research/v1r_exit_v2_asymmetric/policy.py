"""Sequential EXIT architectures A–E."""
from __future__ import annotations

from typing import Any, Optional

from research.v1r_exit_v2_asymmetric.continuation import continuation_supported
from research.v1r_exit_v2_asymmetric.guards import detect_guard_trigger
from research.v1r_exit_v2_asymmetric.states import exit_at_horizon


def apply_architecture(
    bundle: dict[str, Any],
    *,
    arch: str,
    guard: Optional[dict[str, Any]] = None,
    cont_rule: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """
    A: FIXED600
    B: TIME750
    C: Early Guard + FIXED600
    D: Early Guard + unconditional TIME750
    E: Early Guard + 600s Continuation Gate → 600 or 750
    """
    path = bundle["path"]
    if arch == "A":
        ex = exit_at_horizon(path, 600.0)
        return _pack(ex, triggered_guard=False, extended=False, arch=arch, reason="FIXED600")

    if arch == "B":
        ex = exit_at_horizon(path, 750.0)
        return _pack(ex, triggered_guard=False, extended=True, arch=arch, reason="TIME750")

    # C/D/E start with optional early guard
    ghit = detect_guard_trigger(bundle, guard) if guard else {"hit": False}
    if ghit.get("hit"):
        return {
            "ok": True,
            "exit_ret_bps": float(ghit["exit_ret_bps"]),
            "exit_off": float(ghit["exit_off"]),
            "exit_time": float(ghit["exit_time"]),
            "triggered_guard": True,
            "extended": False,
            "arch": arch,
            "reason": ghit.get("reason") or "EARLY_GUARD",
            "guard_trigger_off": float(ghit["trigger_off"]),
        }

    if arch == "C":
        ex = exit_at_horizon(path, 600.0)
        return _pack(ex, triggered_guard=False, extended=False, arch=arch, reason="FIXED600")

    if arch == "D":
        ex = exit_at_horizon(path, 750.0)
        return _pack(ex, triggered_guard=False, extended=True, arch=arch, reason="TIME750")

    if arch == "E":
        # survived to 600 decision — continuation gate
        extend = continuation_supported(bundle, cont_rule) if cont_rule else False
        if extend:
            ex = exit_at_horizon(path, 750.0)
            return _pack(ex, triggered_guard=False, extended=True, arch=arch, reason="CONT_EXTEND_750")
        ex = exit_at_horizon(path, 600.0)
        return _pack(ex, triggered_guard=False, extended=False, arch=arch, reason="CONT_EXIT_600")

    raise ValueError(arch)


def _pack(ex: dict[str, Any], *, triggered_guard: bool, extended: bool, arch: str, reason: str) -> dict[str, Any]:
    if not ex.get("ok"):
        return {"ok": False, "arch": arch}
    return {
        "ok": True,
        "exit_ret_bps": float(ex["exit_ret_bps"]),
        "exit_off": float(ex["exit_off"]),
        "exit_time": float(ex["exit_time"]),
        "triggered_guard": triggered_guard,
        "extended": extended,
        "arch": arch,
        "reason": reason,
        "guard_trigger_off": None,
    }
