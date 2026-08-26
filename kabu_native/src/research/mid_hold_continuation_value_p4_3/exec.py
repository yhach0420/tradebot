"""First Dual Lane-valid executable Buy1 at or after trigger. No past Bid. No post-exit Bid."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from small_paper.v1r_live_dual_lane import BOARD_FRESH_SEC, MIN_BUY1_QTY


def _exec_ok(board: dict[str, np.ndarray], i: int) -> bool:
    if board["special"][i]:
        return False
    if "bid_qty" in board:
        qty = float(board["bid_qty"][i])
        if qty < MIN_BUY1_QTY - 1e-12:
            return False
    if "fresh_sec" in board:
        fresh = float(board["fresh_sec"][i])
        if np.isfinite(fresh) and fresh > BOARD_FRESH_SEC + 1e-12:
            return False
    bid = float(board["bid"][i])
    return bool(np.isfinite(bid) and bid > 0)


def first_valid_executable_buy1(
    board: dict[str, np.ndarray],
    *,
    t_from: float,
    t_until: float,
) -> dict[str, Any]:
    """First valid Buy1 with t_from <= event_time <= t_until. Never reads t > t_until."""
    out: dict[str, Any] = {
        "ok": False,
        "event_time": None,
        "bid": None,
        "uneval_reason": None,
        "leak_n": 0,
    }
    if t_until + 1e-12 < t_from:
        out["uneval_reason"] = "TRIGGER_AFTER_CANONICAL_EXIT"
        return out
    t = board.get("t")
    if t is None or getattr(t, "size", 0) == 0:
        out["uneval_reason"] = "NO_BOARD"
        return out
    i0 = int(np.searchsorted(t, float(t_from), side="left"))
    n = int(t.size)
    for i in range(i0, n):
        ti = float(t[i])
        if ti + 1e-12 < float(t_from):
            continue
        if ti > float(t_until) + 1e-12:
            break
        if not _exec_ok(board, i):
            continue
        out["ok"] = True
        out["event_time"] = ti
        out["bid"] = float(board["bid"][i])
        return out
    out["uneval_reason"] = "NO_VALID_BUY1_BEFORE_CANONICAL_EXIT"
    return out


def checkpoint_exit_pnl_yen_100(fill_price: float, execution_bid: float) -> Optional[float]:
    if fill_price is None or execution_bid is None:
        return None
    fp = float(fill_price)
    xb = float(execution_bid)
    if fp <= 0 or xb <= 0:
        return None
    return (xb - fp) * 100.0
