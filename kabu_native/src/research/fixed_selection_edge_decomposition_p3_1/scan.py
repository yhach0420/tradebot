"""Wait-window ask stats and causal CurrentPrice lookup. No new fill rule."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.e1_x34a_execution_policy.arms import _row_ask_ok, find_ask_cross_fill
from research.fixed_anchor_mechanism_audit_p3_0.grid import AM_END, PM_END, hm_epoch, session_of_epoch
from small_paper.v1r_primary_runtime import WAIT_SEC


def ask_minus_limit_bps(ask: float, limit: float) -> Optional[float]:
    if not np.isfinite(ask) or not np.isfinite(limit) or limit <= 0:
        return None
    return (float(ask) / float(limit) - 1.0) * 10000.0


def wait_ask_stats(board: dict[str, np.ndarray], t0: float, limit: float) -> dict[str, Any]:
    """First / min valid ask in the same 1s window as find_ask_cross_fill."""
    out: dict[str, Any] = {
        "first_ask": None,
        "first_ask_minus_limit_bps": None,
        "min_ask": None,
        "min_ask_minus_limit_bps": None,
        "n_valid_ask": 0,
    }
    t = board.get("t")
    if t is None or getattr(t, "size", 0) == 0 or not np.isfinite(limit) or limit <= 0:
        return out
    lim_t = float(t0) + float(WAIT_SEC)
    i0 = int(np.searchsorted(t, float(t0), side="left"))
    first = None
    min_ask = None
    n = 0
    for i in range(i0, t.size):
        ti = float(t[i])
        if ti + 1e-12 < float(t0):
            continue
        if ti > lim_t + 1e-12:
            break
        if not _row_ask_ok(board, i):
            continue
        ask = float(board["ask"][i])
        n += 1
        if first is None:
            first = ask
        if min_ask is None or ask < min_ask:
            min_ask = ask
    out["n_valid_ask"] = n
    out["first_ask"] = first
    out["min_ask"] = min_ask
    out["first_ask_minus_limit_bps"] = None if first is None else ask_minus_limit_bps(first, limit)
    out["min_ask_minus_limit_bps"] = None if min_ask is None else ask_minus_limit_bps(min_ask, limit)
    return out


def run_fill(board, t0: float, limit: float) -> dict[str, Any]:
    return find_ask_cross_fill(
        board,
        t0=float(t0),
        wait_sec=float(WAIT_SEC),
        limit_price=float(limit),
        sess_end=float(t0) + 3 * 3600.0,
    )


def last_px_at_or_before(t: np.ndarray, px: np.ndarray, until: float) -> Optional[float]:
    if t is None or getattr(t, "size", 0) == 0:
        return None
    i = int(np.searchsorted(t, float(until), side="right") - 1)
    while i >= 0:
        p = float(px[i])
        if np.isfinite(p) and p > 0:
            return p
        i -= 1
    return None


def horizon_status(day: str, session: str, t0: float, horizon_sec: int) -> str:
    """SESSION_INCOMPLETE if checkpoint leaves the same continuous session (no lunch bridge)."""
    chk = float(t0) + float(horizon_sec)
    end = hm_epoch(day, *(AM_END if session == "AM" else PM_END))
    if chk > float(end) + 1e-12:
        return "SESSION_INCOMPLETE"
    sess = session_of_epoch(day, chk)
    if sess is None or sess != session:
        return "SESSION_INCOMPLETE"
    return "OK"
