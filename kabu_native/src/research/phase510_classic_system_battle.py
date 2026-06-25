"""
Phase510 — Classic system battle vs PBv2 baseline (research only).

Standalone classical technical systems (no PBv2 overlay/combination).
Same universe, period, CAP=5 as Phase507.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase451_entry_shape_tournament import _build_price_index_to, _now_iso
from research.phase476_pre_breakout_gate_replay import _load_replay_pool
from research.phase488_current_runtime_replay import _filter_period, _trade_summary_rows
from research.phase493_global_entry_failure_audit import PERIOD_END, PERIOD_START
from research.phase507_classic_indicators import (
    INDICATOR_LOG_FIELDS,
    Bar1m,
    BarIndicatorRow,
    _in_trading_window,
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
    _rank_summaries,
    _run_baseline_runtime,
    _simulate_precomputed_cap,
    _universe_symbols,
    state_trade_logs,
)
from research.phase443_full_runtime_combined_capital_sim import _chronological_pnls_from_log
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

PHASE510_MODE = "phase510_classic_system_battle"
PHASE510_VERDICT = "phase510_classic_system_battle_done"
MAX_WORKERS_CAP = 4

SYSTEM_A_ID = "CLASSIC_A_TREND_FOLLOWING"
SYSTEM_B_ID = "CLASSIC_B_MOMENTUM_CONTINUATION"
SYSTEM_C_ID = "CLASSIC_C_BREAKOUT"
SYSTEM_D_ID = "CLASSIC_D_PULLBACK_RECOVERY"

CLASSIC_SYSTEMS: list[dict[str, str]] = [
    {
        "strategy_id": SYSTEM_A_ID,
        "system_name": "Trend Following",
        "philosophy": "上昇トレンド継続に乗る",
        "entry_rule_id": "SYS_A",
        "exit_rule_id": "ATR_OR_EMA5_OR_STOP",
    },
    {
        "strategy_id": SYSTEM_B_ID,
        "system_name": "Momentum Continuation",
        "philosophy": "強い銘柄はさらに強い",
        "entry_rule_id": "SYS_B",
        "exit_rule_id": "ATR_OR_RSI_OR_STOP",
    },
    {
        "strategy_id": SYSTEM_C_ID,
        "system_name": "Breakout",
        "philosophy": "レンジ突破",
        "entry_rule_id": "SYS_C",
        "exit_rule_id": "DONCHIAN_MID_OR_STOP",
    },
    {
        "strategy_id": SYSTEM_D_ID,
        "system_name": "Pullback Recovery",
        "philosophy": "押し目回復",
        "entry_rule_id": "SYS_D",
        "exit_rule_id": "EMA20_OR_STOP",
    },
]

SUMMARY_FIELDS = [
    "strategy_id",
    "system_name",
    "philosophy",
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
    "system_name",
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


def _f(v: Optional[float]) -> float:
    return float(v) if v is not None else float("nan")


def _entry_sys_a(ind: Mapping[str, Optional[float]], bar: Bar1m) -> bool:
    return (
        bar.close > _f(ind.get("EMA20"))
        and bar.close > _f(ind.get("VWAP"))
        and _f(ind.get("ADX")) > 20
    )


def _entry_sys_b(ind: Mapping[str, Optional[float]], bar: Bar1m) -> bool:
    return _f(ind.get("RSI14")) > 50 and _f(ind.get("STOCH_K")) > _f(ind.get("STOCH_D"))


def _entry_sys_c(ind_rows: Sequence[BarIndicatorRow], i: int, bar: Bar1m) -> bool:
    if i < 1:
        return False
    dh = ind_rows[i - 1].values.get("DONCHIAN_HIGH20")
    return dh is not None and bar.close > _f(dh)


def _ema20_slope_positive(ind_rows: Sequence[BarIndicatorRow], i: int, *, lookback: int = 5) -> bool:
    if i < lookback:
        return False
    cur = ind_rows[i].values.get("EMA20")
    prev = ind_rows[i - lookback].values.get("EMA20")
    if cur is None or prev is None:
        return False
    return float(cur) > float(prev)


def _rsi_recover_40_50(ind_rows: Sequence[BarIndicatorRow], i: int, *, lookback: int = 10) -> bool:
    cur = ind_rows[i].values.get("RSI14")
    if cur is None or float(cur) < 50:
        return False
    start = max(MIN_BARS_WARMUP, i - lookback)
    past = [
        ind_rows[j].values.get("RSI14")
        for j in range(start, i)
        if ind_rows[j].values.get("RSI14") is not None
    ]
    if not past:
        return False
    return min(float(r) for r in past) <= 40


def _entry_sys_d(ind_rows: Sequence[BarIndicatorRow], i: int, bar: Bar1m) -> bool:
    return _ema20_slope_positive(ind_rows, i) and _rsi_recover_40_50(ind_rows, i)


ENTRY_FNS: dict[str, Callable[..., bool]] = {
    "SYS_A": lambda ind, bar, _rows, _i: _entry_sys_a(ind, bar),
    "SYS_B": lambda ind, bar, _rows, _i: _entry_sys_b(ind, bar),
    "SYS_C": lambda ind, bar, rows, i: _entry_sys_c(rows, i, bar),
    "SYS_D": lambda ind, bar, rows, i: _entry_sys_d(rows, i, bar),
}


def _find_exit_system(
    bars: Sequence[Bar1m],
    ind_rows: Sequence[BarIndicatorRow],
    entry_i: int,
    *,
    exit_rule_id: str,
    entry_px: float,
) -> tuple[int, str]:
    peak = entry_px
    atr_mult = 2.0
    for j in range(entry_i + 1, len(bars)):
        ind = ind_rows[j].values
        bar = bars[j]
        if _hard_stop_hit(entry_px, bar.close):
            return j, "hard_stop"
        if exit_rule_id in ("ATR_OR_EMA5_OR_STOP", "ATR_OR_RSI_OR_STOP"):
            peak = max(peak, bar.high)
            atr = _f(ind.get("ATR14"))
            if atr == atr and bar.close < peak - atr_mult * atr:
                return j, "atr_trailing"
            if exit_rule_id == "ATR_OR_EMA5_OR_STOP" and bar.close < _f(ind.get("EMA5")):
                return j, "ema5_break"
            if exit_rule_id == "ATR_OR_RSI_OR_STOP" and _f(ind.get("RSI14")) < 50:
                return j, "rsi_exit"
            continue
        if exit_rule_id == "DONCHIAN_MID_OR_STOP":
            if bar.close < _f(ind.get("DONCHIAN_MID20")):
                return j, "donchian_mid_break"
            continue
        if exit_rule_id == "EMA20_OR_STOP":
            if bar.close < _f(ind.get("EMA20")):
                return j, "ema20_break"
            continue
    return len(bars) - 1, "session_end"


def scan_system_symbol_day(
    *,
    symbol: str,
    day: str,
    bars: Sequence[Bar1m],
    ind_rows: Sequence[BarIndicatorRow],
    entry_rule_id: str,
    exit_rule_id: str,
) -> list[dict[str, Any]]:
    entry_fn = ENTRY_FNS[entry_rule_id]
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
        if not entry_fn(ind, bar, ind_rows, i):
            continue
        exit_i, exit_reason = _find_exit_system(
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


def _baseline_trade_rows(baseline_state: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for log in _trade_summary_rows(baseline_state):
        rows.append(
            {
                "strategy_id": BASELINE_STRATEGY_ID,
                "system_name": "PBv2 Runtime",
                "symbol": str(log.get("symbol") or "").replace(".T", ""),
                "day": str(log.get("day") or "")[:8],
                "entry_time": log.get("entry_time"),
                "exit_time": log.get("exit_time"),
                "entry_price": "",
                "exit_price": "",
                "pnl_yen_100": log.get("pnl_yen"),
                "exit_reason": log.get("exit_reason"),
                "entry_rule_id": "PBv2",
                "exit_rule_id": "RUNTIME",
            }
        )
    return rows


def _strategy_metrics_safe(
    state: Any,
    *,
    strategy_id: str,
    entry_rule_id: str,
    exit_rule_id: str,
    baseline: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    chron = _chronological_pnls_from_log(state.trade_log)
    winners = [p for p in chron if p > 0]
    daily: dict[str, float] = defaultdict(float)
    for log in state.trade_log:
        d = str(log.get("day") or "")[:8]
        daily[d] += float(log.get("pnl_yen") or 0)
    pos_days = sum(1 for v in daily.values() if v > 0)
    neg_days = sum(1 for v in daily.values() if v < 0)
    daily_vals = list(daily.values())
    stability = round(pos_days / max(1, pos_days + neg_days), 4)
    max_dd = _max_drawdown_yen(chron) if chron else 0.0
    row = {
        "strategy_id": strategy_id,
        "entry_rule_id": entry_rule_id,
        "exit_rule_id": exit_rule_id,
        "total_pnl_yen_100": round(sum(chron), 2),
        "profit_factor": _pf(chron),
        "max_drawdown_yen_100": round(max_dd, 2),
        "trades": len(chron),
        "win_rate": round(len(winners) / len(chron), 4) if chron else 0.0,
        "avg_pnl_yen_100": round(statistics.mean(chron), 2) if chron else 0.0,
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


def _enrich_summary_row(row: Mapping[str, Any], spec: Mapping[str, str]) -> dict[str, Any]:
    return {
        **dict(row),
        "system_name": spec.get("system_name", ""),
        "philosophy": spec.get("philosophy", ""),
    }


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

    best_pnl = max(classical, key=lambda r: float(r["total_pnl_yen_100"]))
    best_pf = max(classical, key=lambda r: float(r["profit_factor"] or 0))
    best_dd = min(classical, key=lambda r: float(r["max_drawdown_yen_100"]))
    best_stab = max(classical, key=lambda r: float(r["daily_stability_score"]))

    pnl_positive = [r for r in classical if float(r["total_pnl_yen_100"]) > 0]
    next_deep_dive = (
        max(pnl_positive, key=lambda r: float(r["total_pnl_yen_100"]))["strategy_id"]
        if pnl_positive
        else max(classical, key=lambda r: float(r["profit_factor"] or 0))["strategy_id"]
    )

    by_id = {r["strategy_id"]: r for r in classical}

    def _effective(sid: str) -> bool:
        r = by_id.get(sid, {})
        return float(r.get("total_pnl_yen_100") or 0) > b_pnl or float(r.get("profit_factor") or 0) > b_pf

    return {
        "1_beat_pbv2_systems_exist": bool(beat_pnl),
        "1_beat_pbv2_pnl_list": beat_pnl,
        "2_highest_pnl": best_pnl["strategy_id"],
        "2_highest_pnl_value": best_pnl["total_pnl_yen_100"],
        "3_highest_pf": best_pf["strategy_id"],
        "3_highest_pf_value": best_pf["profit_factor"],
        "4_lowest_dd": best_dd["strategy_id"],
        "4_lowest_dd_value": best_dd["max_drawdown_yen_100"],
        "5_highest_daily_stability": best_stab["strategy_id"],
        "5_highest_daily_stability_value": best_stab["daily_stability_score"],
        "6_trend_following_effective": _effective(SYSTEM_A_ID),
        "7_momentum_continuation_effective": _effective(SYSTEM_B_ID),
        "8_breakout_effective": _effective(SYSTEM_C_ID),
        "9_pullback_effective": _effective(SYSTEM_D_ID),
        "10_next_deep_dive_school": next_deep_dive,
        "beat_baseline_pf": beat_pf,
        "beat_baseline_dd": beat_dd,
        "beat_baseline_stability": beat_stab,
        "baseline_runtime": baseline,
    }


@dataclass
class Phase510Job:
    repo_root: Path
    parallel: bool = True
    max_workers: int = 4

    def run(self) -> dict[str, Any]:
        kabu = resolve_kabu_root(self.repo_root)
        reports = resolve_reports_dir(self.repo_root)
        max_workers = min(max(1, self.max_workers), MAX_WORKERS_CAP)

        baseline_state, baseline_met = _run_baseline_runtime(self.repo_root)
        baseline_met = _enrich_summary_row(
            baseline_met,
            {
                "system_name": "PBv2 Runtime",
                "philosophy": "Phase314 board + classic_late_chase_rsi_guard",
            },
        )

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

        jobs: list[tuple[str, str, str, str]] = []
        for spec in CLASSIC_SYSTEMS:
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
                for tr in scan_system_symbol_day(
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

        spec_by_id = {s["strategy_id"]: s for s in CLASSIC_SYSTEMS}
        summary_rows: list[dict[str, Any]] = [baseline_met]
        daily_rows: list[dict[str, Any]] = _day_rows(baseline_state, BASELINE_STRATEGY_ID)
        trade_rows: list[dict[str, Any]] = _baseline_trade_rows(baseline_state)

        for spec in CLASSIC_SYSTEMS:
            sid = spec["strategy_id"]
            cands = day_candidates.get(sid, [])
            st = _simulate_precomputed_cap(cands, mode=f"{PHASE510_MODE}_{sid}")
            met = _strategy_metrics_safe(
                st,
                strategy_id=sid,
                entry_rule_id=spec["entry_rule_id"],
                exit_rule_id=spec["exit_rule_id"],
                baseline=baseline_met,
            )
            summary_rows.append(_enrich_summary_row(met, spec))
            daily_rows.extend(_day_rows(st, sid))
            for log in state_trade_logs(st, spec):
                trade_rows.append({**log, "system_name": spec["system_name"]})

        _rank_summaries(summary_rows)
        mandatory = _mandatory_answers(summary_rows, baseline_met)
        return {
            "verdict": PHASE510_VERDICT,
            "generated_at": _now_iso(),
            "period_start": PERIOD_START,
            "period_end": PERIOD_END,
            "universe_symbol_count": len(universe),
            "classic_system_count": len(CLASSIC_SYSTEMS),
            "baseline": baseline_met,
            "summary_rows": summary_rows,
            "daily_rows": daily_rows,
            "trade_rows": trade_rows,
            "mandatory_answers": mandatory,
            "systems": CLASSIC_SYSTEMS,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        paths = {
            "summary": reports / "strategy_battle_phase510.csv",
            "daily": reports / "strategy_battle_phase510_daily.csv",
            "trades": reports / "strategy_battle_phase510_trades.csv",
            "report": reports / "strategy_battle_phase510_report.json",
        }
        _write_csv(paths["summary"], SUMMARY_FIELDS, list(result.get("summary_rows") or []))
        _write_csv(paths["daily"], DAILY_FIELDS, list(result.get("daily_rows") or []))
        _write_csv(paths["trades"], TRADE_FIELDS, list(result.get("trade_rows") or []))
        paths["report"].write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return paths
