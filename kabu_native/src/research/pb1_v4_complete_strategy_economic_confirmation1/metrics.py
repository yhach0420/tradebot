"""Confirmation-1 economic metrics. Diagnostic only. Does not retune rules."""
from __future__ import annotations

from collections import defaultdict
from statistics import median
from typing import Any

from research.pb1_v4_complete_strategy_economic_confirmation1 import KNOWN_THESIS_LOST_REASONS, OPS_EXIT_ID, TECHNICAL_EXIT_ID


def _pf(nets: list[float]) -> dict[str, Any]:
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x < 0]
    if losses:
        return {
            "profit_factor": float(sum(wins) / abs(sum(losses))),
            "profit_factor_infinite": False,
            "average_win": (float(sum(wins) / len(wins)) if wins else None),
            "average_loss": float(sum(losses) / len(losses)),
            "win_rate": (float(len(wins) / len(nets)) if nets else None),
        }
    if wins:
        return {
            "profit_factor": None,
            "profit_factor_infinite": True,
            "average_win": float(sum(wins) / len(wins)),
            "average_loss": None,
            "win_rate": 1.0,
        }
    return {
        "profit_factor": None,
        "profit_factor_infinite": False,
        "average_win": None,
        "average_loss": None,
        "win_rate": (0.0 if nets else None),
    }


def _pf_pass(pf: dict[str, Any]) -> bool:
    if pf.get("profit_factor_infinite"):
        return True
    try:
        return float(pf.get("profit_factor")) > 1.0
    except (TypeError, ValueError):
        return False


def _slice_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    nets = [float(t.get("net_pnl_yen") or 0.0) for t in rows]
    gross = [float(t.get("gross_pnl_yen") or 0.0) for t in rows]
    cost = [float(t.get("execution_cost_yen") or 0.0) for t in rows]
    hold = [int(t.get("holding_min") or 0) for t in rows]
    pf = _pf(nets)
    gpf = _pf(gross)
    return {
        "n": len(rows),
        "gross_pnl_yen": float(sum(gross)),
        "execution_cost_yen": float(sum(cost)),
        "net_pnl_yen": float(sum(nets)),
        "mean_net_pnl_per_trade": (float(sum(nets) / len(nets)) if nets else None),
        "median_net_pnl_per_trade": (float(median(nets)) if nets else None),
        "profit_factor": pf.get("profit_factor"),
        "profit_factor_infinite": pf.get("profit_factor_infinite"),
        "win_rate": pf.get("win_rate"),
        "average_win": pf.get("average_win"),
        "average_loss": pf.get("average_loss"),
        "holding_time_mean": (float(sum(hold) / len(hold)) if hold else None),
        "holding_time_median": (float(median(hold)) if hold else None),
        "gross_profit_factor": gpf.get("profit_factor"),
        "gross_profit_factor_infinite": gpf.get("profit_factor_infinite"),
        "gross_mean_per_trade": (float(sum(gross) / len(gross)) if gross else None),
        "net_mean_per_trade": (float(sum(nets) / len(nets)) if nets else None),
    }


def _share(part: float, total: float) -> float | None:
    if total == 0:
        return None
    return float(part) / float(total)


def _top_share(values: list[float], k: int, total: float) -> float | None:
    if not values:
        return None
    tops = sorted(values, reverse=True)[:k]
    return _share(float(sum(tops)), total)


def concentration(trades: list[dict[str, Any]]) -> dict[str, Any]:
    nets = [float(t.get("net_pnl_yen") or 0.0) for t in trades]
    total = float(sum(nets))
    by_day: dict[str, float] = defaultdict(float)
    by_sym: dict[str, float] = defaultdict(float)
    for t in trades:
        by_day[str(t.get("date") or "")] += float(t.get("net_pnl_yen") or 0.0)
        by_sym[str(t.get("symbol") or "")] += float(t.get("net_pnl_yen") or 0.0)
    day_vals = list(by_day.values())
    sym_vals = list(by_sym.values())
    top1_trade = max(nets) if nets else 0.0
    top1_day = max(day_vals) if day_vals else 0.0
    top1_sym = max(sym_vals) if sym_vals else 0.0
    ex_trade = total - top1_trade
    ex_day = total - top1_day
    ex_sym = total - top1_sym
    concentrated = bool(nets) and (ex_trade <= 0 or ex_day <= 0 or ex_sym <= 0)
    return {
        "net_pnl_ex_top1_trade": float(ex_trade) if nets else None,
        "net_pnl_ex_top1_day": float(ex_day) if day_vals else None,
        "net_pnl_ex_top1_symbol": float(ex_sym) if sym_vals else None,
        "top1_trade_pnl_share": _share(top1_trade, total) if nets else None,
        "top3_trade_pnl_share": _top_share(nets, 3, total),
        "top1_day_pnl_share": _share(top1_day, total) if day_vals else None,
        "top3_day_pnl_share": _top_share(day_vals, 3, total),
        "top1_symbol_pnl_share": _share(top1_sym, total) if sym_vals else None,
        "top3_symbol_pnl_share": _top_share(sym_vals, 3, total),
        "ECONOMIC_EDGE_CONCENTRATED": concentrated,
        "do_not_drop_top_contributors": True,
    }


