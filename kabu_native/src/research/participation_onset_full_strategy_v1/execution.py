"""X1_IMMEDIATE_ASK. Fill at observed Ask1. No queue. No mid. No bar. No chase."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.new_entry_breakout_continuation_v1.harvest import ask_entry_ok, snap_at
from research.participation_onset_full_strategy_v1 import BOARD_FRESHNESS_SEC, MIN_QTY


def _ask_row_ok(board: dict[str, np.ndarray], i: int) -> bool:
    snap = {
        "ok": True,
        "executable": bool(board["executable"][i]),
        "continuous": bool(board["continuous"][i]),
        "state": str(board["board_execution_state"][i] or ""),
        "special": bool(board["special"][i]),
        "ask_fresh_sec": board["ask_fresh_sec"][i],
        "bid_fresh_sec": board["bid_fresh_sec"][i],
        "ask": board["ask"][i],
        "bid": board["bid"][i],
        "ask_qty": board["ask_qty"][i],
    }
    ok, _reason = ask_entry_ok(snap, require_qty=True)
    return bool(ok)


def evaluate_execution(
    board: dict[str, np.ndarray],
    *,
    t0: float,
    sess_end: float,
) -> dict[str, Any]:
    flags = {
        "QUEUE_ASSUMED_FILL": 0,
        "BAR_OHLC_FILL": 0,
        "TRADE_PRINT_PASSIVE_FILL": 0,
        "LOOKAHEAD_FILL": 0,
        "REPRICE": 0,
        "CHASE": 0,
    }
    base = {"exec_id": "X1", "ORDERED": True, **flags}
    snap = snap_at(board, float(t0))
    ok, reason = ask_entry_ok(snap, require_qty=True)
    if ok:
        fill_t = max(float(snap["t"]), float(t0))
        ask0 = float(snap["ask"])
        bid0 = float(snap["bid"])
        mid0 = (ask0 + bid0) / 2.0
        spread = (ask0 - bid0) / mid0 * 10000.0 if mid0 > 0 else None
        if fill_t + 1e-12 < float(t0):
            flags["LOOKAHEAD_FILL"] = 1
        return {
            **base,
            **flags,
            "filled": True,
            "WOULD_FILL": True,
            "fill_price": ask0,
            "fill_t": fill_t,
            "ask0": ask0,
            "bid0": bid0,
            "mid0": mid0,
            "spread0": spread,
            "snap_t": float(snap["t"]),
            "waited_sec": float(fill_t - float(t0)),
            "reason": "",
        }
    t = board.get("t")
    if t is None or int(t.size) == 0:
        return {**base, "filled": False, "WOULD_FILL": False, "reason": reason or "NO_BOARD"}
    i0 = int(np.searchsorted(t, float(t0), side="left"))
    for i in range(i0, int(t.size)):
        ti = float(t[i])
        if ti + 1e-12 < float(t0):
            continue
        if ti > float(sess_end) + 1e-12:
            break
        if not _ask_row_ok(board, i):
            continue
        ask = float(board["ask"][i])
        bid = float(board["bid"][i])
        mid = (ask + bid) / 2.0 if (ask == ask and bid == bid) else None
        spread = (ask - bid) / mid * 10000.0 if mid and mid > 0 else None
        if ti + 1e-12 < float(t0):
            flags["LOOKAHEAD_FILL"] = 1
        return {
            **base,
            **flags,
            "filled": True,
            "WOULD_FILL": True,
            "fill_price": ask,
            "fill_t": ti,
            "ask0": ask,
            "bid0": bid,
            "mid0": mid,
            "spread0": spread,
            "snap_t": ti,
            "waited_sec": float(ti - float(t0)),
            "reason": "",
        }
    return {**base, "filled": False, "WOULD_FILL": False, "reason": reason or "NO_ASK_AFTER_T0"}


assert BOARD_FRESHNESS_SEC == 5.0
assert MIN_QTY == 100.0
