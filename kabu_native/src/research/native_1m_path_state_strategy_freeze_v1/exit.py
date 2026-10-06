"""Frozen R11 thesis EXIT: first close not strictly above VWAP, then next open. No V27/persistence/MFE/TP."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.native_1m_path_state_strategy_freeze_v1 import SESSION_FLAT, TIME_STOP_MIN, X1_TAX_BPS

EXIT_SPEC = {
    "thesis": "causal VWAP reclaim in favorable morning state is invalidated by VWAP-loss",
    "vwap_loss_predicate": "on a completed forward bar, above_vwap is False, i.e. close is not strictly greater than session_vwap (close <= vwap, or non-finite close/vwap)",
    "availability_time": "invalidation bar T is available at T+1m; EXIT_PENDING at that bar's HH:MM label",
    "fill_rule": "open of the next forward bar after the invalidation bar; if none, invalidation-bar close",
    "session_flat_ordering": f"each forward bar: if hhmm >= {SESSION_FLAT}, reason=session_flat FIRST, even if also not above VWAP",
    "time_stop": f"if still open after {TIME_STOP_MIN} forward minutes, reason=time_stop (checked after session_flat and vwap_loss)",
    "not_added": ["V27", "persistence", "MFE grace", "stop optimization", "take-profit"],
    "fwd_starts_at": "entry bar (event_time), so hold_min=0 is the entry bar itself",
}


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def exit_reclaim(ep: dict[str, Any]) -> dict[str, Any]:
    fwd = list(ep.get("fwd_bars") or [])
    px = ep.get("x0_entry_open")
    if not fwd or not _finite(px) or float(px) <= 0:
        return {"ok": False}
    px = float(px)
    reason = "session_flat"
    exit_i = min(len(fwd) - 1, TIME_STOP_MIN)
    for i, row in enumerate(fwd):
        hh, above_vw = row[0], row[6]
        if str(hh) >= SESSION_FLAT:
            exit_i, reason = i, "session_flat"
            break
        if above_vw is False:
            exit_i, reason = i, "vwap_loss"
            break
        if i >= TIME_STOP_MIN:
            exit_i, reason = i, "time_stop"
            break
    pending_hh = str(fwd[exit_i][0])
    if exit_i + 1 < len(fwd) and _finite(fwd[exit_i + 1][1]):
        exit_px = float(fwd[exit_i + 1][1])
        fill = "next_open_after_invalidation_bar"
        fill_hh = str(fwd[exit_i + 1][0])
    else:
        exit_px = float(fwd[exit_i][4]) if _finite(fwd[exit_i][4]) else None
        fill = "invalidation_close_last_bar"
        fill_hh = pending_hh
    if not _finite(exit_px):
        return {"ok": False}
    x0 = float((float(exit_px) / px - 1.0) * 10_000.0)
    return {
        "ok": True,
        "exit_reason": reason,
        "exit_fill": fill,
        "exit_pending_time": pending_hh,
        "exit_hh": fill_hh,
        "exit_price": float(exit_px),
        "x0_bps": x0,
        "x1_bps": x0 - float(X1_TAX_BPS),
        "hold_min": int(exit_i),
    }


def exit_spec_sha256() -> str:
    raw = json.dumps(EXIT_SPEC, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw + Path(__file__).read_bytes()).hexdigest()
