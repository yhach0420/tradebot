"""Symbol-specific rolling baselines. At date D, history is < D only."""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

import numpy as np

from research.pb1_structure_and_symbol_context_rca_v1 import LOOKBACK_BASELINE_DAYS


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _median(xs: list[float]) -> float | None:
    vs = [float(x) for x in xs if _finite(x) and float(x) > 0]
    if not vs:
        return None
    return float(np.median(np.asarray(vs, dtype=float)))


def new_baselines() -> dict[str, Any]:
    return {
        "or15": deque(maxlen=LOOKBACK_BASELINE_DAYS),
        "tv": deque(maxlen=LOOKBACK_BASELINE_DAYS),
        "gap": deque(maxlen=LOOKBACK_BASELINE_DAYS),
        "atr": deque(maxlen=60),
        "tod": defaultdict(lambda: deque(maxlen=LOOKBACK_BASELINE_DAYS)),
        "am_vol": deque(maxlen=LOOKBACK_BASELINE_DAYS),
    }


def snapshot(base: dict[str, Any], *, clock: str | None, atr: Any) -> dict[str, Any]:
    med_1m = _median(list(base["tod"].get(str(clock), []))) if clock else None
    med_or = _median(list(base["or15"]))
    med_tv = _median(list(base["tv"]))
    med_gap = _median(list(base["gap"]))
    atrs = [float(x) for x in list(base["atr"]) if _finite(x) and float(x) > 0]
    atr_pctl = None
    if atrs and _finite(atr) and float(atr) > 0:
        atr_pctl = float(sum(1 for x in atrs if x <= float(atr)) / len(atrs))
    return {
        "median_1m_range_tod": med_1m,
        "median_or15": med_or,
        "median_tv0915": med_tv,
        "median_abs_gap_atr": med_gap,
        "atr20_own_pctl": atr_pctl,
        "baseline_days_or15": len(base["or15"]),
        "baseline_days_tod": len(list(base["tod"].get(str(clock), []))) if clock else 0,
        "future_normalization": False,
    }


def commit_day(
    base: dict[str, Any],
    *,
    rec: dict[str, Any],
    session_idx: list[int],
    or_width: Any,
    tv0915: Any,
    gap_atr: Any,
    atr: Any,
) -> None:
    if _finite(or_width) and float(or_width) > 0:
        base["or15"].append(float(or_width))
    if _finite(tv0915) and float(tv0915) > 0:
        base["tv"].append(float(tv0915))
    if _finite(gap_atr) and float(gap_atr) >= 0:
        base["gap"].append(abs(float(gap_atr)))
    if _finite(atr) and float(atr) > 0:
        base["atr"].append(float(atr))
    rets = []
    for i in session_idx:
        t = str(rec["t"][i])
        h, l = rec["h"][i], rec["l"][i]
        if _finite(h) and _finite(l):
            base["tod"][t].append(abs(float(h) - float(l)))
        c = rec["c"][i]
        if _finite(c) and "09:15" <= t <= "11:00":
            rets.append(float(c))
    if len(rets) >= 8:
        arr = np.diff(np.asarray(rets, dtype=float))
        if arr.size:
            base["am_vol"].append(float(np.std(arr)))
