"""Concentration decomposition and diagnostic stress re-aggregation."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional, Sequence

from research.integrated_directional_entry_exit_strategy.exits import TradeResult
from research.ueia_continuous_session_tradability_repair.session import (
    continuous_session_id,
    seconds_since_session_open,
)


def _avg(xs: Sequence[float]) -> Optional[float]:
    return sum(xs) / len(xs) if xs else None


def metrics(trades: Sequence[TradeResult]) -> dict[str, Any]:
    if not trades:
        return {
            "trades": 0, "total_pnl_yen_100": 0.0, "avg_pnl_yen_100": None,
            "profit_factor_yen_100": None, "avg_bps": None, "win_rate": None,
            "max_drawdown_yen": 0.0, "gross_profit_yen": 0.0, "gross_loss_yen": 0.0,
            "daily": {}, "exit_reasons": {}, "top1_symbol": None, "top3_symbols": [],
            "top1_symbol_share": None, "top3_symbol_share": None,
            "top1_trade_share": None, "avg_entry_notional": None,
        }
    pnls = [t.net_pnl_yen_100 for t in trades]
    bps = [t.net_return_bps for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    eq = peak = 0.0
    max_dd = 0.0
    for p in pnls:
        eq += p
        peak = max(peak, eq)
        max_dd = min(max_dd, eq - peak)
    by_day: dict[str, float] = defaultdict(float)
    by_sym: dict[str, float] = defaultdict(float)
    reasons: dict[str, int] = defaultdict(int)
    for t in trades:
        by_day[t.day] += t.net_pnl_yen_100
        by_sym[t.symbol] += t.net_pnl_yen_100
        reasons[t.exit_reason] += 1
    s_ranked = sorted(by_sym.items(), key=lambda x: -abs(x[1]))
    stot = sum(abs(v) for _, v in s_ranked) or 1.0
    abs_pnls = sorted([abs(p) for p in pnls], reverse=True)
    tot_abs = sum(abs_pnls) or 1.0
    notions = [t.entry_ask * 100 for t in trades]
    return {
        "trades": len(trades),
        "total_pnl_yen_100": sum(pnls),
        "avg_pnl_yen_100": sum(pnls) / len(pnls),
        "profit_factor_yen_100": (sum(wins) / abs(sum(losses))) if losses else None,
        "avg_bps": sum(bps) / len(bps),
        "win_rate": len(wins) / len(pnls),
        "max_drawdown_yen": max_dd,
        "gross_profit_yen": sum(wins),
        "gross_loss_yen": sum(losses),
        "daily": dict(by_day),
        "exit_reasons": dict(reasons),
        "top1_symbol": s_ranked[0][0] if s_ranked else None,
        "top3_symbols": [s for s, _ in s_ranked[:3]],
        "top1_symbol_share": abs(s_ranked[0][1]) / stot if s_ranked else None,
        "top3_symbol_share": sum(abs(v) for _, v in s_ranked[:3]) / stot if s_ranked else None,
        "top1_trade_share": abs_pnls[0] / tot_abs,
        "avg_entry_notional": sum(notions) / len(notions),
        "symbols_pnl": dict(s_ranked),
    }


def symbol_table(trades: Sequence[TradeResult]) -> list[dict[str, Any]]:
    bags: dict[str, list[TradeResult]] = defaultdict(list)
    for t in trades:
        bags[t.symbol].append(t)
    tot_abs = sum(abs(t.net_pnl_yen_100) for t in trades) or 1.0
    tot_n = len(trades) or 1
    tot_notional = sum(t.entry_ask * 100 for t in trades) or 1.0
    # bps contribution: equal-weight share of sum(bps) attributed by symbol mean*n / total
    tot_bps_sum = sum(t.net_return_bps for t in trades) or 1.0
    rows = []
    for sym, rows_t in bags.items():
        pnls = [t.net_pnl_yen_100 for t in rows_t]
        bps = [t.net_return_bps for t in rows_t]
        gp = sum(p for p in pnls if p > 0)
        gl = sum(p for p in pnls if p < 0)
        notional = sum(t.entry_ask * 100 for t in rows_t)
        rows.append({
            "symbol": sym,
            "trades": len(rows_t),
            "wins": sum(1 for p in pnls if p > 0),
            "losses": sum(1 for p in pnls if p < 0),
            "total_pnl_yen_100": sum(pnls),
            "avg_pnl_yen_100": sum(pnls) / len(pnls),
            "avg_bps": sum(bps) / len(bps),
            "bps_sum": sum(bps),
            "gross_profit_yen": gp,
            "gross_loss_yen": gl,
            "entry_notional": notional,
            "profit_share": abs(sum(pnls)) / tot_abs,
            "trade_count_share": len(rows_t) / tot_n,
            "notional_share": notional / tot_notional,
            "bps_share": abs(sum(bps)) / (sum(abs(t.net_return_bps) for t in trades) or 1.0),
            "bps_sum_share": sum(bps) / tot_bps_sum if tot_bps_sum else None,
        })
    rows.sort(key=lambda r: -abs(r["total_pnl_yen_100"]))
    return rows


def classify_concentration(sym_rows: list[dict[str, Any]], top_n: int = 3) -> dict[str, Any]:
    if not sym_rows:
        return {"code": "NO_TRADES", "YEN_PRICE_WEIGHT_CONCENTRATION": False, "TRUE_SYMBOL_EDGE_CONCENTRATION": False}
    top = sym_rows[:top_n]
    yen_share = sum(r["profit_share"] for r in top)
    trade_share = sum(r["trade_count_share"] for r in top)
    notional_share = sum(r["notional_share"] for r in top)
    bps_share = sum(r["bps_share"] for r in top)
    # Extreme concentration thresholds for TRUE vs YEN-weighted
    yen_extreme = yen_share >= 0.60
    trade_extreme = trade_share >= 0.50
    bps_extreme = bps_share >= 0.50
    notional_extreme = notional_share >= 0.50
    if yen_extreme and not trade_extreme and not bps_extreme:
        code = "YEN_PRICE_WEIGHT_CONCENTRATION"
    elif yen_extreme and (trade_extreme or bps_extreme):
        code = "TRUE_SYMBOL_EDGE_CONCENTRATION"
    elif yen_extreme and notional_extreme and not bps_extreme:
        code = "YEN_PRICE_WEIGHT_CONCENTRATION"
    else:
        code = "MODERATE_OR_MIXED"
    return {
        "code": code,
        "YEN_PRICE_WEIGHT_CONCENTRATION": code == "YEN_PRICE_WEIGHT_CONCENTRATION",
        "TRUE_SYMBOL_EDGE_CONCENTRATION": code == "TRUE_SYMBOL_EDGE_CONCENTRATION",
        "top3_yen_share": yen_share,
        "top3_trade_share": trade_share,
        "top3_notional_share": notional_share,
        "top3_bps_share": bps_share,
        "top1_yen_share": sym_rows[0]["profit_share"] if sym_rows else None,
        "top1_trade_share": sym_rows[0]["trade_count_share"] if sym_rows else None,
        "top1_bps_share": sym_rows[0]["bps_share"] if sym_rows else None,
        "top1_notional_share": sym_rows[0]["notional_share"] if sym_rows else None,
    }


def exclude_symbols(trades: Sequence[TradeResult], symbols: set[str]) -> list[TradeResult]:
    return [t for t in trades if t.symbol not in symbols]


def leave_one_symbol_out(trades: Sequence[TradeResult]) -> dict[str, Any]:
    syms = sorted({t.symbol for t in trades})
    results = []
    for s in syms:
        m = metrics(exclude_symbols(trades, {s}))
        results.append({"excluded": s, **{k: m[k] for k in (
            "trades", "total_pnl_yen_100", "profit_factor_yen_100", "avg_bps", "avg_pnl_yen_100"
        )}})
    pnls = [r["total_pnl_yen_100"] for r in results]
    pnls_s = sorted(pnls)
    return {
        "n": len(results),
        "rows": results,
        "worst_pnl": min(pnls) if pnls else None,
        "median_pnl": pnls_s[len(pnls_s) // 2] if pnls_s else None,
        "best_pnl": max(pnls) if pnls else None,
        "worst_row": min(results, key=lambda r: r["total_pnl_yen_100"]) if results else None,
    }


def one_trade_per_symbol_session(trades: Sequence[TradeResult]) -> list[TradeResult]:
    ordered = sorted(trades, key=lambda t: (t.entry_time, t.sample_id))
    seen: set[tuple[str, str, str]] = set()
    out = []
    for t in ordered:
        sess = continuous_session_id(t.entry_time) or "?"
        key = (t.day, t.symbol, sess)
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out


def remove_top1_trade(trades: Sequence[TradeResult]) -> tuple[list[TradeResult], Optional[TradeResult]]:
    if not trades:
        return [], None
    top = max(trades, key=lambda t: t.net_pnl_yen_100)
    return [t for t in trades if t is not top and t.episode_id != top.episode_id], top


def time_bands(trades: Sequence[TradeResult]) -> dict[str, dict[str, Any]]:
    bands: dict[str, list[TradeResult]] = {
        "AM": [], "PM": [],
        "open_0_10m": [], "open_10_30m": [], "open_30m_plus": [],
    }
    for t in trades:
        sess = continuous_session_id(t.entry_time)
        if sess == "AM":
            bands["AM"].append(t)
        elif sess == "PM":
            bands["PM"].append(t)
        sec = seconds_since_session_open(t.entry_time)
        if sec is None:
            continue
        if sec < 600:
            bands["open_0_10m"].append(t)
        elif sec < 1800:
            bands["open_10_30m"].append(t)
        else:
            bands["open_30m_plus"].append(t)
    out = {}
    for name, rows in bands.items():
        m = metrics(rows)
        out[name] = {
            "trades": m["trades"],
            "total_pnl_yen_100": m["total_pnl_yen_100"],
            "profit_factor_yen_100": m["profit_factor_yen_100"],
            "avg_bps": m["avg_bps"],
            "STOP": m["exit_reasons"].get("STOP", 0),
            "TARGET": m["exit_reasons"].get("TARGET", 0),
            "TRAILING": m["exit_reasons"].get("TRAILING", 0),
            "MAX_HOLD": m["exit_reasons"].get("MAX_HOLD", 0),
            "SESSION_CLOSE": m["exit_reasons"].get("SESSION_CLOSE", 0),
        }
    return out


def compare_x1_x5(
    x5: Sequence[TradeResult],
    x1: Sequence[TradeResult],
) -> dict[str, Any]:
    """Pair by sample_id (same ENTRY)."""
    x1_by = {t.sample_id: t for t in x1}
    pairs = []
    for t5 in x5:
        t1 = x1_by.get(t5.sample_id)
        if t1 is None:
            continue
        pairs.append((t5, t1))
    if not pairs:
        return {"n_paired": 0}
    pnl_diff = [a.net_pnl_yen_100 - b.net_pnl_yen_100 for a, b in pairs]
    hold_diff = [a.hold_sec - b.hold_sec for a, b in pairs]
    # exit reason contribution of X5 improvement
    by_reason = defaultdict(float)
    for a, b in pairs:
        by_reason[a.exit_reason] += a.net_pnl_yen_100 - b.net_pnl_yen_100
    winners_imp = sum(d for d, (a, b) in zip(pnl_diff, pairs) if b.net_pnl_yen_100 > 0)
    # winner improvement: among X1 winners, how much X5 added; loser improvement among X1 losers
    win_imp = sum(a.net_pnl_yen_100 - b.net_pnl_yen_100 for a, b in pairs if b.net_pnl_yen_100 > 0)
    lose_imp = sum(a.net_pnl_yen_100 - b.net_pnl_yen_100 for a, b in pairs if b.net_pnl_yen_100 <= 0)
    m5 = metrics([a for a, _ in pairs])
    m1 = metrics([b for _, b in pairs])
    return {
        "n_paired": len(pairs),
        "same_entry_n": len(pairs),
        "avg_exit_time_diff_sec": _avg(hold_diff),
        "total_pnl_diff": sum(pnl_diff),
        "avg_pnl_diff": _avg(pnl_diff),
        "x5_total": m5["total_pnl_yen_100"],
        "x1_total": m1["total_pnl_yen_100"],
        "x5_dd": m5["max_drawdown_yen"],
        "x1_dd": m1["max_drawdown_yen"],
        "dd_diff": (m5["max_drawdown_yen"] or 0) - (m1["max_drawdown_yen"] or 0),
        "exit_reason_pnl_diff": dict(by_reason),
        "winner_improvement": win_imp,
        "loser_improvement": lose_imp,
        "x5_exit_reasons": m5["exit_reasons"],
        "x1_exit_reasons": m1["exit_reasons"],
    }
