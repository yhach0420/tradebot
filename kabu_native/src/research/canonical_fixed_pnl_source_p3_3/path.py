"""Causal fill→actual-exit path. No event after exit_time. No future-nearest."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.e1_x33c_baseline_economics.quotes import _row_ok
from research.post_fill_edge_decomposition_p3_2.quotes import last_valid_side, mid_at_or_before


def walk_fill_to_exit(board: dict[str, np.ndarray], fill_t: float, exit_t: float) -> dict[str, Any]:
    """Last-valid Bid1 / MID on ticks with event_t <= exit_t.

    Extrema are taken on the causal state at asof in [fill_t, exit_t].
    Ticks with t > exit_t are never applied. FUTURE_LEAK if any such tick is used.
    """
    out: dict[str, Any] = {
        "max_bid1": None,
        "min_bid1": None,
        "max_mid": None,
        "min_mid": None,
        "n_path_ticks": 0,
        "leak_n": 0,
        "path_evaluable_bid": False,
        "path_evaluable_mid": False,
    }
    t = board.get("t")
    if t is None or getattr(t, "size", 0) == 0:
        return out
    fill_t = float(fill_t)
    exit_t = float(exit_t)
    i_end = int(np.searchsorted(t, exit_t, side="right"))
    last_bid: Optional[float] = None
    last_ask: Optional[float] = None
    leak = 0
    max_bid = min_bid = None
    max_mid = min_mid = None
    n_in = 0
    in_window = False
    for i in range(i_end):
        ti = float(t[i])
        if ti > exit_t + 1e-12:
            leak += 1
            continue
        if _row_ok(board, i, side="bid"):
            last_bid = float(board["bid"][i])
        if _row_ok(board, i, side="ask"):
            last_ask = float(board["ask"][i])
        if ti + 1e-12 < fill_t:
            continue
        in_window = True
        n_in += 1
        if last_bid is not None:
            max_bid = last_bid if max_bid is None else max(max_bid, last_bid)
            min_bid = last_bid if min_bid is None else min(min_bid, last_bid)
        if last_bid is not None and last_ask is not None:
            mid = (float(last_bid) + float(last_ask)) / 2.0
            if np.isfinite(mid) and mid > 0:
                max_mid = mid if max_mid is None else max(max_mid, mid)
                min_mid = mid if min_mid is None else min(min_mid, mid)
    if not in_window:
        if last_bid is not None:
            max_bid = min_bid = last_bid
        if last_bid is not None and last_ask is not None:
            mid = (float(last_bid) + float(last_ask)) / 2.0
            if np.isfinite(mid) and mid > 0:
                max_mid = min_mid = mid
    out["max_bid1"] = max_bid
    out["min_bid1"] = min_bid
    out["max_mid"] = max_mid
    out["min_mid"] = min_mid
    out["n_path_ticks"] = n_in
    out["leak_n"] = leak
    out["path_evaluable_bid"] = max_bid is not None and min_bid is not None
    out["path_evaluable_mid"] = max_mid is not None and min_mid is not None
    return out


def bid1_at_or_before(board: dict[str, np.ndarray], until: float) -> dict[str, Any]:
    px, tt, leak = last_valid_side(board, until, side="bid")
    return {"bid1": px, "bid1_t": tt, "leak_n": leak, "evaluable": px is not None}


def mid_checkpoint(board: dict[str, np.ndarray], until: float) -> dict[str, Any]:
    return mid_at_or_before(board, until)
