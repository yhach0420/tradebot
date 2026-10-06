"""Three frozen next-bar Recovery ENTRY architectures. Exact one bar after VWAP reclaim. No extra filters."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.new_entry_breakout_continuation_v1.rule import session_vwap
from research.recovery_sequence_full_strategy_architecture_v1 import VOLUME_WINDOW

ENTRY_RULE_TEXT = {
    "R1": (
        "RECLAIM_ARM at r: Close[r-1] < VWAP[r-1] AND Close[r] > VWAP[r]. "
        "Exact next bar c=r+1: Close[c] > VWAP[c] AND Close[c] > Close[r]. "
        "Fail aborts arm. No wait for c+1."
    ),
    "R2": (
        "Same RECLAIM_ARM. Next bar c: Low[c] > VWAP[c] AND Close[c] > Open[c]. "
        "Fail aborts arm."
    ),
    "R3": (
        "Same RECLAIM_ARM. Next bar c: Close[c] > VWAP[c] AND "
        "Volume[c] > median(Volume[c-10:c]). No volume multiplier. Fail aborts arm."
    ),
}


def _fin(arr: np.ndarray, i: int) -> float | None:
    if i < 0 or i >= int(arr.size):
        return None
    v = float(arr[i])
    return v if v == v else None


def reclaim_arm(close: np.ndarray, vwap: np.ndarray, r: int) -> bool:
    c0 = _fin(close, r)
    c1 = _fin(close, r - 1)
    v0 = _fin(vwap, r)
    v1 = _fin(vwap, r - 1)
    if c0 is None or c1 is None or v0 is None or v1 is None:
        return False
    return c1 < v1 and c0 > v0


def confirm_r1(close: np.ndarray, vwap: np.ndarray, r: int, c: int) -> bool:
    cc = _fin(close, c)
    vw = _fin(vwap, c)
    cr = _fin(close, r)
    if cc is None or vw is None or cr is None:
        return False
    return cc > vw and cc > cr


def confirm_r2(open_: np.ndarray, low: np.ndarray, close: np.ndarray, vwap: np.ndarray, c: int) -> bool:
    lo = _fin(low, c)
    vw = _fin(vwap, c)
    cl = _fin(close, c)
    op = _fin(open_, c)
    if lo is None or vw is None or cl is None or op is None:
        return False
    return lo > vw and cl > op


def confirm_r3(close: np.ndarray, volume: np.ndarray, vwap: np.ndarray, c: int) -> bool:
    cl = _fin(close, c)
    vw = _fin(vwap, c)
    cur = _fin(volume, c)
    if cl is None or vw is None or cur is None:
        return False
    if c < int(VOLUME_WINDOW):
        return False
    prev = volume[c - int(VOLUME_WINDOW) : c]
    if int(prev.size) != int(VOLUME_WINDOW) or not np.all(np.isfinite(prev)):
        return False
    return cl > vw and cur > float(np.median(prev))


def confirm(entry_id: str, open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray, volume: np.ndarray, vwap: np.ndarray, r: int, c: int) -> bool:
    if entry_id == "R1":
        return confirm_r1(close, vwap, r, c)
    if entry_id == "R2":
        return confirm_r2(open_, low, close, vwap, c)
    if entry_id == "R3":
        return confirm_r3(close, volume, vwap, c)
    return False


def signal_indices(
    entry_id: str,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    vwap: np.ndarray,
) -> list[int]:
    n = int(close.size)
    out: list[int] = []
    pending_r: int | None = None
    for i in range(n):
        if pending_r is not None and i == pending_r + 1:
            if confirm(entry_id, open_, high, low, close, volume, vwap, pending_r, i):
                out.append(i)
            pending_r = None
        if reclaim_arm(close, vwap, i):
            pending_r = i
    return out


def session_vwap_arr(close: np.ndarray, volume: np.ndarray, vwap_num: np.ndarray | None) -> np.ndarray:
    return session_vwap(close, volume, vwap_num)


def already_executed_check() -> dict[str, Any]:
    rows = [
        {
            "study": "E4_X2_Z3",
            "state_sequence": "same-bar Close reclaim first-cross, signal at reclaim bar",
            "execution": "passive floor(mid) 5s queue-assumed ask-cross fill at limit",
            "exit": "Z3",
            "portfolio": "CAP=5 occupancy",
            "exact_duplicate": False,
        },
        {
            "study": "NEW_ENTRY_VWAP_REJECTION_RECLAIM_V1",
            "state_sequence": "same-bar Low<VWAP AND Close>VWAP AND Close>Open",
            "execution": "MID markout / ASK_BID, not this executable pair",
            "exit": "none (ENTRY-only)",
            "portfolio": "not Full Causal strategy",
            "exact_duplicate": False,
        },
        {
            "study": "BREAKOUT_CONTINUATION / FDR / RPFE / Simple-Tech / X6 / EC2",
            "state_sequence": "breakout, failed-breakdown, or Simple-Tech E4 inside-tick",
            "execution": "E4 inside-1tick passive or ASK_CROSS",
            "exit": "various; not next-bar reclaim library",
            "portfolio": "mixed",
            "exact_duplicate": False,
        },
    ]
    return {
        "duplicate": False,
        "reason": "Next-bar reclaim confirmation + marketable Ask1 / Ask1<=mid watch is not an exact prior identity. Shared Z3/occupancy primitives are not a duplicate strategy.",
        "comparisons": rows,
    }
