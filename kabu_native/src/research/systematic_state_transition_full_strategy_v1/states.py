"""Frozen 1m binary states. No new thresholds. PnL unused."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.simple_tech_entry_family import RCI_CROSS_LEVEL
from research.simple_tech_entry_family.stages import _ok, attach_indicators, trend_up, volume_confirm


def bb_above_mid(ind: dict[str, np.ndarray], i: int) -> bool:
    cl = float(ind["close"][i]) if _ok(ind["close"][i]) else None
    mid = float(ind["bb_mid"][i]) if _ok(ind["bb_mid"][i]) else None
    if cl is None or mid is None:
        return False
    return float(cl) > float(mid)


def rci_above_neg80(ind: dict[str, np.ndarray], i: int) -> bool:
    r = float(ind["rci9"][i]) if _ok(ind["rci9"][i]) else None
    if r is None:
        return False
    return float(r) > float(RCI_CROSS_LEVEL)


def close_above_vwap(ind: dict[str, np.ndarray], i: int) -> bool:
    cl = float(ind["close"][i]) if _ok(ind["close"][i]) else None
    vw = float(ind["vwap"][i]) if _ok(ind["vwap"][i]) else None
    if cl is None or vw is None:
        return False
    return float(cl) > float(vw)


STATE_FNS = {
    "S_MA_TREND_UP": trend_up,
    "S_BB_ABOVE_MID": bb_above_mid,
    "S_RCI_ABOVE_NEG80": rci_above_neg80,
    "S_VOL_CONFIRM_1M": volume_confirm,
    "S_CLOSE_ABOVE_VWAP": close_above_vwap,
}


def indicators(raw: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    return attach_indicators(raw)


def state_at(ind: dict[str, np.ndarray], state_id: str, i: int) -> bool:
    fn = STATE_FNS[state_id]
    return bool(fn(ind, int(i)))


def state_series(ind: dict[str, np.ndarray], state_id: str) -> np.ndarray:
    n = int(ind["close"].size)
    out = np.zeros(n, dtype=bool)
    for i in range(n):
        out[i] = state_at(ind, state_id, i)
    return out


def all_state_series(ind: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    return {sid: state_series(ind, sid) for sid in STATE_FNS}


def persist_next_indices(s: np.ndarray) -> list[int]:
    n = int(s.size)
    out: list[int] = []
    pending: int | None = None
    for i in range(n):
        if pending is not None and i == pending + 1:
            if bool(s[i]):
                out.append(i)
            pending = None
        prev = bool(s[i - 1]) if i >= 1 else False
        now = bool(s[i])
        if now and not prev:
            pending = i
    return out


def handoff_next_indices(a: np.ndarray, b: np.ndarray) -> list[int]:
    n = int(a.size)
    out: list[int] = []
    pending: int | None = None
    for i in range(n):
        if pending is not None and i == pending + 1:
            a_now = bool(a[i])
            b_now = bool(b[i])
            b_prev = bool(b[i - 1]) if i >= 1 else False
            if a_now and b_now and not b_prev:
                out.append(i)
            pending = None
        a_prev = bool(a[i - 1]) if i >= 1 else False
        a_now = bool(a[i])
        if a_now and not a_prev:
            pending = i
    return out
