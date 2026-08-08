"""Plan Version 2.1 day-robust joint gates + selection ranking (research-only).

Goal shift (2.1): NOT "positive every day" — losing days are allowed. Required is
independence from 1-2 outlier days plus expectancy on normal days. Definitions here
are pre-registered in P1 BEFORE economics; date-specific conditions (7/22, 7/31 ...)
are forbidden as gates — best days are derived mechanically each time.

No Shadow / Runtime / Paper / Live changes from this module.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from research.e1_x6_provisional.constants import DAYS

# Day 20260722 sensitivity check is a data-anomaly sensitivity requirement carried
# from Plan 1.x (§10.2). It is an exclusion SENSITIVITY, not a date-fitted gate.
SENSITIVITY_EXCLUDE_DAY = "20260722"

ROLLING_CONFIRM_DAYS = ("20260727", "20260728", "20260729", "20260730", "20260731")

PLAN21_GATE_IDS = [
    "total_pnl_gt_0",
    "median_day_pnl_gt_0",
    "ex_best1_day_pnl_gt_0",
    "ex_best2_days_pnl_gt_0",
    "top1_day_contribution_le_30pct_of_gross_positive",
    "top2_days_contribution_le_50pct_of_gross_positive",
    "ex722_pnl_gt_0_and_pf_gt_1",
    "top1_trade_excluded_pnl_gt_0",
    "top1_symbol_excluded_pnl_gt_0",
    "pf_ge_1_10",
    "period_trades_ge_30",
    "each_day_trades_ge_3",
    "dd_and_stop_loss_not_worse_than_base",
    "invalid_source_count_0",
    "ab_determinism_exact",
    "rolling_confirm_total_gt_0",
    "rolling_confirm_median_gt_0",
    "rolling_ex_best_confirm_day_gt_0",
    "lodo_held_out_total_gt_0",
    "lodo_held_out_median_gt_0",
    "lodo_ex_best1_gt_0",
    "lodo_ex_best2_gt_0",
]

ABOLISHED_20_GATES = [
    "all_9_days_pnl_gt_0",
    "worst_day_net_pnl_gt_0",
    "rolling_origin_confirm_5_of_5_positive",
    "refit_lodo_held_out_9_of_9_positive",
    "forward_20_of_20_positive",
]

SELECTION_PRIORITY = (
    "1) ex_best2_days_pnl desc",
    "2) median_day_pnl desc",
    "3) day_pnl_q25 desc",
    "4) top1+top2 day concentration asc",
    "5) max_dd asc-in-magnitude (less negative better)",
    "6) pf desc (NO_LOSS ranked above any finite pf)",
    "7) simplicity: n_entry_features + n_exit_params asc",
    "8) total period pnl desc",
    "tie) strategy_id lex asc",
)


def _median(vals: Sequence[float]) -> float:
    s = sorted(vals)
    n = len(s)
    if n == 0:
        return 0.0
    if n % 2 == 1:
        return float(s[n // 2])
    return float((s[n // 2 - 1] + s[n // 2]) / 2.0)


def _quantile(vals: Sequence[float], q: float) -> float:
    s = sorted(vals)
    if not s:
        return 0.0
    if len(s) == 1:
        return float(s[0])
    pos = q * (len(s) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    frac = pos - lo
    return float(s[lo] * (1 - frac) + s[hi] * frac)


def daily_pnls(
    trades: Sequence[Mapping[str, Any]],
    days: Sequence[str] = DAYS,
) -> dict[str, dict[str, float]]:
    """Per-day net pnl / count over the fixed day list. Zero-trade day => pnl 0.0."""
    out = {d: {"pnl": 0.0, "n": 0} for d in days}
    for t in trades:
        d = str(t.get("day") or "")
        if d not in out:
            continue
        out[d]["pnl"] += float(t.get("net_pnl_yen_100") or 0.0)
        out[d]["n"] += 1
    return out


def best_days_desc(day_pnl: Mapping[str, float]) -> list[str]:
    """Mechanical best-day ordering: pnl desc, tie-break day asc. Never date-fitted."""
    return [d for d, _ in sorted(day_pnl.items(), key=lambda kv: (-kv[1], kv[0]))]


def pf_of(pnls: Sequence[float]) -> tuple[Optional[float], str]:
    wins = sum(p for p in pnls if p > 0)
    losses = sum(p for p in pnls if p < 0)
    if losses < 0:
        return wins / abs(losses), "OK"
    if wins > 0:
        return None, "NO_LOSS"
    return None, "EMPTY"


def _pf_pass(pf: Optional[float], status: str, threshold: float) -> bool:
    if status == "NO_LOSS":
        return True
    if pf is None:
        return False
    return pf >= threshold


def realized_sequence_max_dd(trades: Sequence[Mapping[str, Any]]) -> float:
    """Max drawdown of cumulative net PnL in JST exit order (tie: exit_time, symbol)."""
    rows = sorted(
        trades,
        key=lambda t: (str(t.get("exit_time") or ""), str(t.get("symbol") or "")),
    )
    eq = 0.0
    peak = 0.0
    dd = 0.0
    for t in rows:
        eq += float(t.get("net_pnl_yen_100") or 0.0)
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    return dd


def stop_loss_total(trades: Sequence[Mapping[str, Any]]) -> float:
    """Sum of negative net pnl over STOP exits (<= 0)."""
    tot = 0.0
    for t in trades:
        if str(t.get("exit_reason") or "") == "STOP":
            p = float(t.get("net_pnl_yen_100") or 0.0)
            if p < 0:
                tot += p
    return tot


def day_robust_metrics(
    trades: Sequence[Mapping[str, Any]],
    *,
    days: Sequence[str] = DAYS,
) -> dict[str, Any]:
    """All Plan 2.1 per-package day metrics from a completed-trade ledger."""
    dmap = daily_pnls(trades, days)
    day_pnl = {d: v["pnl"] for d, v in dmap.items()}
    day_n = {d: v["n"] for d, v in dmap.items()}
    pnls = [float(t.get("net_pnl_yen_100") or 0.0) for t in trades]
    total = float(sum(pnls))
    order = best_days_desc(day_pnl)
    best1 = order[0] if order else None
    best2 = order[1] if len(order) > 1 else None
    ex_best1 = total - (day_pnl.get(best1, 0.0) if best1 else 0.0)
    ex_best2 = ex_best1 - (day_pnl.get(best2, 0.0) if best2 else 0.0)
    gross_pos = float(sum(p for p in day_pnl.values() if p > 0))
    top1_share = (
        (day_pnl[best1] / gross_pos) if best1 and gross_pos > 0 and day_pnl[best1] > 0 else None
    )
    top2_sum = sum(
        day_pnl[d] for d in (best1, best2) if d is not None and day_pnl[d] > 0
    )
    top2_share = (top2_sum / gross_pos) if gross_pos > 0 else None

    ex722 = [
        float(t.get("net_pnl_yen_100") or 0.0)
        for t in trades
        if str(t.get("day") or "") != SENSITIVITY_EXCLUDE_DAY
    ]
    ex722_pnl = float(sum(ex722))
    ex722_pf, ex722_pf_status = pf_of(ex722)

    pf, pf_status = pf_of(pnls)
    top1_trade = max(pnls) if pnls else 0.0
    sym_pnl: dict[str, float] = {}
    for t in trades:
        s = str(t.get("symbol") or "")
        sym_pnl[s] = sym_pnl.get(s, 0.0) + float(t.get("net_pnl_yen_100") or 0.0)
    top_sym = max(sym_pnl.items(), key=lambda kv: (kv[1], kv[0]))[0] if sym_pnl else None

    confirm_pnls = {d: day_pnl.get(d, 0.0) for d in ROLLING_CONFIRM_DAYS if d in day_pnl}
    confirm_vals = list(confirm_pnls.values())
    confirm_total = float(sum(confirm_vals))
    confirm_order = best_days_desc(confirm_pnls)
    confirm_best = confirm_order[0] if confirm_order else None
    confirm_ex_best = confirm_total - (confirm_pnls.get(confirm_best, 0.0) if confirm_best else 0.0)

    return {
        "n": len(pnls),
        "total_pnl": total,
        "day_pnl": day_pnl,
        "day_n": day_n,
        "median_day_pnl": _median(list(day_pnl.values())),
        "day_pnl_q25": _quantile(list(day_pnl.values()), 0.25),
        "best1_day": best1,
        "best2_day": best2,
        "ex_best1_day_pnl": ex_best1,
        "ex_best2_days_pnl": ex_best2,
        "gross_positive_day_pnl": gross_pos,
        "top1_day_share_of_gross_positive": top1_share,
        "top2_days_share_of_gross_positive": top2_share,
        "ex722_pnl": ex722_pnl,
        "ex722_pf": ex722_pf,
        "ex722_pf_status": ex722_pf_status,
        "pf": pf,
        "pf_status": pf_status,
        "ex_top1_trade_pnl": total - top1_trade,
        "top1_symbol": top_sym,
        "ex_top1_symbol_pnl": total - (sym_pnl.get(top_sym, 0.0) if top_sym else 0.0),
        "max_dd": realized_sequence_max_dd(trades),
        "stop_loss_total": stop_loss_total(trades),
        "rolling_confirm_day_pnls": confirm_pnls,
        "rolling_confirm_total": confirm_total,
        "rolling_confirm_median": _median(confirm_vals),
        "rolling_confirm_best_day": confirm_best,
        "rolling_ex_best_confirm_day": confirm_ex_best,
    }


def evaluate_plan21_gates(
    metrics: Mapping[str, Any],
    *,
    base_max_dd: Optional[float],
    base_stop_loss_total: Optional[float],
    ab_match: bool,
    invalid_source_n: int,
    lodo_held_out_pnls: Optional[Mapping[str, float]] = None,
    days: Sequence[str] = DAYS,
) -> dict[str, Any]:
    """Plan 2.1 mandatory gate evaluation for one JointStrategyPackage.

    lodo_held_out_pnls: held-out day -> pnl of the package selected by the 2.1
    refit-selection procedure on the other 8 days (sweep-level input).
    """
    m = metrics
    g: dict[str, bool] = {}
    g["total_pnl_gt_0"] = float(m["total_pnl"]) > 0.0
    g["median_day_pnl_gt_0"] = float(m["median_day_pnl"]) > 0.0
    g["ex_best1_day_pnl_gt_0"] = float(m["ex_best1_day_pnl"]) > 0.0
    g["ex_best2_days_pnl_gt_0"] = float(m["ex_best2_days_pnl"]) > 0.0
    t1 = m.get("top1_day_share_of_gross_positive")
    t2 = m.get("top2_days_share_of_gross_positive")
    g["top1_day_contribution_le_30pct_of_gross_positive"] = t1 is not None and t1 <= 0.30 + 1e-12
    g["top2_days_contribution_le_50pct_of_gross_positive"] = t2 is not None and t2 <= 0.50 + 1e-12
    g["ex722_pnl_gt_0_and_pf_gt_1"] = float(m["ex722_pnl"]) > 0.0 and _pf_pass(
        m.get("ex722_pf"), str(m.get("ex722_pf_status")), 1.0 + 1e-12
    )
    g["top1_trade_excluded_pnl_gt_0"] = float(m["ex_top1_trade_pnl"]) > 0.0
    g["top1_symbol_excluded_pnl_gt_0"] = float(m["ex_top1_symbol_pnl"]) > 0.0
    g["pf_ge_1_10"] = _pf_pass(m.get("pf"), str(m.get("pf_status")), 1.10)
    g["period_trades_ge_30"] = int(m["n"]) >= 30
    g["each_day_trades_ge_3"] = all(int(m["day_n"].get(d, 0)) >= 3 for d in days)
    dd_ok = base_max_dd is not None and float(m["max_dd"]) >= float(base_max_dd) - 1e-9
    stop_ok = (
        base_stop_loss_total is not None
        and float(m["stop_loss_total"]) >= float(base_stop_loss_total) - 1e-9
    )
    g["dd_and_stop_loss_not_worse_than_base"] = bool(dd_ok and stop_ok)
    g["invalid_source_count_0"] = int(invalid_source_n) == 0
    g["ab_determinism_exact"] = bool(ab_match)

    g["rolling_confirm_total_gt_0"] = float(m["rolling_confirm_total"]) > 0.0
    g["rolling_confirm_median_gt_0"] = float(m["rolling_confirm_median"]) > 0.0
    g["rolling_ex_best_confirm_day_gt_0"] = float(m["rolling_ex_best_confirm_day"]) > 0.0

    if lodo_held_out_pnls is not None and len(lodo_held_out_pnls) == len(days):
        vals = {d: float(v) for d, v in lodo_held_out_pnls.items()}
        total = float(sum(vals.values()))
        order = best_days_desc(vals)
        ex1 = total - vals[order[0]] if order else total
        ex2 = ex1 - (vals[order[1]] if len(order) > 1 else 0.0)
        g["lodo_held_out_total_gt_0"] = total > 0.0
        g["lodo_held_out_median_gt_0"] = _median(list(vals.values())) > 0.0
        g["lodo_ex_best1_gt_0"] = ex1 > 0.0
        g["lodo_ex_best2_gt_0"] = ex2 > 0.0
    else:
        g["lodo_held_out_total_gt_0"] = False
        g["lodo_held_out_median_gt_0"] = False
        g["lodo_ex_best1_gt_0"] = False
        g["lodo_ex_best2_gt_0"] = False

    return {
        "gates": g,
        "all_pass": all(g.values()),
        "failed": [k for k, v in g.items() if not v],
    }


def simplicity_score(package: Mapping[str, Any]) -> int:
    """Fewer parameters = simpler. n entry features + n distinct exit params."""
    feats = package.get("entry_features") or []
    xs = package.get("exit_spec") or {}
    exit_params = 0
    for k in ("initial_stop_bps", "target_bps", "max_hold_sec"):
        if xs.get(k) is not None:
            exit_params += 1
    tr = xs.get("trailing") or {}
    exit_params += sum(1 for k in ("arm_bps", "giveback") if tr.get(k) is not None)
    np_ = xs.get("no_progress") or {}
    if np_.get("enabled"):
        exit_params += 1
    inv = xs.get("invalidation")
    if inv and str(inv) not in ("", "NONE"):
        exit_params += 1
    return int(len(feats) + exit_params)


def selection_rank_key(
    metrics: Mapping[str, Any],
    *,
    package: Mapping[str, Any],
) -> tuple:
    """Sort key implementing SELECTION_PRIORITY (ascending sort selects first)."""
    m = metrics
    t1 = m.get("top1_day_share_of_gross_positive")
    t2 = m.get("top2_days_share_of_gross_positive")
    conc = (
        (t1 if t1 is not None else 10.0) + (t2 if t2 is not None else 10.0)
    )
    pf = m.get("pf")
    pf_rank = -1e18 if str(m.get("pf_status")) == "NO_LOSS" else -(pf if pf is not None else -1e9)
    return (
        -float(m["ex_best2_days_pnl"]),
        -float(m["median_day_pnl"]),
        -float(m["day_pnl_q25"]),
        conc,
        -float(m["max_dd"]),  # max_dd <= 0; less negative sorts first
        pf_rank,
        simplicity_score(package),
        -float(m["total_pnl"]),
        str(package.get("strategy_id") or ""),
    )


def refit_lodo_selection(
    per_package_day_pnls: Mapping[str, Mapping[str, float]],
    packages_by_id: Mapping[str, Mapping[str, Any]],
    *,
    held_out_day: str,
    days: Sequence[str] = DAYS,
) -> dict[str, Any]:
    """Refit selection on the 8 build days using the 2.1 priority ranking.

    Ranking metrics are recomputed on build days only (day list = 8 days). The
    held-out day's pnl of the selected package is the LODO evidence value.
    Pre-registered: no gate filter at selection (deterministic pure ranking);
    gates apply to the resulting held-out pnl collection.
    """
    build_days = [d for d in days if d != held_out_day]
    best_sid = None
    best_key = None
    for sid in sorted(per_package_day_pnls.keys()):
        dp = per_package_day_pnls[sid]
        sub = {d: float(dp.get(d, 0.0)) for d in build_days}
        vals = list(sub.values())
        total = float(sum(vals))
        order = best_days_desc(sub)
        ex1 = total - sub[order[0]] if order else total
        ex2 = ex1 - (sub[order[1]] if len(order) > 1 else 0.0)
        gross_pos = float(sum(p for p in vals if p > 0))
        b1 = order[0] if order else None
        b2 = order[1] if len(order) > 1 else None
        t1 = (sub[b1] / gross_pos) if b1 and gross_pos > 0 and sub[b1] > 0 else None
        t2sum = sum(sub[d] for d in (b1, b2) if d is not None and sub[d] > 0)
        t2 = (t2sum / gross_pos) if gross_pos > 0 else None
        m = {
            "ex_best2_days_pnl": ex2,
            "median_day_pnl": _median(vals),
            "day_pnl_q25": _quantile(vals, 0.25),
            "top1_day_share_of_gross_positive": t1,
            "top2_days_share_of_gross_positive": t2,
            "max_dd": 0.0,  # trade-level DD not recomputed per subset; neutral term
            "pf": None,
            "pf_status": "EMPTY",
            "total_pnl": total,
        }
        key = selection_rank_key(m, package=packages_by_id[sid])
        if best_key is None or key < best_key:
            best_key = key
            best_sid = sid
    held_pnl = float(per_package_day_pnls[best_sid].get(held_out_day, 0.0)) if best_sid else 0.0
    return {
        "held_out_day": held_out_day,
        "selected_strategy_id": best_sid,
        "held_out_pnl": held_pnl,
    }
