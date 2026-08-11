"""Requested capital-sweep metrics (realized equity DD, daily PF, t-value)."""
from __future__ import annotations

import math
from typing import Any, Optional

import numpy as np

from . import HISTORICAL_DAYS, SYMBOL_285A


def _daily_pf(gross_profit: float, gross_loss: float, has_pnl: bool) -> Any:
    if not has_pnl:
        return "NA"
    if gross_loss > 1e-12:
        return float(gross_profit / gross_loss)
    if gross_profit > 1e-12:
        return "INF"
    return 0.0


def realized_equity_drawdown(events: list[dict], *, initial_capital: float) -> dict[str, Any]:
    """Primary DD on realized equity (EXIT-time order). Reserved cash is NOT a loss."""
    accepted = [
        e for e in events
        if e.get("accepted") and e.get("canonical_exit_time") is not None
    ]
    accepted.sort(key=lambda e: (float(e["canonical_exit_time"]), str(e["symbol"]), str(e["date"])))
    equity = float(initial_capital)
    peak = equity
    peak_t = None
    max_dd_yen = 0.0
    max_dd_pct = 0.0
    dd_peak_time = None
    dd_trough_time = None
    trough_eq = equity
    path = [{"t": None, "equity": equity, "pnl": 0.0, "event": "start"}]
    for e in accepted:
        pnl = float(e.get("realized_pnl_yen") or 0.0)
        t = float(e["canonical_exit_time"])
        equity += pnl
        if equity > peak + 1e-12:
            peak = equity
            peak_t = t
        dd_yen = peak - equity  # positive drawdown magnitude
        dd_pct = (dd_yen / peak * 100.0) if peak > 1e-12 else 0.0
        if dd_yen > max_dd_yen + 1e-12:
            max_dd_yen = dd_yen
            max_dd_pct = dd_pct
            dd_peak_time = peak_t
            dd_trough_time = t
            trough_eq = equity
        path.append({
            "t": t, "equity": equity, "pnl": pnl,
            "symbol": e["symbol"], "date": e["date"],
        })
    return {
        "max_dd_yen": float(max_dd_yen),
        "max_dd_pct": float(max_dd_pct),
        "dd_peak_time": dd_peak_time,
        "dd_trough_time": dd_trough_time,
        "peak_equity": float(peak),
        "trough_equity": float(trough_eq),
        "ending_equity": float(equity),
        "path_n": len(path),
    }


def max_losing_day_streak(day_pnls: dict[str, float]) -> dict[str, Any]:
    days = [d for d in HISTORICAL_DAYS if d in day_pnls]
    best = 0
    best_start = best_end = None
    cur = 0
    cur_start = None
    for d in days:
        if day_pnls[d] < 0:
            if cur == 0:
                cur_start = d
            cur += 1
            if cur > best:
                best = cur
                best_start = cur_start
                best_end = d
        else:
            cur = 0
            cur_start = None
    return {
        "max_losing_day_streak": best,
        "streak_start": best_start,
        "streak_end": best_end,
    }


def max_losing_trade_streak(events: list[dict]) -> int:
    accepted = [
        e for e in events
        if e.get("accepted") and e.get("canonical_exit_time") is not None
    ]
    accepted.sort(key=lambda e: float(e["canonical_exit_time"]))
    best = cur = 0
    for e in accepted:
        if float(e.get("realized_pnl_yen") or 0.0) < 0:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def t_stat_daily_returns(returns: list[float]) -> dict[str, Any]:
    n = len(returns)
    arr = np.asarray(returns, dtype=float)
    mean = float(np.mean(arr)) if n else 0.0
    std = float(np.std(arr, ddof=1)) if n >= 2 else float("nan")
    if n < 2 or not np.isfinite(std) or std < 1e-18:
        t = float("nan")
        p = float("nan")
    else:
        t = mean / (std / math.sqrt(n))
        try:
            from scipy import stats
            p = float(2 * stats.t.sf(abs(t), n - 1))
        except Exception:
            p = float("nan")
    return {
        "mean_daily_return": mean,
        "std_daily_return": std,
        "t_value": float(t) if np.isfinite(t) else None,
        "two_sided_p_value": float(p) if np.isfinite(p) else None,
        "n_days": n,
    }


