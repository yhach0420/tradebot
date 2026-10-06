"""Passive bid for five seconds. The limit is the signal bid. No price improvement."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.event_time_volume_confirmed_impulse import FRESH_SEC, MIN_QTY
from research.event_time_volume_confirmed_impulse_entry import WAIT_SEC


def _fresh(board: dict[str, np.ndarray], index: int) -> bool:
    fresh = float(board["fresh_sec"][index])
    return bool(
        board["executable"][index]
        and not board["special"][index]
        and fresh == fresh
        and fresh <= FRESH_SEC + 1e-12
    )


def passive_bid_fill(
    board: dict[str, np.ndarray],
    *,
    signal_t: float,
    limit_price: float,
    sess_end: float,
) -> Optional[float]:
    """First later qualifying ask at or below the signal bid inside (T, T+5s]."""
    t = board["t"]
    n = int(t.size)
    if n == 0 or not (limit_price == limit_price and limit_price > 0):
        return None
    end = min(float(signal_t) + float(WAIT_SEC), float(sess_end))
    start = int(np.searchsorted(t, float(signal_t), side="right"))
    for index in range(start, n):
        event_t = float(t[index])
        if event_t > end + 1e-12:
            break
        if not _fresh(board, index):
            continue
        bid = float(board["bid"][index])
        ask = float(board["ask"][index])
        bid_qty = float(board["bid_qty"][index])
        ask_qty = float(board["ask_qty"][index])
        if not (
            bid == bid
            and ask == ask
            and bid > 0
            and ask > 0
            and ask >= bid
            and bid_qty == bid_qty
            and ask_qty == ask_qty
            and bid_qty >= MIN_QTY - 1e-12
            and ask_qty >= MIN_QTY - 1e-12
            and ask <= float(limit_price) + 1e-12
        ):
            continue
        return event_t
    return None


def future_bid(
    board: dict[str, np.ndarray],
    nxt: np.ndarray,
    target_t: float,
    sess_end: float,
) -> Optional[float]:
    """First qualifying executable bid at or after the target, inside the session."""
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
    bid = float(board["bid"][index])
    if not (bid == bid and bid > 0):
        return None
    return bid


def bid_next(board: dict[str, np.ndarray]) -> np.ndarray:
    n = int(board["t"].size)
    fresh = board["fresh_sec"]
    bid = board["bid"]
    qty = board["bid_qty"]
    ok = (
        board["executable"]
        & ~board["special"]
        & np.isfinite(fresh)
        & (fresh <= FRESH_SEC + 1e-12)
        & np.isfinite(bid)
        & (bid > 0)
        & np.isfinite(qty)
        & (qty >= MIN_QTY - 1e-12)
    )
    nxt = np.full(n, -1, dtype=np.int32)
    nxt_i = -1
    for index in range(n - 1, -1, -1):
        if bool(ok[index]):
            nxt_i = index
        nxt[index] = nxt_i
    return nxt


def path_excursions(board: dict[str, np.ndarray], signal_t: float, fill_t: float, trigger: float) -> tuple[Optional[float], Optional[float]]:
    """MFE and MAE in bps from the trigger price over (signal_t, fill_t]."""
    if not (trigger == trigger and trigger > 0):
        return None, None
    t = board["t"]
    px = board["px"]
    left = int(np.searchsorted(t, float(signal_t), side="right"))
    right = int(np.searchsorted(t, float(fill_t), side="right"))
    if right <= left:
        return None, None
    sample = px[left:right]
    sample = sample[np.isfinite(sample) & (sample > 0)]
    if int(sample.size) == 0:
        return None, None
    span = 10000.0 / float(trigger)
    return float((np.max(sample) - trigger) * span), float((np.min(sample) - trigger) * span)


def self_check() -> dict[str, bool]:
    board = {
        "t": np.array([0.0, 1.0, 2.0, 6.0], dtype=float),
        "bid": np.array([100.0, 99.0, 100.0, 90.0], dtype=float),
        "ask": np.array([100.0, 99.0, 101.0, 90.0], dtype=float),
        "bid_qty": np.array([100.0, 100.0, 100.0, 100.0], dtype=float),
        "ask_qty": np.array([100.0, 50.0, 100.0, 100.0], dtype=float),
        "fresh_sec": np.zeros(4, dtype=float),
        "executable": np.ones(4, dtype=bool),
        "special": np.zeros(4, dtype=bool),
        "px": np.array([100.0, 99.0, 101.0, 90.0], dtype=float),
    }
    # The event at t=0 is the signal. The t=1 ask is at the limit but its ask qty is below 100.
    thin = passive_bid_fill(board, signal_t=0.0, limit_price=100.0, sess_end=100.0)
    same_time = {
        **board,
        "ask": np.array([100.0, 101.0, 101.0, 101.0], dtype=float),
        "ask_qty": np.full(4, 100.0),
    }
    board["ask_qty"][1] = 100.0
    filled_t = passive_bid_fill(board, signal_t=0.0, limit_price=100.0, sess_end=100.0)
    mfe, mae = path_excursions(board, 0.0, 1.0, 100.0)
    return {
        "same_timestamp_does_not_fill": passive_bid_fill(same_time, signal_t=0.0, limit_price=100.0, sess_end=100.0) is None,
        "thin_ask_does_not_fill": thin is None,
        "qualifying_ask_fills_without_improvement": filled_t == 1.0,
        "window_excludes_sixth_second": passive_bid_fill(
            {
                **board,
                "ask": np.array([101.0, 101.0, 101.0, 90.0], dtype=float),
                "ask_qty": np.full(4, 100.0),
            },
            signal_t=0.0,
            limit_price=100.0,
            sess_end=100.0,
        )
        is None,
        "path_uses_prices_after_signal": mfe == -100.0 and mae == -100.0,
    }
