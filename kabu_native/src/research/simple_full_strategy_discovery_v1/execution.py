"""Three frozen execution modes. Conservative order-book fill. No chase. 5s cancel for passive."""
from __future__ import annotations

import math
from typing import Any, Optional

import numpy as np

from research.e1_x10_risk_universe.tick import jpx_tick_size_yen
from research.new_entry_breakout_continuation_v1.harvest import ask_entry_ok, snap_at
from research.simple_full_strategy_discovery_v1 import BOARD_FRESHNESS_SEC, MIN_QTY, WAIT_SEC


def floor_to_valid_tick(px: float) -> float:
    tick = float(jpx_tick_size_yen(float(px)))
    if tick <= 0:
        return float(px)
    return math.floor(float(px) / tick + 1e-12) * tick


def _ok_row(board: dict[str, np.ndarray], i: int, *, need_ask_qty: bool) -> bool:
    if not bool(board["executable"][i]) or not bool(board["continuous"][i]):
        return False
    if bool(board["special"][i]):
        return False
    fresh = float(board["fresh_sec"][i]) if board["fresh_sec"][i] == board["fresh_sec"][i] else 1e9
    if fresh > float(BOARD_FRESHNESS_SEC) + 1e-12:
        return False
    ask = float(board["ask"][i])
    if not (ask == ask) or ask <= 0:
        return False
    if need_ask_qty:
        aq = float(board["ask_qty"][i])
        if not (aq == aq) or aq < float(MIN_QTY) - 1e-12:
            return False
    return True


def find_limit_fill(
    board: dict[str, np.ndarray],
    *,
    t0: float,
    wait_sec: float,
    limit_price: float,
    sess_end: float,
) -> dict[str, Any]:
    t = board.get("t")
    if t is None or int(t.size) == 0:
        return {"filled": False, "reason": "NO_BOARD"}
    lim_t = min(float(t0) + float(wait_sec), float(sess_end))
    i0 = int(np.searchsorted(t, float(t0), side="left"))
    saw = False
    for i in range(i0, int(t.size)):
        ti = float(t[i])
        if ti + 1e-12 < float(t0):
            continue
        if ti > lim_t + 1e-12:
            break
        saw = True
        if not _ok_row(board, i, need_ask_qty=True):
            continue
        ask = float(board["ask"][i])
        if ask <= float(limit_price) + 1e-12:
            return {
                "filled": True,
                "fill_price": float(limit_price),
                "fill_t": ti,
                "cross_ask": ask,
                "waited_sec": float(ti - t0),
                "reason": "",
            }
    return {"filled": False, "reason": "NO_ASK_CROSS_IN_WINDOW" if saw else "NO_BOARD_IN_WINDOW"}


def evaluate_execution(
    board: dict[str, np.ndarray],
    *,
    exec_id: str,
    t0: float,
    sess_end: float,
) -> dict[str, Any]:
    snap = snap_at(board, float(t0))
    ok, reason = ask_entry_ok(snap, require_qty=True)
    if not ok:
        return {"filled": False, "reason": reason, "exec_id": exec_id, "WOULD_FILL": False, "ORDERED": False}
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
    }
    if exec_id == "X3":
        return {
            **base,
            "filled": True,
            "WOULD_FILL": True,
            "ORDERED": True,
            "fill_price": ask0,
            "fill_t": float(snap["t"]),
            "limit_price": ask0,
            "waited_sec": max(0.0, float(snap["t"]) - float(t0)),
            "reason": "",
        }
    if exec_id == "X1":
        limit = bid0
    elif exec_id == "X2":
        limit = floor_to_valid_tick(mid0)
        if limit <= bid0 + 1e-12:
            limit = bid0
        if limit >= ask0 - 1e-12:
            return {**base, "filled": False, "WOULD_FILL": False, "ORDERED": False, "limit_price": limit, "reason": "MID_NOT_INSIDE"}
    else:
        return {**base, "filled": False, "WOULD_FILL": False, "ORDERED": False, "reason": "UNKNOWN_EXEC"}
    fill = find_limit_fill(board, t0=float(t0), wait_sec=float(WAIT_SEC), limit_price=float(limit), sess_end=float(sess_end))
    return {
        **base,
        "ORDERED": True,
        "limit_price": float(limit),
        "filled": bool(fill.get("filled")),
        "WOULD_FILL": bool(fill.get("filled")),
        "fill_price": fill.get("fill_price"),
        "fill_t": fill.get("fill_t"),
        "waited_sec": fill.get("waited_sec"),
        "reason": fill.get("reason") or "",
    }
