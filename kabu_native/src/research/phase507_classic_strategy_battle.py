"""
Phase507 — Classical technical strategy battle vs BASELINE_RUNTIME (research only).

Full-period CAP=5 replay comparison on core10-dynamic40-price-risk-filter-shadow universe.
No Runtime / Entry / Exit / Order / Discord changes.
"""

from __future__ import annotations

import heapq
import json
import statistics
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _parse_ts, _position_key, _trade_pnl_yen
from research.phase443_full_runtime_combined_capital_sim import CAP, LEVERAGE, STOP_POLICY, CapacityReplayState
from research.phase451_entry_shape_tournament import JST, _build_price_index_to, _now_iso
from research.phase463_trend_pullback_population_tournament import _fill_close_proxy_shadows
from research.phase473_trend_entry_architecture import _entry_block, pass_pbv2
from research.phase476_pre_breakout_gate_replay import _ensure_enriched, _load_replay_pool
from research.phase488_current_runtime_replay import (
    REPLAY_MODE,
    _filter_period,
    _filter_replay_pool_safe,
    _simulate_runtime_replay,
    _summary_metrics,
    _trade_summary_rows,
)
from research.phase493_global_entry_failure_audit import (
    PERIOD_END,
    PERIOD_START,
    _enrich_trade_row,
    _is_loser,
    _medians_from_losers,
    _replay_with_extra_block,
)
from research.phase502_classic_indicator_guard_replay import _build_feature_environment
from research.phase504_runtime_validation_after_phase503 import _load_pilot_flags
from research.phase507_classic_indicators import (
    INDICATOR_LOG_FIELDS,
    Bar1m,
    BarIndicatorRow,
    _in_trading_window,
    compute_bar_indicators,
    indicator_dict_at_entry,
    ticks_to_1m_bars,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from research.phase271_leverage_attribution_and_robustness import build_spec

PHASE507_MODE = "phase507_classic_strategy_battle"
MAX_WORKERS_CAP = 4
INITIAL_EQUITY = 1_500_000.0
HARD_STOP_PCT = -1.2
MIN_BARS_WARMUP = 30
ENTRY_COOLDOWN_SEC = 300

BASELINE_STRATEGY_ID = "BASELINE_RUNTIME"

ENTRY_RULES: dict[str, tuple[str, Callable[[Mapping[str, Optional[float]], Bar1m], bool]]] = {}
EXIT_RULES: dict[str, tuple[str, Callable[[Mapping[str, Optional[float]], Bar1m, Bar1m], bool]]] = {}


def _f(v: Optional[float]) -> float:
    return float(v) if v is not None else float("nan")


def _reg_entry(rule_id: str, desc: str, fn: Callable) -> None:
    ENTRY_RULES[rule_id] = (desc, fn)


def _reg_exit(rule_id: str, desc: str, fn: Callable) -> None:
    EXIT_RULES[rule_id] = (desc, fn)


_reg_entry("T1", "RSI > 50", lambda ind, bar: _f(ind.get("RSI14")) > 50)
_reg_entry("T2", "RSI > 55", lambda ind, bar: _f(ind.get("RSI14")) > 55)
_reg_entry("T3", "RSI > 60", lambda ind, bar: _f(ind.get("RSI14")) > 60)
_reg_entry("T4", "Price > VWAP", lambda ind, bar: bar.close > _f(ind.get("VWAP")))
_reg_entry("T5", "EMA20上", lambda ind, bar: bar.close > _f(ind.get("EMA20")))
_reg_entry("T6", "ADX > 20", lambda ind, bar: _f(ind.get("ADX")) > 20)
_reg_entry(
    "T7",
    "RSI > 55 AND VWAP上",
    lambda ind, bar: _f(ind.get("RSI14")) > 55 and bar.close > _f(ind.get("VWAP")),
)
_reg_entry(
    "T8",
    "RSI > 55 AND ADX > 20",
    lambda ind, bar: _f(ind.get("RSI14")) > 55 and _f(ind.get("ADX")) > 20,
)
_reg_entry(
    "T9",
    "VWAP上 AND ADX > 20",
    lambda ind, bar: bar.close > _f(ind.get("VWAP")) and _f(ind.get("ADX")) > 20,
)
_reg_entry(
    "T10",
    "RSI > 55 AND VWAP上 AND ADX > 20",
    lambda ind, bar: _f(ind.get("RSI14")) > 55
    and bar.close > _f(ind.get("VWAP"))
    and _f(ind.get("ADX")) > 20,
)
_reg_entry(
    "T11",
    "EMA20上 AND RSI > 55",
    lambda ind, bar: bar.close > _f(ind.get("EMA20")) and _f(ind.get("RSI14")) > 55,
)
_reg_entry(
    "T12",
    "EMA20上 AND VWAP上",
    lambda ind, bar: bar.close > _f(ind.get("EMA20")) and bar.close > _f(ind.get("VWAP")),
)
_reg_entry(
    "T13",
    "EMA20上 AND VWAP上 AND ADX > 20",
    lambda ind, bar: bar.close > _f(ind.get("EMA20"))
    and bar.close > _f(ind.get("VWAP"))
    and _f(ind.get("ADX")) > 20,
)
_reg_entry(
    "T14",
    "MACD hist > 0 AND VWAP上",
    lambda ind, bar: _f(ind.get("MACD_histogram")) > 0 and bar.close > _f(ind.get("VWAP")),
)
_reg_entry(
    "T15",
    "Stoch %K > %D AND RSI > 50",
    lambda ind, bar: _f(ind.get("STOCH_K")) > _f(ind.get("STOCH_D")) and _f(ind.get("RSI14")) > 50,
)
_reg_entry(
    "T16",
    "+DI > -DI AND ADX > 20",
    lambda ind, bar: _f(ind.get("PLUS_DI")) > _f(ind.get("MINUS_DI")) and _f(ind.get("ADX")) > 20,
)
_reg_entry(
    "T17",
    "Price > Donchian mid",
    lambda ind, bar: bar.close > _f(ind.get("DONCHIAN_MID20")),
)
_reg_entry(
    "T18",
    "CCI > 0 AND VWAP上",
    lambda ind, bar: _f(ind.get("CCI20")) > 0 and bar.close > _f(ind.get("VWAP")),
)

_reg_exit("E1", "Hard Stopのみ", lambda ind, bar, entry: False)
_reg_exit("E2", "VWAP割れ", lambda ind, bar, entry: bar.close < _f(ind.get("VWAP")))
_reg_exit("E3", "EMA5割れ", lambda ind, bar, entry: bar.close < _f(ind.get("EMA5")))
_reg_exit("E4", "RSI < 50", lambda ind, bar, entry: _f(ind.get("RSI14")) < 50)
_reg_exit("E5", "MACD hist < 0", lambda ind, bar, entry: _f(ind.get("MACD_histogram")) < 0)
_reg_exit(
    "E6",
    "VWAP割れ OR EMA5割れ",
    lambda ind, bar, entry: bar.close < _f(ind.get("VWAP")) or bar.close < _f(ind.get("EMA5")),
)
_reg_exit(
    "E7",
    "VWAP割れ OR RSI < 50",
    lambda ind, bar, entry: bar.close < _f(ind.get("VWAP")) or _f(ind.get("RSI14")) < 50,
)
_reg_exit(
    "E8",
    "EMA5割れ OR RSI < 50",
    lambda ind, bar, entry: bar.close < _f(ind.get("EMA5")) or _f(ind.get("RSI14")) < 50,
)
_reg_exit(
    "E9",
    "MACD hist < 0 OR RSI < 50",
    lambda ind, bar, entry: _f(ind.get("MACD_histogram")) < 0 or _f(ind.get("RSI14")) < 50,
)
_reg_exit("E10", "-DI > +DI", lambda ind, bar, entry: _f(ind.get("MINUS_DI")) > _f(ind.get("PLUS_DI")))
_reg_exit("E11", "CCI < 0", lambda ind, bar, entry: _f(ind.get("CCI20")) < 0)
_reg_exit("E12", "ATR trailing", lambda ind, bar, entry: False)  # handled in scan


CLASSICAL_EXIT_IDS = ("E1", "E2", "E3", "E4", "E6", "E7", "E12")


def build_classical_strategies() -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for eid in ENTRY_RULES:
        for xid in CLASSICAL_EXIT_IDS:
            sid = f"C_{eid}_{xid}"
            out.append(
                {
                    "strategy_id": sid,
                    "entry_rule_id": eid,
                    "exit_rule_id": xid,
                    "entry_desc": ENTRY_RULES[eid][0],
                    "exit_desc": EXIT_RULES[xid][0],
                }
            )
    return out


SUMMARY_FIELDS = [
    "strategy_id",
    "entry_rule_id",
    "exit_rule_id",
    "total_pnl_yen_100",
    "profit_factor",
    "max_drawdown_yen_100",
    "trades",
    "win_rate",
    "avg_pnl_yen_100",
    "positive_day_count",
    "negative_day_count",
    "worst_day_pnl",
    "best_day_pnl",
    "daily_stability_score",
    "baseline_diff_pnl",
    "baseline_diff_pf",
    "baseline_diff_dd",
    "rank_pf",
    "rank_pnl",
    "rank_dd",
    "rank_stability",
    "rank_baseline_diff",
]

DAILY_FIELDS = [
    "strategy_id",
    "day",
    "trade_count",
    "total_pnl_yen_100",
    "profit_factor",
    "win_rate",
]

TRADE_FIELDS = [
    "strategy_id",
    "symbol",
    "day",
    "entry_time",
    "exit_time",
    "entry_price",
    "exit_price",
    "pnl_yen_100",
    "exit_reason",
    "entry_rule_id",
    "exit_rule_id",
    *INDICATOR_LOG_FIELDS,
]


def _hard_stop_hit(entry_px: float, px: float) -> bool:
    if entry_px <= 0:
        return False
    return (px - entry_px) / entry_px * 100.0 <= HARD_STOP_PCT


def _find_exit(
    bars: Sequence[Bar1m],
    ind_rows: Sequence[BarIndicatorRow],
    entry_i: int,
    *,
    exit_rule_id: str,
    entry_px: float,
) -> tuple[int, str]:
    _, exit_fn = EXIT_RULES[exit_rule_id]
    peak = entry_px
    atr_mult = 2.0
    for j in range(entry_i + 1, len(bars)):
        ind = ind_rows[j].values
        bar = bars[j]
        if _hard_stop_hit(entry_px, bar.close):
            return j, "hard_stop"
        if exit_rule_id == "E12":
            peak = max(peak, bar.high)
            atr = _f(ind.get("ATR14"))
            if atr == atr and bar.close < peak - atr_mult * atr:
                return j, "atr_trailing"
            continue
        if exit_fn(ind, bar, bars[entry_i]):
            return j, exit_rule_id.lower()
    return len(bars) - 1, "session_end"


def scan_symbol_day(
    *,
    symbol: str,
    day: str,
    bars: Sequence[Bar1m],
    ind_rows: Sequence[BarIndicatorRow],
    entry_rule_id: str,
    exit_rule_id: str,
) -> list[dict[str, Any]]:
    _, entry_fn = ENTRY_RULES[entry_rule_id]
    trades: list[dict[str, Any]] = []
    last_entry: Optional[datetime] = None
    for i in range(MIN_BARS_WARMUP, len(bars)):
        bar = bars[i]
        if not _in_trading_window(bar.ts):
            continue
        ind = ind_rows[i].values
        if ind.get("RSI14") is None:
            continue
        if last_entry and (bar.ts - last_entry).total_seconds() < ENTRY_COOLDOWN_SEC:
            continue
        if not entry_fn(ind, bar):
            continue
        exit_i, exit_reason = _find_exit(
            bars, ind_rows, i, exit_rule_id=exit_rule_id, entry_px=bar.close
        )
        exit_bar = bars[exit_i]
        pnl = round((exit_bar.close - bar.close) * 100.0, 2)
        sym = symbol if symbol.endswith(".T") else f"{symbol}.T"
        trades.append(
            {
                "symbol": sym,
                "day": day,
                "entry_time": bar.ts.isoformat(),
                "exit_time": exit_bar.ts.isoformat(),
                "entry_price": bar.close,
                "exit_price": exit_bar.close,
                "pnl_yen": pnl,
                "exit_reason": exit_reason,
                **{k: ind.get(k) for k in INDICATOR_LOG_FIELDS},
            }
        )
        last_entry = bar.ts
    return trades


def _simulate_precomputed_cap(
    candidates: Sequence[Mapping[str, Any]],
    *,
    mode: str,
) -> CapacityReplayState:
    spec = build_spec(leverage=LEVERAGE, cap=CAP, stop_policy=STOP_POLICY)
    state = CapacityReplayState(
        scenario_id=mode,
        max_concurrent_positions=CAP,
        spec=spec,
        initial_equity=INITIAL_EQUITY,
        equity_floor=INITIAL_EQUITY * 0.5,
        pnl_resolver=lambda *a, **k: 0.0,
        exit_mode=f"{mode}_baseline",
        shadow_by_key={},
        entry_block_fn=None,
        baseline_accepted_keys=set(),
    )
    entry_heap: list[tuple[datetime, int, str, dict[str, Any]]] = []
    for i, trade in enumerate(candidates):
        ent = _parse_ts(str(trade.get("entry_time") or ""))
        if ent is None:
            continue
        heapq.heappush(entry_heap, (ent, 0, f"e{i:05d}", dict(trade)))
    exit_heap: list[tuple[datetime, int, str, dict[str, Any]]] = []
    while entry_heap or exit_heap:
        next_entry = entry_heap[0] if entry_heap else None
        next_exit = exit_heap[0] if exit_heap else None
        if next_exit is not None and (next_entry is None or next_exit[0] <= next_entry[0]):
            ex_dt, _, key, trade = heapq.heappop(exit_heap)
            ts = ex_dt.isoformat()
            day = str(trade.get("day") or "")[:8]
            pnl = float(_trade_pnl_yen(trade, shares=100) or trade.get("pnl_yen") or 0)
            reason = str(trade.get("exit_reason") or "")
            state.close_position_at(trade, ts=ts, day=day, exit_reason=reason, pnl_yen=pnl)
            continue
        ent_dt, _, _, trade = heapq.heappop(entry_heap)
        ts = ent_dt.isoformat()
        day = str(trade.get("day") or "")[:8]
        if state.try_entry(trade, ts, day):
            ex_dt = _parse_ts(str(trade.get("exit_time") or "")) or ent_dt + timedelta(minutes=5)
            key = _position_key(trade)
            heapq.heappush(exit_heap, (ex_dt, 1, key, trade))
    if state.open_positions:
        last_ts = datetime.now(JST).isoformat()
        state._force_close_all(last_ts, str(trade.get("day") or "")[:8], reason="end_of_period")
    return state


def _strategy_metrics(
    state: CapacityReplayState,
    *,
    strategy_id: str,
    entry_rule_id: str,
    exit_rule_id: str,
    baseline: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    met = _summary_metrics(state, initial_equity=INITIAL_EQUITY)
    chron = met.get("_chron") or []
    daily: dict[str, float] = defaultdict(float)
    for log in state.trade_log:
        d = str(log.get("day") or "")[:8]
        daily[d] += float(log.get("pnl_yen") or 0)
    pos_days = sum(1 for v in daily.values() if v > 0)
    neg_days = sum(1 for v in daily.values() if v < 0)
    daily_vals = list(daily.values())
    stability = round(pos_days / max(1, pos_days + neg_days), 4)
    row = {
        "strategy_id": strategy_id,
        "entry_rule_id": entry_rule_id,
        "exit_rule_id": exit_rule_id,
        "total_pnl_yen_100": met["total_pnl_yen"],
        "profit_factor": met["profit_factor"],
        "max_drawdown_yen_100": met["max_drawdown_yen"],
        "trades": met["trade_count"],
        "win_rate": met["win_rate"],
        "avg_pnl_yen_100": met["expectancy"],
        "positive_day_count": pos_days,
        "negative_day_count": neg_days,
        "worst_day_pnl": round(min(daily_vals), 2) if daily_vals else 0.0,
        "best_day_pnl": round(max(daily_vals), 2) if daily_vals else 0.0,
        "daily_stability_score": stability,
        "baseline_diff_pnl": 0.0,
        "baseline_diff_pf": 0.0,
        "baseline_diff_dd": 0.0,
    }
    if baseline:
        row["baseline_diff_pnl"] = round(float(row["total_pnl_yen_100"]) - float(baseline["total_pnl_yen_100"]), 2)
        row["baseline_diff_pf"] = round(float(row["profit_factor"] or 0) - float(baseline["profit_factor"] or 0), 4)
        row["baseline_diff_dd"] = round(
            float(baseline["max_drawdown_yen_100"]) - float(row["max_drawdown_yen_100"]), 2
        )
    return row


def _day_rows(state: CapacityReplayState, strategy_id: str) -> list[dict[str, Any]]:
    by_day: dict[str, list[float]] = defaultdict(list)
    for log in state.trade_log:
        by_day[str(log.get("day") or "")[:8]].append(float(log.get("pnl_yen") or 0))
    rows: list[dict[str, Any]] = []
    for day in sorted(by_day):
        pnls = by_day[day]
        wins = sum(1 for p in pnls if p > 0)
        rows.append(
            {
                "strategy_id": strategy_id,
                "day": day,
                "trade_count": len(pnls),
                "total_pnl_yen_100": round(sum(pnls), 2),
                "profit_factor": _pf(pnls),
                "win_rate": round(wins / len(pnls), 4) if pnls else 0.0,
            }
        )
    return rows


def _rank_summaries(rows: list[dict[str, Any]]) -> None:
    for key, field, reverse in (
        ("rank_pf", "profit_factor", True),
        ("rank_pnl", "total_pnl_yen_100", True),
        ("rank_dd", "max_drawdown_yen_100", False),
        ("rank_stability", "daily_stability_score", True),
        ("rank_baseline_diff", "baseline_diff_pnl", True),
    ):
        ordered = sorted(rows, key=lambda r: float(r.get(field) or 0), reverse=reverse)
        for i, r in enumerate(ordered, start=1):
            r[key] = i


def _run_baseline_runtime(repo_root: Path) -> tuple[CapacityReplayState, dict[str, Any]]:
    kabu = resolve_kabu_root(repo_root)
    reports = resolve_reports_dir(repo_root)
    price_idx = _build_price_index_to(kabu, period_end=PERIOD_END)
    replay_pool, runtime_shadows = _load_replay_pool(reports)
    replay_pool = _filter_period(replay_pool, start=PERIOD_START, end=PERIOD_END)
    runtime_shadows = _fill_close_proxy_shadows(replay_pool, runtime_shadows, price_idx=price_idx)
    replay_pool = _filter_replay_pool_safe(replay_pool, runtime_shadows)
    _ensure_enriched(replay_pool, price_idx=price_idx)

    pre_rows = [
        _enrich_trade_row({"trade": t, "day": str(t.get("day") or "")[:8], "pnl_yen": 0, "exit_reason": ""})
        for t in replay_pool
        if pass_pbv2(t)
    ]
    medians = _medians_from_losers([r for r in pre_rows if _is_loser(r)])
    feature_row, _ = _build_feature_environment(replay_pool, price_idx=price_idx, medians=medians)

    def guard_c_block(trade: Mapping[str, Any]) -> bool:
        row = feature_row(trade)
        rsi = row.get("rsi_over80")
        return bool(row.get("late_chase_cluster")) and (
            rsi == 1.0 or rsi is True or (isinstance(rsi, (int, float)) and float(rsi) >= 1.0)
        )

    state = _replay_with_extra_block(
        replay_pool,
        runtime_shadows,
        extra_block=guard_c_block,
        mode_suffix="phase507_baseline",
    )
    met = _strategy_metrics(
        state,
        strategy_id=BASELINE_STRATEGY_ID,
        entry_rule_id="PBv2",
        exit_rule_id="RUNTIME",
    )
    return state, met


def _universe_symbols(replay_pool: Sequence[Mapping[str, Any]]) -> list[str]:
    syms = sorted({str(t.get("symbol") or "") for t in replay_pool if t.get("symbol")})
    return [s if s.endswith(".T") else f"{s}.T" for s in syms]


@dataclass
class Phase507Job:
    repo_root: Path
    parallel: bool = False
    max_workers: int = 4

    def run(self) -> dict[str, Any]:
        kabu = resolve_kabu_root(self.repo_root)
        reports = resolve_reports_dir(self.repo_root)
        max_workers = min(max(1, self.max_workers), MAX_WORKERS_CAP)

        baseline_state, baseline_met = _run_baseline_runtime(self.repo_root)
        price_idx = _build_price_index_to(kabu, period_end=PERIOD_END)
        replay_pool, _ = _load_replay_pool(reports)
        replay_pool = _filter_period(replay_pool, start=PERIOD_START, end=PERIOD_END)
        universe = _universe_symbols(replay_pool)
        days = sorted({str(t.get("day") or "")[:8] for t in replay_pool if t.get("day")})

        bar_cache: dict[tuple[str, str], tuple[list[Bar1m], list[BarIndicatorRow]]] = {}
        for sym in universe:
            for day in days:
                series = price_idx.get((sym, day), [])
                if not series:
                    continue
                bars = ticks_to_1m_bars(series)
                if len(bars) < MIN_BARS_WARMUP + 5:
                    continue
                bar_cache[(sym, day)] = (bars, compute_bar_indicators(bars))

        strategies = build_classical_strategies()
        jobs: list[tuple[str, str, str, str]] = []
        for spec in strategies:
            for day in days:
                jobs.append((spec["strategy_id"], spec["entry_rule_id"], spec["exit_rule_id"], day))

        day_candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)

        def _job(job: tuple[str, str, str, str]) -> tuple[str, list[dict[str, Any]]]:
            sid, eid, xid, day = job
            local: list[dict[str, Any]] = []
            for sym in universe:
                cached = bar_cache.get((sym, day))
                if not cached:
                    continue
                bars, ind_rows = cached
                for tr in scan_symbol_day(
                    symbol=sym,
                    day=day,
                    bars=bars,
                    ind_rows=ind_rows,
                    entry_rule_id=eid,
                    exit_rule_id=xid,
                ):
                    tr["strategy_id"] = sid
                    tr["entry_rule_id"] = eid
                    tr["exit_rule_id"] = xid
                    local.append(tr)
            return sid, local

        if self.parallel and len(jobs) > 1:
            with ThreadPoolExecutor(max_workers=max_workers) as ex:
                futs = [ex.submit(_job, j) for j in jobs]
                for fut in as_completed(futs):
                    sid, cands = fut.result()
                    day_candidates[sid].extend(cands)
        else:
            for j in jobs:
                sid, cands = _job(j)
                day_candidates[sid].extend(cands)

        summary_rows: list[dict[str, Any]] = [baseline_met]
        daily_rows: list[dict[str, Any]] = _day_rows(baseline_state, BASELINE_STRATEGY_ID)
        trade_rows: list[dict[str, Any]] = []
        for log in _trade_summary_rows(baseline_state):
            trade_rows.append({**log, "strategy_id": BASELINE_STRATEGY_ID, "entry_rule_id": "PBv2", "exit_rule_id": "RUNTIME"})

        for spec in strategies:
            sid = spec["strategy_id"]
            cands = day_candidates.get(sid, [])
            st = _simulate_precomputed_cap(cands, mode=f"{PHASE507_MODE}_{sid}")
            met = _strategy_metrics(
                st,
                strategy_id=sid,
                entry_rule_id=spec["entry_rule_id"],
                exit_rule_id=spec["exit_rule_id"],
                baseline=baseline_met,
            )
            summary_rows.append(met)
            daily_rows.extend(_day_rows(st, sid))
            for log in state_trade_logs(st, spec):
                trade_rows.append(log)

        _rank_summaries(summary_rows)
        mandatory = _mandatory_answers(summary_rows, baseline_met)
        return {
            "verdict": "classic_strategy_battle_done",
            "generated_at": _now_iso(),
            "period_start": PERIOD_START,
            "period_end": PERIOD_END,
            "universe_symbol_count": len(universe),
            "classical_strategy_count": len(strategies),
            "baseline": baseline_met,
            "summary_rows": summary_rows,
            "daily_rows": daily_rows,
            "trade_rows": trade_rows,
            "mandatory_answers": mandatory,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        paths = {
            "summary": reports / "strategy_battle_summary.csv",
            "daily": reports / "strategy_battle_daily.csv",
            "trades": reports / "strategy_battle_trades.csv",
            "report": reports / "strategy_battle_report.json",
            "review": kabu / "docs" / "operations" / "top5_strategy_review.md",
        }
        _write_csv(paths["summary"], SUMMARY_FIELDS, list(result.get("summary_rows") or []))
        _write_csv(paths["daily"], DAILY_FIELDS, list(result.get("daily_rows") or []))
        _write_csv(paths["trades"], TRADE_FIELDS, list(result.get("trade_rows") or []))
        paths["report"].write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        paths["review"].write_text(_top5_markdown(result), encoding="utf-8")
        return paths


def state_trade_logs(state: CapacityReplayState, spec: Mapping[str, str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for log in state.trade_log:
        tr = log.get("trade") or log
        rows.append(
            {
                "strategy_id": spec["strategy_id"],
                "symbol": str(tr.get("symbol") or "").replace(".T", ""),
                "day": str(log.get("day") or tr.get("day") or "")[:8],
                "entry_time": tr.get("entry_time"),
                "exit_time": log.get("exit_time"),
                "entry_price": tr.get("entry_price"),
                "exit_price": tr.get("exit_price"),
                "pnl_yen_100": log.get("pnl_yen"),
                "exit_reason": log.get("exit_reason"),
                "entry_rule_id": spec["entry_rule_id"],
                "exit_rule_id": spec["exit_rule_id"],
            }
        )
    return rows


def _mandatory_answers(
    summary_rows: Sequence[Mapping[str, Any]],
    baseline: Mapping[str, Any],
) -> dict[str, Any]:
    b_pnl = float(baseline["total_pnl_yen_100"])
    b_pf = float(baseline["profit_factor"] or 0)
    b_dd = float(baseline["max_drawdown_yen_100"])
    b_stab = float(baseline["daily_stability_score"])
    classical = [r for r in summary_rows if r.get("strategy_id") != BASELINE_STRATEGY_ID]

    beat_pnl = [r["strategy_id"] for r in classical if float(r["total_pnl_yen_100"]) > b_pnl]
    beat_pf = [r["strategy_id"] for r in classical if float(r["profit_factor"] or 0) > b_pf]
    beat_dd = [r["strategy_id"] for r in classical if float(r["max_drawdown_yen_100"]) < b_dd]
    beat_stab = [r["strategy_id"] for r in classical if float(r["daily_stability_score"]) > b_stab]

    def _family_stats(prefix: str) -> dict[str, Any]:
        fam = [r for r in classical if str(r.get("entry_rule_id") or "").startswith(prefix) or prefix in str(r.get("entry_rule_id") or "")]
        if not fam:
            return {"count": 0, "best_pnl": None, "beat_baseline_pnl": 0}
        best = max(fam, key=lambda r: float(r["total_pnl_yen_100"]))
        return {
            "count": len(fam),
            "best_strategy": best["strategy_id"],
            "best_pnl": best["total_pnl_yen_100"],
            "beat_baseline_pnl": sum(1 for r in fam if float(r["total_pnl_yen_100"]) > b_pnl),
        }

    boardless_best = max(classical, key=lambda r: float(r["total_pnl_yen_100"]), default=None)
    return {
        "beats_baseline_any": bool(beat_pnl or beat_pf or beat_dd or beat_stab),
        "beat_baseline_pnl": beat_pnl[:10],
        "beat_baseline_pf": beat_pf[:10],
        "beat_baseline_dd": beat_dd[:10],
        "beat_baseline_stability": beat_stab[:10],
        "rsi_family": _family_stats("T"),
        "vwap_family": _family_stats("T4") | {"vwap_entries": [r["strategy_id"] for r in classical if "VWAP" in ENTRY_RULES.get(str(r.get("entry_rule_id")), ("",))[0] and float(r["total_pnl_yen_100"]) > b_pnl][:5]},
        "adx_family": {"adx_beat_pnl": [r["strategy_id"] for r in classical if "ADX" in ENTRY_RULES.get(str(r.get("entry_rule_id")), ("",))[0] and float(r["total_pnl_yen_100"]) > b_pnl][:5]},
        "macd_family": {"macd_beat_pnl": [r["strategy_id"] for r in classical if "MACD" in ENTRY_RULES.get(str(r.get("entry_rule_id")), ("",))[0] and float(r["total_pnl_yen_100"]) > b_pnl][:5]},
        "boardless_best": boardless_best,
        "baseline_runtime": baseline,
    }


def _top5_markdown(result: Mapping[str, Any]) -> str:
    rows = sorted(
        list(result.get("summary_rows") or []),
        key=lambda r: float(r.get("total_pnl_yen_100") or 0),
        reverse=True,
    )
    mandatory = result.get("mandatory_answers") or {}
    lines = [
        "# Phase507 Top-5 Strategy Review",
        "",
        f"**Verdict:** `{result.get('verdict')}`",
        f"**Period:** {result.get('period_start')} – {result.get('period_end')}",
        f"**Universe symbols:** {result.get('universe_symbol_count')}",
        "",
        "## BASELINE_RUNTIME",
        "",
        f"- PnL: {mandatory.get('baseline_runtime', {}).get('total_pnl_yen_100')}",
        f"- PF: {mandatory.get('baseline_runtime', {}).get('profit_factor')}",
        f"- maxDD: {mandatory.get('baseline_runtime', {}).get('max_drawdown_yen_100')}",
        "",
        "## Top 5 by PnL",
        "",
    ]
    for i, r in enumerate(rows[:5], 1):
        lines.append(
            f"{i}. **{r.get('strategy_id')}** — PnL={r.get('total_pnl_yen_100')} PF={r.get('profit_factor')} "
            f"DD={r.get('max_drawdown_yen_100')} trades={r.get('trades')} "
            f"(entry={r.get('entry_rule_id')} exit={r.get('exit_rule_id')})"
        )
    lines.extend(
        [
            "",
            "## Mandatory answers (summary)",
            "",
            f"- Beats baseline (any metric): {mandatory.get('beats_baseline_any')}",
            f"- PnL beaters (sample): {mandatory.get('beat_baseline_pnl')}",
            f"- PF beaters (sample): {mandatory.get('beat_baseline_pf')}",
            f"- Boardless best: {mandatory.get('boardless_best')}",
            "",
            "Research only — no runtime adoption.",
        ]
    )
    return "\n".join(lines) + "\n"


def run_phase507(*, repo_root: Path, parallel: bool = False, max_workers: int = 4) -> dict[str, Any]:
    job = Phase507Job(repo_root=repo_root, parallel=parallel, max_workers=max_workers)
    return job.run()
