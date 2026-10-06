"""Causal daily bars, ATR20 from completed prior sessions, same-day rejection points."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.multi_touch_daily_zone_1m_price_action_v1 import ATR_N, MIN_RANGE_ATR, REJECTION_CLOSE_FRAC


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def daily_from_minutes(rec: dict[str, Any], date: str) -> dict[str, Any] | None:
    hs = [float(x) for x in rec["h"] if _finite(x)]
    ls = [float(x) for x in rec["l"] if _finite(x)]
    cs = [float(x) for x in rec["c"] if _finite(x)]
    os = [float(x) for x in rec["o"] if _finite(x)]
    vs = [float(x) for x in rec["v"] if _finite(x)]
    vas = [float(x) for x in rec["va"] if _finite(x)]
    if not hs or not ls or not cs or not os:
        return None
    high, low, close, opn = max(hs), min(ls), cs[-1], os[0]
    if high <= low or close <= 0:
        return None
    return {
        "date": date,
        "open": opn,
        "high": high,
        "low": low,
        "close": close,
        "volume": float(sum(vs)) if vs else 0.0,
        "trading_value": float(sum(vas)) if vas else 0.0,
        "range": float(high - low),
        "true_range": float(high - low),
    }


def atr20(completed: list[dict[str, Any]]) -> float:
    """ATR of the last ATR_N completed prior sessions. Does not include the current session."""
    if len(completed) < ATR_N:
        return float("nan")
    trs = []
    w = completed[-ATR_N:]
    prev_c = completed[-ATR_N - 1]["close"] if len(completed) > ATR_N else None
    for i, d in enumerate(w):
        h, l, c = float(d["high"]), float(d["low"]), float(d["close"])
        if i == 0:
            pc = float(prev_c) if _finite(prev_c) else c
        else:
            pc = float(w[i - 1]["close"])
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    if len(trs) < ATR_N:
        return float("nan")
    return float(np.mean(trs))


def same_day_reactions(day: dict[str, Any], atr: float, *, next_session: str | None) -> list[dict[str, Any]]:
    """Rejection confirmed on this completed session. Usable only from next_session."""
    if not next_session or not _finite(atr) or atr <= 0:
        return []
    rng = float(day["range"])
    if rng <= 0 or rng < MIN_RANGE_ATR * atr:
        return []
    high, low, close = float(day["high"]), float(day["low"]), float(day["close"])
    close_from_high = (high - close) / rng
    close_from_low = (close - low) / rng
    out: list[dict[str, Any]] = []
    base = {
        "reaction_date": day["date"],
        "confirmed_at": day["date"],
        "available_from": next_session,
        "volume": day.get("volume"),
        "trading_value": day.get("trading_value"),
        "range": rng,
        "atr_at_confirm": atr,
        "future_pivot": False,
        "same_day_confirmation_only": True,
    }
    if close_from_high >= REJECTION_CLOSE_FRAC:
        out.append({**base, "role": "RESISTANCE", "price": high, "rejection_frac": close_from_high})
    if close_from_low >= REJECTION_CLOSE_FRAC:
        out.append({**base, "role": "SUPPORT", "price": low, "rejection_frac": close_from_low})
    return out
