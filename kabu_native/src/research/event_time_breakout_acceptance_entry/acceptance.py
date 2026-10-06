"""Causal five-second acceptance. No duration search."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.event_time_breakout_acceptance_entry import ACCEPTANCE_SEC
from research.event_time_volume_confirmed_impulse import FRESH_SEC, MIN_QTY


def confirm_next(board: dict[str, np.ndarray]) -> np.ndarray:
    fresh = board["fresh_sec"]
    bid = board["bid"]
    ask = board["ask"]
    bid_qty = board["bid_qty"]
    ask_qty = board["ask_qty"]
    ok = (
        board["executable"]
        & ~board["special"]
        & np.isfinite(fresh)
        & (fresh <= FRESH_SEC + 1e-12)
        & np.isfinite(bid)
        & np.isfinite(ask)
        & (bid > 0)
        & (ask > 0)
        & (ask >= bid)
        & np.isfinite(bid_qty)
        & np.isfinite(ask_qty)
        & (bid_qty >= MIN_QTY - 1e-12)
        & (ask_qty >= MIN_QTY - 1e-12)
    )
    n = int(board["t"].size)
    nxt = np.full(n, -1, dtype=np.int32)
    nxt_i = -1
    for index in range(n - 1, -1, -1):
        if bool(ok[index]):
            nxt_i = index
        nxt[index] = nxt_i
    return nxt


def first_confirm(board: dict[str, np.ndarray], nxt: np.ndarray, target_t: float, sess_end: float) -> Optional[dict[str, float]]:
    if float(target_t) > float(sess_end) + 1e-12:
        return None
    t = board["t"]
    n = int(t.size)
    if n == 0:
        return None
    start = int(np.searchsorted(t, float(target_t), side="left"))
    if start >= n:
        return None
    index = int(nxt[start])
    if index < 0:
        return None
    event_t = float(t[index])
    if event_t + 1e-12 < float(target_t) or event_t > float(sess_end) + 1e-12:
        return None
    return {"t": event_t, "bid": float(board["bid"][index]), "ask": float(board["ask"][index])}


def classify_acceptance(
    board: dict[str, np.ndarray],
    nxt: np.ndarray,
    *,
    signal_t: float,
    break_level: float,
    sess_end: float,
) -> dict[str, Any]:
    """Classify one signal. The break level is not recomputed."""
    out: dict[str, Any] = {
        "state": "ACCEPTANCE_UNAVAILABLE",
        "reason": "missing_break_level",
        "confirm_t": None,
        "confirm_bid": None,
        "confirm_ask": None,
    }
    if not (break_level == break_level):
        return out
    deadline = float(signal_t) + float(ACCEPTANCE_SEC)
    if deadline > float(sess_end) + 1e-12:
        out["reason"] = "session_boundary"
        return out
    t = board["t"]
    px = board["px"]
    left = int(np.searchsorted(t, float(signal_t), side="right"))
    right = int(np.searchsorted(t, deadline, side="right"))
    if right > left:
        window_t = t[left:right]
        window_px = px[left:right]
        usable = (window_t <= float(sess_end) + 1e-12) & np.isfinite(window_px) & (window_px > 0)
        prices = window_px[usable]
    else:
        prices = np.asarray([], dtype=float)
    if int(prices.size) == 0:
        out["reason"] = "missing_path"
        return out
    if bool(np.any(prices <= float(break_level) + 1e-12)):
        out["state"] = "BREAKOUT_FAILED_5S"
        out["reason"] = "price_back_through_break"
        return out
    quote = first_confirm(board, nxt, deadline, sess_end)
    if quote is None:
        out["reason"] = "no_confirm_quote"
        return out
    out["confirm_t"] = quote["t"]
    out["confirm_bid"] = quote["bid"]
    out["confirm_ask"] = quote["ask"]
    if not (quote["bid"] > float(break_level) + 1e-12):
        out["state"] = "BREAKOUT_NOT_ACCEPTED"
        out["reason"] = "confirm_bid_not_above_break"
        return out
    out["state"] = "BREAKOUT_ACCEPTED_5S"
    out["reason"] = "held_and_confirm_bid_above_break"
    return out


def _board(times: list[float], prices: list[float], bids: list[float], asks: list[float]) -> dict[str, np.ndarray]:
    n = len(times)
    return {
        "t": np.asarray(times, dtype=float),
        "px": np.asarray(prices, dtype=float),
        "bid": np.asarray(bids, dtype=float),
        "ask": np.asarray(asks, dtype=float),
        "bid_qty": np.full(n, 100.0),
        "ask_qty": np.full(n, 100.0),
        "fresh_sec": np.zeros(n),
        "executable": np.ones(n, dtype=bool),
        "special": np.zeros(n, dtype=bool),
    }


def self_check() -> dict[str, bool]:
    held = _board([0, 1, 2, 5, 6], [10, 12, 12, 13, 14], [10, 11, 11, 12, 13], [11, 12, 12, 13, 14])
    nxt = confirm_next(held)
    accepted = classify_acceptance(held, nxt, signal_t=0.0, break_level=10.0, sess_end=100.0)
    equal_price = _board([0, 2, 5], [10, 10, 12], [10, 11, 12], [11, 12, 13])
    failed = classify_acceptance(equal_price, confirm_next(equal_price), signal_t=0.0, break_level=10.0, sess_end=100.0)
    bid_not_above = _board([0, 2, 5], [10, 12, 12], [10, 11, 10], [11, 12, 11])
    not_accepted = classify_acceptance(bid_not_above, confirm_next(bid_not_above), signal_t=0.0, break_level=10.0, sess_end=100.0)
    early_end = classify_acceptance(held, nxt, signal_t=0.0, break_level=10.0, sess_end=4.0)
    no_path = _board([0, 6], [10, 12], [10, 12], [11, 13])
    missing = classify_acceptance(no_path, confirm_next(no_path), signal_t=0.0, break_level=10.0, sess_end=100.0)
    later_better = _board([0, 1, 5, 6], [10, 12, 11, 15], [10, 11, 10, 14], [11, 12, 11, 15])
    first_only = classify_acceptance(later_better, confirm_next(later_better), signal_t=0.0, break_level=10.0, sess_end=100.0)
    return {
        "held_five_seconds_is_accepted": accepted["state"] == "BREAKOUT_ACCEPTED_5S" and accepted["confirm_ask"] == 13.0,
        "price_equal_to_break_fails": failed["state"] == "BREAKOUT_FAILED_5S",
        "confirm_bid_must_clear_break": not_accepted["state"] == "BREAKOUT_NOT_ACCEPTED",
        "session_boundary_is_unavailable": early_end["state"] == "ACCEPTANCE_UNAVAILABLE" and early_end["reason"] == "session_boundary",
        "missing_path_is_unavailable": missing["state"] == "ACCEPTANCE_UNAVAILABLE" and missing["reason"] == "missing_path",
        "first_confirm_quote_is_used": first_only["state"] == "BREAKOUT_NOT_ACCEPTED" and first_only["confirm_t"] == 5.0,
    }
