"""Phase B economics: cost, PnL, DD/STOP, candidate metrics, gates, ranking.

DD/STOP formulas mirror base_recut / day_robust_gates (identical aggregation).
Cost: entry_ask * 100 * 0.0005 once (5bps round-trip, single application).
"""
from __future__ import annotations

import statistics
from typing import Any, Mapping, Optional, Sequence

from .evaluation_plan import ROLLING_ORIGIN_5FOLD, sens_722_summary
from .source_manifest import DAYS

LOT = 100
COST_RATE = 0.0005  # 5bps once
ROUNDTRIP_COST_BPS = 5.0

BASE_MAX_DD = -587_949.39
BASE_STOP_LOSS_TOTAL = -1_930_719.04
BASE_COMPLETED = 915
BASE_PNL = 775_217.19


def yen_roundtrip_cost(entry_ask: float) -> float:
    return float(entry_ask) * LOT * COST_RATE


def net_pnl_yen(entry_ask: float, exit_bid: float) -> dict[str, float]:
    cost = yen_roundtrip_cost(entry_ask)
    gross = (float(exit_bid) - float(entry_ask)) * LOT
    return {
        "gross_pnl_yen_100": gross,
        "cost_yen_100": cost,
        "net_pnl_yen_100": gross - cost,
    }


def realized_sequence_max_dd(trades: Sequence[Mapping[str, Any]]) -> float:
    """Identical to base_recut._max_dd / day_robust_gates.realized_sequence_max_dd."""
    rows = sorted(
        trades,
        key=lambda t: (str(t.get("exit_time") or ""), str(t.get("symbol") or "")),
    )
    eq = peak = dd = 0.0
    for t in rows:
        eq += float(t.get("net_pnl_yen_100") or 0.0)
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    return dd


def stop_loss_total(trades: Sequence[Mapping[str, Any]]) -> float:
    """Identical to base_recut._stop_loss_total."""
    return sum(
        p for t in trades
        if str(t.get("exit_reason") or "") == "STOP"
        and (p := float(t.get("net_pnl_yen_100") or 0.0)) < 0
    )


def _median(vals: Sequence[float]) -> float:
    return float(statistics.median(vals)) if vals else 0.0


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


def best_days_desc(day_pnl: Mapping[str, float]) -> list[str]:
    """pnl desc, tie-break day asc."""
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


def daily_pnls(trades: Sequence[Mapping[str, Any]], days: Sequence[str] = DAYS) -> dict[str, dict[str, float]]:
    out = {d: {"pnl": 0.0, "n": 0} for d in days}
    for t in trades:
        d = str(t.get("day") or "")
        if d not in out:
            continue
        out[d]["pnl"] += float(t.get("net_pnl_yen_100") or 0.0)
        out[d]["n"] += 1
    return out


