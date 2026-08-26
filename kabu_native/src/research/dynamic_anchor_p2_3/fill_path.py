"""WAIT_SEC=1.0 ask-path description. Does not change fill contract or test other waits."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.e1_x34a_execution_policy.arms import _bps, _row_ask_ok
from small_paper.v1r_primary_runtime import WAIT_SEC


def last_bid_at_or_before(board: dict[str, np.ndarray], t: float) -> Optional[float]:
    arr = board.get("t")
    if arr is None or arr.size == 0:
        return None
    i = int(np.searchsorted(arr, float(t), side="right") - 1)
    if i < 0:
        return None
    bid = float(board["bid"][i])
    if not np.isfinite(bid) or bid <= 0:
        return None
    return bid


def wait_ask_path(
    board: dict[str, np.ndarray],
    *,
    signal_time: float,
    limit_bid: Optional[float],
    wait_sec: float = WAIT_SEC,
) -> dict[str, Any]:
    """Causal ask path on [signal_time, signal_time+wait_sec]. WAIT_SEC frozen at 1.0."""
    out: dict[str, Any] = {
        "signal_time": float(signal_time),
        "wait_sec": float(wait_sec),
        "limit_bid_at_t1": None if limit_bid is None else float(limit_bid),
        "first_ask_after_t1": None,
        "min_ask_during_wait": None,
        "last_ask_before_expiry": None,
        "first_ask_minus_limit_bps": None,
        "min_ask_minus_limit_bps": None,
        "valid_ask_n": 0,
    }
    t = board.get("t")
    if t is None or t.size == 0 or limit_bid is None or not np.isfinite(float(limit_bid)) or float(limit_bid) <= 0:
        return out
    lim = float(limit_bid)
    lim_t = float(signal_time) + float(wait_sec)
    i0 = int(np.searchsorted(t, float(signal_time), side="left"))
    first = None
    min_ask = None
    last = None
    n = 0
    for i in range(i0, t.size):
        ti = float(t[i])
        if ti + 1e-12 < float(signal_time):
            continue
        if ti > lim_t + 1e-12:
            break
        if not _row_ask_ok(board, i):
            continue
        ask = float(board["ask"][i])
        n += 1
        if first is None:
            first = ask
        if min_ask is None or ask < min_ask:
            min_ask = ask
        last = ask
    out["valid_ask_n"] = n
    out["first_ask_after_t1"] = first
    out["min_ask_during_wait"] = min_ask
    out["last_ask_before_expiry"] = last
    if first is not None:
        out["first_ask_minus_limit_bps"] = float(_bps(first, lim))
    if min_ask is not None:
        out["min_ask_minus_limit_bps"] = float(_bps(min_ask, lim))
    return out
