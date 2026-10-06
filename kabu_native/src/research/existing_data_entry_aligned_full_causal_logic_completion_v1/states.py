"""Ternary 1m states. UNKNOWN != FALSE. No new thresholds."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.simple_tech_entry_family import EMA_SLOPE_BARS, RCI_CROSS_LEVEL, VOLUME_MEDIAN_BARS, VOLUME_MULT
from research.simple_tech_entry_family.stages import _ok, attach_indicators
from research.existing_data_entry_aligned_full_causal_logic_completion_v1 import STATE_IDS

TRUE = 1
FALSE = 0
UNKNOWN = -1


def indicators(raw: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    return attach_indicators(raw)


def _ma(ind: dict[str, np.ndarray], i: int) -> int:
    e9 = float(ind["ema9"][i]) if _ok(ind["ema9"][i]) else None
    e21 = float(ind["ema21"][i]) if _ok(ind["ema21"][i]) else None
    j = i - int(EMA_SLOPE_BARS)
    if j < 0 or e9 is None or e21 is None or not _ok(ind["ema21"][j]):
        return UNKNOWN
    return TRUE if (e9 > e21 and float(e21) > float(ind["ema21"][j])) else FALSE


def _bb(ind: dict[str, np.ndarray], i: int) -> int:
    cl = float(ind["close"][i]) if _ok(ind["close"][i]) else None
    mid = float(ind["bb_mid"][i]) if _ok(ind["bb_mid"][i]) else None
    if cl is None or mid is None:
        return UNKNOWN
    return TRUE if float(cl) > float(mid) else FALSE


def _rci(ind: dict[str, np.ndarray], i: int) -> int:
    r = float(ind["rci9"][i]) if _ok(ind["rci9"][i]) else None
    if r is None:
        return UNKNOWN
    return TRUE if float(r) > float(RCI_CROSS_LEVEL) else FALSE


def _vol(ind: dict[str, np.ndarray], i: int) -> int:
    w = int(VOLUME_MEDIAN_BARS)
    if i < w:
        return UNKNOWN
    vol = float(ind["volume"][i]) if _ok(ind["volume"][i]) else None
    if vol is None or vol <= 0:
        return UNKNOWN
    base = ind["volume"][i - w : i]
    if int(base.size) != w or not np.all(np.isfinite(base)):
        return UNKNOWN
    med = float(np.median(base))
    return TRUE if vol >= float(VOLUME_MULT) * med else FALSE


def _vwap(ind: dict[str, np.ndarray], i: int) -> int:
    cl = float(ind["close"][i]) if _ok(ind["close"][i]) else None
    vw = float(ind["vwap"][i]) if _ok(ind["vwap"][i]) else None
    if cl is None or vw is None:
        return UNKNOWN
    return TRUE if float(cl) > float(vw) else FALSE


STATE_FNS = {
    "S_MA_TREND_UP": _ma,
    "S_BB_ABOVE_MID": _bb,
    "S_RCI_ABOVE_NEG80": _rci,
    "S_VOL_CONFIRM_1M": _vol,
    "S_CLOSE_ABOVE_VWAP": _vwap,
}


def state_series(ind: dict[str, np.ndarray], state_id: str) -> np.ndarray:
    n = int(ind["close"].size)
    out = np.full(n, UNKNOWN, dtype=np.int8)
    fn = STATE_FNS[state_id]
    for i in range(n):
        out[i] = int(fn(ind, i))
    return out


def all_state_series(ind: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    return {sid: state_series(ind, sid) for sid in STATE_IDS}


def persist_next_indices(s: np.ndarray) -> list[int]:
    n = int(s.size)
    out: list[int] = []
    pending: int | None = None
    for i in range(n):
        if pending is not None and i == pending + 1:
            if int(s[i]) == TRUE:
                out.append(i)
            pending = None
        prev = int(s[i - 1]) if i >= 1 else UNKNOWN
        now = int(s[i])
        if now == TRUE and prev == FALSE:
            pending = i
    return out


def handoff_next_indices(a: np.ndarray, b: np.ndarray) -> list[int]:
    n = int(a.size)
    out: list[int] = []
    pending: int | None = None
    for i in range(n):
        if pending is not None and i == pending + 1:
            a_now = int(a[i])
            b_now = int(b[i])
            b_prev = int(b[i - 1]) if i >= 1 else UNKNOWN
            if a_now == TRUE and b_now == TRUE and b_prev == FALSE:
                out.append(i)
            pending = None
        a_prev = int(a[i - 1]) if i >= 1 else UNKNOWN
        a_now = int(a[i])
        if a_now == TRUE and a_prev == FALSE:
            pending = i
    return out


def fire_holding_false(idxs: list[int], series: dict[str, np.ndarray], holding: list[str]) -> int | None:
    for i in idxs:
        for sid in holding:
            if int(series[str(sid)][int(i)]) == FALSE:
                return int(i)
    return None


assert tuple(STATE_FNS) == STATE_IDS
assert UNKNOWN != FALSE
assert TRUE != FALSE
