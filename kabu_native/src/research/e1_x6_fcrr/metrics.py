"""FCRR metrics, gates, Rolling-origin, FIXED_SPEC_DAY_DELETION."""
from __future__ import annotations

import statistics
from typing import Any, Mapping, Optional, Sequence

from .config import DAYS, ROLLING_ORIGIN_5FOLD


def _median(xs: Sequence[float]) -> float:
    return float(statistics.median(xs)) if xs else 0.0


def pf_of(pnls: Sequence[float]) -> tuple[Optional[float], str]:
    wins = sum(p for p in pnls if p > 0)
    losses = sum(p for p in pnls if p < 0)
    if losses < 0:
        return wins / abs(losses), "OK"
    if wins > 0:
        return None, "NO_LOSS"
    return None, "EMPTY"


def summarize_trades(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    pnls = [float(t["net_pnl_yen_100"]) for t in trades]
    total = float(sum(pnls))
    pf, pfs = pf_of(pnls)
    day_pnl = {d: 0.0 for d in DAYS}
    sym_pnl: dict[str, float] = {}
    stop_n = stop_loss = early30 = early60 = nop = 0
    for t in trades:
        d = str(t.get("day") or "")
        if d in day_pnl:
            day_pnl[d] += float(t["net_pnl_yen_100"])
        s = str(t.get("symbol") or "")
        sym_pnl[s] = sym_pnl.get(s, 0.0) + float(t["net_pnl_yen_100"])
        er = str(t.get("exit_reason") or "")
        if er == "STOP":
            stop_n += 1
            p = float(t["net_pnl_yen_100"])
            if p < 0:
                stop_loss += p
            hs = float(t.get("holding_sec") or 0)
            if hs <= 30:
                early30 += 1
            if hs <= 60:
                early60 += 1
        if er == "NO_PROGRESS":
            nop += 1
    top_trade = max(pnls) if pnls else 0.0
    top_sym = max(sym_pnl.items(), key=lambda kv: (kv[1], kv[0]))[0] if sym_pnl else None
    # max DD exit order
    rows = sorted(trades, key=lambda t: (str(t.get("exit_time") or ""), str(t.get("symbol") or "")))
    eq = peak = dd = 0.0
    for t in rows:
        eq += float(t["net_pnl_yen_100"])
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    ex722 = [float(t["net_pnl_yen_100"]) for t in trades if str(t.get("day")) != "20260722"]
    ex722_pf, ex722_pfs = pf_of(ex722)
    return {
        "n": len(pnls),
        "pnl": total,
        "pf": pf,
        "pf_status": pfs,
        "wins": sum(1 for p in pnls if p > 0),
        "losses": sum(1 for p in pnls if p < 0),
        "draws": sum(1 for p in pnls if p == 0),
        "day_pnl": day_pnl,
        "median_day_pnl": _median(list(day_pnl.values())),
        "max_dd": dd,
        "stop_n": stop_n,
        "stop_loss_total": stop_loss,
        "stop_loss_per_trade": (stop_loss / len(pnls)) if pnls else 0.0,
        "early_stop_30s_n": early30,
        "early_stop_60s_n": early60,
        "no_progress_n": nop,
        "ex_top1_trade_pnl": total - top_trade,
        "ex_top1_symbol_pnl": total - (sym_pnl.get(top_sym, 0.0) if top_sym else 0.0),
        "top1_symbol": top_sym,
        "ex722_pnl": float(sum(ex722)),
        "ex722_pf": ex722_pf,
        "ex722_pf_status": ex722_pfs,
        "ex722_n": len(ex722),
    }


def fixed_spec_day_deletion(day_pnl: Mapping[str, float]) -> dict[str, Any]:
    rows = []
    all_ok = True
    for held in DAYS:
        rem = sum(v for d, v in day_pnl.items() if d != held)
        ok = rem >= 0.0 - 1e-12
        all_ok = all_ok and ok
        rows.append({"held_out_day": held, "remaining_pnl": rem, "pass": ok})
    return {"rows": rows, "all_pass": all_ok}


def rolling_origin_from_day_pnls(
    per_candidate_day_pnl: Mapping[str, Mapping[str, float]],
) -> dict[str, Any]:
    """Select by build total pnl (family fixed); confirm day pnl of selected."""
    folds = []
    confirm_vals = []
    pos_folds = 0
    for fid, spec in ROLLING_ORIGIN_5FOLD.items():
        build = spec["build"]
        confirm = spec["confirm"]
        best_id = None
        best_key = None
        for cid, dp in per_candidate_day_pnl.items():
            bt = sum(float(dp.get(d, 0.0)) for d in build)
            key = (-bt, cid)
            if best_key is None or key < best_key:
                best_key = key
                best_id = cid
        cp = float((per_candidate_day_pnl.get(best_id) or {}).get(confirm, 0.0))
        confirm_vals.append(cp)
        if cp > 0:
            pos_folds += 1
        folds.append({
            "fold": fid, "build": build, "confirm": confirm,
            "selected": best_id, "confirm_pnl": cp,
            "family": "E1_X6_FCRR", "feature_direction": "FCRR_shared",
        })
    return {
        "folds": folds,
        "positive_confirm_folds": pos_folds,
        "confirm_median": _median(confirm_vals),
        "direction_reversals": 0,  # single family / direction
        "same_family_folds": 5,
        "pass": pos_folds >= 3 and _median(confirm_vals) > 0,
    }


def evaluate_gates(
    metrics: Mapping[str, Any],
    *,
    core_metrics: Optional[Mapping[str, Any]],
    core_evaluable: bool,
    base_all_usable: Mapping[str, Any],
    rolling: Mapping[str, Any],
    day_del: Mapping[str, Any],
    determinism_ok: bool,
    precommit_ok: bool,
    safety_ok: bool,
    leakage_ok: bool,
) -> dict[str, Any]:
    g: dict[str, bool] = {}
    g["source_ok"] = True  # Gate0 checked separately
    g["leakage_ok"] = leakage_ok
    g["determinism_ok"] = determinism_ok
    g["safety_ok"] = safety_ok
    g["precommit_ok"] = precommit_ok
    g["final_run_integrity"] = True
    g["fold_completeness"] = True
    g["all_usable_pnl_gt_0"] = float(metrics["pnl"]) > 0
    g["all_usable_pf_ge_1_10"] = (
        metrics.get("pf_status") == "NO_LOSS"
        or (metrics.get("pf") is not None and float(metrics["pf"]) >= 1.10 - 1e-12)
    )
    if core_evaluable and core_metrics is not None:
        g["core_pnl_gt_0"] = float(core_metrics["pnl"]) > 0
        g["core_pf_ge_1_10"] = (
            core_metrics.get("pf_status") == "NO_LOSS"
            or (core_metrics.get("pf") is not None and float(core_metrics["pf"]) >= 1.10 - 1e-12)
        )
        g["core_trades_ge_30"] = int(core_metrics["n"]) >= 30
    else:
        g["core_pnl_gt_0"] = False
        g["core_pf_ge_1_10"] = False
        g["core_trades_ge_30"] = False
        g["core_not_evaluable"] = True
    g["ex722_pnl_gt_0"] = float(metrics["ex722_pnl"]) > 0
    g["ex722_pf_gt_1"] = (
        metrics.get("ex722_pf_status") == "NO_LOSS"
        or (metrics.get("ex722_pf") is not None and float(metrics["ex722_pf"]) > 1.0 + 1e-12)
    )
    g["ex722_trades_ge_30"] = int(metrics["ex722_n"]) >= 30
    g["trade_support_all"] = int(metrics["n"]) >= 30
    g["rolling_pass"] = bool(rolling.get("pass"))
    g["day_deletion_pass"] = bool(day_del.get("all_pass"))
    g["ex_top1_trade"] = float(metrics["ex_top1_trade_pnl"]) > 0
    g["ex_top1_symbol"] = float(metrics["ex_top1_symbol_pnl"]) > 0
    # BASE compare (ALL_USABLE)
    b_pf = base_all_usable.get("pf")
    b_stop = float(base_all_usable.get("stop_loss_total") or 0.0)
    b_stop_pt = float(base_all_usable.get("stop_loss_per_trade") or 0.0)
    b_dd = float(base_all_usable.get("max_dd") or 0.0)
    m_pf = metrics.get("pf")
    g["base_pf_improved"] = (
        m_pf is not None and b_pf is not None and float(m_pf) > float(b_pf) + 1e-12
    ) or metrics.get("pf_status") == "NO_LOSS"
    g["base_stop_improved"] = float(metrics["stop_loss_total"]) > b_stop - 1e-9  # less negative better? 
    # stop_loss is negative sum; "improved" means greater (closer to 0) 
    g["base_stop_improved"] = float(metrics["stop_loss_total"]) >= b_stop - 1e-9
    g["base_stop_per_trade_improved"] = float(metrics["stop_loss_per_trade"]) >= b_stop_pt - 1e-9
    g["base_dd_improved"] = float(metrics["max_dd"]) >= b_dd - 1e-9

    failed = [k for k, v in g.items() if k != "core_not_evaluable" and not v]
    return {"gates": g, "failed": failed, "all_pass": len(failed) == 0}
