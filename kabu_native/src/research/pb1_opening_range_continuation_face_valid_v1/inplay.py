"""Descriptive in-play measures. Constructed before outcomes. Not a threshold search."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from research.pb1_opening_range_continuation_face_valid_v1 import (
    IN_PLAY_ABS_GAP_ATR,
    IN_PLAY_TV_WINDOW_PCTL,
    IN_PLAY_XS_RANK_MAX,
    WINDOW_TV_LOOKBACK_DAYS,
    WINDOW_TV_MIN_OBS,
)


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


class WindowTVHistory:
    """Same 09:00–09:15 window sum, prior completed days only."""

    def __init__(self) -> None:
        self.xs: dict[str, list[float]] = defaultdict(list)

    def pctl(self, symbol: str, value: float) -> float | None:
        hist = [float(x) for x in self.xs[str(symbol)] if _finite(x)]
        if len(hist) < int(WINDOW_TV_MIN_OBS) or not _finite(value):
            return None
        k = int(sum(1 for x in hist if x <= float(value)))
        return float(k / len(hist))

    def commit(self, symbol: str, value: float) -> None:
        if not _finite(value):
            return
        xs = self.xs[str(symbol)]
        xs.append(float(value))
        if len(xs) > int(WINDOW_TV_LOOKBACK_DAYS):
            del xs[0]


def abs_gap_atr(open_px: Any, prior_close: Any, atr: Any) -> float:
    if not (_finite(open_px) and _finite(prior_close) and _finite(atr) and float(atr) > 0):
        return float("nan")
    return float(abs(float(open_px) - float(prior_close)) / float(atr))


def xs_rank_pct(values: dict[str, float]) -> dict[str, float]:
    rows = [(s, float(v)) for s, v in values.items() if _finite(v) and float(v) > 0]
    n = len(rows)
    if n <= 0:
        return {}
    rows.sort(key=lambda x: (-x[1], x[0]))
    out: dict[str, float] = {}
    for i, (s, _) in enumerate(rows):
        out[s] = float(i / n)
    return out


def in_play_flag(abs_gap: Any, tv_pctl: Any, xs: Any) -> bool:
    if _finite(abs_gap) and float(abs_gap) >= float(IN_PLAY_ABS_GAP_ATR):
        return True
    if _finite(tv_pctl) and float(tv_pctl) >= float(IN_PLAY_TV_WINDOW_PCTL):
        return True
    if _finite(xs) and float(xs) <= float(IN_PLAY_XS_RANK_MAX):
        return True
    return False


def daily_bias(sma5: Any, sma25: Any, sma75: Any) -> str:
    if not (_finite(sma5) and _finite(sma25) and _finite(sma75)):
        return "na"
    a, b, c = float(sma5), float(sma25), float(sma75)
    if a > b > c:
        return "bull_aligned"
    if a < b < c:
        return "bear_aligned"
    return "mixed"


def near_ma(px: Any, ma: Any, atr: Any, mult: float) -> bool:
    if not (_finite(px) and _finite(ma) and _finite(atr) and float(atr) > 0):
        return False
    return bool(abs(float(px) - float(ma)) / float(atr) <= float(mult))


def tv_sequence(impulse: Any, retest: Any, trigger: Any) -> str:
    vals = [impulse, retest, trigger]
    if not all(_finite(x) for x in vals):
        return "insufficient"
    a, b, c = float(impulse), float(retest), float(trigger)
    elevated = a >= 0.50
    contract = b + 0.05 < a
    return_up = c + 0.02 >= b and c >= 0.45
    if elevated and contract and return_up:
        return "impulse_elevated_retest_contract_reaccel"
    if elevated and contract:
        return "impulse_elevated_retest_contract"
    if elevated:
        return "impulse_elevated_no_contract"
    return "not_elevated_open"
