"""100-share yen PnL and aggregation."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional, Sequence

from research.directional_edge_economic_closure_passive_execution.constants import COST_RATE, LOT


def net_pnl_yen_100(entry_price: float, exit_price: float, qty: int = LOT) -> dict[str, float]:
    """Correct fixed-lot economics."""
    q = float(qty)
    gross = (exit_price - entry_price) * q
    cost = entry_price * q * COST_RATE
    net = gross - cost
    notional = entry_price * q
    gross_bps = (exit_price - entry_price) / entry_price * 10000.0 if entry_price > 0 else 0.0
    net_bps = net / notional * 10000.0 if notional > 0 else 0.0
    return {
        "qty": q,
        "gross_pnl_yen": gross,
        "cost_yen": cost,
        "net_pnl_yen_100": net,
        "gross_return_bps": gross_bps,
        "net_return_bps": net_bps,
        "entry_notional_yen": notional,
        "return_on_notional": net / notional if notional else 0.0,
    }


def legacy_yen_from_cadj_bps(cadj_bps: float, entry_ask: float) -> float:
    """CDEED formula: cadj/10000 * entry_ask * 100 — correct per-trade, not for averaging vs mean bps."""
    return cadj_bps / 10000.0 * entry_ask * 100.0


def summarize_trades(trades: Sequence[dict[str, Any]]) -> dict[str, Any]:
    if not trades:
        return {
            "n": 0, "total_pnl_yen_100": 0.0, "avg_pnl_yen_100": None, "median_pnl_yen_100": None,
            "win_rate_yen_100": None, "profit_factor_yen_100": None, "avg_return_bps": None,
            "median_return_bps": None, "profit_factor_bps": None, "average_entry_notional": None,
            "max_entry_notional": None, "capital_weighted_return": None, "max_drawdown_yen_100": None,
            "signals": 0, "fills": 0, "fill_rate": None, "partial_fills": 0, "no_fills": 0,
            "per_signal_pnl_yen": None, "per_fill_pnl_yen": None,
        }
    nets = [t["net_pnl_yen_100"] for t in trades]
    bps = [t["net_return_bps"] for t in trades]
    notions = [t["entry_notional_yen"] for t in trades]
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x < 0]  # NO_FILL zeros excluded from PF
    bw = [x for x in bps if x > 0]
    bl = [x for x in bps if x < 0]
    s_nets = sorted(nets)
    s_bps = sorted(bps)
    # drawdown on chronological if time present
    ordered = sorted(trades, key=lambda t: t.get("exit_time") or t.get("entry_time") or "")
    eq = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in ordered:
        eq += t["net_pnl_yen_100"]
        peak = max(peak, eq)
        max_dd = min(max_dd, eq - peak)
    tot_net = sum(nets)
    tot_notional = sum(notions) or 1.0
    fills = [t for t in trades if (t.get("filled_qty") or t.get("qty") or 0) > 0 and t.get("status") != "NO_FILL"]
    no_fills = [t for t in trades if t.get("status") == "NO_FILL"]
    partials = [t for t in trades if t.get("status") == "PARTIAL_FILL"]
    n_sig = len(trades)
    return {
        "n": len(trades),
        "signals": n_sig,
        "fills": len(fills),
        "fill_rate": len(fills) / n_sig if n_sig else None,
        "partial_fills": len(partials),
        "no_fills": len(no_fills),
        "total_pnl_yen_100": tot_net,
        "avg_pnl_yen_100": tot_net / len(nets),
        "median_pnl_yen_100": s_nets[len(s_nets) // 2],
        "win_rate_yen_100": len(wins) / len(nets),
        "profit_factor_yen_100": (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else None,
        "avg_return_bps": sum(bps) / len(bps),
        "median_return_bps": s_bps[len(s_bps) // 2],
        "profit_factor_bps": (sum(bw) / abs(sum(bl))) if bl and sum(bl) != 0 else None,
        "average_entry_notional": sum(notions) / len(notions),
        "max_entry_notional": max(notions),
        "capital_weighted_return": tot_net / tot_notional,
        "max_drawdown_yen_100": max_dd,
        "per_signal_pnl_yen": tot_net / n_sig,
        "per_fill_pnl_yen": (sum(t["net_pnl_yen_100"] for t in fills) / len(fills)) if fills else None,
    }


def by_day(trades: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    bags: dict[str, list] = defaultdict(list)
    for t in trades:
        bags[str(t.get("day") or "?")].append(t)
    return {d: summarize_trades(rows) for d, rows in sorted(bags.items())}


def by_symbol(trades: Sequence[dict[str, Any]], top: int = 30) -> dict[str, dict[str, Any]]:
    bags: dict[str, list] = defaultdict(list)
    for t in trades:
        bags[str(t.get("symbol") or "?")].append(t)
    ranked = sorted(bags.items(), key=lambda x: -abs(summarize_trades(x[1])["total_pnl_yen_100"]))
    return {s: summarize_trades(rows) for s, rows in ranked[:top]}


def dependence(trades: Sequence[dict[str, Any]]) -> dict[str, Any]:
    if not trades:
        return {"top1_trade_share": None, "top5_share": None, "top10_share": None, "top1_symbol": None, "top3_symbol": None, "top1_day": None}
    nets = sorted([t["net_pnl_yen_100"] for t in trades], key=abs, reverse=True)
    tot = sum(abs(x) for x in nets) or 1.0
    by_s = defaultdict(float)
    by_d = defaultdict(float)
    for t in trades:
        by_s[t.get("symbol")] += t["net_pnl_yen_100"]
        by_d[t.get("day")] += t["net_pnl_yen_100"]
    s_ranked = sorted(by_s.values(), key=abs, reverse=True)
    d_ranked = sorted(by_d.items(), key=lambda x: -abs(x[1]))
    stot = sum(abs(x) for x in s_ranked) or 1.0
    return {
        "top1_trade_share": abs(nets[0]) / tot,
        "top5_share": sum(abs(x) for x in nets[:5]) / tot,
        "top10_share": sum(abs(x) for x in nets[:10]) / tot,
        "top1_symbol_share": abs(s_ranked[0]) / stot if s_ranked else None,
        "top3_symbol_share": sum(abs(x) for x in s_ranked[:3]) / stot if s_ranked else None,
        "top1_day": d_ranked[0][0] if d_ranked else None,
        "top1_day_pnl": d_ranked[0][1] if d_ranked else None,
    }


def price_bands(trades: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    bands = {"<1000": [], "1000-3000": [], "3000-10000": [], ">=10000": []}
    for t in trades:
        p = t.get("entry_price") or 0
        if p < 1000:
            bands["<1000"].append(t)
        elif p < 3000:
            bands["1000-3000"].append(t)
        elif p < 10000:
            bands["3000-10000"].append(t)
        else:
            bands[">=10000"].append(t)
    return {k: summarize_trades(v) for k, v in bands.items()}


def notional_bands(trades: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    bands = {"<100k": [], "100k-300k": [], "300k-1M": [], ">=1M": []}
    for t in trades:
        n = t.get("entry_notional_yen") or 0
        if n < 100_000:
            bands["<100k"].append(t)
        elif n < 300_000:
            bands["100k-300k"].append(t)
        elif n < 1_000_000:
            bands["300k-1M"].append(t)
        else:
            bands[">=1M"].append(t)
    return {k: summarize_trades(v) for k, v in bands.items()}
