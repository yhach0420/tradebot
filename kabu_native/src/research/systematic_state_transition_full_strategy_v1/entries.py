"""PERSIST_NEXT / HANDOFF_NEXT plus Recovery R2 canary ENTRY. No static AND."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.recovery_sequence_full_strategy_architecture_v1.entries import signal_indices as recovery_signal_indices
from research.systematic_state_transition_full_strategy_v1.states import all_state_series, handoff_next_indices, persist_next_indices
from research.systematic_state_transition_full_strategy_v1.spec import frozen_library


def library_signal_indices(ind: dict[str, np.ndarray], candidate: dict[str, Any]) -> list[int]:
    series = all_state_series(ind)
    tmpl = str(candidate["TEMPLATE"])
    if tmpl == "PERSIST_NEXT":
        return persist_next_indices(series[str(candidate["STATE_A"])])
    if tmpl == "HANDOFF_NEXT":
        return handoff_next_indices(series[str(candidate["STATE_A"])], series[str(candidate["STATE_B"])])
    raise RuntimeError(f"UNKNOWN_TEMPLATE:{tmpl}")


def canary_r2_indices(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    vwap: np.ndarray,
) -> list[int]:
    return recovery_signal_indices("R2", open_, high, low, close, volume, vwap)


def frozen_candidates() -> list[dict[str, Any]]:
    return frozen_library()
