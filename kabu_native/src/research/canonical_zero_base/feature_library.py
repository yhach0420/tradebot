"""Past-only canonical feature library (decision-time safe)."""
from __future__ import annotations

from typing import Any, Optional, Sequence

from research.canonical_zero_base.canonical_loader import Tick


def _ret(prices: Sequence[float], n: int) -> Optional[float]:
    if len(prices) <= n or prices[-1 - n] <= 0:
        return None
    return (prices[-1] - prices[-1 - n]) / prices[-1 - n]


def _slope(prices: Sequence[float], n: int) -> Optional[float]:
    if len(prices) < n + 1:
        return None
    return (prices[-1] - prices[-1 - n]) / n


def features_at(ticks: Sequence[Tick], i: int) -> dict[str, Any]:
    """Compute features using ticks[0:i+1] only (past inclusive of decision tick)."""
    if i < 0 or i >= len(ticks):
        return {"quote_quality": False}
    win = ticks[max(0, i - 120) : i + 1]
    prices = [t.px for t in win]
    t = ticks[i]
    b = t.board
    out: dict[str, Any] = {
        "quote_quality": bool(b.canonical_quote_valid),
        "canonical_top_imbalance": b.canonical_top_imbalance,
        "canonical_depth_imbalance": b.canonical_depth_imbalance,
        "canonical_bid_qty": b.canonical_bid_qty,
        "canonical_ask_qty": b.canonical_ask_qty,
        "spread": b.canonical_spread,
        "spread_bps": b.canonical_spread_bps,
        "best_bid": b.canonical_best_bid,
        "best_ask": b.canonical_best_ask,
        "px": t.px,
    }
    for n, key in ((1, "return_1s"), (3, "return_3s"), (5, "return_5s"), (10, "return_10s"), (30, "return_30s"), (60, "return_60s")):
        # approximate by tick count (stride-based); named *s for contract
        out[key] = _ret(prices, min(n, len(prices) - 1)) if len(prices) > 1 else None
    out["slope_5s"] = _slope(prices, min(5, len(prices) - 1)) if len(prices) > 1 else None
    out["slope_15s"] = _slope(prices, min(15, len(prices) - 1)) if len(prices) > 1 else None
    out["slope_30s"] = _slope(prices, min(30, len(prices) - 1)) if len(prices) > 1 else None
    if len(prices) >= 10:
        s5 = _slope(prices, 5)
        s10 = _slope(prices, 10)
        out["acceleration"] = (s5 - s10) if s5 is not None and s10 is not None else None
    else:
        out["acceleration"] = None
    recent = prices[-30:] if len(prices) >= 2 else prices
    out["recent_high"] = max(recent) if recent else None
    out["recent_low"] = min(recent) if recent else None
    if out["recent_high"] and out["recent_high"] > 0:
        out["distance_from_high"] = (out["recent_high"] - t.px) / out["recent_high"]
        out["drawdown_from_high"] = out["distance_from_high"]
    else:
        out["distance_from_high"] = out["drawdown_from_high"] = None
    if out["recent_low"] and out["recent_low"] > 0:
        out["bounce_from_low"] = (t.px - out["recent_low"]) / out["recent_low"]
    else:
        out["bounce_from_low"] = None
    # structure
    if len(prices) >= 6:
        out["higher_high"] = prices[-1] > max(prices[-6:-1])
        out["higher_low"] = min(prices[-3:]) > min(prices[-6:-3])
        out["lower_high"] = prices[-1] < max(prices[-6:-1])
        out["lower_low"] = min(prices[-3:]) < min(prices[-6:-3])
    else:
        out["higher_high"] = out["higher_low"] = out["lower_high"] = out["lower_low"] = None
    for n, key in ((5, "price_range_5s"), (15, "price_range_15s"), (30, "price_range_30s"), (60, "price_range_60s")):
        seg = prices[-n:] if len(prices) >= 2 else prices
        if len(seg) >= 2 and min(seg) > 0:
            out[key] = (max(seg) - min(seg)) / min(seg)
        else:
            out[key] = None
    r30 = out.get("price_range_30s")
    r5 = out.get("price_range_5s")
    out["compression_ratio"] = (r5 / r30) if r5 is not None and r30 and r30 > 0 else None
    out["breakout_distance"] = out["distance_from_high"]
    # volume proxies via cumulative vol deltas
    vols = [t.vol for t in win if t.vol is not None]
    if len(vols) >= 6:
        dv = [vols[j] - vols[j - 1] for j in range(1, len(vols)) if vols[j] is not None and vols[j - 1] is not None]
        dv = [max(0.0, float(x)) for x in dv]
        out["volume_1s"] = dv[-1] if dv else None
        out["volume_5s"] = sum(dv[-5:]) if len(dv) >= 5 else (sum(dv) if dv else None)
        out["volume_15s"] = sum(dv[-15:]) if len(dv) >= 15 else None
        out["volume_30s"] = sum(dv[-30:]) if len(dv) >= 30 else None
        base = sum(dv[-30:-5]) / 25.0 if len(dv) >= 30 else None
        out["volume_vs_recent_baseline"] = (out["volume_5s"] / base) if base and out.get("volume_5s") is not None else None
        out["volume_acceleration"] = (sum(dv[-5:]) - sum(dv[-10:-5])) if len(dv) >= 10 else None
        out["volume_dryup"] = (out["volume_vs_recent_baseline"] is not None and out["volume_vs_recent_baseline"] < 0.5)
    else:
        for k in ("volume_1s", "volume_5s", "volume_15s", "volume_30s", "volume_vs_recent_baseline", "volume_acceleration", "volume_dryup"):
            out[k] = None
    # tick flow proxies
    if len(prices) >= 6:
        ups = sum(1 for a, b in zip(prices[-6:-1], prices[-5:]) if b > a)
        dns = sum(1 for a, b in zip(prices[-6:-1], prices[-5:]) if b < a)
        out["uptick_count"] = ups
        out["downtick_count"] = dns
        out["uptick_ratio"] = ups / (ups + dns) if (ups + dns) else None
        out["consecutive_upticks"] = 0
        for p0, p1 in zip(reversed(prices[:-1]), reversed(prices[1:])):
            if p1 > p0:
                out["consecutive_upticks"] += 1
            else:
                break
        out["consecutive_downticks"] = 0
        for p0, p1 in zip(reversed(prices[:-1]), reversed(prices[1:])):
            if p1 < p0:
                out["consecutive_downticks"] += 1
            else:
                break
    else:
        out["uptick_count"] = out["downtick_count"] = out["uptick_ratio"] = None
        out["consecutive_upticks"] = out["consecutive_downticks"] = None
    # board changes vs prior tick
    if i > 0:
        pb = ticks[i - 1].board
        out["bid_qty_change"] = (b.canonical_bid_qty or 0) - (pb.canonical_bid_qty or 0)
        out["ask_qty_change"] = (b.canonical_ask_qty or 0) - (pb.canonical_ask_qty or 0)
        out["best_bid_change"] = (b.canonical_best_bid or 0) - (pb.canonical_best_bid or 0)
        out["best_ask_change"] = (b.canonical_best_ask or 0) - (pb.canonical_best_ask or 0)
        out["spread_change"] = (b.canonical_spread or 0) - (pb.canonical_spread or 0)
        out["ask_depletion"] = out["ask_qty_change"] is not None and out["ask_qty_change"] < 0
        out["bid_replenishment"] = out["bid_qty_change"] is not None and out["bid_qty_change"] > 0
        out["bid_depletion"] = out["bid_qty_change"] is not None and out["bid_qty_change"] < 0
        out["ask_replenishment"] = out["ask_qty_change"] is not None and out["ask_qty_change"] > 0
    else:
        for k in (
            "bid_qty_change", "ask_qty_change", "best_bid_change", "best_ask_change", "spread_change",
            "ask_depletion", "bid_replenishment", "bid_depletion", "ask_replenishment",
        ):
            out[k] = None
    top = b.canonical_top_imbalance
    depth = b.canonical_depth_imbalance
    out["top_vs_depth_divergence"] = (abs(top - depth) if top is not None and depth is not None else None)
    out["book_flip"] = (
        i > 0
        and ticks[i - 1].board.canonical_top_imbalance is not None
        and top is not None
        and (ticks[i - 1].board.canonical_top_imbalance - 0.5) * (top - 0.5) < 0
    )
    # realized vol proxy
    if len(prices) >= 10:
        rets = [(prices[j] - prices[j - 1]) / prices[j - 1] for j in range(-9, 0) if prices[j - 1] > 0]
        out["realized_vol"] = (sum(abs(r) for r in rets) / len(rets)) if rets else None
    else:
        out["realized_vol"] = None
    return out


FEATURE_DICTIONARY = [
    {"name": k, "group": g}
    for g, names in (
        ("PRICE", ["return_5s", "return_30s", "slope_15s", "acceleration", "distance_from_high", "bounce_from_low", "higher_low", "compression_ratio", "realized_vol"]),
        ("VOLUME", ["volume_5s", "volume_vs_recent_baseline", "volume_acceleration", "volume_dryup"]),
        ("FLOW", ["uptick_ratio", "consecutive_upticks", "consecutive_downticks"]),
        ("BOARD", ["canonical_top_imbalance", "canonical_depth_imbalance", "ask_depletion", "bid_replenishment", "spread_bps", "top_vs_depth_divergence"]),
        ("LIQUIDITY", ["spread_bps", "canonical_bid_qty", "canonical_ask_qty"]),
        ("CONTEXT", ["quote_quality"]),
    )
    for k in names
]