def session_pnls(trades: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    out: dict[str, float] = {}
    for t in trades:
        wid = f"{t.get('day')}_{t.get('am_pm')}"
        out[wid] = out.get(wid, 0.0) + float(t.get("net_pnl_yen_100") or 0.0)
    return out


def simplicity_score(cand: Mapping[str, Any]) -> int:
    """Fewer parameters = simpler. features + exit knobs."""
    n_feat = len(cand.get("features_used") or [])
    exit_params = 3  # stop + no_progress + max_hold always
    if cand.get("trailing"):
        exit_params += 2  # arm + giveback
    if cand.get("invalidation"):
        exit_params += 1
    return int(n_feat + exit_params)


def candidate_metrics(
    trades: Sequence[Mapping[str, Any]],
    *,
    days: Sequence[str] = DAYS,
    windows_included: Optional[Sequence[str]] = None,
) -> dict[str, Any]:
    """Full per-candidate aggregation required by Phase B §7."""
    dmap = daily_pnls(trades, days)
    day_pnl = {d: float(v["pnl"]) for d, v in dmap.items()}
    day_n = {d: int(v["n"]) for d, v in dmap.items()}
    pnls = [float(t.get("net_pnl_yen_100") or 0.0) for t in trades]
    total = float(sum(pnls))
    gross_profit = float(sum(p for p in pnls if p > 0))
    gross_loss = float(sum(p for p in pnls if p < 0))
    wins = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p < 0)
    pf, pf_status = pf_of(pnls)
    order = best_days_desc(day_pnl)
    best1 = order[0] if order else None
    best2 = order[1] if len(order) > 1 else None
    ex_best1 = total - (day_pnl.get(best1, 0.0) if best1 else 0.0)
    ex_best2 = ex_best1 - (day_pnl.get(best2, 0.0) if best2 else 0.0)
    gross_pos = float(sum(p for p in day_pnl.values() if p > 0))
    top1_share = (
        (day_pnl[best1] / gross_pos) if best1 and gross_pos > 0 and day_pnl[best1] > 0 else 0.0
    )
    top2_sum = sum(day_pnl[d] for d in (best1, best2) if d is not None and day_pnl[d] > 0)
    top2_share = (top2_sum / gross_pos) if gross_pos > 0 else 0.0

    top1_trade = max(pnls) if pnls else 0.0
    sym_pnl: dict[str, float] = {}
    sym_n: dict[str, int] = {}
    exit_reason_n: dict[str, int] = {}
    exit_reason_pnl: dict[str, float] = {}
    for t in trades:
        s = str(t.get("symbol") or "")
        p = float(t.get("net_pnl_yen_100") or 0.0)
        sym_pnl[s] = sym_pnl.get(s, 0.0) + p
        sym_n[s] = sym_n.get(s, 0) + 1
        er = str(t.get("exit_reason") or "")
        exit_reason_n[er] = exit_reason_n.get(er, 0) + 1
        exit_reason_pnl[er] = exit_reason_pnl.get(er, 0.0) + p
    top_sym = max(sym_pnl.items(), key=lambda kv: (kv[1], kv[0]))[0] if sym_pnl else None
    worst_sym = min(sym_pnl.items(), key=lambda kv: (kv[1], kv[0]))[0] if sym_pnl else None

    win_pnl = session_pnls(trades)
    if windows_included:
        for wid in windows_included:
            win_pnl.setdefault(wid, 0.0)

    n_trades = len(pnls)
    days_with = sum(1 for d in days if day_n[d] > 0)
    total_n = sum(day_n.values()) or 1
    day_share = {d: day_n[d] / total_n for d in days}
    share_order = sorted(days, key=lambda d: (-day_share[d], d))
    max_day_share = day_share[share_order[0]] if share_order else 0.0
    top2_trade_share = sum(day_share[d] for d in share_order[:2])

    stop_n = exit_reason_n.get("STOP", 0)
    stop_tot = stop_loss_total(trades)
    max_dd = realized_sequence_max_dd(trades)

    sens = sens_722_summary(day_pnl)
    ex722_trades = [t for t in trades if str(t.get("day")) != "20260722"]
    ex722_pnls = [float(t.get("net_pnl_yen_100") or 0.0) for t in ex722_trades]
    ex722_pf, ex722_pf_status = pf_of(ex722_pnls)
    sens["ex722_pf"] = ex722_pf
    sens["ex722_pf_status"] = ex722_pf_status
    sens["ex722_median_day_pnl"] = _median(
        [day_pnl[d] for d in days if d != "20260722"]
    )

    best_trade = max(trades, key=lambda t: float(t.get("net_pnl_yen_100") or 0.0)) if trades else None
    worst_trade = min(trades, key=lambda t: float(t.get("net_pnl_yen_100") or 0.0)) if trades else None
    best_day = best1
    worst_day = min(day_pnl.items(), key=lambda kv: (kv[1], kv[0]))[0] if day_pnl else None

    mfe = [float(t["mfe_yen"]) for t in trades if t.get("mfe_yen") is not None]
    mae = [float(t["mae_yen"]) for t in trades if t.get("mae_yen") is not None]

    return {
        "completed_trades": n_trades,
        "wins": wins,
        "losses": losses,
        "win_rate": (wins / n_trades) if n_trades else 0.0,
        "total_pnl": total,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "pf": pf,
        "pf_status": pf_status,
        "avg_pnl": (total / n_trades) if n_trades else 0.0,
        "median_trade_pnl": _median(pnls),
        "day_pnl": day_pnl,
        "day_n": day_n,
        "session_pnl": win_pnl,
        "median_day_pnl": _median(list(day_pnl.values())),
        "day_pnl_q25": _quantile(list(day_pnl.values()), 0.25),
        "best1_day": best1,
        "best2_day": best2,
        "worst_day": worst_day,
        "ex_best1_day_pnl": ex_best1,
        "ex_best2_days_pnl": ex_best2,
        "gross_positive_day_pnl": gross_pos,
        "top1_day_share_of_gross_positive": top1_share,
        "top2_days_share_of_gross_positive": top2_share,
        "ex_top1_trade_pnl": total - top1_trade,
        "top1_symbol": top_sym,
        "worst_symbol": worst_sym,
        "ex_top1_symbol_pnl": total - (sym_pnl.get(top_sym, 0.0) if top_sym else 0.0),
        "symbol_pnl": sym_pnl,
        "symbol_n": sym_n,
        "exit_reason_n": exit_reason_n,
        "exit_reason_pnl": exit_reason_pnl,
        "max_dd": max_dd,
        "stop_n": stop_n,
        "stop_loss_total": stop_tot,
        "days_with_trades": days_with,
        "max_day_trade_share": max_day_share,
        "top2_day_trade_share": top2_trade_share,
        "day_trade_share": day_share,
        "best_trade": (
            {"symbol": best_trade.get("symbol"), "pnl": best_trade.get("net_pnl_yen_100"),
             "day": best_trade.get("day")} if best_trade else None
        ),
        "worst_trade": (
            {"symbol": worst_trade.get("symbol"), "pnl": worst_trade.get("net_pnl_yen_100"),
             "day": worst_trade.get("day")} if worst_trade else None
        ),
        "mfe_median": _median(mfe) if mfe else None,
        "mae_median": _median(mae) if mae else None,
        "sensitivity_20260722": sens,
        "base_comparison": {
            "delta_completed": n_trades - BASE_COMPLETED,
            "delta_pnl": total - BASE_PNL,
            "delta_max_dd": max_dd - BASE_MAX_DD,
            "delta_stop_loss_total": stop_tot - BASE_STOP_LOSS_TOTAL,
            "base_completed": BASE_COMPLETED,
            "base_pnl": BASE_PNL,
            "base_max_dd": BASE_MAX_DD,
            "base_stop_loss_total": BASE_STOP_LOSS_TOTAL,
        },
    }


