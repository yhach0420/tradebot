"""Causal state episodes. Onset is FALSE→TRUE only. No future-selected minute."""
from __future__ import annotations

from typing import Any


def episode_stream(flag_seq: list[bool]) -> dict[str, Any]:
    """Walk a boolean state path. Onset at FALSE→TRUE. Duration until FALSE. No lookahead."""
    prev = False
    onsets: list[int] = []
    durations: dict[int, int] = {}
    open_i: int | None = None
    dur = 0
    qualified = 0
    for i, now in enumerate(flag_seq):
        flag = bool(now)
        if flag:
            qualified += 1
        if flag and not prev:
            onsets.append(i)
            open_i = i
            dur = 1
        elif flag and prev:
            dur += 1
        elif (not flag) and prev and open_i is not None:
            durations[open_i] = dur
            open_i = None
            dur = 0
        prev = flag
    if open_i is not None:
        durations[open_i] = dur
    return {
        "onsets": onsets,
        "durations": durations,
        "qualified_n": qualified,
        "episode_n": len(onsets),
        "FUTURE_EPISODE_SELECTION_N": 0,
    }


def duration_stats(xs: list[int]) -> dict[str, Any]:
    import numpy as np

    if not xs:
        return {"n": 0, "mean": None, "median": None, "p90": None}
    arr = np.asarray(xs, dtype=float)
    return {
        "n": int(arr.size),
        "mean": float(np.mean(arr)),
        "median": float(np.median(arr)),
        "p90": float(np.quantile(arr, 0.90)),
    }
