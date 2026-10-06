"""Economic metrics and PRIMARY SUCCESS GATE. No threshold search."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np

from research.am_entry_profit_improvement import ELIGIBLE_DAYS
from research.anchor_timing_robustness.metrics import maxdd, trade_stats
from research.entry_rank_shape_audit.oof import delta_series_stats


def _pnl(t: dict[str, Any]) -> float:
    return float(t.get("pnl_yen_100") or 0.0)


def daily_pnls(trades: list[dict[str, Any]], days: list[str]) -> list[dict[str, Any]]:
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        by[str(t.get("date") or "")].append(t)
    out = []
    for d in days:
        xs = by.get(d) or []
        pnls = [_pnl(t) for t in xs]
        out.append(
            {
                "date": d,
                "trade_count": len(xs),
                "pnl_yen_100": round(sum(pnls), 4) if pnls else 0.0,
            }
        )
    return out


def economic_pack(trades: list[dict[str, Any]], days: list[str] | None = None) -> dict[str, Any]:
    use_days = [str(d) for d in (days or ELIGIBLE_DAYS)]
    st = trade_stats(trades)
    dd = float(maxdd(trades)) if trades else 0.0
    daily = daily_pnls(trades, use_days)
    dpnls = [float(r["pnl_yen_100"]) for r in daily]
    pos = sum(1 for p in dpnls if p > 1e-9)
    neg = sum(1 for p in dpnls if p < -1e-9)
    zero = len(dpnls) - pos - neg
    win_n = int(st.get("win") or 0)
    loss_n = int(st.get("loss") or 0)
    flat_n = int(st.get("draw") or 0)
    return {
        "trade_count": int(st.get("trades") or 0),
        "net_pnl_yen_100": float(st.get("pnl") or 0.0),
        "gross_profit": float(st.get("gross_profit") or 0.0),
        "gross_loss": float(st.get("gross_loss") or 0.0),
        "profit_factor": st.get("PF"),
        "max_drawdown_yen_100": dd,
        "win_n": win_n,
        "loss_n": loss_n,
        "flat_n": flat_n,
        "win_rate": st.get("win_rate"),
        "positive_day_n": pos,
        "negative_day_n": neg,
        "zero_day_n": zero,
        "mean_daily_pnl": float(np.mean(dpnls)) if dpnls else 0.0,
        "median_daily_pnl": float(np.median(dpnls)) if dpnls else 0.0,
        "daily": daily,
        "trades": trades,
    }


def _pf_num(v: Any) -> float:
    if v is None:
        return 0.0
    if v in ("Infinity", float("inf")):
        return float("inf")
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def paired_delta(cand_daily: list[dict[str, Any]], curr_daily: list[dict[str, Any]]) -> dict[str, Any]:
    by_c = {str(r["date"]): float(r.get("pnl_yen_100") or 0.0) for r in cand_daily}
    by_r = {str(r["date"]): float(r.get("pnl_yen_100") or 0.0) for r in curr_daily}
    days = [str(r["date"]) for r in curr_daily] or [str(r["date"]) for r in cand_daily]
    deltas = []
    rows = []
    for d in days:
        delta = float(by_c.get(d, 0.0)) - float(by_r.get(d, 0.0))
        deltas.append(delta)
        rows.append(
            {
                "date": d,
                "CANDIDATE": by_c.get(d, 0.0),
                "CURRENT": by_r.get(d, 0.0),
                "DELTA": delta,
            }
        )
    st = delta_series_stats(deltas) if deltas else {
        "mean": None,
        "median": None,
        "positive_days": 0,
        "negative_days": 0,
        "ex_best_day": None,
        "ex_top3_days": None,
        "n_days": 0,
    }
    zero = sum(1 for v in deltas if abs(v) <= 1e-12)
    return {
        "PAIRED_POS_DAYS": int(st.get("positive_days") or 0),
        "PAIRED_NEG_DAYS": int(st.get("negative_days") or 0),
        "PAIRED_ZERO_DAYS": zero,
        "PAIRED_MEDIAN_DAILY_DELTA": st.get("median"),
        "EX_BEST_DAY_PNL_DELTA": st.get("ex_best_day"),
        "EX_TOP3_DAYS_PNL_DELTA": st.get("ex_top3_days"),
        "daily": rows,
        "deltas": deltas,
    }


def spec_sort_key(row: dict[str, Any]) -> tuple:
    mean_pnl = float(row.get("mean_inner_pnl") or 0.0)
    med = float(row.get("median_daily_pnl") or 0.0)
    pf = _pf_num(row.get("profit_factor"))
    pf_s = 1e18 if pf == float("inf") else pf
    dd = abs(float(row.get("max_drawdown_yen_100") or 0.0))
    arch = str(row.get("architecture_id") or "")
    rep = str(row.get("representation_id") or "")
    return (-mean_pnl, -med, -pf_s, dd, arch, rep)


def success_gate(
    cand: dict[str, Any],
    curr: dict[str, Any],
    paired: dict[str, Any],
    *,
    integrity_ok: bool,
) -> dict[str, Any]:
    a = float(cand.get("net_pnl_yen_100") or 0.0) > float(curr.get("net_pnl_yen_100") or 0.0)
    b = _pf_num(cand.get("profit_factor")) > _pf_num(curr.get("profit_factor"))
    c = abs(float(cand.get("max_drawdown_yen_100") or 0.0)) <= abs(float(curr.get("max_drawdown_yen_100") or 0.0))
    med = paired.get("PAIRED_MEDIAN_DAILY_DELTA")
    d = med is not None and float(med) > 0
    e = int(paired.get("PAIRED_POS_DAYS") or 0) > int(paired.get("PAIRED_NEG_DAYS") or 0)
    exb = paired.get("EX_BEST_DAY_PNL_DELTA")
    f = exb is not None and float(exb) > 0
    ext = paired.get("EX_TOP3_DAYS_PNL_DELTA")
    g = ext is not None and float(ext) >= 0
    h = bool(integrity_ok)
    gates = {
        "A_NET_PNL": a,
        "B_PF": b,
        "C_MAX_DD": c,
        "D_PAIRED_MEDIAN": d,
        "E_PAIRED_POS_GT_NEG": e,
        "F_EX_BEST_DAY": f,
        "G_EX_TOP3_DAYS": g,
        "H_INTEGRITY": h,
    }
    return {
        "gates": gates,
        "AM_ENTRY_PROFIT_IMPROVEMENT_PASS": all(gates.values()),
    }