def evaluate_candidate_gates(metrics: Mapping[str, Any]) -> dict[str, Any]:
    """Candidate-unit gates §8 (1–18). Rolling/LODO applied separately at selection."""
    m = metrics
    sens = m["sensitivity_20260722"]
    g: dict[str, bool] = {}
    g["total_pnl_gt_0"] = float(m["total_pnl"]) > 0.0
    g["median_day_pnl_gt_0"] = float(m["median_day_pnl"]) > 0.0
    g["ex_best1_day_pnl_gt_0"] = float(m["ex_best1_day_pnl"]) > 0.0
    g["ex_best2_days_pnl_gt_0"] = float(m["ex_best2_days_pnl"]) > 0.0
    g["top1_day_contribution_le_30pct"] = float(m["top1_day_share_of_gross_positive"]) <= 0.30 + 1e-12
    g["top2_days_contribution_le_50pct"] = float(m["top2_days_share_of_gross_positive"]) <= 0.50 + 1e-12
    g["ex_top1_trade_pnl_gt_0"] = float(m["ex_top1_trade_pnl"]) > 0.0
    g["ex_top1_symbol_pnl_gt_0"] = float(m["ex_top1_symbol_pnl"]) > 0.0
    g["pf_ge_1_10"] = _pf_pass(m.get("pf"), str(m.get("pf_status")), 1.10)
    g["completed_trades_ge_30"] = int(m["completed_trades"]) >= 30
    g["days_with_trades_ge_6"] = int(m["days_with_trades"]) >= 6
    g["max_day_trade_share_le_30pct"] = float(m["max_day_trade_share"]) <= 0.30 + 1e-12
    g["top2_day_trade_share_le_50pct"] = float(m["top2_day_trade_share"]) <= 0.50 + 1e-12
    g["max_dd_ge_base"] = float(m["max_dd"]) >= BASE_MAX_DD - 1e-9
    g["stop_loss_ge_base"] = float(m["stop_loss_total"]) >= BASE_STOP_LOSS_TOTAL - 1e-9
    g["ex722_pnl_gt_0"] = float(sens["ex722_total_pnl"]) > 0.0
    g["ex722_pf_gt_1"] = _pf_pass(sens.get("ex722_pf"), str(sens.get("ex722_pf_status")), 1.0 + 1e-12)
    g["direction_agreement_ex722"] = bool(sens["direction_agreement_with_full"])
    return {
        "gates": g,
        "all_pass": all(g.values()),
        "failed": [k for k, v in g.items() if not v],
    }


