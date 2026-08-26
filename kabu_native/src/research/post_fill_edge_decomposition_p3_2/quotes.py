"""Causal last valid Buy1/Sell1 and CurrentPrice. event_t <= asof only."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.e1_x33c_baseline_economics.quotes import _row_ok


def last_valid_side(
    board: dict[str, np.ndarray],
    until: float,
    *,
    side: str,
) -> tuple[Optional[float], Optional[float], int]:
    """Last valid bid or ask with t <= until. Returns (px, t, leak_n)."""
    t = board.get("t")
    if t is None or getattr(t, "size", 0) == 0:
        return None, None, 0
    j = int(np.searchsorted(t, float(until), side="right") - 1)
    leak = 0
    while j >= 0:
        tj = float(t[j])
        if tj > float(until) + 1e-12:
            leak += 1
            j -= 1
            continue
        if _row_ok(board, j, side=side):
            px = float(board["ask" if side == "ask" else "bid"][j])
            return px, tj, leak
        j -= 1
    return None, None, leak


def mid_at_or_before(board: dict[str, np.ndarray], until: float) -> dict[str, Any]:
    """Independent last valid Buy1 and Sell1, then mid. Do not substitute CurrentPrice."""
    bid, bid_t, leak_b = last_valid_side(board, until, side="bid")
    ask, ask_t, leak_a = last_valid_side(board, until, side="ask")
    leak = int(leak_b) + int(leak_a)
    out: dict[str, Any] = {
        "bid": bid,
        "ask": ask,
        "bid_t": bid_t,
        "ask_t": ask_t,
        "mid": None,
        "spread_bps": None,
        "evaluable": False,
        "leak_n": leak,
    }
    if bid is None or ask is None:
        return out
    mid = (float(bid) + float(ask)) / 2.0
    if not np.isfinite(mid) or mid <= 0:
        return out
    out["mid"] = float(mid)
    out["spread_bps"] = (float(ask) - float(bid)) / float(mid) * 10000.0
    out["evaluable"] = True
    return out