def summarize_capital_run(sim: dict[str, Any], *, initial_capital: float) -> dict[str, Any]:
    events = sim["events"]
    days = list(HISTORICAL_DAYS)

    # Per-trade accepted PnL by date (EXIT attribution by signal date = day of trade)
    by_day_trades: dict[str, list[float]] = {d: [] for d in days}
    for e in events:
        if not e.get("accepted"):
            continue
        d = e["date"]
        if d in by_day_trades:
            by_day_trades[d].append(float(e.get("realized_pnl_yen") or 0.0))

    daily_rows = []
    day_pnl: dict[str, float] = {}
    finite_pfs: list[float] = []
    inf_days = 0
    na_days = 0
    cum = 0.0
    returns: list[float] = []

    for d in days:
        pnls = by_day_trades[d]
        realized = float(sum(pnls))
        day_pnl[d] = realized
        gp = float(sum(max(x, 0.0) for x in pnls))
        gl = float(abs(sum(min(x, 0.0) for x in pnls)))
        has = len(pnls) > 0
        dpf = _daily_pf(gp, gl, has)
        if dpf == "INF":
            inf_days += 1
        elif dpf == "NA":
            na_days += 1
        elif isinstance(dpf, (int, float)) and np.isfinite(dpf):
            finite_pfs.append(float(dpf))

        start_eq = float(initial_capital) + cum
        ret = (realized / start_eq) if abs(start_eq) > 1e-12 else 0.0
        returns.append(ret)
        cum += realized
        end_eq = float(initial_capital) + cum

        raw_ds = sim.get("day_stats") or []
        if isinstance(raw_ds, dict):
            ds = raw_ds.get(d) or {}
        else:
            ds = next((x for x in raw_ds if x.get("date") == d), {}) or {}
        daily_rows.append({
            "date": d,
            "start_equity": start_eq,
            "realized_pnl": realized,
            "daily_return": ret,
            "gross_profit": gp,
            "gross_loss": gl,
            "daily_PF": dpf,
            "fills": int(ds.get("fills") or len(pnls)),
            "admitted": int(ds.get("admitted") or 0),
            "capital_blocked": int(ds.get("capital_blocked") or 0),
            "capacity_blocked": int(ds.get("capacity_blocked") or 0),
            "ending_equity": end_eq,
        })

    wins = sum(1 for d in days if day_pnl[d] > 0)
    losses = sum(1 for d in days if day_pnl[d] < 0)
    flats = sum(1 for d in days if day_pnl[d] == 0)
    win_rate_decided = (wins / (wins + losses)) if (wins + losses) else None
    win_rate_14 = wins / 14.0

    mean_pf = float(np.mean(finite_pfs)) if finite_pfs else None
    std_pf = float(np.std(finite_pfs, ddof=1)) if len(finite_pfs) >= 2 else (0.0 if len(finite_pfs) == 1 else None)

    # worst finite PF
    finite_day_pfs = [
        (r["date"], r["daily_PF"], r["realized_pnl"])
        for r in daily_rows
        if isinstance(r["daily_PF"], (int, float))
    ]
    if finite_day_pfs:
        worst = min(finite_day_pfs, key=lambda x: float(x[1]))
        worst_pf, worst_date, worst_pnl = float(worst[1]), worst[0], float(worst[2])
    else:
        worst_pf = worst_date = worst_pnl = None

    dd = realized_equity_drawdown(events, initial_capital=initial_capital)
    streak = max_losing_day_streak(day_pnl)
    tstats = t_stat_daily_returns(returns)

    accepted = [e for e in events if e.get("accepted")]
    total_pnl = float(sum(float(e.get("realized_pnl_yen") or 0.0) for e in accepted))
    gp_all = float(sum(max(float(e.get("realized_pnl_yen") or 0.0), 0.0) for e in accepted))
    gl_all = float(abs(sum(min(float(e.get("realized_pnl_yen") or 0.0), 0.0) for e in accepted)))
    overall_pf = (gp_all / gl_all) if gl_all > 1e-12 else ("INF" if gp_all > 0 else None)

    a285 = [e for e in accepted if e.get("symbol") == SYMBOL_285A]
    pnl_285 = float(sum(float(e.get("realized_pnl_yen") or 0.0) for e in a285))
    share_285 = (pnl_285 / total_pnl) if abs(total_pnl) > 1e-12 else None

    ending = float(initial_capital) + total_pnl
    return {
        "initial_capital": float(initial_capital),
        "ending_equity": ending,
        "total_pnl": total_pnl,
        "total_return_pct": (total_pnl / initial_capital * 100.0) if initial_capital else None,
        "capital_efficiency": (total_pnl / initial_capital) if initial_capital else None,
        "wins": wins,
        "losses": losses,
        "flat_days": flats,
        "win_rate_decided": win_rate_decided,
        "win_rate_14": win_rate_14,
        "daily_pf_mean_finite": mean_pf,
        "daily_pf_inf_days": inf_days,
        "daily_pf_na_days": na_days,
        "daily_pf_std_finite": std_pf,
        "daily_pf_finite_n": len(finite_pfs),
        "worst_daily_pf": worst_pf,
        "worst_pf_date": worst_date,
        "worst_daily_pnl_yen": worst_pnl,
        "max_dd_yen": dd["max_dd_yen"],
        "max_dd_pct": dd["max_dd_pct"],
        "dd_peak_time": dd["dd_peak_time"],
        "dd_trough_time": dd["dd_trough_time"],
        "max_losing_day_streak": streak["max_losing_day_streak"],
        "streak_start": streak["streak_start"],
        "streak_end": streak["streak_end"],
        "max_losing_trade_streak": max_losing_trade_streak(events),
        **tstats,
        "overall_pf": overall_pf,
        "fills": int(sim.get("accepted_fills") or len(accepted)),
        "admitted": int(sim.get("orders_admitted") or 0),
        "capital_blocked": int(sim.get("capital_blocked") or 0),
        "capacity_blocked": int(
            sim.get("capacity_blocked")
            or sim.get("admission_blocked_capacity")
            or 0
        ),
        "max_open_plus_pending": int(sim.get("max_open_plus_pending") or 0),
        "hard_cap_violations": int(sim.get("hard_cap_violations") or 0),
        "cash_never_negative": bool(sim.get("cash_never_negative", True)),
        "max_required_notional": float(
            sim.get("max_invested_capital")
            or sim.get("max_invested_yen")
            or sim.get("max_pending_reserve")
            or 0.0
        ),
        "fills_285a": len(a285),
        "pnl_285a": pnl_285,
        "share_285a": share_285,
        "daily": daily_rows,
        "equity_dd": dd,
    }