def selection_rank_key(metrics: Mapping[str, Any], cand: Mapping[str, Any]) -> tuple:
    """Ascending sort → first is best (frozen ranking priority)."""
    t1 = float(metrics.get("top1_day_share_of_gross_positive") or 10.0)
    t2 = float(metrics.get("top2_days_share_of_gross_positive") or 10.0)
    pf = metrics.get("pf")
    pf_rank = -1e18 if str(metrics.get("pf_status")) == "NO_LOSS" else -(pf if pf is not None else -1e9)
    return (
        -float(metrics["ex_best2_days_pnl"]),
        -float(metrics["median_day_pnl"]),
        -float(metrics["day_pnl_q25"]),
        t1 + t2,
        -float(metrics["max_dd"]),
        pf_rank,
        simplicity_score(cand),
        -float(metrics["total_pnl"]),
        str(cand.get("strategy_id") or ""),
    )


def rank_on_days(
    candidates: Sequence[Mapping[str, Any]],
    per_sid_day_pnl: Mapping[str, Mapping[str, float]],
    build_days: Sequence[str],
) -> str:
    """Select top strategy_id by frozen ranking on build_days only."""
    best_sid = None
    best_key = None
    for cand in candidates:
        sid = cand["strategy_id"]
        dp = per_sid_day_pnl.get(sid) or {}
        sub = {d: float(dp.get(d, 0.0)) for d in build_days}
        vals = list(sub.values())
        total = float(sum(vals))
        order = best_days_desc(sub)
        ex1 = total - sub[order[0]] if order else total
        ex2 = ex1 - (sub[order[1]] if len(order) > 1 else 0.0)
        gross_pos = float(sum(p for p in vals if p > 0))
        b1 = order[0] if order else None
        b2 = order[1] if len(order) > 1 else None
        t1 = (sub[b1] / gross_pos) if b1 and gross_pos > 0 and sub[b1] > 0 else 0.0
        t2sum = sum(sub[d] for d in (b1, b2) if d is not None and sub[d] > 0)
        t2 = (t2sum / gross_pos) if gross_pos > 0 else 0.0
        m = {
            "ex_best2_days_pnl": ex2,
            "median_day_pnl": _median(vals),
            "day_pnl_q25": _quantile(vals, 0.25),
            "top1_day_share_of_gross_positive": t1,
            "top2_days_share_of_gross_positive": t2,
            "max_dd": 0.0,
            "pf": None,
            "pf_status": "EMPTY",
            "total_pnl": total,
        }
        key = selection_rank_key(m, cand)
        if best_key is None or key < best_key:
            best_key = key
            best_sid = sid
    assert best_sid is not None
    return best_sid


