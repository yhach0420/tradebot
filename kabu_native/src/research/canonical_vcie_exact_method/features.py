"""Minimal VCIE observations only — no broad feature search."""
from __future__ import annotations

from typing import Any, Optional, Sequence

from research.canonical_vcie_exact_method.constants import CONTEXT_LOOKBACK_SEC
from research.canonical_vcie_exact_method.loader import Tick


def _idxs(ticks: Sequence[Tick], i: int, sec: float) -> list[int]:
    t1 = ticks[i].ts
    out = []
    for j in range(i, -1, -1):
        if (t1 - ticks[j].ts).total_seconds() > sec:
            break
        out.append(j)
    return list(reversed(out))


def _sum_vol(ticks: Sequence[Tick], idxs: list[int]) -> Optional[float]:
    """Sum positive deltas; if any missing (None) in window with trades expected, still sum known — but if all None return None."""
    s = 0.0
    any_known = False
    for j in idxs:
        d = ticks[j].volume_delta
        if d is None:
            continue
        any_known = True
        if d > 0:
            s += d
    return s if any_known else None


def context_at(ticks: Sequence[Tick], i: int) -> dict[str, Any]:
    ix = _idxs(ticks, i, CONTEXT_LOOKBACK_SEC)
    if len(ix) < 5:
        return {"context_type": "NOT_READY", "ok": False}
    prices = [ticks[j].px for j in ix if ticks[j].px is not None and ticks[j].px > 0]
    if len(prices) < 5:
        return {"context_type": "NOT_READY", "ok": False}
    px = prices[-1]
    hi, lo = max(prices), min(prices)
    ret = (px - prices[0]) / prices[0]
    rng = (hi - lo) / lo if lo > 0 else 1.0
    dist_hi = (hi - px) / hi if hi > 0 else 0.0
    dist_lo = (px - lo) / lo if lo > 0 else 0.0
    # classify
    if ret > 0.004 and dist_hi < 0.001:
        ctype = "ALREADY_RISING"
    elif ret < -0.003:
        ctype = "FALLING"
    elif rng <= 0.006 and abs(ret) <= 0.003:
        ctype = "HOLD"
    elif 0 < dist_hi <= 0.008 and ret > -0.002 and ret < 0.004:
        ctype = "CONTROLLED_PULLBACK"
    else:
        ctype = "NOT_READY"
    return {
        "ok": True,
        "context_type": ctype,
        "context_range_60s": _range(ticks, i, 60),
        "context_range_120s": rng,
        "context_return_60s": _ret(ticks, i, 60),
        "context_return_120s": ret,
        "recent_high": hi,
        "recent_low": lo,
        "distance_from_recent_high": dist_hi,
        "distance_from_recent_low": dist_lo,
        "price_extension": ret if ret > 0 else 0.0,
        "predefined_breakout_level": hi,  # fixed before cross
    }


def _range(ticks: Sequence[Tick], i: int, sec: float) -> Optional[float]:
    ix = _idxs(ticks, i, sec)
    ps = [ticks[j].px for j in ix if ticks[j].px]
    if len(ps) < 2:
        return None
    lo = min(ps)
    return (max(ps) - lo) / lo if lo > 0 else None


def _ret(ticks: Sequence[Tick], i: int, sec: float) -> Optional[float]:
    ix = _idxs(ticks, i, sec)
    ps = [ticks[j].px for j in ix if ticks[j].px]
    if len(ps) < 2 or ps[0] <= 0:
        return None
    return (ps[-1] - ps[0]) / ps[0]


def volume_burst_at(ticks: Sequence[Tick], i: int) -> dict[str, Any]:
    """volume_10s_ratio / volume_30s_ratio vs prior median baseline."""
    v5 = _sum_vol(ticks, _idxs(ticks, i, 5))
    v10 = _sum_vol(ticks, _idxs(ticks, i, 10))
    v30 = _sum_vol(ticks, _idxs(ticks, i, 30))
    # prior windows ending before current 10s/30s
    # baseline: rolling medians of non-overlapping 10s blocks in prior 120s / 300s
    def prior_med(win: float, look: float) -> Optional[float]:
        ix_all = _idxs(ticks, i, look)
        if len(ix_all) < 8:
            return None
        t_cut = ticks[i].ts
        prior = [j for j in ix_all if (t_cut - ticks[j].ts).total_seconds() > win]
        if len(prior) < 4:
            return None
        chunks: list[float] = []
        k0 = 0
        while k0 < len(prior):
            t_start = ticks[prior[k0]].ts
            chunk_idx: list[int] = []
            k = k0
            while k < len(prior) and (ticks[prior[k]].ts - t_start).total_seconds() <= win:
                chunk_idx.append(prior[k])
                k += 1
            sv = _sum_vol(ticks, chunk_idx)
            if sv is not None:
                chunks.append(sv)
            if k == k0:
                k += 1
            k0 = k
        if not chunks:
            return None
        chunks.sort()
        return chunks[len(chunks) // 2]

    med10 = prior_med(10, 120)
    med30 = prior_med(30, 300)
    r10 = (v10 / med10) if (v10 is not None and med10 and med10 > 0) else None
    r30 = (v30 / med30) if (v30 is not None and med30 and med30 > 0) else None
    burst = bool(r10 is not None and r10 >= 1.3)
    return {
        "volume_5s": v5,
        "volume_10s": v10,
        "volume_30s": v30,
        "prior_volume_10s_median_120s": med10,
        "prior_volume_30s_median_300s": med30,
        "volume_10s_ratio": r10,
        "volume_30s_ratio": r30,
        "volume_burst": burst,
    }


def trade_side_at(ticks: Sequence[Tick], i: int, *, sec: float = 10.0) -> dict[str, Any]:
    ix = _idxs(ticks, i, sec)
    buy_v = sell_v = 0.0
    buy_n = sell_n = unk_n = 0
    confs = []
    consec = 0
    for j in ix:
        t = ticks[j]
        if t.volume_delta is None or t.volume_delta <= 0:
            continue
        if t.trade_side == "BUY":
            buy_v += t.volume_delta
            buy_n += 1
            confs.append(t.trade_side_confidence)
        elif t.trade_side == "SELL":
            sell_v += t.volume_delta
            sell_n += 1
            confs.append(t.trade_side_confidence)
        elif t.trade_side == "UNKNOWN":
            unk_n += 1
    # consecutive buys at end
    for j in range(i, max(-1, i - 20), -1):
        t = ticks[j]
        if t.volume_delta is None or t.volume_delta <= 0:
            continue
        if t.trade_side == "BUY":
            consec += 1
        else:
            break
    tot = buy_v + sell_v
    ratio = buy_v / tot if tot > 0 else None
    conf = (sum(confs) / len(confs)) if confs else 0.0
    return {
        "aggressive_buy_count_10s": float(buy_n),
        "aggressive_buy_volume_10s": buy_v,
        "aggressive_sell_volume_10s": sell_v,
        "aggressive_buy_ratio_10s": ratio,
        "signed_volume_10s": buy_v - sell_v,
        "consecutive_aggressive_buys": float(consec),
        "trade_direction_confidence": conf,
        "unknown_trades_10s": float(unk_n),
        "trade_side_ok": bool(ratio is not None and conf >= 0.55 and ratio >= 0.55),
    }


def liquidity_at(t: Tick) -> dict[str, Any]:
    return {
        "spread": t.board.canonical_spread,
        "spread_bps": t.board.canonical_spread_bps,
        "canonical_ask_qty": t.board.canonical_ask_qty,
        "quote_quality": bool(t.board.canonical_quote_valid and not t.board.canonical_crossed),
    }
