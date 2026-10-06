"""Apply the frozen detector's existing genuine-resistance definition. No new rule."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.v2_structural_resistance_detector_validation_v1.detector import SessionDetector, _board_at


def genuine_resistance_at_signal(book: dict[str, Any]) -> dict[int, bool]:
    """Causal admit map for FULL signal indexes. State is the detector before the signal event."""
    det = SessionDetector()
    signals = {int(i) for i in book["signals"]}
    admit: dict[int, bool] = {}
    times = book["t"]
    prices = book["px"]
    vol = book.get("classified")
    buy = book.get("ask10")
    sell = book.get("bid10")
    board_t = book.get("board_t")
    board_bid = book.get("bb") if book.get("bb") is not None else book.get("board_bid")
    board_ask = book.get("board_ask")
    n = int(times.size)
    for i in range(n):
        t = float(times[i])
        if i in signals:
            pre = float(book["pre_high"][i])
            match = det.pre_break_match(pre_break=pre, t=t)
            zone = match.get("zone")
            # Frozen study definition: PRE_BREAK_HIGH lies in an already-known UPPER_REJECTION_ZONE.
            admit[i] = bool(match.get("matched") and zone is not None and zone.zone_type == "UPPER_REJECTION_ZONE")
        px = float(prices[i])
        v = float(vol[i]) if vol is not None else 0.0
        bv = float(buy[i]) if buy is not None else 0.0
        sv = float(sell[i]) if sell is not None else 0.0
        bid = ask = float("nan")
        if board_t is not None and board_bid is not None and board_ask is not None:
            bid, ask = _board_at(board_t, board_bid, board_ask, t)
        det.step(i=i, t=t, px=px, vol=v, buy_vol=bv, sell_vol=sv, bid=bid, ask=ask)
    return admit


def gated_book(book: dict[str, Any], admit: dict[int, bool]) -> dict[str, Any]:
    kept = [int(i) for i in book["signals"] if admit.get(int(i), False)]
    out = dict(book)
    out["signals"] = np.asarray(kept, dtype=book["signals"].dtype)
    return out
