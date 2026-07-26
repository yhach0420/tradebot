"""Minimal IIC observations — not a broad feature library."""
from __future__ import annotations

from typing import Any, Optional, Sequence

from research.integrated_initial_impulse_continuation.loader import Tick


def idxs_back(ticks: Sequence[Tick], i: int, sec: float) -> list[int]:
    t1 = ticks[i].ts
    out = []
    for j in range(i, -1, -1):
        if (t1 - ticks[j].ts).total_seconds() > sec:
            break
        out.append(j)
    return list(reversed(out))


def window_flow(ticks: Sequence[Tick], i: int, sec: float) -> dict[str, float]:
    buy_v = sell_v = 0.0
    buy_n = sell_n = 0
    for j in idxs_back(ticks, i, sec):
        t = ticks[j]
        if t.volume_delta is None or t.volume_delta <= 0:
            continue
        if t.trade_side == "BUY":
            buy_v += t.volume_delta
            buy_n += 1
        elif t.trade_side == "SELL":
            sell_v += t.volume_delta
            sell_n += 1
    tot = buy_v + sell_v
    return {
        "buy_v": buy_v, "sell_v": sell_v, "buy_n": float(buy_n), "sell_n": float(sell_n),
        "buy_ratio": (buy_v / tot) if tot > 0 else 0.0,
        "vol": tot, "freq": float(buy_n + sell_n),
    }


def quiet_base_metrics(ticks: Sequence[Tick], i: int, sec: float) -> dict[str, Any]:
    ix = idxs_back(ticks, i, sec)
    prices = [ticks[j].px for j in ix if ticks[j].px and ticks[j].px > 0]
    if len(prices) < 6:
        return {"ok": False, "reason": "history_short"}
    lo, hi = min(prices), max(prices)
    mid = prices[-1]
    range_bps = ((hi - lo) / mid * 10000.0) if mid > 0 else 1e9
    ret = (prices[-1] - prices[0]) / prices[0] if prices[0] > 0 else 0.0
    fl = window_flow(ticks, i, sec)
    spread = ticks[i].board.canonical_spread_bps
    return {
        "ok": True,
        "base_low": lo,
        "base_high": hi,
        "range_bps": range_bps,
        "ret": ret,
        "flow": fl,
        "spread_bps": spread,
        "n": len(prices),
    }
