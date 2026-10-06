"""O1 persist-next, O2 handoff-next, O3 reclaim-accept-next. Frozen historical operators. No new thresholds."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.recovery_sequence_full_strategy_architecture_v1.entries import signal_indices as recovery_signal_indices
from research.systematic_state_transition_full_strategy_v1.states import handoff_next_indices, persist_next_indices


def persist_next_at(
    now: dict[str, bool],
    prev: dict[str, bool],
    prev2: dict[str, bool],
    pid: str,
) -> bool:
    onset = (not bool(prev2.get(pid))) and bool(prev.get(pid))
    return bool(onset and bool(now.get(pid)))


def handoff_next_at(
    now: dict[str, bool],
    prev: dict[str, bool],
    prev2: dict[str, bool],
    a: str,
    b: str,
) -> bool:
    onset_a = (not bool(prev2.get(a))) and bool(prev.get(a))
    onset_b = (not bool(prev.get(b))) and bool(now.get(b))
    return bool(onset_a and bool(now.get(a)) and onset_b)


def evaluate_row(
    cand: dict[str, Any],
    now: dict[str, bool],
    prev: dict[str, bool],
    prev2: dict[str, bool],
) -> bool:
    template = str(cand["TEMPLATE"])
    prim = list(cand["PRIMITIVES"])
    if template == "O1_PERSIST_NEXT":
        return persist_next_at(now, prev, prev2, prim[0])
    if template == "O2_HANDOFF_NEXT":
        return handoff_next_at(now, prev, prev2, prim[0], prim[1])
    if template == "O3_RECLAIM_ACCEPT_NEXT":
        return False
    raise ValueError(template)


def series_persist_indices(s: np.ndarray) -> list[int]:
    return persist_next_indices(s)


def series_handoff_indices(a: np.ndarray, b: np.ndarray) -> list[int]:
    return handoff_next_indices(a, b)


def reclaim_accept_indices(
    rid: str,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    vwap: np.ndarray,
) -> list[int]:
    return recovery_signal_indices(str(rid), open_, high, low, close, volume, vwap)
