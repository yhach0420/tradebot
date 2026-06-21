"""
Phase488 — Current Runtime Full Replay & Equity Simulation (research only).

Replays production PBv2 runtime (Phase472 adoption; no Phase473–487 changes) with
Hard Stop → No Progress → Board Dynamic Trailing exit stack, CAP=5.
"""

from __future__ import annotations

import heapq
import json
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from research.equity_curve_shadow import build_daily_equity_rows, compute_scenario_metrics
from research.market_sector_heat import _pf, _write_csv
from research.phase365_production_stack_validation import phase364_blocked_only
from research.phase382_capital_constrained_backtest import _parse_ts, _position_key
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase436_pullback_guard_redesign_shadow import guard_high_drift
from research.phase440_boundary_capacity_audit import ShadowExitInfo
from research.phase443_full_runtime_combined_capital_sim import (
    CAP,
    LEVERAGE,
    STOP_POLICY,
    CapacityReplayState,
    _chronological_pnls_from_log,
    _day_from_ts,
)
from research.phase451_entry_shape_tournament import (
    JST,
    _build_price_index_to,
    _now_iso,
    _symbol_pnl_from_log,
)
from research.phase463_trend_pullback_population_tournament import (
    _fill_close_proxy_shadows,
    _filter_replay_pool,
    _valid_replay_trade,
    _weak_shape_block,
)
from research.phase464_pre_gate_archetype_audit import _passes_board_gate
from research.phase465b_trend_gate_redesign import _concentration
from research.phase470_momentum_necessity_tournament import late_chase_block
from research.phase473_trend_entry_architecture import _entry_block, pass_pbv2
from research.phase476_pre_breakout_gate_replay import _ensure_enriched, _load_replay_pool
from research.phase271_leverage_attribution_and_robustness import build_spec
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.entry_expectancy_score_shadow import (
    MOMENTUM_SCORE_CUTOFF_P33,
    momentum_score_cutoff_pass,
)

PERIOD_START = "20250529"
PERIOD_END = "20260619"
REPLAY_MODE = "phase488_current_runtime"
FOCUS_SYMBOLS = ("6976", "4062", "6920", "3441", "6492", "7256", "7600")
EQUITY_LEVELS = (1_000_000, 1_500_000, 3_000_000, 5_000_000)
MAX_WORKERS_CAP = 4

REPLAY_FIELDS = [
    "position_key",
    "symbol",
    "day",
    "entry_time",
    "exit_time",
    "hold_sec",
    "pnl_yen",
    "exit_reason",
    "cumulative_pnl",
]

DAY_FIELDS = ["day", "trade_count", "total_pnl_yen", "profit_factor", "win_rate"]
SYMBOL_FIELDS = ["symbol", "trade_count", "total_pnl_yen", "profit_factor", "win_rate"]
EQUITY_FIELDS = [
    "initial_equity_yen",
    "final_equity_yen",
    "total_pnl_yen",
    "return_pct",
    "max_drawdown_yen",
    "max_drawdown_pct",
    "cagr_pct",
    "accepted_count",
    "profit_factor",
]
CONCENTRATION_FIELDS = [
    "test",
    "total_pnl_yen",
    "profit_factor",
    "max_drawdown_yen",
    "accepted_count",
    "delta_pnl_vs_full",
    "top_day_share",
    "top_symbol_share",
]


def pass_pre472(trade: Mapping[str, Any]) -> bool:
    if not momentum_score_cutoff_pass(trade, cutoff=MOMENTUM_SCORE_CUTOFF_P33):
        return False
    if not _passes_board_gate(trade):
        return False
    if guard_high_drift(trade):
        return False
    if _weak_shape_block(trade):
        return False
    if phase364_blocked_only(trade):
        return False
    return True


def _safe_day_from_ts(val: Any) -> bool:
    dt = _parse_ts(val)
    if dt is None:
        return False
    try:
        dt.astimezone(JST).strftime("%Y%m%d")
    except (OverflowError, OSError, ValueError):
        return False
    return True


