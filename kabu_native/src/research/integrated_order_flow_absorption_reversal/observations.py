"""Minimal IOAR flow / impact observations — not a broad feature library."""
from __future__ import annotations

from typing import Any, Sequence

from research.integrated_order_flow_absorption_reversal.loader import Tick


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
    buy_n = sell_n = down = 0
    last_px = None
    for j in idxs_back(ticks, i, sec):
        t = ticks[j]
        if t.px is not None and last_px is not None and t.px < last_px:
            down += 1
        if t.px is not None:
            last_px = t.px
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
        "sell_ratio": (sell_v / tot) if tot > 0 else 0.0,
        "vol": tot, "freq": float(buy_n + sell_n), "down_ticks": float(down),
        "sell_qty_per_down_tick": (sell_v / down) if down > 0 else (sell_v if sell_v > 0 else 0.0),
        "down_tick_per_sell_qty": (down / sell_v) if sell_v > 0 else 0.0,
    }


def detect_bid_replenish(t: Tick) -> str:
    """Classify replenishment; 'snapshot_only' is NOT confirmed replenishment."""
    bid = t.board.canonical_best_bid
    bq = t.board.canonical_bid_qty
    if bid is None or bq is None:
        return "none"
    # 1: same best bid price, qty recovers after decrease
    if t.prev_bid_px is not None and abs(bid - t.prev_bid_px) < 1e-9:
        if t.prev_bid_qty is not None and bq > t.prev_bid_qty:
            # only count if prior decrease or sell hit at bid
            if t.trade_side == "SELL" or (t.prev_bid_qty is not None):
                return "same_price_qty_recover"
    # 2: bid disappeared / stepped down then restored to prior price
    if t.prev_bid_px is not None and bid > t.prev_bid_px:
        return "bid_step_up"
    if t.prev_bid_px is not None and bid == t.prev_bid_px and t.prev_bid_qty is not None and bq > t.prev_bid_qty * 1.05:
        return "same_price_qty_recover"
    return "none"


def balance_snapshot(ticks: Sequence[Tick], i: int, sec: float) -> dict[str, Any]:
    fl = window_flow(ticks, i, sec)
    t = ticks[i]
    prices = [ticks[j].px for j in idxs_back(ticks, i, sec) if ticks[j].px]
    return {
        "ok": len(prices) >= 5 and t.board.canonical_best_bid is not None,
        "px": t.px,
        "bid": t.board.canonical_best_bid,
        "ask": t.board.canonical_best_ask,
        "spread": t.board.canonical_spread_bps,
        "bq": t.board.canonical_bid_qty,
        "aq": t.board.canonical_ask_qty,
        "flow": fl,
        "hi": max(prices) if prices else None,
        "lo": min(prices) if prices else None,
    }
