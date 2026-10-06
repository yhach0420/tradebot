"""Prior-only symbol behavior descriptors and a small descriptive archetype set.

Not clustered on PB1 returns. Thresholds are predeclared bins, not searched.
"""
from __future__ import annotations

from collections import deque
from typing import Any

import numpy as np

from research.pb1_structure_and_symbol_context_rca_v1 import LOOKBACK_ARCHETYPE_DAYS


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _sign(x: float) -> int:
    if x > 0:
        return 1
    if x < 0:
        return -1
    return 0


def _px(rec: dict[str, Any], session_idx: list[int], clock: str) -> float | None:
    for i in session_idx:
        if str(rec["t"][i]) == clock:
            v = rec["c"][i]
            return float(v) if _finite(v) else None
    return None


def day_behavior(
    rec: dict[str, Any],
    session_idx: list[int],
    *,
    pdc: Any,
    tv0915: Any,
    atr: Any,
) -> dict[str, Any] | None:
    if not session_idx:
        return None
    o = rec["o"][session_idx[0]]
    if not _finite(o) or float(o) <= 0:
        return None
    o0 = float(o)
    c0915 = _px(rec, session_idx, "09:15")
    c1030 = _px(rec, session_idx, "10:30")
    if c0915 is None or c1030 is None:
        return None
    am = _sign(c0915 - o0)
    later = _sign(c1030 - o0)
    rest = _sign(c1030 - c0915)
    gap = _sign(o0 - float(pdc)) if _finite(pdc) else 0
    rets = []
    prev = None
    for i in session_idx:
        t = str(rec["t"][i])
        if t < "09:15" or t > "11:00":
            continue
        c = rec["c"][i]
        if not _finite(c):
            continue
        if prev is not None:
            rets.append(float(c) - float(prev))
        prev = float(c)
    ac = None
    if len(rets) >= 12:
        a = np.asarray(rets[:-1], dtype=float)
        b = np.asarray(rets[1:], dtype=float)
        if float(np.std(a)) > 0 and float(np.std(b)) > 0:
            ac = float(np.corrcoef(a, b)[0, 1])
    return {
        "persist": bool(am != 0 and am == later),
        "reversal": bool(am != 0 and rest != 0 and am != rest),
        "gap_cont": bool(gap != 0 and gap == later),
        "autocorr": ac,
        "tv0915": float(tv0915) if _finite(tv0915) else None,
        "atr": float(atr) if _finite(atr) else None,
    }


def new_history() -> deque:
    return deque(maxlen=LOOKBACK_ARCHETYPE_DAYS)


def describe(hist: deque, *, atr_pctl: Any, med_tv: Any) -> dict[str, Any]:
    rows = list(hist)
    n = len(rows)
    if n < 15:
        return {
            "archetype": "INSUFFICIENT_HISTORY",
            "n_prior_days": n,
            "persist_rate": None,
            "reversal_rate": None,
            "gap_cont_rate": None,
            "autocorr": None,
            "atr_pctl": atr_pctl,
            "return_based": False,
        }
    persist = float(sum(1 for r in rows if r.get("persist")) / n)
    reversal = float(sum(1 for r in rows if r.get("reversal")) / n)
    gap = float(sum(1 for r in rows if r.get("gap_cont")) / n)
    acs = [float(r["autocorr"]) for r in rows if _finite(r.get("autocorr"))]
    ac = float(np.median(np.asarray(acs))) if acs else None
    # Predeclared descriptive bins. Not searched on PB1 outcomes.
    if reversal >= 0.55:
        arch = "OPENING_REVERSAL_PRONE"
    elif persist >= 0.58:
        arch = "TREND_PERSISTENT"
    elif persist <= 0.42:
        arch = "MEAN_REVERTING"
    elif _finite(atr_pctl) and float(atr_pctl) >= 0.66:
        arch = "HIGH_VOL_ACTIVE"
    elif _finite(atr_pctl) and float(atr_pctl) <= 0.33:
        arch = "LOW_VOL_LARGE_CAP"
    else:
        arch = "MIXED"
    return {
        "archetype": arch,
        "n_prior_days": n,
        "persist_rate": persist,
        "reversal_rate": reversal,
        "gap_cont_rate": gap,
        "autocorr": ac,
        "atr_pctl": atr_pctl,
        "median_tv0915": med_tv,
        "return_based": False,
        "pb1_outcome_used": False,
    }
