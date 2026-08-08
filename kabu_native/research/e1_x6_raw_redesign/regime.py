"""Market regime states with persistence and hysteresis (thresholds frozen here).

States: TREND_UP / RANGE_LOW_VOL / EXPANSION_UP / RISK_OFF_UNSTABLE / NEUTRAL.
ENTRY is forbidden in RISK_OFF_UNSTABLE. Thresholds are fixed BEFORE any PnL is
seen and are never tuned on results.
"""
from __future__ import annotations

from typing import Any

import numpy as np

REGIME_STATES = ("TREND_UP", "RANGE_LOW_VOL", "EXPANSION_UP", "RISK_OFF_UNSTABLE", "NEUTRAL")

REGIME_DEFINITIONS: dict[str, Any] = {
    "priority_order": ["RISK_OFF_UNSTABLE", "EXPANSION_UP", "TREND_UP", "RANGE_LOW_VOL", "NEUTRAL"],
    "hysteresis": "leave a non-NEUTRAL state only after its raw condition fails 2 consecutive grids",
    "standard": {
        "TREND_UP": {
            "raw": "mkt_up_ratio_60s>=0.58 AND mkt_ret_60s_med_bps>0 AND mkt_ret_300s_med_bps>0",
            "persistence": "raw true in >=3 of last 4 grids",
        },
        "EXPANSION_UP": {
            "raw": (
                "mkt_up_ratio_60s>=0.55 AND mkt_ret_60s_med_bps>0 AND mkt_vol_expansion>=1.20 "
                "AND (mkt_up_ratio_60s - mkt_up_ratio_60s[g-12]) >= 0.08"
            ),
            "persistence": "raw true at current grid (up-ratio delta is itself 60s-based)",
        },
        "RISK_OFF_UNSTABLE": {
            "raw": (
                "mkt_evaluable_n<30 OR mkt_up_ratio_60s<=0.40 OR "
                "(mkt_ret_60s_med_bps<0 AND mkt_ret_300s_med_bps<0) OR mkt_spread_worse_ratio>=0.30"
            ),
            "persistence": "immediate (safety state)",
        },
        "RANGE_LOW_VOL": {
            "raw": (
                "not RISK_OFF raw AND 0.42<=mkt_up_ratio_60s<=0.58 AND mkt_vol_expansion<=0.90 "
                "AND |mkt_ret_60s_med_bps|<=1.0"
            ),
            "persistence": "raw true in >=3 of last 4 grids",
        },
        "NEUTRAL": {"raw": "fallback", "persistence": "none"},
    },
    "strict": {
        "TREND_UP": {
            "raw": (
                "mkt_up_ratio_60s>=0.65 AND mkt_ret_60s_med_bps>0 AND mkt_ret_300s_med_bps>0 "
                "AND mkt_spread_worse_ratio<=0.20"
            ),
            "persistence": "raw true in >=5 of last 6 grids",
        },
        "EXPANSION_UP": {
            "raw": (
                "mkt_up_ratio_60s>=0.65 AND mkt_ret_60s_med_bps>0 AND mkt_vol_expansion>=1.20 "
                "AND (mkt_up_ratio_60s - mkt_up_ratio_60s[g-12]) >= 0.08 "
                "AND mkt_spread_worse_ratio<=0.20"
            ),
            "persistence": "raw true in >=5 of last 6 grids",
        },
        "RISK_OFF_UNSTABLE": "same as standard",
        "RANGE_LOW_VOL": "same as standard",
        "NEUTRAL": "fallback",
    },
}


def _persist(raw: np.ndarray, need: int, window: int) -> np.ndarray:
    n = raw.shape[0]
    out = np.zeros(n, dtype=bool)
    c = raw.astype(np.int32)
    run = np.cumsum(c)
    for g in range(n):
        lo = max(0, g - window + 1)
        cnt = run[g] - (run[lo - 1] if lo > 0 else 0)
        out[g] = cnt >= need
    return out


def classify_regime(mkt: dict[str, np.ndarray], *, strict: bool) -> list[str]:
    """Grid-wise regime with persistence + 2-grid hysteresis. NaN-safe (fails=False)."""
    up = mkt["mkt_up_ratio_60s"]
    r60 = mkt["mkt_ret_60s_med_bps"]
    r300 = mkt["mkt_ret_300s_med_bps"]
    vexp = mkt["mkt_vol_expansion"]
    sworse = mkt["mkt_spread_worse_ratio"]
    nev = mkt["mkt_evaluable_n"]
    n = up.shape[0]

    def _ge(a, b):
        with np.errstate(invalid="ignore"):
            return np.where(np.isnan(a), False, a >= b)

    def _gt(a, b):
        with np.errstate(invalid="ignore"):
            return np.where(np.isnan(a), False, a > b)

    def _le(a, b):
        with np.errstate(invalid="ignore"):
            return np.where(np.isnan(a), False, a <= b)

    up_delta = np.full(n, np.nan)
    if n > 12:
        up_delta[12:] = up[12:] - up[:-12]

    risk_raw = (
        np.where(np.isnan(nev), True, nev < 30)   # unknown breadth => unsafe
        | _le(up, 0.40)
        | (_gt(-r60, 0) & _gt(-r300, 0))
        | _ge(sworse, 0.30)
    )

    if strict:
        trend_raw = _ge(up, 0.65) & _gt(r60, 0) & _gt(r300, 0) & _le(sworse, 0.20)
        exp_raw = (_ge(up, 0.65) & _gt(r60, 0) & _ge(vexp, 1.20)
                   & _ge(up_delta, 0.08) & _le(sworse, 0.20))
        trend_p = _persist(trend_raw, 5, 6)
        exp_p = _persist(exp_raw, 5, 6)
    else:
        trend_raw = _ge(up, 0.58) & _gt(r60, 0) & _gt(r300, 0)
        exp_raw = _ge(up, 0.55) & _gt(r60, 0) & _ge(vexp, 1.20) & _ge(up_delta, 0.08)
        trend_p = _persist(trend_raw, 3, 4)
        exp_p = exp_raw
    range_raw = (~risk_raw & _ge(up, 0.42) & _le(up, 0.58) & _le(vexp, 0.90)
                 & _le(np.abs(r60), 1.0))
    range_p = _persist(range_raw, 3, 4)

    raw_of = {"TREND_UP": trend_raw, "EXPANSION_UP": exp_raw, "RANGE_LOW_VOL": range_raw}
    states: list[str] = []
    cur = "NEUTRAL"
    fail_streak = 0
    for g in range(n):
        if risk_raw[g]:
            nxt = "RISK_OFF_UNSTABLE"
        else:
            cand = "NEUTRAL"
            if exp_p[g]:
                cand = "EXPANSION_UP"
            elif trend_p[g]:
                cand = "TREND_UP"
            elif range_p[g]:
                cand = "RANGE_LOW_VOL"
            if cur in raw_of and cand != cur:
                # hysteresis: keep current state until its raw fails twice in a row
                if not raw_of[cur][g]:
                    fail_streak += 1
                else:
                    fail_streak = 0
                nxt = cur if fail_streak < 2 else cand
                if nxt != cur:
                    fail_streak = 0
            else:
                nxt = cand
                fail_streak = 0
        if nxt != cur:
            cur = nxt
        states.append(cur)
    return states
