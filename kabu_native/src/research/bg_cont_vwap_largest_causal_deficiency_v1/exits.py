"""Three precommitted VWAP-reclaim thesis exits. No V27, no TP, no MFE grid. Causal MFE only."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.behavior_group_sequence_mechanism_v1.replay import _exit_from_fwd
from research.bg_cont_vwap_largest_causal_deficiency_v1 import FAVOR_BPS, SESSION_FLAT, TIME_STOP_MIN, X1_TAX_BPS

EVENT_ORDER_E0 = (
    "1. session_flat if hhmm >= 15:20",
    "2. first completed bar with close not strictly above session VWAP → vwap_loss EXIT_PENDING",
    "3. i >= 20 → time_stop",
    "Fill: next bar open after invalidation bar, else invalidation close",
)
EVENT_ORDER_E1_E2 = (
    "1. session_flat if hhmm >= 15:20 (any state, including VWAP_WARNING)",
    "2. if state == VWAP_WARNING and i >= 20 → time_stop (canonical exit precedes confirm)",
    "3. VWAP state machine (OPEN / VWAP_WARNING / EXIT_PENDING)",
    "4. if state still OPEN and i >= 20 → time_stop",
    "Fill: next bar open after invalidation bar, else invalidation close",
)

EXIT_SPECS = (
    {
        "exit_id": "E0",
        "name": "BASELINE_FIRST_VWAP_LOSS",
        "family": "frozen_parent",
        "persist_bars": 1,
        "mfe8_grace": False,
        "uses_parent__exit_from_fwd": True,
        "thesis": "First completed bar close <= session VWAP after reclaim.",
    },
    {
        "exit_id": "E1",
        "name": "VWAP_LOSS_PERSIST_2",
        "family": "persist_2",
        "persist_bars": 2,
        "mfe8_grace": False,
        "uses_parent__exit_from_fwd": False,
        "thesis": "One-bar VWAP loss is a warning; second consecutive close <= VWAP confirms thesis failure.",
    },
    {
        "exit_id": "E2",
        "name": "MFE8_ASYMMETRIC_VWAP_GRACE",
        "family": "mfe8_asymmetric",
        "persist_bars": 2,
        "mfe8_grace": True,
        "mfe_activate_bps": FAVOR_BPS,
        "uses_parent__exit_from_fwd": False,
        "thesis": "Before causal MFE>=8bps, first VWAP loss exits (E0). After MFE>=8bps, one-bar grace (E1).",
    },
)


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def spec_sha(spec: dict[str, Any]) -> str:
    raw = json.dumps({k: v for k, v in spec.items() if k != "spec_sha256"}, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _fill(fwd: list, exit_i: int) -> tuple[float | None, str]:
    if exit_i + 1 < len(fwd) and _finite(fwd[exit_i + 1][1]):
        return float(fwd[exit_i + 1][1]), "next_open_after_invalidation_bar"
    px = float(fwd[exit_i][4]) if _finite(fwd[exit_i][4]) else None
    return px, "invalidation_close_last_bar"


def exit_e0_parent(event: dict[str, Any], parent_spec: dict[str, Any]) -> dict[str, Any]:
    got = _exit_from_fwd(event, parent_spec)
    if not got.get("ok"):
        return got
    got["state_log"] = ["OPEN", "EXIT_PENDING", "CLOSED"]
    got["mfe8_activated_at"] = None
    got["first_vwap_loss_at"] = None
    got["warning_cancelled_at"] = []
    got["confirmed_exit_at"] = None
    got["warning_cancel_n"] = 0
    got["mfe8_grace_used"] = False
    got["exit_id"] = "E0"
    return got


def exit_state_machine(event: dict[str, Any], *, persist2: bool, mfe8_grace: bool, exit_id: str) -> dict[str, Any]:
    fwd = list(event.get("fwd_bars") or [])
    px = event.get("x0_entry_open")
    if not fwd or not _finite(px) or float(px) <= 0:
        return {"ok": False}
    px = float(px)
    state = "OPEN"
    mfe = 0.0
    mfe8_at = None
    first_loss_at = None
    cancelled: list[str] = []
    grace_used = False
    reason = "session_flat"
    exit_i = min(len(fwd) - 1, TIME_STOP_MIN)
    log = ["OPEN"]
    for i, row in enumerate(fwd):
        hh, hi, cl, above_vw = row[0], row[2], row[4], row[6]
        if _finite(hi) and px > 0:
            hb = (float(hi) / px - 1.0) * 10_000.0
            if hb > mfe:
                mfe = hb
            if mfe8_at is None and mfe >= FAVOR_BPS:
                mfe8_at = str(hh)
                log.append(f"MFE8@{hh}")
        lost = above_vw is False
        if str(hh) >= SESSION_FLAT:
            state = "EXIT_PENDING"
            exit_i, reason = i, "session_flat"
            log.append(f"SESSION_FLAT@{hh}")
            break
        if state == "VWAP_WARNING" and i >= TIME_STOP_MIN:
            state = "EXIT_PENDING"
            exit_i, reason = i, "time_stop"
            log.append(f"TIME_STOP_DURING_WARNING@{hh}")
            break
        use_grace = bool(persist2) and (not mfe8_grace or mfe8_at is not None)
        if state == "OPEN":
            if lost:
                first_loss_at = first_loss_at or str(hh)
                if use_grace:
                    state = "VWAP_WARNING"
                    grace_used = bool(mfe8_grace)
                    log.append(f"VWAP_WARNING@{hh}")
                else:
                    state = "EXIT_PENDING"
                    exit_i, reason = i, "vwap_loss"
                    log.append(f"VWAP_LOSS_E0@{hh}")
                    break
            elif i >= TIME_STOP_MIN:
                state = "EXIT_PENDING"
                exit_i, reason = i, "time_stop"
                log.append(f"TIME_STOP@{hh}")
                break
        elif state == "VWAP_WARNING":
            if not lost:
                state = "OPEN"
                cancelled.append(str(hh))
                log.append(f"WARNING_CANCELLED@{hh}")
            else:
                state = "EXIT_PENDING"
                exit_i, reason = i, "vwap_loss_persist2"
                log.append(f"VWAP_LOSS_CONFIRMED@{hh}")
                break
    log.append("CLOSED")
    exit_px, fill = _fill(fwd, exit_i)
    if not _finite(exit_px):
        return {"ok": False}
    x0 = float((float(exit_px) / px - 1.0) * 10_000.0)
    return {
        "ok": True,
        "exit_reason": reason,
        "exit_fill": fill,
        "x0_bps": x0,
        "x1_bps": x0 - float(X1_TAX_BPS),
        "hold_min": int(exit_i),
        "mfe_bps": mfe,
        "state_log": log,
        "mfe8_activated_at": mfe8_at,
        "first_vwap_loss_at": first_loss_at,
        "warning_cancelled_at": cancelled,
        "confirmed_exit_at": str(fwd[exit_i][0]) if reason.startswith("vwap") else None,
        "warning_cancel_n": len(cancelled),
        "mfe8_grace_used": grace_used,
        "exit_id": exit_id,
        "final_state": "CLOSED",
    }


def run_exit(event: dict[str, Any], *, exit_id: str, parent_spec: dict[str, Any]) -> dict[str, Any]:
    if exit_id == "E0":
        return exit_e0_parent(event, parent_spec)
    if exit_id == "E1":
        return exit_state_machine(event, persist2=True, mfe8_grace=False, exit_id="E1")
    if exit_id == "E2":
        return exit_state_machine(event, persist2=True, mfe8_grace=True, exit_id="E2")
    raise ValueError(exit_id)