def monthly_net(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_m: dict[str, float] = defaultdict(float)
    n_m: dict[str, int] = defaultdict(int)
    for t in trades:
        m = str(t.get("date") or "")[:6]
        by_m[m] += float(t.get("net_pnl_yen") or 0.0)
        n_m[m] += 1
    return [{"month": m, "net_pnl_yen": float(by_m[m]), "trade_n": int(n_m[m])} for m in sorted(by_m)]


def drawdown_rows(trades: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], float]:
    eq = 0.0
    peak = 0.0
    max_dd = 0.0
    rows = []
    for t in trades:
        eq += float(t.get("net_pnl_yen") or 0.0)
        peak = max(peak, eq)
        dd = eq - peak
        max_dd = min(max_dd, dd)
        rows.append(
            {
                "date": t.get("date"),
                "symbol": t.get("symbol"),
                "entry_t": t.get("entry_t"),
                "net_pnl_yen": t.get("net_pnl_yen"),
                "equity": eq,
                "drawdown": dd,
            }
        )
    return rows, float(max_dd)


def economic_metrics(*, replay: dict[str, Any]) -> dict[str, Any]:
    trades = list(replay.get("trades") or [])
    blocked = list(replay.get("blocked_rows") or [])
    core = _slice_stats(trades)
    sessions: dict[str, float] = defaultdict(float)
    for t in trades:
        sessions[str(t.get("date") or "")] += float(t.get("net_pnl_yen") or 0.0)
    sess_vals = list(sessions.values())
    dd_rows, max_dd = drawdown_rows(trades)
    e0 = _slice_stats([t for t in trades if str(t.get("entry_type") or "") == "E0"])
    e1 = _slice_stats([t for t in trades if str(t.get("entry_type") or "") == "E1"])
    tech = _slice_stats([t for t in trades if str(t.get("exit_reason") or "") == TECHNICAL_EXIT_ID])
    flat = _slice_stats([t for t in trades if str(t.get("exit_reason") or "") == OPS_EXIT_ID])
    lost_rows: dict[str, list[dict[str, Any]]] = {k: [] for k in KNOWN_THESIS_LOST_REASONS}
    lost_rows["OTHER"] = []
    for t in trades:
        if not t.get("thesis_death"):
            continue
        reason = str(t.get("THESIS_LOST_REASON") or "")
        if reason in lost_rows:
            lost_rows[reason].append(t)
        else:
            lost_rows["OTHER"].append(t)
    long_rows = [t for t in trades if str(t.get("side") or "").lower() in {"bull", "long"}]
    short_rows = [t for t in trades if str(t.get("side") or "").lower() in {"bear", "short"}]
    reentries = [t for t in trades if int(t.get("reentry_n") or 0) > 0]
    firsts = [t for t in trades if int(t.get("reentry_n") or 0) == 0]
    cap_blocked = [r for r in blocked if str(r.get("block_reason") or "") == "CAP"]
    ss_blocked = [r for r in blocked if str(r.get("block_reason") or "") == "SAME_SYMBOL"]
    gross = float(core["gross_pnl_yen"])
    cost = float(core["execution_cost_yen"])
    net = float(core["net_pnl_yen"])
    tax_ok = abs((gross - cost) - net) <= 1e-6
    conc = concentration(trades)
    pf = {"profit_factor": core.get("profit_factor"), "profit_factor_infinite": core.get("profit_factor_infinite")}
    return {
        "signal_n": int(replay.get("signal_n") or 0),
        "fill_n": int(replay.get("fill_n") or 0),
        "trade_n": int(core["n"]),
        "gross_pnl_yen": gross,
        "execution_cost_yen": cost,
        "net_pnl_yen": net,
        "gross_minus_cost_equals_net": tax_ok,
        "mean_net_pnl_per_trade": core.get("mean_net_pnl_per_trade"),
        "median_net_pnl_per_trade": core.get("median_net_pnl_per_trade"),
        "profit_factor": core.get("profit_factor"),
        "profit_factor_infinite": core.get("profit_factor_infinite"),
        "win_rate": core.get("win_rate"),
        "average_win": core.get("average_win"),
        "average_loss": core.get("average_loss"),
        "max_drawdown": max_dd,
        "positive_session_n": sum(1 for v in sess_vals if v > 0),
        "negative_session_n": sum(1 for v in sess_vals if v < 0),
        "session_n": len(sess_vals),
        "holding_time_mean": core.get("holding_time_mean"),
        "holding_time_median": core.get("holding_time_median"),
        "max_concurrent": int(replay.get("max_concurrent") or 0),
        "CAP_blocked_n": int(replay.get("cap_blocked_n") or 0),
        "same_symbol_blocked_n": int(replay.get("same_symbol_blocked_n") or 0),
        "reentry_n": len(reentries),
        "E0": e0,
        "E1": e1,
        "exit_technical": tech,
        "exit_session_flat": flat,
        "thesis_lost_reasons": {k: _slice_stats(v) for k, v in lost_rows.items()},
        "long": _slice_stats(long_rows),
        "short": _slice_stats(short_rows),
        "reentry": _slice_stats(reentries),
        "first_entry": _slice_stats(firsts),
        "execution_tax": {
            "gross_pnl_yen": gross,
            "execution_cost_yen": cost,
            "net_pnl_yen": net,
            "gross_profit_factor": core.get("gross_profit_factor"),
            "gross_profit_factor_infinite": core.get("gross_profit_factor_infinite"),
            "net_profit_factor": core.get("profit_factor"),
            "net_profit_factor_infinite": core.get("profit_factor_infinite"),
            "gross_mean_per_trade": core.get("gross_mean_per_trade"),
            "net_mean_per_trade": core.get("net_mean_per_trade"),
            "execution_tax_destroyed_gross_edge": bool(gross > 0 and net <= 0),
        },
        "blocked_hypothetical_not_used_for_verdict": {
            "CAP_blocked_n": len(cap_blocked),
            "CAP_blocked_net_pnl_yen": float(sum(float(r.get("net_pnl_yen") or 0.0) for r in cap_blocked)),
            "same_symbol_blocked_n": len(ss_blocked),
            "same_symbol_blocked_net_pnl_yen": float(sum(float(r.get("net_pnl_yen") or 0.0) for r in ss_blocked)),
        },
        "concentration": conc,
        "monthly_net_pnl": monthly_net(trades),
        "sessions": [{"date": d, "net_pnl_yen": float(sessions[d])} for d in sorted(sessions)],
        "drawdown": dd_rows,
        "primary_gate": {
            "net_pnl_yen_gt_0": net > 0,
            "profit_factor_gt_1": _pf_pass(pf),
            "mean_net_pnl_per_trade_gt_0": bool(core.get("mean_net_pnl_per_trade") is not None and float(core["mean_net_pnl_per_trade"]) > 0),
        },
    }


