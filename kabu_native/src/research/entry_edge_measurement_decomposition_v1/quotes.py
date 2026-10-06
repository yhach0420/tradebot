"""Quote markout identities. Diagnostic formulas only. No thresholds."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.new_entry_breakout_continuation_v1.harvest import CONTINUOUS_STATES, _fresh_ok


def _f(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def mid(bid: Any, ask: Any) -> Optional[float]:
    b = _f(bid)
    a = _f(ask)
    if b is None or a is None or b <= 0 or a <= 0:
        return None
    return (b + a) / 2.0


def bps_ratio(num: Any, den: Any) -> Optional[float]:
    n = _f(num)
    d = _f(den)
    if n is None or d is None or d <= 0 or n <= 0:
        return None
    return (n / d - 1.0) * 10000.0


def spread_bps(bid: Any, ask: Any) -> Optional[float]:
    m = mid(bid, ask)
    b = _f(bid)
    a = _f(ask)
    if m is None or b is None or a is None or m <= 0:
        return None
    return (a - b) / m * 10000.0


def entry_half_spread_bps(bid0: Any, ask0: Any) -> Optional[float]:
    m = mid(bid0, ask0)
    a = _f(ask0)
    if m is None or a is None or m <= 0:
        return None
    return (a - m) / m * 10000.0


def exit_half_spread_bps(bidh: Any, askh: Any) -> Optional[float]:
    m = mid(bidh, askh)
    b = _f(bidh)
    if m is None or b is None or m <= 0:
        return None
    return (m - b) / m * 10000.0


def residual_ask_bid(mid_m: Any, ask_bid: Any, entry_hs: Any, exit_hs: Any) -> Optional[float]:
    parts = [_f(mid_m), _f(ask_bid), _f(entry_hs), _f(exit_hs)]
    if any(p is None for p in parts):
        return None
    approx = float(parts[0]) - float(parts[2]) - float(parts[3])
    return float(parts[1]) - approx


def _ask_ok(board: dict[str, np.ndarray], i: int) -> bool:
    if not bool(board["executable"][i]) or not bool(board["continuous"][i]):
        return False
    if bool(board["special"][i]):
        return False
    if str(board["board_execution_state"][i] or "") not in CONTINUOUS_STATES:
        return False
    if not _fresh_ok(board["ask_fresh_sec"][i]):
        return False
    ask = float(board["ask"][i])
    aq = float(board["ask_qty"][i])
    if not (ask == ask) or ask <= 0:
        return False
    if not (aq == aq) or aq <= 0:
        return False
    return True


def last_ask_before(board: dict[str, np.ndarray], *, t0: float, mark_t: float) -> tuple[Optional[float], Optional[float]]:
    t = board.get("t")
    if t is None or int(t.size) == 0:
        return None, None
    i_hi = int(np.searchsorted(t, float(mark_t), side="right") - 1)
    i_lo = int(np.searchsorted(t, float(t0), side="right"))
    for i in range(i_hi, i_lo - 1, -1):
        if i < 0:
            break
        ti = float(t[i])
        if ti > float(mark_t) + 1e-12:
            continue
        if ti <= float(t0) + 1e-12:
            break
        if _ask_ok(board, i):
            return float(board["ask"][i]), ti
    return None, None
