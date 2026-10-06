"""Two executable BUY modes. Fill at observed Ask1. No resting passive. No queue. No OHLC."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.new_entry_breakout_continuation_v1.harvest import ask_entry_ok, snap_at
from research.recovery_sequence_full_strategy_architecture_v1 import BOARD_FRESHNESS_SEC, MIN_QTY, WAIT_SEC


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
    exec_id: str,
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
    snap = snap_at(board, float(t0))
    ok, reason = ask_entry_ok(snap, require_qty=True)
    if not ok:
        return {
            "filled": False,
            "WOULD_FILL": False,
            "ORDERED": False,
            "reason": reason,
            "exec_id": exec_id,
            **flags,
        }
    ask0 = float(snap["ask"])
    bid0 = float(snap["bid"])
    mid0 = (ask0 + bid0) / 2.0
    spread = (ask0 - bid0) / mid0 * 10000.0 if mid0 > 0 else None
    base = {
        "exec_id": exec_id,
        "ask0": ask0,
        "bid0": bid0,
        "mid0": mid0,
        "spread0": spread,
        "snap_t": float(snap["t"]),
        "ORDERED": True,
        **flags,
    }
    if exec_id == "X1":
        fill_t = max(float(snap["t"]), float(t0))
        return {
            **base,
            "filled": True,
            "WOULD_FILL": True,
            "fill_price": ask0,
            "fill_t": fill_t,
            "waited_sec": float(fill_t - float(t0)),
            "reason": "",
        }
    if exec_id != "X2":
        return {**base, "filled": False, "WOULD_FILL": False, "ORDERED": False, "reason": "UNKNOWN_EXEC"}
    t = board.get("t")
    if t is None or int(t.size) == 0:
        return {**base, "filled": False, "WOULD_FILL": False, "reason": "NO_BOARD"}
    lim_t = min(float(t0) + float(WAIT_SEC), float(sess_end))
    i0 = int(np.searchsorted(t, float(t0), side="left"))
    for i in range(i0, int(t.size)):
        ti = float(t[i])
        if ti + 1e-12 < float(t0):
            continue
        if ti > lim_t + 1e-12:
            break
        if not _ask_row_ok(board, i):
            continue
        ask = float(board["ask"][i])
        if ask <= float(mid0) + 1e-12:
            if ti + 1e-12 < float(t0):
                flags["LOOKAHEAD_FILL"] = 1
            return {
                **base,
                "filled": True,
                "WOULD_FILL": True,
                "fill_price": ask,
                "fill_t": ti,
                "waited_sec": float(ti - float(t0)),
                "reason": "",
                **flags,
            }
    return {**base, "filled": False, "WOULD_FILL": False, "reason": "NO_ASK_LE_MID_IN_5S"}
