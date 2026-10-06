"""Daytrade suitability metrics. Spread not inferred. No future return."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from research.daytrade_historical_research_foundation_v2 import PAPER_LOT_QTY, TSE_REGULAR_MINUTE_N_FULL
from research.fixed_daytrade_universe_v1.schema import finite_number


def _median(xs: list[float]) -> float | None:
    arr = np.asarray(xs, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return None
    return float(np.median(arr))


def _p80(xs: list[float]) -> float | None:
    arr = np.asarray(xs, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return None
    return float(np.percentile(arr, 80))


def minute_metrics(df: pd.DataFrame, *, session_n_expected: int) -> dict[str, Any]:
    if df is None or df.empty:
        return {"minute_metrics_complete": False, "reason": "no_minute_rows"}
    d = df.copy()
    d["date"] = d["date"].astype(str)
    d["time_label"] = d["time_label"].astype(str)
    dates = sorted(d["date"].unique())
    abs_1m: list[float] = []
    zero_change_n = 0
    bar_n = 0
    clock_presence: dict[str, int] = {}
    daily_vol: list[float] = []
    daily_va: list[float] = []
    daily_range: list[float] = []
    for day, g in d.groupby("date", sort=True):
        g = g.sort_values("time_label")
        bar_n += int(len(g))
        closes = [finite_number(x) for x in g["close"].tolist()]
        opens = [finite_number(x) for x in g["open"].tolist()]
        highs = [finite_number(x) for x in g["high"].tolist()]
        lows = [finite_number(x) for x in g["low"].tolist()]
        vos = [finite_number(x) for x in g["volume"].tolist()]
        vas = [finite_number(x) for x in g["trading_value"].tolist()]
        prev = None
        for o, h, l, c, t in zip(opens, highs, lows, closes, g["time_label"].tolist()):
            clock_presence[str(t)[:5]] = clock_presence.get(str(t)[:5], 0) + 1
            if o is not None and c is not None and o == c:
                zero_change_n += 1
            if prev not in {None, 0.0} and c is not None:
                abs_1m.append(abs(c - prev) / prev)
            prev = c
        if vos:
            daily_vol.append(float(sum(v or 0.0 for v in vos)))
        if vas:
            daily_va.append(float(sum(v or 0.0 for v in vas)))
        hs = [x for x in highs if x is not None]
        ls = [x for x in lows if x is not None]
        if hs and ls and prev:
            pass
        day_h = max(hs) if hs else None
        day_l = min(ls) if ls else None
        day_c = next((x for x in reversed(closes) if x is not None), None)
        if day_h is not None and day_l is not None and day_c not in {None, 0.0}:
            daily_range.append((day_h - day_l) / day_c)
    n_days = max(len(dates), 1)
    active_ratio = bar_n / float(TSE_REGULAR_MINUTE_N_FULL * n_days)
    half = max(n_days // 2, 1)
    first_va = daily_va[:half]
    last_va = daily_va[-half:]
    # same-clock stability: std of presence rate across 09:00-15:29 regular labels that appear
    rates = [v / float(n_days) for v in clock_presence.values()] if clock_presence else []
    vol_share = np.asarray(daily_vol, dtype=float)
    if vol_share.size and vol_share.sum() > 0:
        p = vol_share / vol_share.sum()
        herfindahl = float((p ** 2).sum())
    else:
        herfindahl = None
    last_close = finite_number(d.sort_values(["date", "time_label"])["close"].iloc[-1])
    return {
        "minute_metrics_complete": True,
        "session_coverage_minute_days": len(dates),
        "active_minute_ratio": active_ratio,
        "tick_frequency": None,
        "tick_frequency_source": "unavailable_bulk_ticks_not_fully_ingested; minute_print_proxy_only",
        "minute_print_n": bar_n,
        "zero_change_minute_ratio": (zero_change_n / float(bar_n)) if bar_n else None,
        "median_abs_1m_return": _median(abs_1m),
        "p80_abs_1m_return": _p80(abs_1m),
        "median_daily_range": _median(daily_range),
        "p80_daily_range": _p80(daily_range),
        "median_daily_volume": _median(daily_vol),
        "median_daily_trading_value": _median(daily_va),
        "same_clock_presence_median": _median(rates),
        "same_clock_presence_p20": float(np.percentile(rates, 20)) if rates else None,
        "active_day_concentration_herfindahl": herfindahl,
        "first_half_median_va": _median(first_va),
        "second_half_median_va": _median(last_va),
        "price_median": float(np.median(pd.to_numeric(d["close"], errors="coerce").dropna())) if len(d) else None,
        "price_p20": float(np.percentile(pd.to_numeric(d["close"], errors="coerce").dropna(), 20)) if len(d) else None,
        "price_p80": float(np.percentile(pd.to_numeric(d["close"], errors="coerce").dropna(), 80)) if len(d) else None,
        "last_close": last_close,
        "notional_100": (last_close * PAPER_LOT_QTY) if last_close is not None else None,
        "bid_ask_historical": False,
        "spread_inferred": False,
        "session_n_expected": session_n_expected,
    }


assert TSE_REGULAR_MINUTE_N_FULL == 330
