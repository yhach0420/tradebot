"""Symbol/date/clock noise units. Prior 20 sessions only. No future day. No full-period norm."""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

import numpy as np

from research.pb1_v3_1_face_validity_fix import NOISE_LOOKBACK_SESSIONS, NOISE_MIN_OBS


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _median(xs: list[float]) -> float | None:
    vs = [float(x) for x in xs if _finite(x) and float(x) > 0]
    if len(vs) < int(NOISE_MIN_OBS):
        return None
    return float(np.median(np.asarray(vs, dtype=float)))


class NoiseHistory:
    """NORMAL_1M_RANGE at the same clock from prior completed sessions."""

    def __init__(self) -> None:
        n = int(NOISE_LOOKBACK_SESSIONS)
        self.tod: dict[str, dict[str, deque]] = defaultdict(lambda: defaultdict(lambda: deque(maxlen=n)))
        self.or15: dict[str, deque] = defaultdict(lambda: deque(maxlen=n))
        self.open_1m: dict[str, deque] = defaultdict(lambda: deque(maxlen=n))

    def normal_1m(self, symbol: str, clock: str | None) -> float | None:
        if not clock:
            return None
        return _median(list(self.tod[str(symbol)][str(clock)]))

    def normal_or15(self, symbol: str) -> float | None:
        return _median(list(self.or15[str(symbol)]))

    def normal_opening_1m(self, symbol: str) -> float | None:
        return _median(list(self.open_1m[str(symbol)]))

    def snapshot(self, symbol: str, clock: str | None) -> dict[str, Any]:
        n1m = self.normal_1m(symbol, clock)
        return {
            "NORMAL_1M_RANGE": n1m,
            "normal_OR15": self.normal_or15(symbol),
            "normal_opening_1m_range": self.normal_opening_1m(symbol),
            "noise_lookback_sessions": int(NOISE_LOOKBACK_SESSIONS),
            "noise_obs_tod": len(list(self.tod[str(symbol)][str(clock)])) if clock else 0,
            "future_normalization": False,
        }

    def commit_day(
        self,
        symbol: str,
        *,
        rec: dict[str, Any],
        session_idx: list[int],
        or_width: Any,
    ) -> None:
        opening: list[float] = []
        for i in session_idx:
            t = str(rec["t"][i])
            h, l = rec["h"][i], rec["l"][i]
            if not (_finite(h) and _finite(l)):
                continue
            rng = abs(float(h) - float(l))
            if rng <= 0:
                continue
            self.tod[str(symbol)][t].append(rng)
            if "09:00" <= t <= "09:14":
                opening.append(rng)
        if _finite(or_width) and float(or_width) > 0:
            self.or15[str(symbol)].append(float(or_width))
        if opening:
            self.open_1m[str(symbol)].append(float(np.median(np.asarray(opening, dtype=float))))
