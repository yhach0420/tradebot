"""V2 genuine-in-play. Semantic calibration from V1 blinded charts. xs rank never sufficient alone."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from research.pb1_opening_range_continuation_face_valid_v2 import (
    IN_PLAY_ABS_GAP_ATR,
    IN_PLAY_GAP_AND_TV_GAP,
    IN_PLAY_GAP_AND_TV_PCTL,
    IN_PLAY_GAP_AND_XS_GAP,
    IN_PLAY_GAP_AND_XS_RANK,
    IN_PLAY_TV_AND_XS_PCTL,
    IN_PLAY_TV_AND_XS_RANK,
    IN_PLAY_TV_PCTL_ELEVATED,
    V1_IN_PLAY_ABS_GAP_ATR,
    V1_IN_PLAY_TV_WINDOW_PCTL,
    V1_IN_PLAY_XS_RANK_MAX,
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


def v1_in_play_flag(abs_gap: Any, tv_pctl: Any, xs: Any) -> bool:
    if _finite(abs_gap) and float(abs_gap) >= float(V1_IN_PLAY_ABS_GAP_ATR):
        return True
    if _finite(tv_pctl) and float(tv_pctl) >= float(V1_IN_PLAY_TV_WINDOW_PCTL):
        return True
    if _finite(xs) and float(xs) <= float(V1_IN_PLAY_XS_RANK_MAX):
        return True
    return False


def in_play_flag(abs_gap: Any, tv_pctl: Any, xs: Any) -> bool:
    """Genuine in-play. xs rank is never sufficient by itself."""
    g = float(abs_gap) if _finite(abs_gap) else None
    tv = float(tv_pctl) if _finite(tv_pctl) else None
    x = float(xs) if _finite(xs) else None
    if g is not None and g >= float(IN_PLAY_ABS_GAP_ATR):
        return True
    if tv is not None and tv >= float(IN_PLAY_TV_PCTL_ELEVATED):
        return True
    if g is not None and tv is not None and g >= float(IN_PLAY_GAP_AND_TV_GAP) and tv >= float(IN_PLAY_GAP_AND_TV_PCTL):
        return True
    if tv is not None and x is not None and tv >= float(IN_PLAY_TV_AND_XS_PCTL) and x <= float(IN_PLAY_TV_AND_XS_RANK):
        return True
    if g is not None and x is not None and g >= float(IN_PLAY_GAP_AND_XS_GAP) and x <= float(IN_PLAY_GAP_AND_XS_RANK):
        return True
    return False


def in_play_reason(abs_gap: Any, tv_pctl: Any, xs: Any) -> str:
    g = float(abs_gap) if _finite(abs_gap) else None
    tv = float(tv_pctl) if _finite(tv_pctl) else None
    x = float(xs) if _finite(xs) else None
    if g is not None and g >= float(IN_PLAY_ABS_GAP_ATR):
        return "distinctive_gap"
    if tv is not None and tv >= float(IN_PLAY_TV_PCTL_ELEVATED):
        return "own_clock_elevated"
    if g is not None and tv is not None and g >= float(IN_PLAY_GAP_AND_TV_GAP) and tv >= float(IN_PLAY_GAP_AND_TV_PCTL):
        return "moderate_gap_and_elevated_tv"
    if tv is not None and x is not None and tv >= float(IN_PLAY_TV_AND_XS_PCTL) and x <= float(IN_PLAY_TV_AND_XS_RANK):
        return "elevated_tv_and_top_quartile_xs"
    if g is not None and x is not None and g >= float(IN_PLAY_GAP_AND_XS_GAP) and x <= float(IN_PLAY_GAP_AND_XS_RANK):
        return "moderate_gap_and_top_xs"
    if x is not None and x <= 0.50:
        return "xs_rank_alone_not_in_play"
    return "ordinary_quiet"


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
