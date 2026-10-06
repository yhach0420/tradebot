"""Research-only SHORT_W5: exact Long Corrected Passive Fill mirror. Not Runtime."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.e1_x28_executable_joint import BOARD_FRESHNESS_SEC, MIN_QTY
from research.post_open_causal_downside_mechanism_discovery_v1 import W5_WAIT_SEC

SHORT_W5_SPEC_ID = "SHORT_W5_FROZEN_ASK_MIRROR_V1"


def limit_ask_at_t0(board: dict[str, np.ndarray], t0: float) -> Optional[float]:
    t = board.get("t")
    if t is None or t.size == 0:
        return None
    i = int(np.searchsorted(t, t0, side="right") - 1)
    if i < 0:
        return None
    ask = float(board["ask"][i])
    if not np.isfinite(ask) or ask <= 0:
        return None
    return ask


def find_bid_cross_fill(
    board: dict[str, np.ndarray],
    *,
    t0: float,
    wait_sec: float,
    limit_price: float,
    sess_end: float,
    require_executable_continuous: bool = True,
) -> dict[str, Any]:
    """
    Conservative short fill: first future snapshot with
      CONTINUOUS_TRADING_EXECUTABLE AND
      Bid1.Price >= limit AND qty>=100 AND bid freshness OK AND not special.
    fill_price = frozen Ask limit (no improvement vs crossed Bid).
    No reprice, chase, Bid fallback, or synthetic fill.
    """
    t = board["t"]
    if t.size == 0:
        return {"filled": False, "reason": "NO_BOARD"}
    lim_t = min(float(t0) + float(wait_sec), float(sess_end))
    i0 = int(np.searchsorted(t, t0, side="left"))
    saw_book = False
    exec_arr = board.get("executable")
    state_arr = board.get("board_execution_state")
    fresh_arr = board.get("bid_fresh_sec")
    if fresh_arr is None:
        fresh_arr = board.get("fresh_sec")
    for i in range(i0, t.size):
        ti = float(t[i])
        if ti + 1e-12 < t0:
            continue
        if ti > lim_t + 1e-12:
            break
        saw_book = True
        if require_executable_continuous and exec_arr is not None and int(getattr(exec_arr, "size", 0) or 0) > i:
            if not bool(exec_arr[i]):
                continue
        if board["special"][i]:
            continue
        fresh = float(fresh_arr[i]) if fresh_arr is not None and np.isfinite(fresh_arr[i]) else 0.0
        if fresh > BOARD_FRESHNESS_SEC + 1e-12:
            continue
        qty = board["bid_qty"][i]
        if not np.isfinite(qty) or qty < MIN_QTY:
            continue
        bid = float(board["bid"][i])
        if not np.isfinite(bid) or bid <= 0:
            continue
        if bid + 1e-12 >= float(limit_price):
            state = None
            if state_arr is not None and int(getattr(state_arr, "size", 0) or 0) > i:
                state = str(state_arr[i] or "")
            return {
                "filled": True,
                "fill_price": float(limit_price),
                "limit_price": float(limit_price),
                "fill_t": ti,
                "fill_event_time": ti,
                "cross_bid": bid,
                "cross_bid_qty": float(qty),
                "board_execution_state": state,
                "waited_sec": float(ti - t0),
            }
    return {
        "filled": False,
        "reason": "NO_BID_CROSS_IN_WINDOW" if saw_book else "NO_BOARD_IN_WINDOW",
        "fill_evidence_note": "PASSIVE_QUEUE_FILL_UNOBSERVABLE — queue/touch not used",
    }


def standalone_short_fill(
    board: dict[str, np.ndarray],
    *,
    t0: float,
    wait_sec: float,
    limit_price: float,
    sess_end: float,
) -> dict[str, Any]:
    fill = find_bid_cross_fill(
        board,
        t0=float(t0),
        wait_sec=float(wait_sec),
        limit_price=float(limit_price),
        sess_end=float(sess_end),
        require_executable_continuous=True,
    )
    filled = bool(fill.get("filled"))
    return {
        "WOULD_FILL": filled,
        "WOULD_EXPIRE": (not filled),
        "fill_price": float(fill["fill_price"]) if filled else None,
        "fill_t": fill.get("fill_t") if filled else None,
        "fill_reason": None if filled else str(fill.get("reason") or "OTHER"),
        "nonfill_class": None if filled else str(fill.get("reason") or "OTHER"),
        "wait_sec": float(wait_sec),
        "repricing": False,
        "chase": False,
        "fallback_bid": False,
        "synthetic_fill": False,
    }


assert abs(float(W5_WAIT_SEC) - 5.0) < 1e-12
assert abs(float(MIN_QTY) - 100.0) < 1e-12
assert abs(float(BOARD_FRESHNESS_SEC) - 5.0) < 1e-12
