"""Minimal FCR observations for 5 stages — no broad feature library."""
from __future__ import annotations

from typing import Any, Optional, Sequence

from research.canonical_fcr_exact_method.loader import Tick


def _idxs(ticks: Sequence[Tick], i: int, sec: float) -> list[int]:
    t1 = ticks[i].ts
    out = []
    for j in range(i, -1, -1):
        if (t1 - ticks[j].ts).total_seconds() > sec:
            break
        out.append(j)
    return list(reversed(out))


def _prices(ticks: Sequence[Tick], idxs: list[int]) -> list[float]:
    return [ticks[j].px for j in idxs if ticks[j].px is not None and ticks[j].px > 0]


def causal_vwap(ticks: Sequence[Tick], i: int, sec: float = 300.0) -> dict[str, Any]:
    """Rebuild VWAP from px * volume_delta only; never fabricate."""
    ix = _idxs(ticks, i, sec)
    num = den = 0.0
    any_v = False
    for j in ix:
        t = ticks[j]
        if t.px is None or t.px <= 0:
            continue
        if t.volume_delta is None:
            continue
        if t.volume_delta <= 0:
            continue
        any_v = True
        num += t.px * t.volume_delta
        den += t.volume_delta
    if not any_v or den <= 0:
        return {"vwap": None, "status": "VWAP_NOT_EVALUABLE", "price_above_vwap": None}
    vwap = num / den
    px = ticks[i].px
    return {
        "vwap": vwap,
        "status": "OK",
        "price_above_vwap": (px > vwap) if px else None,
    }


def trend_context(ticks: Sequence[Tick], i: int, *, slope_min: float = 0.0) -> dict[str, Any]:
    ix60 = _idxs(ticks, i, 60)
    ix120 = _idxs(ticks, i, 120)
    ix300 = _idxs(ticks, i, 300)
    p60, p120, p300 = _prices(ticks, ix60), _prices(ticks, ix120), _prices(ticks, ix300)
    if len(p120) < 8:
        return {"ok": False, "reason": "history_short", "state": "IDLE"}
    ret60 = (p60[-1] - p60[0]) / p60[0] if len(p60) >= 4 and p60[0] > 0 else None
    ret120 = (p120[-1] - p120[0]) / p120[0] if p120[0] > 0 else None
    ret300 = (p300[-1] - p300[0]) / p300[0] if len(p300) >= 8 and p300[0] > 0 else None
    slope60 = (p60[-1] - p60[0]) / max(1, len(p60) - 1) if len(p60) >= 4 else None
    slope120 = (p120[-1] - p120[0]) / max(1, len(p120) - 1)
    # higher high / higher low counts on 300s
    hh = hl = ll = 0
    if len(p300) >= 6:
        for k in range(2, len(p300)):
            if p300[k] > max(p300[max(0, k - 5) : k]):
                hh += 1
            if min(p300[max(0, k - 2) : k + 1]) > min(p300[max(0, k - 5) : max(1, k - 2)] or [p300[k]]):
                hl += 1
            if p300[k] < min(p300[max(0, k - 5) : k]):
                ll += 1
    hi300, lo300 = max(p300 or p120), min(p300 or p120)
    px = p120[-1]
    dist_hi = (hi300 - px) / hi300 if hi300 > 0 else None
    # impulse size: max upswing in 300s
    impulse = 0.0
    trough = p300[0] if p300 else px
    for p in (p300 or p120):
        trough = min(trough, p)
        impulse = max(impulse, (p - trough) / trough if trough > 0 else 0)
    vwap = causal_vwap(ticks, i, 300)
    spread_bps = ticks[i].board.canonical_spread_bps
    # trades freq
    trades = sum(1 for j in ix60 if ticks[j].volume_delta is not None and ticks[j].volume_delta > 0)
    ok = bool(
        ret120 is not None and ret120 > max(slope_min, 0.0)
        and slope120 is not None and slope120 > 0
        and impulse >= 0.0012
        and (dist_hi is None or dist_hi > 0.0004)  # not glued to extreme high
        and (ll < 4)
        and (spread_bps is None or spread_bps < 100)
        and (hh + hl) >= 1
    )
    if ret120 is not None and ret120 < 0:
        ok = False
    return {
        "ok": ok,
        "return_60s": ret60,
        "return_120s": ret120,
        "return_300s": ret300,
        "slope_60s": slope60,
        "slope_120s": slope120,
        "higher_high_count_300s": float(hh),
        "higher_low_count_300s": float(hl),
        "lower_low_count_300s": float(ll),
        "distance_from_recent_high": dist_hi,
        "initial_impulse_size": impulse,
        "trade_frequency_60s": float(trades),
        "spread_bps": spread_bps,
        "vwap_status": vwap["status"],
        "price_above_vwap": vwap["price_above_vwap"],
        "impulse_high": hi300,
        "reason": "" if ok else "not_trend",
    }


def window_flow(ticks: Sequence[Tick], i: int, sec: float) -> dict[str, float]:
    buy_v = sell_v = 0.0
    buy_n = sell_n = 0
    for j in _idxs(ticks, i, sec):
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
        "signed": buy_v - sell_v,
        "freq": float(buy_n + sell_n),
    }
