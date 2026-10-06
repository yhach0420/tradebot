"""Standalone Corrected Passive Fill diagnostics. Does not change the fill rule."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.e1_x28_executable_joint import BOARD_FRESHNESS_SEC, MIN_QTY
from research.e1_x34a_execution_policy.arms import find_ask_cross_fill
from research.entry_target_architecture.path import mids_at_marks

EARLY_SEC = (1.0, 5.0, 30.0)


def limit_bid_at_t0(board: dict[str, np.ndarray], t0: float) -> Optional[float]:
    t = board.get("t")
    if t is None or t.size == 0:
        return None
    i = int(np.searchsorted(t, t0, side="right") - 1)
    if i < 0:
        return None
    bid = float(board["bid"][i])
    if not np.isfinite(bid) or bid <= 0:
        return None
    return bid


def classify_nonfill(
    board: dict[str, np.ndarray],
    *,
    t0: float,
    wait_sec: float,
    limit_price: float,
    sess_end: float,
    require_executable_continuous: bool = True,
) -> str:
    """Map a WOULD_FILL=false wait window. Does not decide fill."""
    t = board.get("t")
    if t is None or int(t.size) == 0:
        return "NO_VALID_CONTINUOUS_BOARD"
    lim_t = min(float(t0) + float(wait_sec), float(sess_end))
    i0 = int(np.searchsorted(t, t0, side="left"))
    n_snap = 0
    n_nonexec = 0
    n_eligible = 0
    exec_arr = board.get("executable")
    for i in range(i0, int(t.size)):
        ti = float(t[i])
        if ti + 1e-12 < float(t0):
            continue
        if ti > lim_t + 1e-12:
            break
        n_snap += 1
        if require_executable_continuous and exec_arr is not None and int(getattr(exec_arr, "size", 0) or 0) > i:
            if not bool(exec_arr[i]):
                n_nonexec += 1
                continue
        if board["special"][i]:
            continue
        fresh = float(board["fresh_sec"][i]) if np.isfinite(board["fresh_sec"][i]) else 0.0
        if fresh > BOARD_FRESHNESS_SEC + 1e-12:
            continue
        qty = board["ask_qty"][i]
        if not np.isfinite(qty) or qty < MIN_QTY:
            continue
        ask = float(board["ask"][i])
        if not np.isfinite(ask) or ask <= 0:
            continue
        n_eligible += 1
    if n_eligible > 0:
        return "NO_ASK_CROSS_WITHIN_WAIT"
    if n_snap == 0:
        return "NO_VALID_CONTINUOUS_BOARD"
    if n_nonexec > 0 and n_eligible == 0:
        return "BOARD_BECAME_NONEXECUTABLE"
    if n_eligible == 0:
        return "NO_VALID_CONTINUOUS_BOARD"
    return "OTHER"


def early_returns(
    valid_t: np.ndarray,
    valid_mid: np.ndarray,
    *,
    t0: float,
    t_lo: float,
) -> dict[str, Optional[float]]:
    marks = np.asarray([float(t0)] + [float(t0) + float(s) for s in EARLY_SEC], dtype=float)
    lo = np.full(marks.shape, float(t_lo), dtype=float)
    mids, _future = mids_at_marks(valid_t, valid_mid, marks, lo)
    out: dict[str, Optional[float]] = {
        "ret_1s": None,
        "ret_5s": None,
        "ret_30s": None,
    }
    m0 = float(mids[0]) if np.isfinite(mids[0]) and float(mids[0]) > 0 else None
    if m0 is None:
        return out
    keys = ("ret_1s", "ret_5s", "ret_30s")
    for i, key in enumerate(keys, start=1):
        mx = float(mids[i]) if np.isfinite(mids[i]) and float(mids[i]) > 0 else None
        if mx is None:
            continue
        out[key] = float(mx / m0 - 1.0)
    return out


def standalone_fill(
    board: dict[str, np.ndarray],
    *,
    t0: float,
    wait_sec: float,
    limit_price: float,
    sess_end: float,
    min_qty: float = MIN_QTY,
) -> dict[str, Any]:
    # C14 frozen engine has no min_qty kwarg (qty floor is MIN_QTY=100).
    # Research-only overlay: zero sub-threshold ask qty so the frozen scan skips it.
    scan_board = board
    if float(min_qty) > float(MIN_QTY) + 1e-12:
        scan_board = dict(board)
        aq = np.array(board["ask_qty"], copy=True)
        mask = np.isfinite(aq) & (aq < float(min_qty) - 1e-12)
        aq[mask] = 0.0
        scan_board["ask_qty"] = aq
    fill = find_ask_cross_fill(
        scan_board,
        t0=float(t0),
        wait_sec=float(wait_sec),
        limit_price=float(limit_price),
        sess_end=float(sess_end),
        require_executable_continuous=True,
    )
    filled = bool(fill.get("filled"))
    reason = None if filled else str(fill.get("reason") or "OTHER")
    klass = None
    if not filled:
        klass = classify_nonfill(
            board,
            t0=float(t0),
            wait_sec=float(wait_sec),
            limit_price=float(limit_price),
            sess_end=float(sess_end),
            require_executable_continuous=True,
        )
    return {
        "WOULD_FILL": filled,
        "WOULD_EXPIRE": (not filled),
        "fill_price": float(fill["fill_price"]) if filled else None,
        "fill_t": fill.get("fill_t") if filled else None,
        "fill_reason": reason,
        "nonfill_class": klass,
    }