def failure_decomposition(metrics: dict[str, Any]) -> dict[str, Any]:
    gross = float(metrics.get("gross_pnl_yen") or 0.0)
    net = float(metrics.get("net_pnl_yen") or 0.0)
    tax = dict(metrics.get("execution_tax") or {})
    tech = dict(metrics.get("exit_technical") or {})
    flat = dict(metrics.get("exit_session_flat") or {})
    blocked = dict(metrics.get("blocked_hypothetical_not_used_for_verdict") or {})
    reentry = dict(metrics.get("reentry") or {})
    first = dict(metrics.get("first_entry") or {})
    labels = []
    if gross <= 0:
        labels.append("ENTRY_ITSELF_HAS_NO_GROSS_EDGE")
    if tax.get("execution_tax_destroyed_gross_edge"):
        labels.append("RAW_EDGE_EXISTS_BUT_EXECUTION_TAX_DESTROYS_IT")
    if float(tech.get("net_pnl_yen") or 0.0) <= 0 and int(tech.get("n") or 0) > 0:
        labels.append("TECHNICAL_EXIT_NET_NONPOSITIVE")
    if float(flat.get("net_pnl_yen") or 0.0) <= 0 and int(flat.get("n") or 0) > 0:
        labels.append("SESSION_FLAT_NET_NONPOSITIVE")
    if float(blocked.get("CAP_blocked_net_pnl_yen") or 0.0) > 0:
        labels.append("CAP_BLOCKS_POSITIVE_HYPOTHETICAL_TRADES")
    if float(blocked.get("same_symbol_blocked_net_pnl_yen") or 0.0) > 0:
        labels.append("SAME_SYMBOL_BLOCKS_POSITIVE_HYPOTHETICAL_TRADES")
    if float(reentry.get("net_pnl_yen") or 0.0) < 0 and int(reentry.get("n") or 0) > 0:
        labels.append("REENTRY_DESTROYS_EXPECTANCY")
    if float(first.get("net_pnl_yen") or 0.0) <= 0:
        labels.append("FIRST_ENTRY_NET_NONPOSITIVE")
    return {
        "ENTRY": {"gross_pnl_yen": gross, "note": "Frozen V4 E0/E1 stream; not retuned"},
        "EXECUTION_TAX": tax,
        "TECHNICAL_EXIT": tech,
        "SESSION_FLAT": flat,
        "CAP": {"blocked_n": blocked.get("CAP_blocked_n"), "blocked_hypothetical_net_pnl_yen": blocked.get("CAP_blocked_net_pnl_yen")},
        "SAME_SYMBOL": {"blocked_n": blocked.get("same_symbol_blocked_n"), "blocked_hypothetical_net_pnl_yen": blocked.get("same_symbol_blocked_net_pnl_yen")},
        "OCCUPANCY": {"max_concurrent": metrics.get("max_concurrent"), "trade_n": metrics.get("trade_n")},
        "REENTRY": reentry,
        "labels": labels,
        "do_not_retune_entry": True,
    }
