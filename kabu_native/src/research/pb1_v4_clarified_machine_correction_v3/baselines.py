"""Same-clock prior-only opening 5m baselines. Not first-bar-only for all three."""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

import numpy as np

from research.pb1_opening_range_continuation_face_valid_v2.machine import _finite
from research.pb1_v3_1_face_validity_fix import NOISE_LOOKBACK_SESSIONS
from research.pb1_v4_clarified_machine_correction_v3 import OPENING_5M_MIN_OBS
from research.pb1_v4_machine_implementation.bars5 import FIVE_WINDOWS_OPEN, agg_window

CLOCKS = FIVE_WINDOWS_OPEN


def _median(xs: list[float], *, min_obs: int) -> float | None:
    vs = [float(x) for x in xs if _finite(x) and float(x) > 0]
    if len(vs) < int(min_obs):
        return None
    return float(np.median(np.asarray(vs, dtype=float)))


def _iqr(xs: list[float]) -> float | None:
    vs = [float(x) for x in xs if _finite(x) and float(x) > 0]
    if len(vs) < 4:
        return None
    arr = np.asarray(vs, dtype=float)
    return float(np.subtract(*np.percentile(arr, [75, 25])))


class SameClockOpeningHistory:
    def __init__(self) -> None:
        n = int(NOISE_LOOKBACK_SESSIONS)
        self.by_clock: dict[str, dict[tuple[str, str], deque]] = defaultdict(lambda: defaultdict(lambda: deque(maxlen=n)))
        self.dates: dict[str, dict[tuple[str, str], deque]] = defaultdict(lambda: defaultdict(lambda: deque(maxlen=n)))

    def median(self, symbol: str, t0: str, t1: str) -> float | None:
        return _median(list(self.by_clock[str(symbol)][(t0, t1)]), min_obs=int(OPENING_5M_MIN_OBS))

    def snapshot_bar(self, symbol: str, bar: dict[str, Any], *, atr20: Any) -> dict[str, Any]:
        t0, t1 = str(bar.get("t0") or ""), str(bar.get("t1") or "")
        med = self.median(symbol, t0, t1)
        xs = list(self.by_clock[str(symbol)][(t0, t1)])
        rng = bar.get("range")
        return {
            "clock": f"{t0}-{t1}",
            "observation_n": len(xs),
            "median": med,
            "robust_dispersion_iqr": _iqr(xs),
            "current_over_baseline": (float(rng) / float(med)) if _finite(rng) and _finite(med) and float(med) > 0 else None,
            "atr20": float(atr20) if _finite(atr20) else None,
            "date_coverage": list(self.dates[str(symbol)][(t0, t1)]),
            "future_dates": False,
        }

    def snapshot_open(self, symbol: str, bars: list[dict[str, Any]], *, atr20: Any) -> dict[str, Any]:
        clocks = [self.snapshot_bar(symbol, b, atr20=atr20) for b in bars[:3]]
        meds = [c.get("median") for c in clocks]
        mean_med = None
        finite = [float(m) for m in meds if _finite(m) and float(m) > 0]
        if finite:
            mean_med = float(sum(finite) / len(finite))
        return {
            "same_clock": clocks,
            "mean_clock_median": mean_med,
            "first_bar_only_used_for_all_three": False,
            "atr20": float(atr20) if _finite(atr20) else None,
        }

    def commit_day(self, symbol: str, rec: dict[str, Any], session_idx: list[int], date: str) -> None:
        for t0, t1 in CLOCKS:
            bar = agg_window(rec, session_idx, t0, t1)
            if bar is None:
                continue
            rng = bar.get("range")
            if _finite(rng) and float(rng) > 0:
                self.by_clock[str(symbol)][(t0, t1)].append(float(rng))
                self.dates[str(symbol)][(t0, t1)].append(str(date))
