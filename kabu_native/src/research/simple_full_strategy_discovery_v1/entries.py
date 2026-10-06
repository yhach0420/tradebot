"""Five frozen ENTRY families. First-cross of full state. No extra filters."""
from __future__ import annotations

from typing import Any, Callable

import numpy as np

from research.new_entry_breakout_continuation_v1.rule import session_vwap
from research.simple_full_strategy_discovery_v1 import VOLUME_WINDOW

ENTRY_RULE_TEXT = {
    "E1": "Close[i] > Close[i-1] AND Close[i] > VWAP[i]. First-cross of full state.",
    "E2": "Close[i] > Close[i-1] AND Volume[i] > median(Volume[i-10:i]). First-cross of full state.",
    "E3": "Low[i] > Low[i-1] AND Close[i] > Open[i]. First-cross of full state.",
    "E4": "Close[i-1] < VWAP[i-1] AND Close[i] > VWAP[i]. First-cross of full state.",
    "E5": "Close[i] > High[i-1] AND Close[i] > Open[i]. First-cross of full state.",
}


def _fin(arr: np.ndarray, i: int) -> float | None:
    if i < 0 or i >= int(arr.size):
        return None
    v = float(arr[i])
    return v if v == v else None


def _state_e1(open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray, volume: np.ndarray, vwap: np.ndarray, i: int) -> bool:
    c0 = _fin(close, i)
    c1 = _fin(close, i - 1)
    vw = _fin(vwap, i)
    if c0 is None or c1 is None or vw is None:
        return False
    return c0 > c1 and c0 > vw


def _state_e2(open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray, volume: np.ndarray, vwap: np.ndarray, i: int) -> bool:
    c0 = _fin(close, i)
    c1 = _fin(close, i - 1)
    cur = _fin(volume, i)
    if c0 is None or c1 is None or cur is None:
        return False
    if i < int(VOLUME_WINDOW):
        return False
    prev = volume[i - int(VOLUME_WINDOW) : i]
    if int(prev.size) != int(VOLUME_WINDOW) or not np.all(np.isfinite(prev)):
        return False
    return c0 > c1 and cur > float(np.median(prev))


def _state_e3(open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray, volume: np.ndarray, vwap: np.ndarray, i: int) -> bool:
    lo0 = _fin(low, i)
    lo1 = _fin(low, i - 1)
    c0 = _fin(close, i)
    op = _fin(open_, i)
    if lo0 is None or lo1 is None or c0 is None or op is None:
        return False
    return lo0 > lo1 and c0 > op


def _state_e4(open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray, volume: np.ndarray, vwap: np.ndarray, i: int) -> bool:
    c0 = _fin(close, i)
    c1 = _fin(close, i - 1)
    v0 = _fin(vwap, i)
    v1 = _fin(vwap, i - 1)
    if c0 is None or c1 is None or v0 is None or v1 is None:
        return False
    return c1 < v1 and c0 > v0


def _state_e5(open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray, volume: np.ndarray, vwap: np.ndarray, i: int) -> bool:
    c0 = _fin(close, i)
    h1 = _fin(high, i - 1)
    op = _fin(open_, i)
    if c0 is None or h1 is None or op is None:
        return False
    return c0 > h1 and c0 > op


STATES: dict[str, Callable[..., bool]] = {
    "E1": _state_e1,
    "E2": _state_e2,
    "E3": _state_e3,
    "E4": _state_e4,
    "E5": _state_e5,
}


def signal_at(
    entry_id: str,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    vwap: np.ndarray,
    i: int,
) -> dict[str, Any]:
    fn = STATES[entry_id]
    now = bool(fn(open_, high, low, close, volume, vwap, i))
    prev = bool(fn(open_, high, low, close, volume, vwap, i - 1)) if i >= 1 else False
    return {"STATE": now, "STATE_PREV": prev, "FIRST_CROSS": bool(now and not prev), "SIGNAL": bool(now and not prev)}


def session_vwap_arr(close: np.ndarray, volume: np.ndarray, vwap_num: np.ndarray | None) -> np.ndarray:
    return session_vwap(close, volume, vwap_num)