def _filter_replay_pool_safe(
    replay_pool: Sequence[Mapping[str, Any]],
    np_shadows: Mapping[str, Any],
) -> list[dict[str, Any]]:
    out = _filter_replay_pool(replay_pool, np_shadows)
    safe: list[dict[str, Any]] = []
    dropped = 0
    for trade in out:
        key = _position_key(trade)
        if not _valid_replay_trade(trade, np_shadows.get(key)):
            dropped += 1
            continue
        if not _safe_day_from_ts(trade.get("exit_time")):
            dropped += 1
            continue
        safe.append(trade)
    if dropped:
        print(f"phase488 dropped invalid exit timestamps: {dropped}", flush=True)
    return safe


def _filter_period(pool: Sequence[Mapping[str, Any]], *, start: str, end: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for trade in pool:
        day = str(trade.get("day") or "")[:8]
        if day < start or day > end:
            continue
        out.append(dict(trade))
    return out


def _simulate_runtime_replay(
    candidates: Sequence[Mapping[str, Any]],
    shadow_by_key: Mapping[str, ShadowExitInfo],
    *,
    mode: str,
    entry_block_fn: Optional[Callable[[Mapping[str, Any]], bool]] = None,
    initial_equity: float = 1_500_000.0,
) -> CapacityReplayState:
    spec = build_spec(leverage=LEVERAGE, cap=CAP, stop_policy=STOP_POLICY)
    state = CapacityReplayState(
        scenario_id=mode,
        max_concurrent_positions=CAP,
        spec=spec,
        initial_equity=float(initial_equity),
        equity_floor=float(initial_equity) * 0.5,
        pnl_resolver=lambda *a, **k: 0.0,
        exit_mode=mode,
        shadow_by_key=dict(shadow_by_key),
        entry_block_fn=entry_block_fn,
        baseline_accepted_keys=set(),
    )

    entry_heap: list[tuple[datetime, int, str, dict[str, Any]]] = []
    for i, trade in enumerate(candidates):
        ent = _parse_ts(str(trade.get("entry_time") or ""))
        if ent is None:
            continue
        heapq.heappush(entry_heap, (ent, 0, f"e{i:05d}", dict(trade)))

    exit_heap: list[tuple[datetime, int, str, dict[str, Any]]] = []
    open_trade: dict[str, dict[str, Any]] = {}

    if entry_heap:
        first_day = _day_from_ts(entry_heap[0][0].isoformat())
        state._record_equity(ts="", day=first_day, event_type="start")

    while entry_heap or exit_heap:
        next_entry = entry_heap[0] if entry_heap else None
        next_exit = exit_heap[0] if exit_heap else None

        if next_exit is not None and (next_entry is None or next_exit[0] <= next_entry[0]):
            ex_dt, _, key, trade = heapq.heappop(exit_heap)
            ts = ex_dt.isoformat()
            day = _day_from_ts(ts)
            si = shadow_by_key.get(key) or ShadowExitInfo(0, "", 0, 0, 0, False, False)
            pnl, reason = state._close_pnl(trade, si)
            state.close_position_at(trade, ts=ts, day=day, exit_reason=reason, pnl_yen=pnl)
            open_trade.pop(key, None)
            continue

        ent_dt, _, _, trade = heapq.heappop(entry_heap)
        ts = ent_dt.isoformat()
        day = _day_from_ts(ts)
        if state.try_entry(trade, ts, day):
            key = _position_key(trade)
            si = shadow_by_key.get(key) or ShadowExitInfo(0, "", 0, 0, 0, False, False)
            ex_dt = state._exit_dt(trade, si)
            open_trade[key] = trade
            heapq.heappush(exit_heap, (ex_dt, 1, key, trade))
            state._record_equity(ts=ts, day=day, event_type="entry")

    if state.open_positions:
        last_ts = max(
            (_parse_ts(str(t.get("exit_time") or "")) or datetime.min.replace(tzinfo=JST) for t in open_trade.values()),
            default=datetime.now(JST),
        ).isoformat()
        state._force_close_all(last_ts, _day_from_ts(last_ts), reason="end_of_period")

    return state


def _trade_summary_rows(state: CapacityReplayState) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cum = 0.0
    for log in sorted(
        state.trade_log,
        key=lambda r: (
            _parse_ts(str(r.get("exit_time") or "")) or datetime.min.replace(tzinfo=JST),
            str(r.get("symbol") or ""),
        ),
    ):
        pnl = float(log.get("pnl_yen") or 0)
        cum += pnl
        tr = log.get("trade") or log
        rows.append(
            {
                "position_key": _position_key(tr),
                "symbol": str(tr.get("symbol") or "").replace(".T", ""),
                "day": str(log.get("day") or tr.get("day") or "")[:8],
                "entry_time": tr.get("entry_time"),
                "exit_time": log.get("exit_time"),
                "hold_sec": log.get("hold_sec"),
                "pnl_yen": round(pnl, 2),
                "exit_reason": log.get("exit_reason"),
                "cumulative_pnl": round(cum, 2),
            }
        )
    return rows


def _summary_metrics(state: CapacityReplayState, *, initial_equity: float) -> dict[str, Any]:
    chron = _chronological_pnls_from_log(state.trade_log)
    winners = [p for p in chron if p > 0]
    losers = [p for p in chron if p < 0]
    daily_rows = build_daily_equity_rows(state) if state.equity_curve else []
    metrics = compute_scenario_metrics(state, daily_rows=daily_rows) if state.equity_curve else {}
    total_pnl = round(sum(chron), 2)
    max_dd = float(metrics.get("max_drawdown_yen") or _max_drawdown_yen(chron, starting=initial_equity))
    final_eq = float(metrics.get("final_equity") or (initial_equity + total_pnl))
    top_day, top_sym = _concentration(state.trade_log)
    return {
        "total_pnl_yen": total_pnl,
        "profit_factor": _pf(chron),
        "win_rate": round(len(winners) / len(chron), 4) if chron else 0.0,
        "max_drawdown_yen": round(max_dd, 2),
        "trade_count": len(chron),
        "accepted_count": state.accepted_trade_count,
        "avg_winner": round(statistics.mean(winners), 2) if winners else 0.0,
        "avg_loser": round(statistics.mean(losers), 2) if losers else 0.0,
        "best_trade": round(max(chron), 2) if chron else 0.0,
        "worst_trade": round(min(chron), 2) if chron else 0.0,
        "expectancy": round(statistics.mean(chron), 2) if chron else 0.0,
        "final_equity_yen": round(final_eq, 2),
        "return_pct": round((final_eq - initial_equity) / initial_equity * 100.0, 4) if initial_equity else 0.0,
        "top_day_share": top_day,
        "top_symbol_share": top_sym,
        "_chron": chron,
        "_daily_rows": daily_rows,
    }


def _day_attribution(trade_log: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_day: dict[str, list[float]] = {}
    for log in trade_log:
        day = str(log.get("day") or "")[:8]
        by_day.setdefault(day, []).append(float(log.get("pnl_yen") or 0))
    rows: list[dict[str, Any]] = []
    for day in sorted(by_day):
        pnls = by_day[day]
        wins = sum(1 for p in pnls if p > 0)
        rows.append(
            {
                "day": day,
                "trade_count": len(pnls),
                "total_pnl_yen": round(sum(pnls), 2),
                "profit_factor": _pf(pnls),
                "win_rate": round(wins / len(pnls), 4) if pnls else 0.0,
            }
        )
    return rows


def _symbol_attribution(trade_log: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_sym: dict[str, list[float]] = {}
    for log in trade_log:
        sym = str(log.get("symbol") or "").replace(".T", "")
        by_sym.setdefault(sym, []).append(float(log.get("pnl_yen") or 0))
    rows: list[dict[str, Any]] = []
    for sym in sorted(by_sym, key=lambda s: sum(by_sym[s]), reverse=True):
        pnls = by_sym[sym]
        wins = sum(1 for p in pnls if p > 0)
        rows.append(
            {
                "symbol": sym,
                "trade_count": len(pnls),
                "total_pnl_yen": round(sum(pnls), 2),
                "profit_factor": _pf(pnls),
                "win_rate": round(wins / len(pnls), 4) if pnls else 0.0,
            }
        )
    return rows


def _cagr_pct(*, initial: float, final: float, start_day: str, end_day: str) -> Optional[float]:
    if initial <= 0 or final <= 0 or not start_day or not end_day:
        return None
    try:
        d0 = datetime.strptime(start_day, "%Y%m%d")
        d1 = datetime.strptime(end_day, "%Y%m%d")
        days = max((d1 - d0).days, 1)
        if days < 60:
            return None
        years = days / 365.25
        return round(((final / initial) ** (1.0 / years) - 1.0) * 100.0, 4)
    except ValueError:
        return None


def _equity_row(state: CapacityReplayState, *, initial_equity: float, start_day: str, end_day: str) -> dict[str, Any]:
    met = _summary_metrics(state, initial_equity=initial_equity)
    max_dd_pct = round(met["max_drawdown_yen"] / initial_equity * 100.0, 4) if initial_equity else 0.0
    return {
        "initial_equity_yen": initial_equity,
        "final_equity_yen": met["final_equity_yen"],
        "total_pnl_yen": met["total_pnl_yen"],
        "return_pct": met["return_pct"],
        "max_drawdown_yen": met["max_drawdown_yen"],
        "max_drawdown_pct": max_dd_pct,
        "cagr_pct": _cagr_pct(
            initial=initial_equity,
            final=float(met["final_equity_yen"]),
            start_day=start_day,
            end_day=end_day,
        ),
        "accepted_count": met["accepted_count"],
        "profit_factor": met["profit_factor"],
    }


def _symbol_contribution(trade_log: Sequence[Mapping[str, Any]], symbol: str) -> dict[str, Any]:
    sym_pnl = _symbol_pnl_from_log(trade_log)
    code = symbol.replace(".T", "")
    pnl = float(sym_pnl.get(code, sym_pnl.get(f"{code}.T", 0.0)))
    total = sum(float(r.get("pnl_yen") or 0) for r in trade_log)
    count = sum(1 for r in trade_log if str(r.get("symbol") or "").replace(".T", "") == code)
    return {
        "symbol": code,
        "trade_count": count,
        "total_pnl_yen": round(pnl, 2),
        "share_of_total_pnl": round(pnl / total, 4) if total else 0.0,
    }


def _verdict(
    *,
    summary: Mapping[str, Any],
    sym6976: Mapping[str, Any],
    sym4062: Mapping[str, Any],
) -> str:
    pnl = float(summary.get("total_pnl_yen") or 0)
    pf = float(summary.get("profit_factor") or 0)
    top_sym = float(summary.get("top_symbol_share") or 0)
    top_day = float(summary.get("top_day_share") or 0)
    share6976 = abs(float(sym6976.get("share_of_total_pnl") or 0))
    if pnl <= 0 or pf < 1.0:
        return "runtime_needs_rework"
    if top_sym > 0.35 or top_day > 0.40 or share6976 > 0.45:
        return "runtime_concentration_risk"
    if pnl > 0 and pf >= 1.15:
        return "runtime_validated"
    return "runtime_concentration_risk"


def _next_actions(verdict: str, summary: Mapping[str, Any], phase472: Mapping[str, Any]) -> list[str]:
    actions = [f"Verdict: {verdict}"]
    if verdict == "runtime_validated":
        actions.append("Current PBv2 runtime validated on replay window — continue paper monitoring")
    elif verdict == "runtime_concentration_risk":
        actions.append("Monitor symbol/day concentration; consider shadow guards from Phase483–487 only")
    else:
        actions.append("Runtime underperforms on replay — revisit entry/exit stack before live enable")
    actions.append(f"Phase472 delta PnL: {phase472.get('delta_pnl_yen')}")
    actions.append(f"Total replay PnL: {summary.get('total_pnl_yen')}")
    return actions


def run_phase488(*, repo_root: Path, parallel: bool = False, max_workers: int = 4) -> dict[str, Any]:
    max_workers = min(max(1, max_workers), MAX_WORKERS_CAP)
    kabu = resolve_kabu_root(repo_root)
    reports = resolve_reports_dir(repo_root)
    price_idx = _build_price_index_to(kabu, period_end=PERIOD_END)

    replay_pool, runtime_shadows = _load_replay_pool(reports)
    replay_pool = _filter_period(replay_pool, start=PERIOD_START, end=PERIOD_END)
    runtime_shadows = _fill_close_proxy_shadows(replay_pool, runtime_shadows, price_idx=price_idx)
    replay_pool = _filter_replay_pool_safe(replay_pool, runtime_shadows)
    _ensure_enriched(replay_pool, price_idx=price_idx)

    actual_days = sorted({str(t.get("day") or "")[:8] for t in replay_pool if t.get("day")})
    start_day = actual_days[0] if actual_days else PERIOD_START
    end_day = actual_days[-1] if actual_days else PERIOD_END
    print(
        f"phase488 pool {len(replay_pool)} days {start_day}..{end_day} shadows {len(runtime_shadows)}",
        flush=True,
    )

    def _run(entry_fn: Callable[[Mapping[str, Any]], bool], mode: str, initial: float = 1_500_000.0) -> CapacityReplayState:
        return _simulate_runtime_replay(
            replay_pool,
            runtime_shadows,
            mode=mode,
            entry_block_fn=_entry_block(entry_fn),
            initial_equity=initial,
        )

    current_state = _run(pass_pbv2, REPLAY_MODE, initial=1_500_000.0)
    summary = _summary_metrics(current_state, initial_equity=1_500_000.0)
    trade_rows = _trade_summary_rows(current_state)
    day_rows = _day_attribution(current_state.trade_log)
    symbol_rows = _symbol_attribution(current_state.trade_log)

    best_day = max(day_rows, key=lambda r: float(r["total_pnl_yen"])) if day_rows else {}
    worst_day = min(day_rows, key=lambda r: float(r["total_pnl_yen"])) if day_rows else {}

    sym6976 = _symbol_contribution(current_state.trade_log, "6976")
    sym4062 = _symbol_contribution(current_state.trade_log, "4062")

    equity_rows: list[dict[str, Any]] = []
    concentration_rows: list[dict[str, Any]] = []
    full_pnl = float(summary["total_pnl_yen"])

    def _conc_row(test: str, state: CapacityReplayState) -> dict[str, Any]:
        met = _summary_metrics(state, initial_equity=1_500_000.0)
        td, ts = _concentration(state.trade_log)
        return {
            "test": test,
            "total_pnl_yen": met["total_pnl_yen"],
            "profit_factor": met["profit_factor"],
            "max_drawdown_yen": met["max_drawdown_yen"],
            "accepted_count": met["accepted_count"],
            "delta_pnl_vs_full": round(float(met["total_pnl_yen"]) - full_pnl, 2),
            "top_day_share": td,
            "top_symbol_share": ts,
        }

    sym_counts: dict[str, int] = {}
    for log in current_state.trade_log:
        sym = str(log.get("symbol") or "")
        sym_counts[sym] = sym_counts.get(sym, 0) + 1
    top_sym = max(sym_counts, key=sym_counts.get) if sym_counts else "6976.T"
    top_day = best_day.get("day", "")

    def _pool_excluding_symbol(sym: str) -> list[dict[str, Any]]:
        return [t for t in replay_pool if str(t.get("symbol") or "") != sym]

    jobs = [
        (f"equity_{eq}", lambda e=eq: _equity_row(_run(pass_pbv2, f"{REPLAY_MODE}_eq{e}", initial=float(e)), initial_equity=e, start_day=start_day, end_day=end_day))
        for eq in EQUITY_LEVELS
    ]
    jobs.extend(
        [
            ("exclude_6976", lambda: _conc_row("exclude_6976", _simulate_runtime_replay(_pool_excluding_symbol("6976.T"), runtime_shadows, mode=f"{REPLAY_MODE}_ex6976", entry_block_fn=_entry_block(pass_pbv2), initial_equity=1_500_000.0))),
            ("exclude_4062", lambda: _conc_row("exclude_4062", _simulate_runtime_replay(_pool_excluding_symbol("4062.T"), runtime_shadows, mode=f"{REPLAY_MODE}_ex4062", entry_block_fn=_entry_block(pass_pbv2), initial_equity=1_500_000.0))),
            ("exclude_top_symbol", lambda: _conc_row("exclude_top_symbol", _simulate_runtime_replay(_pool_excluding_symbol(top_sym), runtime_shadows, mode=f"{REPLAY_MODE}_extop", entry_block_fn=_entry_block(pass_pbv2), initial_equity=1_500_000.0))),
            ("exclude_top_day", lambda: _conc_row("exclude_top_day", _simulate_runtime_replay([t for t in replay_pool if str(t.get("day") or "")[:8] != str(top_day)], runtime_shadows, mode=f"{REPLAY_MODE}_extday", entry_block_fn=_entry_block(pass_pbv2), initial_equity=1_500_000.0))),
            ("pre472", lambda: _summary_metrics(_run(pass_pre472, f"{REPLAY_MODE}_pre472"), initial_equity=1_500_000.0)),
        ]
    )

    results: dict[str, Any] = {}
    if parallel and len(jobs) > 1:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = {ex.submit(fn): name for name, fn in jobs}
            for fut in as_completed(futs):
                results[futs[fut]] = fut.result()
    else:
        for name, fn in jobs:
            results[name] = fn()

    for eq in EQUITY_LEVELS:
        equity_rows.append(results[f"equity_{eq}"])
    for key in ("exclude_6976", "exclude_4062", "exclude_top_symbol", "exclude_top_day"):
        concentration_rows.append(results[key])
    concentration_rows.append(
        {
            "test": "full",
            "total_pnl_yen": summary["total_pnl_yen"],
            "profit_factor": summary["profit_factor"],
            "max_drawdown_yen": summary["max_drawdown_yen"],
            "accepted_count": summary["accepted_count"],
            "delta_pnl_vs_full": 0.0,
            "top_day_share": summary["top_day_share"],
            "top_symbol_share": summary["top_symbol_share"],
        }
    )

    pre472 = results["pre472"]
    phase472_impact = {
        "pre472_pnl": pre472["total_pnl_yen"],
        "pre472_pf": pre472["profit_factor"],
        "pre472_maxdd": pre472["max_drawdown_yen"],
        "pre472_trade_count": pre472["trade_count"],
        "current_pnl": summary["total_pnl_yen"],
        "current_pf": summary["profit_factor"],
        "current_maxdd": summary["max_drawdown_yen"],
        "current_trade_count": summary["trade_count"],
        "delta_pnl_yen": round(float(summary["total_pnl_yen"]) - float(pre472["total_pnl_yen"]), 2),
        "delta_pf": round(float(summary["profit_factor"] or 0) - float(pre472["profit_factor"] or 0), 4),
        "delta_maxdd_yen": round(float(summary["max_drawdown_yen"]) - float(pre472["max_drawdown_yen"]), 2),
        "delta_trade_count": int(summary["trade_count"]) - int(pre472["trade_count"]),
    }

    verdict = _verdict(summary=summary, sym6976=sym6976, sym4062=sym4062)
    eq_map = {r["initial_equity_yen"]: r for r in equity_rows}

    mandatory = {
        "1_total_pnl": summary["total_pnl_yen"],
        "2_profit_factor": summary["profit_factor"],
        "3_max_drawdown_yen": summary["max_drawdown_yen"],
        "4_win_rate": summary["win_rate"],
        "5_trade_count": summary["trade_count"],
        "6_equity_1m": eq_map.get(1_000_000),
        "7_equity_1p5m": eq_map.get(1_500_000),
        "8_equity_3m": eq_map.get(3_000_000),
        "9_equity_5m": eq_map.get(5_000_000),
        "10_6976_contribution_share": sym6976.get("share_of_total_pnl"),
        "11_4062_contribution_share": sym4062.get("share_of_total_pnl"),
        "12_top_symbol_share": summary["top_symbol_share"],
        "13_top_day_share": summary["top_day_share"],
        "14_worst_day": worst_day,
        "15_best_day": best_day,
        "16_phase472_improvement": phase472_impact,
        "17_runtime_adoption_valid": verdict == "runtime_validated",
        "18_current_biggest_weakness": _weakness(summary, sym6976, worst_day),
        "19_next_actions": _next_actions(verdict, summary, phase472_impact),
        "verdict": verdict,
        "period_requested": f"{PERIOD_START}-{PERIOD_END}",
        "period_actual": f"{start_day}-{end_day}",
        "runtime_spec": {
            "entry": "PBv2 (momentum<=0.2546, board mid/high, HD, WS, late chase)",
            "exit": "Hard Stop -1.2% → No Progress → Board Dynamic Trailing (high 1.0%/60%, low 0.6%/40%)",
            "cap": CAP,
            "leverage": LEVERAGE,
        },
    }

    return {
        "generated_at": _now_iso(),
        "period_start": start_day,
        "period_end": end_day,
        "verdict": verdict,
        "mandatory_answers": mandatory,
        "_trade_rows": trade_rows,
        "_day_rows": day_rows,
        "_symbol_rows": symbol_rows,
        "_equity_rows": equity_rows,
        "_concentration_rows": concentration_rows,
        "_phase472": phase472_impact,
        "_summary": summary,
    }


def _weakness(summary: Mapping[str, Any], sym6976: Mapping[str, Any], worst_day: Mapping[str, Any]) -> str:
    parts: list[str] = []
    if float(sym6976.get("share_of_total_pnl") or 0) > 0.3:
        parts.append("6976 concentration")
    if float(summary.get("top_day_share") or 0) > 0.25:
        parts.append("day concentration")
    if worst_day and float(worst_day.get("total_pnl_yen") or 0) < -50000:
        parts.append(f"worst_day {worst_day.get('day')}")
    if float(summary.get("max_drawdown_yen") or 0) > abs(float(summary.get("total_pnl_yen") or 1)) * 0.5:
        parts.append("drawdown vs pnl ratio")
    return "; ".join(parts) if parts else "moderate tail risk on stop_low_mfe cohort"


@dataclass
class Phase488Job:
    repo_root: Path
    parallel: bool = False
    max_workers: int = 4

    def run(self) -> dict[str, Any]:
        return run_phase488(repo_root=self.repo_root, parallel=self.parallel, max_workers=self.max_workers)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "replay": reports / "phase488_current_runtime_replay.csv",
            "day": reports / "phase488_day_attribution.csv",
            "symbol": reports / "phase488_symbol_attribution.csv",
            "equity": reports / "phase488_equity_simulation.csv",
            "concentration": reports / "phase488_concentration_risk.csv",
            "summary": reports / "phase488_summary.json",
        }
        _write_csv(paths["replay"], REPLAY_FIELDS, list(result.get("_trade_rows") or []))
        _write_csv(paths["day"], DAY_FIELDS, list(result.get("_day_rows") or []))
        _write_csv(paths["symbol"], SYMBOL_FIELDS, list(result.get("_symbol_rows") or []))
        _write_csv(paths["equity"], EQUITY_FIELDS, list(result.get("_equity_rows") or []))
        _write_csv(paths["concentration"], CONCENTRATION_FIELDS, list(result.get("_concentration_rows") or []))
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

        doc_root = self.repo_root / "kabu_native"
        if not (doc_root / "docs").is_dir():
            doc_root = self.repo_root
        report = doc_root / "docs" / "operations" / "phase488_current_runtime_replay.md"
        self._write_report(report, result)
        paths["report"] = report
        return paths

    def _write_report(self, report: Path, result: Mapping[str, Any]) -> None:
        m = result.get("mandatory_answers") or {}
        lines = [
            "# Phase488 — Current Runtime Full Replay & Equity Simulation",
            "",
            f"**Verdict:** `{result.get('verdict')}`",
            f"**Period:** {m.get('period_actual')} (requested {m.get('period_requested')})",
            "",
            "## Mandatory answers",
            "",
            f"1. Total PnL: **{m.get('1_total_pnl')}**",
            f"2. PF: **{m.get('2_profit_factor')}**",
            f"3. maxDD: **{m.get('3_max_drawdown_yen')}**",
            f"4. Win rate: **{m.get('4_win_rate')}**",
            f"5. Trade count: **{m.get('5_trade_count')}**",
            f"6. Equity 1M: **{m.get('6_equity_1m')}**",
            f"7. Equity 1.5M: **{m.get('7_equity_1p5m')}**",
            f"8. Equity 3M: **{m.get('8_equity_3m')}**",
            f"9. Equity 5M: **{m.get('9_equity_5m')}**",
            f"10. 6976 share: **{m.get('10_6976_contribution_share')}**",
            f"11. 4062 share: **{m.get('11_4062_contribution_share')}**",
            f"12. top_symbol_share: **{m.get('12_top_symbol_share')}**",
            f"13. top_day_share: **{m.get('13_top_day_share')}**",
            f"14. Worst day: **{m.get('14_worst_day')}**",
            f"15. Best day: **{m.get('15_best_day')}**",
            f"16. Phase472 impact: **{m.get('16_phase472_improvement')}**",
            f"17. Runtime valid: **{m.get('17_runtime_adoption_valid')}**",
            f"18. Weakness: **{m.get('18_current_biggest_weakness')}**",
            f"19. Next actions: {m.get('19_next_actions')}",
            "",
            f"**Verdict:** `{result.get('verdict')}`",
            "",
        ]
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("\n".join(lines), encoding="utf-8")
