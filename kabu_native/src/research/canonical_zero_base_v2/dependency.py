"""Dependency / leave-one-out metrics."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Sequence

from research.canonical_zero_base_v2.cap5 import CapTrade


def _pf(pnls: list[float]) -> float | None:
    wins = sum(p for p in pnls if p > 0)
    losses = -sum(p for p in pnls if p < 0)
    if losses <= 0:
        return float("inf") if wins > 0 else None
    return wins / losses


def dependency_metrics(trades: Sequence[CapTrade]) -> dict[str, Any]:
    if not trades:
        return {
            "DEPENDENCY_PASS": False,
            "DEPENDENCY_BLOCKED": True,
            "reason": "no_trades",
            "top1_symbol_profit_ratio": None,
            "top3_symbol_profit_ratio": None,
            "top1_day_profit_ratio": None,
            "leave_one_symbol_out_pf": {},
            "leave_one_day_out_pf": {},
        }
    by_sym: dict[str, float] = defaultdict(float)
    by_day: dict[str, float] = defaultdict(float)
    for t in trades:
        by_sym[t.symbol] += t.pnl_5bps
        by_day[t.day] += t.pnl_5bps
    pos_sym = sorted([v for v in by_sym.values() if v > 0], reverse=True)
    pos_day = sorted([v for v in by_day.values() if v > 0], reverse=True)
    tot_pos = sum(pos_sym) or 1.0
    tot_pos_d = sum(pos_day) or 1.0
    top1_s = (pos_sym[0] / tot_pos) if pos_sym else 0.0
    top3_s = (sum(pos_sym[:3]) / tot_pos) if pos_sym else 0.0
    top1_d = (pos_day[0] / tot_pos_d) if pos_day else 0.0

    loso = {}
    for s in list(by_sym.keys())[:30]:
        pnls = [t.pnl_5bps for t in trades if t.symbol != s]
        loso[s] = _pf(pnls)
    lodo = {}
    for d in by_day:
        pnls = [t.pnl_5bps for t in trades if t.day != d]
        lodo[d] = _pf(pnls) if pnls else None

    pos_days = sum(1 for v in by_day.values() if v > 0)
    neg_days = sum(1 for v in by_day.values() if v < 0)
    # gates
    loso_ok = all((v or 0) > 1 for v in loso.values() if v is not None) if loso else False
    lodo_vals = [v for v in lodo.values() if v is not None]
    lodo_ok = all(v > 1 for v in lodo_vals) if lodo_vals else False
    pass_ = (
        top1_s < 0.40
        and top3_s < 0.65
        and top1_d < 0.50
        and loso_ok
        and lodo_ok
        and pos_days > neg_days
    )
    return {
        "DEPENDENCY_PASS": bool(pass_),
        "DEPENDENCY_BLOCKED": not bool(pass_),
        "top1_symbol_profit_ratio": top1_s,
        "top3_symbol_profit_ratio": top3_s,
        "top1_day_profit_ratio": top1_d,
        "leave_one_symbol_out_pf": {k: (round(v, 4) if isinstance(v, float) and v != float("inf") else v) for k, v in list(loso.items())[:15]},
        "leave_one_day_out_pf": {k: (round(v, 4) if isinstance(v, float) and v != float("inf") else v) for k, v in lodo.items()},
        "pos_days": pos_days,
        "neg_days": neg_days,
        "symbol_n": len(by_sym),
        "day_n": len(by_day),
    }
