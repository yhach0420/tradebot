"""Prior-only stock baselines. Dates < D only. No full-period normalization."""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

import numpy as np

from research.pb1_opening_range_continuation_face_valid_v2.machine import _finite
from research.pb1_v3_1_face_validity_fix import NOISE_LOOKBACK_SESSIONS
from research.pb1_v4_machine_implementation import OPENING_5M_MIN_OBS
from research.pb1_v4_machine_implementation.bars5 import agg_window


def _median(xs: list[float], *, min_obs: int) -> float | None:
    vs = [float(x) for x in xs if _finite(x) and float(x) > 0]
    if len(vs) < int(min_obs):
        return None
    return float(np.median(np.asarray(vs, dtype=float)))


class Opening5mHistory:
    """NORMAL_OPENING_5M_RANGE = median prior 09:00-09:04 5m ranges."""

    def __init__(self) -> None:
        n = int(NOISE_LOOKBACK_SESSIONS)
        self.first5: dict[str, deque] = defaultdict(lambda: deque(maxlen=n))

    def normal(self, symbol: str) -> float | None:
        return _median(list(self.first5[str(symbol)]), min_obs=int(OPENING_5M_MIN_OBS))

    def snapshot(self, symbol: str) -> dict[str, Any]:
        return {
            "NORMAL_OPENING_5M_RANGE": self.normal(symbol),
            "opening_5m_obs": len(list(self.first5[str(symbol)])),
            "noise_lookback_sessions": int(NOISE_LOOKBACK_SESSIONS),
            "noise_min_obs": int(OPENING_5M_MIN_OBS),
            "future_normalization": False,
        }

    def commit_day(self, symbol: str, rec: dict[str, Any], session_idx: list[int]) -> None:
        bar = agg_window(rec, session_idx, "09:00", "09:04")
        if bar is None:
            return
        rng = bar.get("range")
        if _finite(rng) and float(rng) > 0:
            self.first5[str(symbol)].append(float(rng))
