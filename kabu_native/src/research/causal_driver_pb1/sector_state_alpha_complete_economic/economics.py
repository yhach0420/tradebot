"""Frozen-gate economics. Costs other than 8 bps do not change trades."""
from __future__ import annotations

from collections import defaultdict
from statistics import median
from typing import Any

from research.pb1_v4_complete_strategy_build_and_economic_validation import SHARES
from research.pb1_v4_complete_strategy_build_and_economic_validation.clocks import hhmm_to_min
from research.pb1_v4_complete_strategy_build_and_economic_validation.fill import apply_x1_tax, signed_gross

TARGETS = ("6590", "6787", "6861", "6941", "6961")
FOLDS = (
    ("EARLY", "20240917", "20251126"),
    ("MIDDLE", "20251127", "20260421"),
    ("LATE", "20260422", "20260911"),
)


def _finite(x: float) -> bool:
    return x == x and x not in (float("inf"), float("-inf"))


def _pf(nets: list[float]) -> dict[str, Any]:
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x < 0]
    if losses:
        return {"profit_factor": float(sum(wins) / abs(sum(losses))), "profit_factor_infinite": False}
    if wins:
        return {"profit_factor": None, "profit_factor_infinite": True}
    return {"profit_factor": None, "profit_factor_infinite": False}


def _dd(rows: list[dict[str, Any]], key: str) -> float | None:
    ordered = sorted(rows, key=lambda r: (str(r.get("date") or ""), str(r.get("exit_t") or ""), str(r.get("symbol") or "")))
    eq = 0.0
    peak = 0.0
    worst = 0.0
    for row in ordered:
        eq += float(row.get(key) or 0.0)
        if not _finite(eq):
            return None
        peak = max(peak, eq)
        worst = max(worst, peak - eq)
    return float(worst)


def stamp_primary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        gross = signed_gross(side="long", entry_px=float(row["entry_px"]), exit_px=float(row["exit_px"]))
        tax = apply_x1_tax(gross_yen=gross, entry_px=float(row["entry_px"]))
        hold = (hhmm_to_min(str(row["exit_t"])) or 0) - (hhmm_to_min(str(row["entry_t"])) or 0)
        notional = float(row["entry_px"]) * float(SHARES)
        net = float(tax["net_pnl_yen"])
        out.append({
            **row,
            "shares": int(SHARES),
            "gross_pnl_yen": float(gross),
            "execution_cost_yen": float(tax["execution_cost_yen"]),
            "net_pnl_yen": net,
            "net_bps": float(net / notional * 10000.0) if notional else None,
            "holding_min": int(hold),
            "same_bar_entry": False,
            "used_mid": False,
        })
    return out


def _with_cost(rows: list[dict[str, Any]], bps: float) -> list[dict[str, Any]]:
    scaled = []
    for row in rows:
        cost = float(bps) / 10000.0 * float(row["entry_px"]) * float(SHARES)
        net = float(row["gross_pnl_yen"]) - cost
        scaled.append({**row, "execution_cost_yen": cost, "net_pnl_yen": net})
    return scaled


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    nets = [float(r["net_pnl_yen"]) for r in rows]
    gross = [float(r["gross_pnl_yen"]) for r in rows]
    bps = [float(r["net_bps"]) for r in rows if r.get("net_bps") is not None]
    holds = [int(r["holding_min"]) for r in rows]
    pf = _pf(nets)
    return {
        "trade_n": len(rows),
        "gross_pnl_yen": float(sum(gross)) if gross else 0.0,
        "execution_cost_yen": float(sum(float(r["execution_cost_yen"]) for r in rows)) if rows else 0.0,
        "net_pnl_yen": float(sum(nets)) if nets else 0.0,
        "gross_profit_yen": float(sum(x for x in gross if x > 0)),
        "gross_loss_yen": float(sum(x for x in gross if x < 0)),
        "profit_factor": pf["profit_factor"],
        "profit_factor_infinite": pf["profit_factor_infinite"],
        "win_n": sum(1 for x in nets if x > 0),
        "loss_n": sum(1 for x in nets if x < 0),
        "flat_n": sum(1 for x in nets if x == 0),
        "win_rate": (float(sum(1 for x in nets if x > 0) / len(nets)) if nets else None),
        "mean_net_trade_yen": (float(sum(nets) / len(nets)) if nets else None),
        "median_net_trade_yen": (float(median(nets)) if nets else None),
        "mean_net_bps": (float(sum(bps) / len(bps)) if bps else None),
        "median_net_bps": (float(median(bps)) if bps else None),
        "max_drawdown_yen": _dd(rows, "net_pnl_yen"),
        "average_holding_minutes": (float(sum(holds) / len(holds)) if holds else None),
        "median_holding_minutes": (float(median(holds)) if holds else None),
    }


def _compact(rows: list[dict[str, Any]]) -> dict[str, Any]:
    core = _summary(rows)
    return {
        "trade_n": core["trade_n"],
        "net_pnl_yen": core["net_pnl_yen"],
        "profit_factor": core["profit_factor"],
        "profit_factor_infinite": core["profit_factor_infinite"],
        "mean_net_trade_yen": core["mean_net_trade_yen"],
        "median_net_trade_yen": core["median_net_trade_yen"],
        "max_drawdown_yen": core["max_drawdown_yen"],
        "mean_net_bps": core["mean_net_bps"],
        "median_net_bps": core["median_net_bps"],
        "gross_pnl_yen": core["gross_pnl_yen"],
        "execution_cost_yen": core["execution_cost_yen"],
    }


