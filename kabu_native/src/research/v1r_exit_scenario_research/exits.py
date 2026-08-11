"""Causal Failure / Completion EXIT detectors + FIXED600 fallback."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.e1_x35r_exit_contract.contracts import canonical_fixed_exit
from research.v1r_exit_scenario_research.recon import HORIZONS, causal_state_at


# Minimal concept-level rules (frozen after historical discovery — not 8/10 tuned)
# Failure: persistent sell pressure without recovery by decision horizon
FAILURE_DECISION_OFF = 30.0
FAILURE_MAE_MAX = -35.0       # bps
FAILURE_RET_MAX = -20.0       # still underwater
FAILURE_REQUIRE_NO_RECOVERY = True

# Completion: after meaningful MFE, giveback signals exhaustion
COMPLETION_MIN_MFE = 45.0
COMPLETION_GIVEBACK_FRAC = 0.50
COMPLETION_MIN_OFF = 60.0     # don't complete too early
COMPLETION_CHECK_OFFS = (60, 90, 120, 180, 300, 600)


def detect_failure(recon: dict[str, Any], *, off: float = FAILURE_DECISION_OFF) -> dict[str, Any]:
    """Causal Failure EXIT — ENTRY thesis collapsed (persistent selling)."""
    st = causal_state_at(recon, off)
    hit = False
    if st.get("mae") is not None and st.get("ret") is not None:
        hit = (
            float(st["mae"]) <= FAILURE_MAE_MAX
            and float(st["ret"]) <= FAILURE_RET_MAX
            and (not st.get("recovery_continuation"))
        )
        if FAILURE_REQUIRE_NO_RECOVERY and st.get("recovery_continuation"):
            hit = False
    # reinforce with sell_pressure concept
    if hit and not st.get("sell_pressure_persist"):
        # still allow if mae/ret thresholds met strongly
        if float(st["mae"]) > -50:
            hit = False
    return {
        "hit": bool(hit),
        "off": off,
        "reason": "FAILURE_SELL_PERSISTENCE" if hit else None,
        "state": st,
    }


def detect_completion(recon: dict[str, Any]) -> dict[str, Any]:
    """Causal Completion EXIT — rise phenomenon exhausted (giveback after MFE)."""
    for off in COMPLETION_CHECK_OFFS:
        if off < COMPLETION_MIN_OFF:
            continue
        st = causal_state_at(recon, float(off))
        mfe = st.get("mfe")
        gb = st.get("giveback")
        ret = st.get("ret")
        if mfe is None or gb is None:
            continue
        if float(mfe) >= COMPLETION_MIN_MFE and float(gb) >= COMPLETION_GIVEBACK_FRAC * float(mfe):
            if ret is not None and float(ret) <= 0.65 * float(mfe):
                return {
                    "hit": True,
                    "off": float(off),
                    "reason": "COMPLETION_MFE_GIVEBACK",
                    "state": st,
                }
    return {"hit": False, "off": None, "reason": None, "state": None}


def exit_at_off(
    path: dict[str, Any],
    off: float,
) -> dict[str, Any]:
    """Executable exit: first valid bid at or after fill+off (same as FIXED lookup)."""
    if not path.get("ok") or path["offs"].size == 0:
        return {"ok": False}
    # Prefer canonical_fixed_exit semantics with horizon = off
    ex = canonical_fixed_exit(path, float(off))
    if ex.get("ok"):
        return {
            "ok": True,
            "exit_time": float(ex["exit_time"]),
            "exit_off": float(ex["exit_off"]),
            "exit_ret_bps": float(ex["exit_ret_bps"]),
            "reason": ex.get("reason") or "FIRST_VALID_BUY1_AT_OR_AFTER_TARGET",
        }
    # fallback last available
    offs, rets, times = path["offs"], path["rets"], path["times"]
    j = int(np.searchsorted(offs, off, side="left"))
    if j >= offs.size:
        j = offs.size - 1
    if j < 0:
        return {"ok": False}
    return {
        "ok": True,
        "exit_time": float(times[j]),
        "exit_off": float(offs[j]),
        "exit_ret_bps": float(rets[j]),
        "reason": "SESSION_CLOSE_FALLBACK",
    }


def apply_exit_policy(
    recon: dict[str, Any],
    path: dict[str, Any],
    *,
    use_failure: bool,
    use_completion: bool,
    hold_sec: float = 600.0,
) -> dict[str, Any]:
    """
    Priority: Failure → Completion → FIXED hold.
    All detectors causal at their decision offs.
    """
    # rebuild path handle from recon if needed — caller passes path
    fail = detect_failure(recon) if use_failure else {"hit": False}
    if fail.get("hit"):
        ex = exit_at_off(path, float(fail["off"]))
        if ex.get("ok"):
            return {
                **ex,
                "policy_reason": fail["reason"],
                "policy": "FAILURE",
                "decision_off": fail["off"],
            }

    if use_completion:
        comp = detect_completion(recon)
        if comp.get("hit"):
            ex = exit_at_off(path, float(comp["off"]))
            if ex.get("ok"):
                return {
                    **ex,
                    "policy_reason": comp["reason"],
                    "policy": "COMPLETION",
                    "decision_off": comp["off"],
                }

    ex = canonical_fixed_exit(path, hold_sec)
    if ex.get("ok"):
        return {
            "ok": True,
            "exit_time": float(ex["exit_time"]),
            "exit_off": float(ex["exit_off"]),
            "exit_ret_bps": float(ex["exit_ret_bps"]),
            "reason": ex.get("reason"),
            "policy_reason": "FIXED600",
            "policy": "FIXED600",
            "decision_off": hold_sec,
        }
    return {"ok": False, "policy": "NONE"}


def simple_stop_exit(path: dict[str, Any], *, stop_bps: float = 50.0) -> dict[str, Any]:
    """Baseline comparison only — MAE-style price stop."""
    if not path.get("ok"):
        return {"ok": False}
    offs, rets, times = path["offs"], path["rets"], path["times"]
    for i, r in enumerate(rets):
        if float(r) <= -abs(stop_bps):
            return {
                "ok": True,
                "exit_time": float(times[i]),
                "exit_off": float(offs[i]),
                "exit_ret_bps": float(r),
                "policy": "SIMPLE_STOP",
                "policy_reason": f"STOP_{stop_bps}",
            }
    ex = canonical_fixed_exit(path, 600.0)
    if ex.get("ok"):
        return {
            "ok": True,
            "exit_time": float(ex["exit_time"]),
            "exit_off": float(ex["exit_off"]),
            "exit_ret_bps": float(ex["exit_ret_bps"]),
            "policy": "SIMPLE_STOP_THEN_FIXED",
            "policy_reason": "FIXED600",
        }
    return {"ok": False}