def rolling_origin_eval(
    candidates: Sequence[Mapping[str, Any]],
    per_sid_day_pnl: Mapping[str, Mapping[str, float]],
) -> dict[str, Any]:
    folds = []
    confirm_pnls: dict[str, float] = {}
    for fid, spec in ROLLING_ORIGIN_5FOLD.items():
        if fid == "rule":
            continue
        build = list(spec["build"])
        confirm = str(spec["confirm"])
        sid = rank_on_days(candidates, per_sid_day_pnl, build)
        cp = float((per_sid_day_pnl.get(sid) or {}).get(confirm, 0.0))
        confirm_pnls[confirm] = cp
        build_total = sum(float((per_sid_day_pnl.get(sid) or {}).get(d, 0.0)) for d in build)
        folds.append({
            "fold": fid,
            "build_days": build,
            "confirm_day": confirm,
            "selected_strategy_id": sid,
            "build_total_pnl": build_total,
            "confirm_pnl": cp,
            "selection_input_days": build,
        })
    vals = list(confirm_pnls.values())
    total = float(sum(vals))
    order = best_days_desc(confirm_pnls)
    ex_best = total - confirm_pnls[order[0]] if order else total
    gates = {
        "confirm_total_gt_0": total > 0.0,
        "confirm_median_gt_0": _median(vals) > 0.0,
        "ex_best_confirm_day_gt_0": ex_best > 0.0,
    }
    return {
        "folds": folds,
        "confirm_day_pnls": confirm_pnls,
        "confirm_total": total,
        "confirm_median": _median(vals),
        "ex_best_confirm_day_pnl": ex_best,
        "gates": gates,
        "all_pass": all(gates.values()),
    }


def lodo_fixed_spec(
    selected_sid: str,
    per_sid_day_pnl: Mapping[str, Mapping[str, float]],
    days: Sequence[str] = DAYS,
) -> dict[str, Any]:
    """FIXED_SPEC_DAY_DELETION: same candidate, each day deleted once."""
    dp = per_sid_day_pnl.get(selected_sid) or {}
    rows = []
    for held in days:
        sub = {d: float(dp.get(d, 0.0)) for d in days if d != held}
        rows.append({
            "held_out_day": held,
            "strategy_id": selected_sid,
            "ex_held_total_pnl": float(sum(sub.values())),
            "ex_held_median_day_pnl": _median(list(sub.values())),
        })
    return {"mode": "FIXED_SPEC_DAY_DELETION", "rows": rows}


def lodo_reselect(
    candidates: Sequence[Mapping[str, Any]],
    per_sid_day_pnl: Mapping[str, Mapping[str, float]],
    days: Sequence[str] = DAYS,
) -> dict[str, Any]:
    """RESELECT_LODO_STABILITY: select on 8 days, apply once to held-out."""
    held_pnls: dict[str, float] = {}
    rows = []
    for held in days:
        build = [d for d in days if d != held]
        sid = rank_on_days(candidates, per_sid_day_pnl, build)
        hp = float((per_sid_day_pnl.get(sid) or {}).get(held, 0.0))
        held_pnls[held] = hp
        rows.append({
            "held_out_day": held,
            "build_days": build,
            "selected_strategy_id": sid,
            "held_out_pnl": hp,
        })
    vals = list(held_pnls.values())
    total = float(sum(vals))
    order = best_days_desc(held_pnls)
    ex1 = total - held_pnls[order[0]] if order else total
    ex2 = ex1 - (held_pnls[order[1]] if len(order) > 1 else 0.0)
    gates = {
        "held_out_total_gt_0": total > 0.0,
        "held_out_median_gt_0": _median(vals) > 0.0,
        "ex_best1_held_out_gt_0": ex1 > 0.0,
        "ex_best2_held_out_gt_0": ex2 > 0.0,
    }
    return {
        "mode": "RESELECT_LODO_STABILITY",
        "rows": rows,
        "held_out_day_pnls": held_pnls,
        "held_out_total": total,
        "held_out_median": _median(vals),
        "ex_best1_held_out_pnl": ex1,
        "ex_best2_held_out_pnl": ex2,
        "gates": gates,
        "all_pass": all(gates.values()),
    }