def _share(part: float, total: float) -> dict[str, Any]:
    if total <= 0:
        return {"raw_yen": float(part), "share": None, "ratio_meaningful": False}
    return {"raw_yen": float(part), "share": float(part / total), "ratio_meaningful": True}


def fold_of(date: str) -> str:
    for name, lo, hi in FOLDS:
        if lo <= date <= hi:
            return name
    return "OUTSIDE"


def economics(trades: list[dict[str, Any]], *, eligible_by_fold: dict[str, int]) -> dict[str, Any]:
    primary = _summary(trades)
    costs = {}
    for bps in (8, 12, 16):
        rows = trades if bps == 8 else _with_cost(trades, bps)
        if bps != 8:
            for row, src in zip(rows, trades):
                notional = float(src["entry_px"]) * float(SHARES)
                row["net_bps"] = float(row["net_pnl_yen"] / notional * 10000.0) if notional else None
        costs[str(bps)] = {"bps": bps, **_compact(rows)}
    by_fold = {}
    for name, lo, hi in FOLDS:
        chunk = [r for r in trades if lo <= str(r["date"]) <= hi]
        item = _compact(chunk)
        item["eligible_days"] = int(eligible_by_fold.get(name) or 0)
        item["fold"] = name
        item["label"] = "RETROSPECTIVE_DEVELOPMENT_ECONOMIC_FEASIBILITY"
        by_fold[name] = item
    months: dict[str, list[dict[str, Any]]] = defaultdict(list)
    days: dict[str, list[dict[str, Any]]] = defaultdict(list)
    symbols: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in trades:
        months[str(row["date"])[:6]].append(row)
        days[str(row["date"])].append(row)
        symbols[str(row["symbol"])].append(row)
    monthly = []
    for month in sorted(months):
        item = _compact(months[month])
        monthly.append({"month": month, "trade_n": item["trade_n"], "net_pnl_yen": item["net_pnl_yen"], "profit_factor": item["profit_factor"], "mean_net_trade_yen": item["mean_net_trade_yen"]})
    symbol_rows = []
    for symbol in TARGETS:
        item = _compact(symbols.get(symbol, []))
        symbol_rows.append({"symbol": symbol, **item})
    total = float(primary["net_pnl_yen"])
    symbol_nets = {row["symbol"]: float(row["net_pnl_yen"]) for row in symbol_rows}
    day_nets = {day: float(sum(float(r["net_pnl_yen"]) for r in rows)) for day, rows in days.items()}
    month_nets = {row["month"]: float(row["net_pnl_yen"]) for row in monthly}
    top_symbol = max(symbol_nets, key=lambda k: symbol_nets[k]) if symbol_nets else None
    top_day = max(day_nets, key=lambda k: day_nets[k]) if day_nets else None
    top_month = max(month_nets, key=lambda k: month_nets[k]) if month_nets else None
    loss_symbol = min(symbol_nets, key=lambda k: symbol_nets[k]) if symbol_nets else None
    loss_day = min(day_nets, key=lambda k: day_nets[k]) if day_nets else None
    positive_folds = [name for name, row in by_fold.items() if float(row["net_pnl_yen"]) > 0]
    g1 = float(primary["net_pnl_yen"]) > 0
    g2 = bool(primary["profit_factor_infinite"]) or (primary["profit_factor"] is not None and float(primary["profit_factor"]) > 1.0)
    g3 = primary["mean_net_trade_yen"] is not None and float(primary["mean_net_trade_yen"]) > 0
    g4 = primary["median_net_trade_yen"] is not None and float(primary["median_net_trade_yen"]) >= 0
    g5 = primary["max_drawdown_yen"] is not None and _finite(float(primary["max_drawdown_yen"]))
    g6 = float(costs["12"]["net_pnl_yen"]) > 0
    g7 = len(positive_folds) >= 2
    gates = {
        "G1_net_pnl_gt_0": g1,
        "G2_pf_gt_1": g2,
        "G3_mean_net_trade_gt_0": g3,
        "G4_median_net_trade_gte_0": g4,
        "G5_max_drawdown_finite": g5,
        "G6_12bps_net_pnl_gt_0": g6,
        "G7_at_least_two_folds_positive": g7,
    }
    return {
        "primary_8bps": primary,
        "costs": costs,
        "folds": by_fold,
        "monthly": monthly,
        "symbols": symbol_rows,
        "positive_folds": positive_folds,
        "positive_fold_n": len(positive_folds),
        "gates": gates,
        "all_gates_pass": all(gates.values()),
        "concentration": {
            "top_symbol": top_symbol,
            "top_symbol_net_pnl": _share(symbol_nets.get(top_symbol, 0.0), total) if top_symbol else None,
            "top_day": top_day,
            "top_day_net_pnl": _share(day_nets.get(top_day, 0.0), total) if top_day else None,
            "top_month": top_month,
            "top_month_net_pnl": _share(month_nets.get(top_month, 0.0), total) if top_month else None,
            "largest_loss_symbol": loss_symbol,
            "largest_loss_symbol_yen": symbol_nets.get(loss_symbol) if loss_symbol else None,
            "largest_loss_day": loss_day,
            "largest_loss_day_yen": day_nets.get(loss_day) if loss_day else None,
        },
    }
