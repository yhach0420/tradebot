"""Dependency / leave-one-out gates."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Sequence

from research.canonical_zero_base.cap5_portfolio import CapTrade


def dependency_metrics(trades: Sequence[CapTrade]) -> dict[str, Any]:
    if not trades:
        return {"n": 0, "DEPENDENCY_PASS": False, "DEPENDENCY_BLOCKED": True, "reason": "no_trades"}
    by_sym: dict[str, list[float]] = defaultdict(list)
    by_day: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        by_sym[t.symbol].append(t.pnl_5bps)
        by_day[t.day].append(t.pnl_5bps)
    total = sum(t.pnl_5bps for t in trades)
    sym_pnl = {s: sum(v) for s, v in by_sym.items()}
    day_pnl = {d: sum(v) for d, v in by_day.items()}
    top1_s = max(sym_pnl.values()) if sym_pnl else 0.0
    top3_s = sum(sorted(sym_pnl.values(), reverse=True)[:3])
    top1_d = max(day_pnl.values()) if day_pnl else 0.0
    pos_profit = sum(v for v in sym_pnl.values() if v > 0) or 1.0
    day_pos = sum(v for v in day_pnl.values() if v > 0) or 1.0

    def _pf(pnls: list[float]):
        gp = sum(p for p in pnls if p > 0)
        gl = sum(p for p in pnls if p < 0)
        if gl < 0:
            return gp / abs(gl)
        return None

    loso = {}
    for s in by_sym:
        sub = [t.pnl_5bps for t in trades if t.symbol != s]
        loso[s] = _pf(sub)
    lodo = {}
    for d in by_day:
        sub = [t.pnl_5bps for t in trades if t.day != d]
        lodo[d] = _pf(sub)

    top1_sym_ratio = (top1_s / pos_profit) if total > 0 else (top1_s / abs(total) if total else 1.0)
    # use share of gross profit when positive total else blocked
    if total > 0:
        top1_sym_ratio = top1_s / pos_profit if pos_profit else 1.0
        top3_sym_ratio = top3_s / pos_profit if pos_profit else 1.0
        top1_day_ratio = top1_d / day_pos if day_pos else 1.0
    else:
        top1_sym_ratio = top3_sym_ratio = top1_day_ratio = 1.0

    loso_ok = all(v is None or v > 1 for v in loso.values()) if loso else False
    lodo_ok = all(v is None or v > 1 for v in lodo.values()) if lodo else False
    pos_d = sum(1 for v in day_pnl.values() if v > 0)
    neg_d = sum(1 for v in day_pnl.values() if v <= 0)

    pass_gate = (
        total > 0
        and top1_sym_ratio < 0.40
        and top3_sym_ratio < 0.65
        and top1_day_ratio < 0.50
        and loso_ok
        and lodo_ok
        and pos_d > neg_d
    )
    return {
        "n": len(trades),
        "total_pnl": total,
        "top1_symbol_profit_ratio": top1_sym_ratio,
        "top3_symbol_profit_ratio": top3_sym_ratio,
        "top1_day_profit_ratio": top1_day_ratio,
        "leave_one_symbol_out_pf": loso,
        "leave_one_day_out_pf": lodo,
        "pos_days": pos_d,
        "neg_days": neg_d,
        "DEPENDENCY_PASS": pass_gate,
        "DEPENDENCY_BLOCKED": not pass_gate,
    }
