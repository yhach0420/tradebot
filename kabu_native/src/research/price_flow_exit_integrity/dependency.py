"""Symbol / day dependency audit gates."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Sequence

from research.pbv2_zero_base_revalidation.metrics import pnl_metric_block
from research.price_flow_exit_integrity.trades import SimTrade


def _pf(pnls: Sequence[float]) -> float | None:
    if not pnls:
        return None
    return pnl_metric_block(list(pnls), list(pnls)).get("PF_5bps")


def dependency_audit(trades: Sequence[SimTrade], *, label: str) -> dict[str, Any]:
    by_sym: dict[str, list[float]] = defaultdict(list)
    by_day: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        by_sym[t.symbol].append(t.pnl_5bps)
        by_day[t.day].append(t.pnl_5bps)

    sym_pnl = {s: sum(v) for s, v in by_sym.items()}
    day_pnl = {d: sum(v) for d, v in by_day.items()}
    total = sum(sym_pnl.values())
    pos_total = sum(v for v in sym_pnl.values() if v > 0)
    # share of positive profit concentration (if total<=0 use abs gross profit)
    denom = pos_total if pos_total > 0 else (abs(total) if abs(total) > 1e-9 else 1.0)

    sym_sorted = sorted(sym_pnl.items(), key=lambda kv: kv[1], reverse=True)
    day_sorted = sorted(day_pnl.items(), key=lambda kv: kv[1], reverse=True)

    def share(items: list[tuple[str, float]], k: int) -> float:
        return round(sum(v for _, v in items[:k] if v > 0) / denom, 4)

    top1_sym = sym_sorted[0] if sym_sorted else ("", 0.0)
    top1_day = day_sorted[0] if day_sorted else ("", 0.0)

    # leave-one-out
    loo_sym = []
    for s in list(by_sym):
        pnls = [p for sym, xs in by_sym.items() if sym != s for p in xs]
        loo_sym.append({"exclude": s, "n": len(pnls), "pnl_5bps": round(sum(pnls), 2), "PF_5bps": _pf(pnls)})
    loo_day = []
    for d in list(by_day):
        pnls = [p for day, xs in by_day.items() if day != d for p in xs]
        loo_day.append({"exclude": d, "n": len(pnls), "pnl_5bps": round(sum(pnls), 2), "PF_5bps": _pf(pnls)})

    max_sym = top1_sym[0]
    max_day = top1_day[0]
    pf_ex_sym = next((r["PF_5bps"] for r in loo_sym if r["exclude"] == max_sym), None)
    pf_ex_day = next((r["PF_5bps"] for r in loo_day if r["exclude"] == max_day), None)

    top1_sym_share = share(sym_sorted, 1)
    top1_day_share = share(day_sorted, 1)
    # If total profit <= 0, concentration gates use positive-share denom; still flag if top1 dominates positives
    blocked = False
    reasons = []
    if total > 0 and top1_sym_share >= 0.40:
        blocked = True
        reasons.append("top1_symbol_ge_40pct")
    if total > 0 and top1_day_share >= 0.50:
        blocked = True
        reasons.append("top1_day_ge_50pct")
    if total > 0 and (pf_ex_sym is None or pf_ex_sym < 1.0):
        blocked = True
        reasons.append("leave_one_max_symbol_PF_lt_1")
    if total > 0 and (pf_ex_day is None or pf_ex_day < 1.0):
        blocked = True
        reasons.append("leave_one_max_day_PF_lt_1")

    return {
        "label": label,
        "n": len(trades),
        "total_pnl_5bps": round(total, 2),
        "top1_symbol": top1_sym[0],
        "top1_symbol_pnl": round(top1_sym[1], 2),
        "top1_symbol_pnl_share": top1_sym_share,
        "top3_symbol_pnl_share": share(sym_sorted, 3),
        "top5_symbol_pnl_share": share(sym_sorted, 5),
        "top1_day": top1_day[0],
        "top1_day_pnl": round(top1_day[1], 2),
        "top1_day_pnl_share": top1_day_share,
        "top2_day_pnl_share": share(day_sorted, 2),
        "symbol_trades": {s: len(v) for s, v in by_sym.items()},
        "symbol_pnl": {s: round(v, 2) for s, v in sym_pnl.items()},
        "day_pnl": {d: round(v, 2) for d, v in day_pnl.items()},
        "leave_one_symbol_out": sorted(loo_sym, key=lambda r: r["pnl_5bps"]),
        "leave_one_day_out": sorted(loo_day, key=lambda r: r["pnl_5bps"]),
        "pf_after_exclude_max_symbol": pf_ex_sym,
        "pf_after_exclude_max_day": pf_ex_day,
        "dependency_blocked": blocked,
        "block_reasons": reasons,
        "verdict": "DEPENDENCY_BLOCKED" if blocked else "DEPENDENCY_AUDIT_READY",
    }
