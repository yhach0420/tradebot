"""Quote / flow helpers for IDEES."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional, Sequence

from research.continuous_directional_vs_execution_edge.labels import tick_size_jpy
from research.ueia_continuous_session_tradability_repair.session import continuous_session_id
from research.upward_edge_identification_audit.loader import Tick


def bid(t: Tick) -> Optional[float]:
    b = t.board.canonical_best_bid
    return float(b) if b and b > 0 else None


def ask(t: Tick) -> Optional[float]:
    a = t.board.canonical_best_ask
    return float(a) if a and a > 0 else None


def mid(t: Tick) -> Optional[float]:
    b, a = bid(t), ask(t)
    if b is None or a is None:
        return None
    return 0.5 * (b + a)


def spread_bps(t: Tick) -> Optional[float]:
    b, a = bid(t), ask(t)
    if b is None or a is None or a <= 0:
        return None
    return (a - b) / a * 10000.0


def bps_from_entry(entry: float, px: float) -> float:
    return (px - entry) / entry * 10000.0 if entry > 0 else 0.0


def max_mid_before(ticks: Sequence[Tick], i: int, lookback_sec: float) -> Optional[float]:
    t0 = ticks[i].ts
    start = t0 - timedelta(seconds=lookback_sec)
    sess = continuous_session_id(t0)
    best = None
    for j in range(i, -1, -1):
        t = ticks[j]
        if continuous_session_id(t.ts) != sess:
            break
        if t.ts < start:
            break
        m = mid(t)
        if m is not None:
            best = m if best is None else max(best, m)
    return best


def flow_stats(ticks: Sequence[Tick], i: int, window_sec: float, t_ref: Optional[datetime] = None) -> dict:
    """Buy/sell trade counts and qty in [t_ref-window, t_ref] ending at ticks[i] or t_ref."""
    t_end = t_ref or ticks[i].ts
    t_start = t_end - timedelta(seconds=window_sec)
    sess = continuous_session_id(t_end)
    buy_n = sell_n = 0
    buy_q = sell_q = 0.0
    # scan backward from i, and a bit forward if t_ref > ticks[i]
    j0 = i
    while j0 > 0 and ticks[j0].ts > t_start:
        j0 -= 1
    j1 = i
    while j1 + 1 < len(ticks) and ticks[j1 + 1].ts <= t_end:
        j1 += 1
    for j in range(j0, j1 + 1):
        t = ticks[j]
        if continuous_session_id(t.ts) != sess:
            continue
        if t.ts < t_start or t.ts > t_end:
            continue
        if not t.volume_delta or t.volume_delta <= 0:
            continue
        q = float(t.volume_delta)
        if t.trade_side == "BUY":
            buy_n += 1
            buy_q += q
        elif t.trade_side == "SELL":
            sell_n += 1
            sell_q += q
    tot = buy_n + sell_n
    return {
        "buy_n": buy_n, "sell_n": sell_n,
        "buy_q": buy_q, "sell_q": sell_q,
        "buy_ratio": (buy_n / tot) if tot else None,
    }


def min_mid_window(ticks: Sequence[Tick], i: int, end_ts: datetime, window_sec: float) -> Optional[float]:
    start = end_ts - timedelta(seconds=window_sec)
    sess = continuous_session_id(end_ts)
    best = None
    j = i
    while j > 0 and ticks[j].ts > start:
        j -= 1
    for k in range(j, len(ticks)):
        t = ticks[k]
        if t.ts > end_ts:
            break
        if continuous_session_id(t.ts) != sess:
            continue
        if t.ts < start:
            continue
        m = mid(t)
        if m is not None:
            best = m if best is None else min(best, m)
    return best


def tick_size(px: float) -> float:
    return tick_size_jpy(px)
