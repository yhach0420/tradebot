"""Diagnostic slices. No category is dropped from the strategy."""
from __future__ import annotations

from collections import defaultdict
from statistics import median
from typing import Any

from research.pb1_v4_complete_strategy_economic_failure_decomposition import NOTIONAL_BUCKETS
from research.pb1_v4_complete_strategy_economic_failure_decomposition.classify import classify_entry_path


def _num(xs: list[float]) -> dict[str, Any]:
    ys = [float(x) for x in xs if x is not None]
    if not ys:
        return {"n": 0, "mean": None, "median": None, "sum": 0.0}
    return {"n": len(ys), "mean": float(sum(ys) / len(ys)), "median": float(median(ys)), "sum": float(sum(ys))}


def group_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    nets = [float(r.get("net_pnl_yen") or 0.0) for r in rows]
    gross = [float(r.get("gross_pnl_yen") or 0.0) for r in rows]
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x < 0]
    pf = (float(sum(wins) / abs(sum(losses))) if losses else None)
    return {
        "n": len(rows),
        "small_n": len(rows) < 10,
        "gross_pnl_yen": float(sum(gross)),
        "net_pnl_yen": float(sum(nets)),
        "gross_bps": _num([r.get("gross_bps") for r in rows if r.get("gross_bps") is not None]),
        "net_bps": _num([r.get("net_bps") for r in rows if r.get("net_bps") is not None]),
        "realized_bps": _num([r.get("realized_gross_bps") for r in rows if r.get("realized_gross_bps") is not None]),
        "MFE_bps": _num([r.get("MFE_bps") for r in rows if r.get("MFE_bps") is not None]),
        "MAE_bps": _num([r.get("MAE_bps") for r in rows if r.get("MAE_bps") is not None]),
        "MFE_capture_ratio": _num([r.get("MFE_capture_ratio") for r in rows if r.get("MFE_capture_ratio") is not None]),
        "time_to_MFE": _num([r.get("time_to_MFE") for r in rows if r.get("time_to_MFE") is not None]),
        "holding_time": _num([r.get("holding_min") for r in rows if r.get("holding_min") is not None]),
        "profit_factor_yen": pf,
        "mean_directional_bps": _num([r.get("realized_gross_bps") for r in rows if r.get("realized_gross_bps") is not None]).get("mean"),
    }


def apply_classes(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        d = dict(r)
        d["entry_class"] = classify_entry_path(r) if r.get("path_ok") else "AMBIGUOUS"
        out.append(d)
    return out


def by_key(rows: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    bags: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        bags[str(r.get(key) or "UNKNOWN")].append(r)
    return {k: group_stats(v) for k, v in sorted(bags.items(), key=lambda kv: -len(kv[1]))}


def notional_buckets(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for name, lo, hi in NOTIONAL_BUCKETS:
        bag = []
        for r in rows:
            px = float(r.get("entry_px") or 0.0)
            if px < lo:
                continue
            if hi is not None and px >= hi:
                continue
            if hi is None and px < lo:
                continue
            bag.append(r)
        stats = group_stats(bag)
        stats["bucket"] = name
        stats["px_lo"] = lo
        stats["px_hi"] = hi
        out.append(stats)
    return out


def exit_reason_slice(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    bags: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if r.get("ops_flatten"):
            bags["SESSION_FLAT"].append(r)
        else:
            bags[str(r.get("THESIS_LOST_REASON") or "UNKNOWN")].append(r)
    return {k: group_stats(v) for k, v in bags.items()}


def occupied_blocks(flat_trade: dict[str, Any], blocked: list[dict[str, Any]]) -> int:
    d = str(flat_trade.get("date") or "")
    a = str(flat_trade.get("entry_t") or "")
    b = str(flat_trade.get("exit_t") or "")
    n = 0
    for r in blocked:
        if str(r.get("date") or "") != d:
            continue
        st = str(r.get("signal_t") or r.get("entry_t") or "")
        if a < st < b:
            n += 1
    return n


def execution_tax_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    gpnn = 0
    cost_over_mfe = []
    cost_over_abs_real = []
    for r in rows:
        g = float(r.get("gross_pnl_yen") or 0.0)
        n = float(r.get("net_pnl_yen") or 0.0)
        cost = float(r.get("execution_cost_yen") or 0.0)
        if g > 0 and n <= 0:
            gpnn += 1
        notional = float(r.get("entry_notional_yen") or 0.0)
        mfe = float(r.get("MFE_bps") or 0.0)
        mfe_yen = (mfe / 10_000.0) * notional if notional else 0.0
        if mfe_yen > 0:
            cost_over_mfe.append(cost / mfe_yen)
        real = abs(g)
        if real > 0:
            cost_over_abs_real.append(cost / real)
    return {
        "gross_pnl_yen": float(sum(float(r.get("gross_pnl_yen") or 0.0) for r in rows)),
        "execution_cost_yen": float(sum(float(r.get("execution_cost_yen") or 0.0) for r in rows)),
        "net_pnl_yen": float(sum(float(r.get("net_pnl_yen") or 0.0) for r in rows)),
        "gross_positive_but_net_negative_n": gpnn,
        "cost_as_fraction_of_MFE_mean": (sum(cost_over_mfe) / len(cost_over_mfe) if cost_over_mfe else None),
        "cost_as_fraction_of_abs_realized_gross_mean": (sum(cost_over_abs_real) / len(cost_over_abs_real) if cost_over_abs_real else None),
        "primary_cause": False,
        "note": "gross already negative on V1; tax is secondary",
    }