def pareto_comparison(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Display-only comparison — do NOT auto-select optimal capital."""
    def _best(key, *, maximize=True, pred=None):
        cand = [r for r in rows if r.get(key) is not None and (pred is None or pred(r))]
        if not cand:
            return None
        return (max if maximize else min)(cand, key=lambda r: float(r[key]))

    t_max = _best("t_value", maximize=True)
    dd_min = _best("max_dd_pct", maximize=False)
    wr_max = _best("win_rate_decided", maximize=True)
    pf_max = _best("overall_pf", maximize=True, pred=lambda r: isinstance(r.get("overall_pf"), (int, float)))

    # capital block nearly gone: blocked <= 1% of admitted or blocked==0
    cleared = [
        r for r in rows
        if int(r.get("capital_blocked") or 0) == 0
        or (
            int(r.get("admitted") or 0) > 0
            and int(r.get("capital_blocked") or 0) / max(int(r["admitted"]), 1) < 0.01
        )
    ]
    block_clear = min(cleared, key=lambda r: float(r["initial_capital"])) if cleared else None

    # diminishing incremental pnl/capital
    incr = []
    for a, b in zip(rows, rows[1:]):
        d_cap = float(b["initial_capital"]) - float(a["initial_capital"])
        d_pnl = float(b["total_pnl"]) - float(a["total_pnl"])
        incr.append({
            "from": a["initial_capital"],
            "to": b["initial_capital"],
            "incremental_pnl": d_pnl,
            "incremental_capital": d_cap,
            "incremental_pnl_per_capital": (d_pnl / d_cap) if d_cap else None,
            "capital_efficiency_to": b.get("capital_efficiency"),
        })
    # plateau: first point where incremental_pnl_per_capital drops below 50% of first step
    plateau = None
    if incr and incr[0].get("incremental_pnl_per_capital"):
        base = float(incr[0]["incremental_pnl_per_capital"])
        for row in incr:
            v = row.get("incremental_pnl_per_capital")
            if v is not None and base > 0 and float(v) < 0.5 * base:
                plateau = row
                break

    def _cap(r):
        return None if r is None else r["initial_capital"]

    return {
        "t_value_max_capital": _cap(t_max),
        "t_value_max": None if t_max is None else t_max.get("t_value"),
        "win_rate_max_capital": _cap(wr_max),
        "win_rate_max": None if wr_max is None else wr_max.get("win_rate_decided"),
        "max_dd_pct_min_capital": _cap(dd_min),
        "max_dd_pct_min": None if dd_min is None else dd_min.get("max_dd_pct"),
        "overall_pf_max_capital": _cap(pf_max),
        "overall_pf_max": None if pf_max is None else pf_max.get("overall_pf"),
        "capital_block_cleared_min_capital": _cap(block_clear),
        "capital_block_at_clear": None if block_clear is None else block_clear.get("capital_blocked"),
        "pnl_increment_plateau": plateau,
        "incremental": incr,
        "note": "Pareto comparison only — no automatic optimal capital selection",
    }
