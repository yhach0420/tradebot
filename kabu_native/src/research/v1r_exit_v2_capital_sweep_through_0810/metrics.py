"""Metrics for EXIT V2 capital sweep — same definitions as prior V1R capital sweep."""
from __future__ import annotations

import math
from typing import Any

import numpy as np

from research.v1r_capital_sweep_0p5m_10m.metrics import (
    _daily_pf,
    max_losing_trade_streak,
    realized_equity_drawdown,
    t_stat_daily_returns,
)


def summarize_capital_run(
    sim: dict[str, Any],
    *,
    initial_capital: float,
    days: tuple[str, ...],
) -> dict[str, Any]:
    events = sim["events"]
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
            "classification": "RETROSPECTIVE_REFERENCE_ONLY" if d == "20260810" else "HISTORICAL",
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

    mean_pf = float(np.mean(finite_pfs)) if finite_pfs else None
    std_pf = float(np.std(finite_pfs, ddof=1)) if len(finite_pfs) >= 2 else (
        0.0 if len(finite_pfs) == 1 else None
    )

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

    # max losing streak — same flat semantics as prior sweep (flat breaks streak)
    best = 0
    best_start = best_end = None
    cur = 0
    cur_start = None
    for d in days:
        if day_pnl[d] < 0:
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

    tstats = t_stat_daily_returns(returns)

    accepted = [e for e in events if e.get("accepted")]
    total_pnl = float(sum(float(e.get("realized_pnl_yen") or 0.0) for e in accepted))
    gp_all = float(sum(max(float(e.get("realized_pnl_yen") or 0.0), 0.0) for e in accepted))
    gl_all = float(abs(sum(min(float(e.get("realized_pnl_yen") or 0.0), 0.0) for e in accepted)))
    overall_pf = (gp_all / gl_all) if gl_all > 1e-12 else ("INF" if gp_all > 0 else None)

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
        "max_losing_day_streak": best,
        "streak_start": best_start,
        "streak_end": best_end,
        "max_losing_trade_streak": max_losing_trade_streak(events),
        **tstats,
        "overall_pf": overall_pf,
        "fills": int(sim.get("accepted_fills") or len(accepted)),
        "admitted": int(sim.get("orders_admitted") or 0),
        "capital_blocked": int(sim.get("capital_blocked") or 0),
        "capacity_blocked": int(
            sim.get("capacity_blocked") or sim.get("admission_blocked_capacity") or 0
        ),
        "max_open_plus_pending": int(sim.get("max_open_plus_pending") or 0),
        "hard_cap_violations": int(sim.get("hard_cap_violations") or 0),
        "cash_never_negative": bool(sim.get("cash_never_negative", True)),
        "daily": daily_rows,
        "equity_dd": dd,
        "n_days": len(days),
    }


def pick_candidates(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Display-only capital candidates — no auto-adopt."""
    def best(key, *, maximize=True):
        cand = [r for r in rows if r.get(key) is not None]
        if not cand:
            return None
        return (max if maximize else min)(cand, key=lambda r: float(r[key]))

    wr = best("win_rate_decided", maximize=True)
    tv = best("t_value", maximize=True)
    dd = best("max_dd_pct", maximize=False)
    # PF stability = lowest daily_pf_std among rows with enough finite PF days
    stab_cand = [r for r in rows if r.get("daily_pf_std_finite") is not None and (r.get("daily_pf_finite_n") or 0) >= 5]
    stab = min(stab_cand, key=lambda r: float(r["daily_pf_std_finite"])) if stab_cand else None

    # Balance: rank-sum of (higher win_rate, higher t, lower dd%, lower pf_std)
    scored = []
    for r in rows:
        if r.get("t_value") is None or r.get("win_rate_decided") is None:
            continue
        scored.append(r)

    def rank_score(r):
        # higher better for wr/t; lower better for dd/std
        return (
            -float(r["win_rate_decided"]),
            -float(r["t_value"]),
            float(r["max_dd_pct"]),
            float(r.get("daily_pf_std_finite") or 1e9),
        )

    balance = min(scored, key=rank_score) if scored else None

    # Lower-cap efficiency: max capital_efficiency among caps <= 4M with t>0
    low = [
        r for r in rows
        if float(r["initial_capital"]) <= 4_000_000 and (r.get("t_value") or -1) > 0
    ]
    lower = max(low, key=lambda r: float(r.get("capital_efficiency") or -1e99)) if low else None

    def _cap(r):
        return None if r is None else int(r["initial_capital"])

    return {
        "win_rate_max": {"capital": _cap(wr), "value": None if wr is None else wr["win_rate_decided"]},
        "t_value_max": {"capital": _cap(tv), "value": None if tv is None else tv["t_value"]},
        "dd_pct_min": {"capital": _cap(dd), "value": None if dd is None else dd["max_dd_pct"]},
        "pf_stability_best": {
            "capital": _cap(stab),
            "value": None if stab is None else stab["daily_pf_std_finite"],
        },
        "balance": {
            "capital": _cap(balance),
            "win_rate": None if balance is None else balance["win_rate_decided"],
            "t_value": None if balance is None else balance["t_value"],
            "max_dd_pct": None if balance is None else balance["max_dd_pct"],
            "daily_pf_std": None if balance is None else balance.get("daily_pf_std_finite"),
        },
        "lower_cap_efficiency": {
            "capital": _cap(lower),
            "capital_efficiency": None if lower is None else lower.get("capital_efficiency"),
            "total_pnl": None if lower is None else lower.get("total_pnl"),
        },
        "risk": {"capital": _cap(dd), "max_dd_pct": None if dd is None else dd["max_dd_pct"]},
        "note": "Comparison only — no Production capital selection",
    }
