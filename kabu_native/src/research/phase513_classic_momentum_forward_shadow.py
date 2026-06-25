"""
Phase513 — Classic Momentum forward shadow (research replay + live shadow types).

CLASSIC_MOMENTUM_SHADOW:
  ENTRY: RSI14 > 50 AND Stoch K > D
  EXIT: session_end_only + hard_stop (-1.2%, same as runtime)

Research only. No adoption. No Runtime entry changes.
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _parse_ts
from research.phase451_entry_shape_tournament import _build_price_index_to, _now_iso
from research.phase476_pre_breakout_gate_replay import _load_replay_pool
from research.phase488_current_runtime_replay import _filter_period, _trade_summary_rows
from research.phase493_global_entry_failure_audit import PERIOD_END, PERIOD_START
from research.phase507_classic_indicators import (
    Bar1m,
    BarIndicatorRow,
    compute_bar_indicators,
    ticks_to_1m_bars,
)
from research.phase507_classic_strategy_battle import (
    BASELINE_STRATEGY_ID,
    ENTRY_COOLDOWN_SEC,
    HARD_STOP_PCT,
    MIN_BARS_WARMUP,
    _day_rows,
    _hard_stop_hit,
    _run_baseline_runtime,
    _simulate_precomputed_cap,
    _universe_symbols,
)
from research.phase510_classic_system_battle import _strategy_metrics_safe
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

PHASE513_VERDICT = "phase513_momentum_shadow_done"
STRATEGY_ID = "CLASSIC_MOMENTUM_SHADOW"
MIN_TRADING_DAYS = 5
MIN_TRADES = 50
SYMBOL_6976 = "6976"

TRADE_FIELDS = [
    "strategy_id",
    "symbol",
    "day",
    "entry_time",
    "exit_time",
    "pnl_yen_100",
    "pnl_pct",
    "hold_minutes",
    "mfe_pct",
    "mae_pct",
    "entry_rsi",
    "entry_stoch_k",
    "entry_stoch_d",
    "exit_reason",
]

DAILY_FIELDS = [
    "day",
    "strategy_id",
    "total_pnl_yen_100",
    "profit_factor",
    "trade_count",
    "win_rate",
    "avg_pnl_yen_100",
    "avg_hold_minutes",
    "best_trade_pnl",
    "worst_trade_pnl",
    "baseline_pnl_yen_100",
    "beats_baseline",
    "top1_symbol_profit_share_pct",
    "top3_symbol_profit_share_pct",
    "top1_day_profit_share_pct",
    "top3_day_profit_share_pct",
    "gini_coefficient",
]


def _f(v: Optional[float]) -> float:
    return float(v) if v is not None else float("nan")


def _float(v: Any) -> float:
    try:
        if v is None or v == "":
            return 0.0
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _entry_momentum(ind: Mapping[str, Optional[float]]) -> bool:
    return _f(ind.get("RSI14")) > 50 and _f(ind.get("STOCH_K")) > _f(ind.get("STOCH_D"))


def _find_exit_session_end(
    bars: Sequence[Bar1m],
    entry_i: int,
    *,
    entry_px: float,
) -> tuple[int, str, float, float]:
    peak = trough = entry_px
    for j in range(entry_i + 1, len(bars)):
        bar = bars[j]
        peak = max(peak, bar.high)
        trough = min(trough, bar.low)
        if _hard_stop_hit(entry_px, bar.close):
            return j, "hard_stop", peak, trough
    last = bars[-1]
    return len(bars) - 1, "session_end", peak, trough


def scan_momentum_shadow_symbol_day(
    *,
    symbol: str,
    day: str,
    bars: Sequence[Bar1m],
    ind_rows: Sequence[BarIndicatorRow],
) -> list[dict[str, Any]]:
    from research.phase507_classic_indicators import _in_trading_window

    trades: list[dict[str, Any]] = []
    last_entry: Optional[datetime] = None
    sym = symbol if symbol.endswith(".T") else f"{symbol}.T"
    for i in range(MIN_BARS_WARMUP, len(bars)):
        bar = bars[i]
        if not _in_trading_window(bar.ts):
            continue
        ind = ind_rows[i].values
        if ind.get("RSI14") is None:
            continue
        if last_entry and (bar.ts - last_entry).total_seconds() < ENTRY_COOLDOWN_SEC:
            continue
        if not _entry_momentum(ind):
            continue
        exit_i, reason, peak, trough = _find_exit_session_end(bars, i, entry_px=bar.close)
        exit_bar = bars[exit_i]
        ent_px = bar.close
        pnl_pct = round((exit_bar.close - ent_px) / ent_px * 100.0, 4) if ent_px > 0 else 0.0
        mfe = round((peak - ent_px) / ent_px * 100.0, 4) if ent_px > 0 else 0.0
        mae = round((trough - ent_px) / ent_px * 100.0, 4) if ent_px > 0 else 0.0
        hold = max(0.0, (exit_bar.ts - bar.ts).total_seconds() / 60.0)
        trades.append(
            {
                "strategy_id": STRATEGY_ID,
                "symbol": sym,
                "day": day,
                "entry_time": bar.ts.isoformat(),
                "exit_time": exit_bar.ts.isoformat(),
                "entry_price": ent_px,
                "exit_price": exit_bar.close,
                "pnl_yen": round((exit_bar.close - ent_px) * 100.0, 2),
                "pnl_pct": pnl_pct,
                "hold_minutes": round(hold, 2),
                "mfe_pct": mfe,
                "mae_pct": mae,
                "entry_rsi": ind.get("RSI14"),
                "entry_stoch_k": ind.get("STOCH_K"),
                "entry_stoch_d": ind.get("STOCH_D"),
                "exit_reason": reason,
            }
        )
        last_entry = bar.ts
    return trades


def _gini(values: Sequence[float]) -> float:
    xs = sorted(v for v in values if v > 0)
    if not xs:
        return 0.0
    n = len(xs)
    total = sum(xs)
    if total <= 0:
        return 0.0
    cum = sum((i + 1) * x for i, x in enumerate(xs))
    return round((2 * cum) / (n * total) - (n + 1) / n, 4)


def _robustness_for_trades(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    pnls = [_float(t.get("pnl_yen_100") if t.get("pnl_yen_100") is not None else t.get("pnl_yen")) for t in trades]
    wins = sorted([p for p in pnls if p > 0], reverse=True)
    gross = sum(wins)
    top1 = round(wins[0] / gross * 100.0, 2) if wins and gross > 0 else 0.0
    top5 = round(sum(wins[:5]) / gross * 100.0, 2) if wins and gross > 0 else 0.0
    top10 = round(sum(wins[:10]) / gross * 100.0, 2) if wins and gross > 0 else 0.0

    sym_pnl: dict[str, float] = defaultdict(float)
    for t in trades:
        p = _float(t.get("pnl_yen_100") if t.get("pnl_yen_100") is not None else t.get("pnl_yen"))
        sym_pnl[str(t.get("symbol") or "").replace(".T", "")] += p
    total = sum(pnls)
    sym_rank = sorted(sym_pnl.items(), key=lambda x: x[1], reverse=True)
    top1_sym = round(sym_rank[0][1] / total * 100.0, 2) if total and sym_rank else 0.0
    top3_sym = round(sum(v for _, v in sym_rank[:3]) / total * 100.0, 2) if total and sym_rank else 0.0

    day_pnl: dict[str, float] = defaultdict(float)
    for t in trades:
        d = str(t.get("day") or "")[:8]
        p = _float(t.get("pnl_yen_100") if t.get("pnl_yen_100") is not None else t.get("pnl_yen"))
        day_pnl[d] += p
    day_rank = sorted(day_pnl.items(), key=lambda x: x[1], reverse=True)
    top1_day = round(day_rank[0][1] / total * 100.0, 2) if total and day_rank else 0.0
    top3_day = round(sum(v for _, v in day_rank[:3]) / total * 100.0, 2) if total and day_rank else 0.0

    return {
        "top1_trade_profit_share_pct": top1,
        "top5_trade_profit_share_pct": top5,
        "top10_trade_profit_share_pct": top10,
        "top1_symbol_profit_share_pct": top1_sym,
        "top3_symbol_profit_share_pct": top3_sym,
        "top1_day_profit_share_pct": top1_day,
        "top3_day_profit_share_pct": top3_day,
        "gini_coefficient": _gini(pnls),
        "top1_symbol": sym_rank[0][0] if sym_rank else "",
        "symbol_6976_share_pct": round(sym_pnl.get(SYMBOL_6976, 0) / total * 100.0, 2) if total else 0.0,
        "single_symbol_dependency": bool(sym_rank and top1_sym >= 40 and sym_rank[0][1] > 0),
        "single_day_dependency": bool(day_rank and top1_day >= 35 and day_rank[0][1] > 0),
    }


@dataclass
class Phase513Job:
    repo_root: Path
    parallel: bool = True
    max_workers: int = 4

    def run(self) -> dict[str, Any]:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        kabu = resolve_kabu_root(self.repo_root)
        reports = resolve_reports_dir(self.repo_root)
        max_workers = min(max(1, self.max_workers), 4)

        baseline_state, baseline_met = _run_baseline_runtime(self.repo_root)
        baseline_by_day = {str(r["day"])[:8]: _float(r["total_pnl_yen_100"]) for r in _day_rows(baseline_state, BASELINE_STRATEGY_ID)}

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

        jobs = [(day, sym) for day in days for sym in universe if (sym, day) in bar_cache]
        all_candidates: list[dict[str, Any]] = []

        def _job(day_sym: tuple[str, str]) -> list[dict[str, Any]]:
            day, sym = day_sym
            bars, ind_rows = bar_cache[(sym, day)]
            return scan_momentum_shadow_symbol_day(symbol=sym, day=day, bars=bars, ind_rows=ind_rows)

        if self.parallel and jobs:
            with ThreadPoolExecutor(max_workers=max_workers) as ex:
                for fut in as_completed(ex.submit(_job, j) for j in jobs):
                    all_candidates.extend(fut.result())
        else:
            for j in jobs:
                all_candidates.extend(_job(j))

        state = _simulate_precomputed_cap(all_candidates, mode="phase513_momentum_shadow")
        metrics = _strategy_metrics_safe(
            state,
            strategy_id=STRATEGY_ID,
            entry_rule_id="RSI50_STOCH_KD",
            exit_rule_id="SESSION_END_ONLY",
            baseline=baseline_met,
        )

        trade_rows: list[dict[str, Any]] = []
        for log in state.trade_log:
            if not log.get("exit_time"):
                continue
            tr = log.get("trade") or log
            trade_rows.append(
                {
                    "strategy_id": STRATEGY_ID,
                    "symbol": str(tr.get("symbol") or "").replace(".T", ""),
                    "day": str(log.get("day") or tr.get("day") or "")[:8],
                    "entry_time": tr.get("entry_time"),
                    "exit_time": log.get("exit_time"),
                    "pnl_yen_100": _float(log.get("pnl_yen")),
                    "pnl_pct": tr.get("pnl_pct"),
                    "hold_minutes": tr.get("hold_minutes"),
                    "mfe_pct": tr.get("mfe_pct"),
                    "mae_pct": tr.get("mae_pct"),
                    "entry_rsi": tr.get("entry_rsi"),
                    "entry_stoch_k": tr.get("entry_stoch_k"),
                    "entry_stoch_d": tr.get("entry_stoch_d"),
                    "exit_reason": log.get("exit_reason"),
                }
            )

        by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for tr in trade_rows:
            by_day[str(tr["day"])[:8]].append(tr)

        daily_rows: list[dict[str, Any]] = []
        for day in sorted(by_day):
            dtrades = by_day[day]
            pnls = [_float(t["pnl_yen_100"]) for t in dtrades]
            holds = [_float(t["hold_minutes"]) for t in dtrades if t.get("hold_minutes") is not None]
            wins = sum(1 for p in pnls if p > 0)
            rob = _robustness_for_trades(dtrades)
            b_pnl = baseline_by_day.get(day, 0.0)
            daily_rows.append(
                {
                    "day": day,
                    "strategy_id": STRATEGY_ID,
                    "total_pnl_yen_100": round(sum(pnls), 2),
                    "profit_factor": _pf(pnls),
                    "trade_count": len(pnls),
                    "win_rate": round(wins / len(pnls), 4) if pnls else 0.0,
                    "avg_pnl_yen_100": round(statistics.mean(pnls), 2) if pnls else 0.0,
                    "avg_hold_minutes": round(statistics.mean(holds), 2) if holds else 0.0,
                    "best_trade_pnl": round(max(pnls), 2) if pnls else 0.0,
                    "worst_trade_pnl": round(min(pnls), 2) if pnls else 0.0,
                    "baseline_pnl_yen_100": round(b_pnl, 2),
                    "beats_baseline": sum(pnls) > b_pnl,
                    **rob,
                }
            )

        overall_rob = _robustness_for_trades(trade_rows)
        trading_days = len(daily_rows)
        trade_count = len(trade_rows)
        completion_met = trading_days >= MIN_TRADING_DAYS or trade_count >= MIN_TRADES

        sess_trades = [t for t in trade_rows if str(t.get("exit_reason") or "") == "session_end"]
        sess_wins = sum(1 for t in sess_trades if _float(t["pnl_yen_100"]) > 0)
        sess_win_rate = round(sess_wins / len(sess_trades), 4) if sess_trades else 0.0

        daily_pf_above_1 = sum(1 for d in daily_rows if _float(d.get("profit_factor") or 0) > 1.0)
        daily_pnl_positive = sum(1 for d in daily_rows if _float(d.get("total_pnl_yen_100") or 0) > 0)
        days_beat_baseline = sum(1 for d in daily_rows if d.get("beats_baseline"))

        fragile = (
            overall_rob.get("single_symbol_dependency")
            or overall_rob.get("single_day_dependency")
            or (overall_rob.get("top10_trade_profit_share_pct") or 0) >= 50
        )
        verdict_class = "classic_candidate_fragile" if fragile else "classic_candidate_robust"

        mandatory = {
            "1_pf_above_1_sustained": daily_pf_above_1 >= max(3, trading_days // 2),
            "1_cumulative_pf": metrics.get("profit_factor"),
            "2_pnl_positive_sustained": daily_pnl_positive >= max(3, trading_days // 2),
            "2_cumulative_pnl": metrics.get("total_pnl_yen_100"),
            "3_symbol_6976_dependency_gone": overall_rob.get("symbol_6976_share_pct", 100) < 25,
            "3_symbol_6976_share_pct": overall_rob.get("symbol_6976_share_pct"),
            "4_single_symbol_dependency_resolved": not overall_rob.get("single_symbol_dependency"),
            "5_single_day_dependency_resolved": not overall_rob.get("single_day_dependency"),
            "6_session_end_win_pattern": sess_win_rate >= 0.75 and len(sess_trades) >= 10,
            "6_session_end_win_rate": sess_win_rate,
            "6_session_end_count": len(sess_trades),
            "7_days_beating_pbv2": days_beat_baseline,
            "7_days_beating_pbv2_list": [d["day"] for d in daily_rows if d.get("beats_baseline")],
            "8_verdict": verdict_class,
            "completion_met": completion_met,
            "trading_days": trading_days,
            "trade_count": trade_count,
            "baseline_runtime": baseline_met,
            "shadow_metrics": metrics,
            "overall_robustness": overall_rob,
        }

        return {
            "verdict": PHASE513_VERDICT if completion_met else "phase513_momentum_shadow_collecting",
            "generated_at": _now_iso(),
            "period_start": PERIOD_START,
            "period_end": PERIOD_END,
            "strategy_id": STRATEGY_ID,
            "entry_rule": "RSI14 > 50 AND Stoch K > D",
            "exit_rule": "session_end_only + hard_stop -1.2%",
            "trade_rows": trade_rows,
            "daily_rows": daily_rows,
            "mandatory_answers": mandatory,
            "baseline": baseline_met,
            "metrics": metrics,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        paths = {
            "trades": reports / "phase513_momentum_shadow_trades.csv",
            "daily": reports / "phase513_momentum_shadow_daily.csv",
            "report": reports / "phase513_momentum_shadow_report.json",
            "docs": kabu / "docs" / "operations" / "phase513_classic_momentum_shadow.md",
        }
        _write_csv(paths["trades"], TRADE_FIELDS, list(result.get("trade_rows") or []))
        _write_csv(paths["daily"], DAILY_FIELDS, list(result.get("daily_rows") or []))
        paths["report"].write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        paths["docs"].write_text(_render_docs(result), encoding="utf-8")
        return paths


def _render_docs(result: Mapping[str, Any]) -> str:
    ma = result.get("mandatory_answers") or {}
    m = result.get("metrics") or {}
    rob = ma.get("overall_robustness") or {}
    lines = [
        "# Phase513 — Classic Momentum Forward Shadow",
        "",
        f"**Verdict:** `{result.get('verdict')}`",
        f"**Strategy:** `{result.get('strategy_id')}`",
        "",
        "## Rules",
        "",
        "- ENTRY: RSI14 > 50 AND Stoch K > D",
        "- EXIT: session_end_only + hard_stop -1.2%",
        "- Shadow only — no Runtime adoption",
        "",
        "## Cumulative vs BASELINE",
        "",
        f"| | Shadow | BASELINE |",
        f"|--|--------|----------|",
        f"| PnL | {m.get('total_pnl_yen_100')} | {ma.get('baseline_runtime', {}).get('total_pnl_yen_100')} |",
        f"| PF | {m.get('profit_factor')} | {ma.get('baseline_runtime', {}).get('profit_factor')} |",
        f"| Trades | {ma.get('trade_count')} | 440 |",
        "",
        "## Mandatory answers",
        "",
        f"1. PF>1 sustained: **{ma.get('1_pf_above_1_sustained')}** (cumulative PF={ma.get('1_cumulative_pf')})",
        f"2. PnL positive sustained: **{ma.get('2_pnl_positive_sustained')}**",
        f"3. 6976 dependency gone: **{ma.get('3_symbol_6976_dependency_gone')}** (share={ma.get('3_symbol_6976_share_pct')}%)",
        f"4. single_symbol_dependency resolved: **{ma.get('4_single_symbol_dependency_resolved')}**",
        f"5. single_day_dependency resolved: **{ma.get('5_single_day_dependency_resolved')}**",
        f"6. session_end win pattern: **{ma.get('6_session_end_win_pattern')}** (wr={ma.get('6_session_end_win_rate')})",
        f"7. days beating PBv2: **{ma.get('7_days_beating_pbv2')}**",
        f"8. verdict: **{ma.get('8_verdict')}**",
        "",
        "## Robustness",
        "",
        f"- top10 trade share: {rob.get('top10_trade_profit_share_pct')}%",
        f"- top1 symbol share: {rob.get('top1_symbol_profit_share_pct')}% ({rob.get('top1_symbol')})",
        f"- top1 day share: {rob.get('top1_day_profit_share_pct')}%",
        f"- Gini: {rob.get('gini_coefficient')}",
    ]
    return "\n".join(lines) + "\n"
