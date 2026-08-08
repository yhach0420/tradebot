"""Routed opportunity metrics, support gates, baselines."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from . import (
    DEC_AGG,
    DEC_PAS,
    DEC_SKIP,
    MAX_DAY_CONTRIB,
    MAX_SYMBOL_CONTRIB,
    MIN_DAYS,
    MIN_PASSIVE_FILLS,
    MIN_SIGNALS,
    MIN_SYMBOL_DAYS,
)


def routed_net(row: dict, decision: str, H: int = 600) -> float:
    if decision == DEC_SKIP:
        return 0.0
    if decision == DEC_AGG:
        return float(row.get(f"AGG_NET_{H}") or 0.0)
    if decision == DEC_PAS:
        return float(row.get(f"PASSIVE_NET_{H}") or 0.0)
    raise ValueError(decision)


def oracle_best(row: dict, H: int = 600) -> float:
    """Diagnostic upper bound — never for selection."""
    return max(float(row.get(f"AGG_NET_{H}") or 0.0), float(row.get(f"PASSIVE_NET_{H}") or 0.0), 0.0)


def summarize_decisions(
    rows: list[dict],
    decisions: list[str],
    *,
    label: str = "",
) -> dict[str, Any]:
    assert len(rows) == len(decisions)
    n = len(rows)
    n_skip = sum(1 for d in decisions if d == DEC_SKIP)
    n_agg = sum(1 for d in decisions if d == DEC_AGG)
    n_pas = sum(1 for d in decisions if d == DEC_PAS)
    pas_fill = sum(
        1 for r, d in zip(rows, decisions) if d == DEC_PAS and r.get("PASSIVE_FILL")
    )
    selected = n_agg + n_pas

    opp = {H: [] for H in (300, 600, 900)}
    filled_rets = []
    mid300, mid600 = [], []
    mfe, mae = [], []
    pos_mid = 0
    pos_exec = 0
    agg_contrib = 0.0
    pas_contrib = 0.0

    for r, d in zip(rows, decisions):
        for H in (300, 600, 900):
            opp[H].append(routed_net(r, d, H))
        if d == DEC_AGG:
            agg_contrib += float(r.get("AGG_NET_600") or 0.0)
            filled_rets.append(float(r.get("AGG_NET_600") or 0.0))
            if (r.get("AGG_NET_600") or 0) > 0:
                pos_exec += 1
        elif d == DEC_PAS and r.get("PASSIVE_FILL"):
            pas_contrib += float(r.get("PASSIVE_NET_600") or 0.0)
            filled_rets.append(float(r.get("PASSIVE_NET_600") or 0.0))
            if (r.get("PASSIVE_NET_600") or 0) > 0:
                pos_exec += 1
        if d != DEC_SKIP:
            if r.get("MID300") is not None:
                mid300.append(float(r["MID300"]))
                if r["MID300"] > 0:
                    pos_mid += 1
            if r.get("MID600") is not None:
                mid600.append(float(r["MID600"]))
            if r.get("MFE") is not None:
                mfe.append(float(r["MFE"]))
            if r.get("MAE") is not None:
                mae.append(float(r["MAE"]))

    def _mean(xs):
        return float(np.mean(xs)) if xs else None

    o600 = opp[600]
    pos_c = sum(x for x in o600 if x > 0)
    neg_c = sum(x for x in o600 if x < 0)
    pf = float(pos_c / abs(neg_c)) if abs(neg_c) > 1e-12 else None

    # weightings
    def _bal(group_fn, H=600):
        by: dict[Any, list[float]] = defaultdict(list)
        for r, d in zip(rows, decisions):
            by[group_fn(r)].append(routed_net(r, d, H))
        means = [float(np.mean(v)) for v in by.values() if v]
        return float(np.mean(means)) if means else None

    # day results
    by_day: dict[str, list[float]] = defaultdict(list)
    for r, d in zip(rows, decisions):
        by_day[r["date"]].append(routed_net(r, d, 600))
    day_means = {d: float(np.mean(v)) for d, v in by_day.items() if v}
    day_vals = list(day_means.values())

    # concentration of positive mass
    day_pos = {d: sum(x for x in v if x > 0) for d, v in by_day.items()}
    tot_pos = sum(day_pos.values())
    top_day_share = None
    if tot_pos > 1e-12:
        top_day_share = float(max(day_pos.values()) / tot_pos)

    by_sym: dict[str, list[float]] = defaultdict(list)
    for r, d in zip(rows, decisions):
        by_sym[r["symbol"]].append(routed_net(r, d, 600))
    sym_pos = {s: sum(x for x in v if x > 0) for s, v in by_sym.items()}
    tot_sp = sum(sym_pos.values())
    top_sym_share = float(max(sym_pos.values()) / tot_sp) if tot_sp > 1e-12 else None

    trades_filled = n_agg + pas_fill
    return {
        "label": label,
        "signals": n,
        "selected": selected,
        "skip": n_skip,
        "aggressive_count": n_agg,
        "passive_signal_count": n_pas,
        "passive_fill_count": pas_fill,
        "passive_fill_rate_among_pas_signals": (
            float(pas_fill / n_pas) if n_pas else None
        ),
        "trades_actually_filled": trades_filled,
        "opp_w_ret300": _mean(opp[300]),
        "opp_w_ret600": _mean(opp[600]),
        "opp_w_ret900": _mean(opp[900]),
        "filled_mean_ret600": _mean(filled_rets),
        "pf_equiv_600": pf,
        "ss_balanced_ret600": _bal(lambda r: (r["date"], r["symbol"], r["session"])),
        "day_balanced_ret600": _bal(lambda r: r["date"]),
        "agg_route_contrib_600": float(agg_contrib),
        "pas_route_contrib_600": float(pas_contrib),
        "mid300_mean": _mean(mid300),
        "mid600_mean": _mean(mid600),
        "positive_mid_share": float(pos_mid / selected) if selected else None,
        "positive_executable_share_among_filled": (
            float(pos_exec / trades_filled) if trades_filled else None
        ),
        "mfe_mean": _mean(mfe),
        "mae_mean": _mean(mae),
        "day_means": day_means,
        "positive_days": sum(1 for v in day_vals if v > 0),
        "negative_days": sum(1 for v in day_vals if v < 0),
        "n_days": len(day_vals),
        "median_day": float(np.median(day_vals)) if day_vals else None,
        "worst_day": float(min(day_vals)) if day_vals else None,
        "best_day": float(max(day_vals)) if day_vals else None,
        "max_day_contrib_share": top_day_share,
        "max_symbol_contrib_share": top_sym_share,
        "severe_day_concentration": bool(
            top_day_share is not None and top_day_share > MAX_DAY_CONTRIB
        ),
        "severe_symbol_concentration": bool(
            top_sym_share is not None and top_sym_share > MAX_SYMBOL_CONTRIB
        ),
    }


def support_ok(rows: list[dict], decisions: list[str]) -> dict[str, Any]:
    n_sel = sum(1 for d in decisions if d != DEC_SKIP)
    n_pas = sum(1 for d in decisions if d == DEC_PAS)
    n_pas_fill = sum(
        1 for r, d in zip(rows, decisions) if d == DEC_PAS and r.get("PASSIVE_FILL")
    )
    n_agg = sum(1 for d in decisions if d == DEC_AGG)
    symdays = {(r["date"], r["symbol"]) for r, d in zip(rows, decisions) if d != DEC_SKIP}
    days = {r["date"] for r, d in zip(rows, decisions) if d != DEC_SKIP}

    uses_pas = n_pas > 0
    ok = (
        n_sel >= MIN_SIGNALS
        and len(symdays) >= MIN_SYMBOL_DAYS
        and len(days) >= MIN_DAYS
    )
    if uses_pas:
        ok = ok and n_pas_fill >= MIN_PASSIVE_FILLS
    # AGG-only still needs min signals
    if n_sel == 0:
        ok = False
    return {
        "ok": ok,
        "status": "OK" if ok else "INSUFFICIENT_EXECUTION_SUPPORT",
        "selected": n_sel,
        "passive_signals": n_pas,
        "passive_fills": n_pas_fill,
        "aggressive": n_agg,
        "symbol_days": len(symdays),
        "days": len(days),
    }


def baseline_decisions(rows: list[dict], mode: str) -> list[str]:
    if mode == "SKIP_ALL":
        return [DEC_SKIP] * len(rows)
    if mode == "AGGRESSIVE_ALL":
        return [DEC_AGG] * len(rows)
    if mode == "PASSIVE_ALL":
        return [DEC_PAS] * len(rows)
    raise ValueError(mode)
