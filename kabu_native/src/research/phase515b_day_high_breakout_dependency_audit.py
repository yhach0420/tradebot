"""
Phase515B — day_high breakout dependency audit (research only).

Audits P515A_B_005 vs BASELINE_RUNTIME and P515A_M_002 reference.
PBv2 Exit fixed. No adoption.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _parse_ts, _position_key
from research.phase451_entry_shape_tournament import JST, _build_price_index_to, _now_iso
from research.phase476_pre_breakout_gate_replay import _load_replay_pool
from research.phase488_current_runtime_replay import _filter_period, _trade_summary_rows
from research.phase493_global_entry_failure_audit import PERIOD_END, PERIOD_START
from research.phase507_classic_indicators import (
    Bar1m,
    BarIndicatorRow,
    _in_trading_window,
    compute_bar_indicators,
    ticks_to_1m_bars,
)
from research.phase507_classic_strategy_battle import (
    BASELINE_STRATEGY_ID,
    MIN_BARS_WARMUP,
    _run_baseline_runtime,
    _simulate_precomputed_cap,
    _universe_symbols,
)
from research.phase510_classic_system_battle import _strategy_metrics_safe
from research.phase515a_classic_entry_parameter_robustness import EntrySpec, scan_entry_pb_exit_day
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

PHASE515B_VERDICT = "phase515b_day_high_breakout_dependency_audit_done"
MAX_WORKERS_CAP = 4

STRATEGY_B005 = "P515A_B_005"
STRATEGY_M002 = "P515A_M_002"
SYMBOL_6976 = "6976"
DAY_615 = "20260615"
SESSION_OPEN_HM = (9, 0)

SPEC_B005 = EntrySpec(
    strategy_id=STRATEGY_B005,
    family="breakout",
    description="day_high",
    params=(("tags", frozenset({"day_high"})),),
)
SPEC_M002 = EntrySpec(
    strategy_id=STRATEGY_M002,
    family="momentum",
    description="RSI>45 StochK>D+0 roc",
    params=(
        ("rsi_thresh", 45),
        ("stoch_margin", 0),
        ("extras", frozenset({"roc"})),
    ),
)
CLASSICAL_SPECS = (SPEC_B005, SPEC_M002)

DEP_SUMMARY_FIELDS = [
    "strategy_id",
    "description",
    "total_pnl_yen_100",
    "profit_factor",
    "max_drawdown_yen_100",
    "trades",
    "win_rate",
    "avg_pnl_yen_100",
    "top1_trade_profit_share_pct",
    "top5_trade_profit_share_pct",
    "top10_trade_profit_share_pct",
    "top1_symbol_profit_share_pct",
    "top3_symbol_profit_share_pct",
    "top1_day_profit_share_pct",
    "top3_day_profit_share_pct",
    "single_symbol_dependency",
    "single_day_dependency",
    "trade_concentration_dependency",
    "overall_verdict",
]

EXCLUSION_FIELDS = [
    "strategy_id",
    "exclusion_type",
    "excluded_keys",
    "remaining_trades",
    "remaining_pnl_yen_100",
    "remaining_pf",
    "beats_baseline_pnl",
    "remains_positive",
]

TIMING_FIELDS = [
    "strategy_id",
    "symbol",
    "day",
    "entry_time",
    "exit_time",
    "pnl_yen_100",
    "minutes_from_open",
    "previous_day_high_distance_pct",
    "intraday_high_before_entry",
    "day_high_update_count_before_entry",
    "entry_is_first_breakout",
    "entry_is_late_breakout",
    "high_update_continues_after_entry",
    "time_to_next_high_update_min",
    "mfe_pct",
    "mae_pct",
    "mfe_mae_ratio",
    "timing_class",
]

WIN_LOSS_FIELDS = [
    "bucket",
    "trade_count",
    "median_minutes_from_open",
    "median_mfe_pct",
    "median_mae_pct",
    "median_mfe_mae_ratio",
    "median_entry_rsi",
    "median_entry_stoch_k",
    "median_entry_vwap_distance_pct",
    "median_entry_adx",
    "median_entry_volume_ratio",
    "median_pnl_yen_100",
]

OVERLAP_FIELDS = [
    "bucket",
    "trade_count",
    "total_pnl_yen_100",
    "profit_factor",
    "win_rate",
    "share_of_b005_pnl_pct",
]


def _float(v: Any) -> float:
    try:
        if v is None or v == "":
            return 0.0
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _f(v: Optional[float]) -> float:
    return float(v) if v is not None else float("nan")


def _trade_rows_from_state(state: Any, strategy_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for log in state.trade_log:
        if not log.get("exit_time"):
            continue
        tr = log.get("trade") or log
        rows.append(
            {
                "strategy_id": strategy_id,
                "symbol": str(tr.get("symbol") or "").replace(".T", ""),
                "day": str(log.get("day") or tr.get("day") or "")[:8],
                "entry_time": tr.get("entry_time"),
                "exit_time": log.get("exit_time"),
                "entry_price": _float(tr.get("entry_price")),
                "exit_price": _float(tr.get("exit_price")),
                "pnl_yen_100": _float(log.get("pnl_yen")),
                "exit_reason": log.get("exit_reason"),
                "position_key": _position_key(tr),
            }
        )
    return rows


def _dependency_metrics(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    pnls = [_float(t.get("pnl_yen_100")) for t in trades]
    n = len(pnls)
    total = sum(pnls)
    wins = sorted([p for p in pnls if p > 0], reverse=True)
    gross = sum(wins)
    top1_t = round(wins[0] / gross * 100.0, 2) if wins and gross > 0 else 0.0
    top5_t = round(sum(wins[:5]) / gross * 100.0, 2) if wins and gross > 0 else 0.0
    top10_t = round(sum(wins[:10]) / gross * 100.0, 2) if wins and gross > 0 else 0.0

    sym_pnl: dict[str, float] = defaultdict(float)
    day_pnl: dict[str, float] = defaultdict(float)
    for t in trades:
        sym_pnl[str(t.get("symbol") or "").replace(".T", "")] += _float(t.get("pnl_yen_100"))
        day_pnl[str(t.get("day") or "")[:8]] += _float(t.get("pnl_yen_100"))
    sym_rank = sorted(sym_pnl.items(), key=lambda x: x[1], reverse=True)
    day_rank = sorted(day_pnl.items(), key=lambda x: x[1], reverse=True)
    top1_sym = round(sym_rank[0][1] / total * 100.0, 2) if total and sym_rank else 0.0
    top3_sym = round(sum(v for _, v in sym_rank[:3]) / total * 100.0, 2) if total and sym_rank else 0.0
    top1_day = round(day_rank[0][1] / total * 100.0, 2) if total and day_rank else 0.0
    top3_day = round(sum(v for _, v in day_rank[:3]) / total * 100.0, 2) if total and day_rank else 0.0

    single_sym = bool(sym_rank and top1_sym >= 40 and sym_rank[0][1] > 0)
    single_day = bool(day_rank and top1_day >= 35 and day_rank[0][1] > 0)
    trade_conc = top10_t >= 50.0
    fragile = single_sym or single_day or trade_conc
    return {
        "total_pnl_yen_100": round(total, 2),
        "profit_factor": _pf(pnls),
        "trades": n,
        "win_rate": round(sum(1 for p in pnls if p > 0) / n, 4) if n else 0.0,
        "avg_pnl_yen_100": round(total / n, 2) if n else 0.0,
        "top1_trade_profit_share_pct": top1_t,
        "top5_trade_profit_share_pct": top5_t,
        "top10_trade_profit_share_pct": top10_t,
        "top1_symbol_profit_share_pct": top1_sym,
        "top3_symbol_profit_share_pct": top3_sym,
        "top1_day_profit_share_pct": top1_day,
        "top3_day_profit_share_pct": top3_day,
        "single_symbol_dependency": single_sym,
        "single_day_dependency": single_day,
        "trade_concentration_dependency": trade_conc,
        "overall_verdict": "classic_candidate_fragile" if fragile else "classic_candidate_robust",
        "_sym_rank": sym_rank,
        "_day_rank": day_rank,
    }


def _exclusion_rows(
    strategy_id: str,
    trades: Sequence[Mapping[str, Any]],
    *,
    baseline_pnl: float,
) -> list[dict[str, Any]]:
    sym_pnl: dict[str, float] = defaultdict(float)
    day_pnl: dict[str, float] = defaultdict(float)
    for t in trades:
        sym_pnl[str(t.get("symbol") or "").replace(".T", "")] += _float(t.get("pnl_yen_100"))
        day_pnl[str(t.get("day") or "")[:8]] += _float(t.get("pnl_yen_100"))
    sym_rank = sorted(sym_pnl.items(), key=lambda x: x[1], reverse=True)
    day_rank = sorted(day_pnl.items(), key=lambda x: x[1], reverse=True)

    def _filter(exclude_sym: set[str], exclude_day: set[str]) -> list[dict[str, Any]]:
        return [
            t
            for t in trades
            if str(t.get("symbol") or "").replace(".T", "") not in exclude_sym
            and str(t.get("day") or "")[:8] not in exclude_day
        ]

    specs: list[tuple[str, set[str], set[str]]] = [
        ("top1_symbol", {sym_rank[0][0]} if sym_rank else set(), set()),
        ("top3_symbols", {s for s, _ in sym_rank[:3]}, set()),
        ("top5_symbols", {s for s, _ in sym_rank[:5]}, set()),
        (f"symbol_{SYMBOL_6976}", {SYMBOL_6976}, set()),
        ("top1_day", set(), {day_rank[0][0]} if day_rank else set()),
        ("top3_days", set(), {d for d, _ in day_rank[:3]}),
        (f"day_{DAY_615}", set(), {DAY_615}),
    ]
    rows: list[dict[str, Any]] = []
    for ex_type, ex_sym, ex_day in specs:
        rem = _filter(ex_sym, ex_day)
        pnls = [_float(t.get("pnl_yen_100")) for t in rem]
        pnl = round(sum(pnls), 2)
        rows.append(
            {
                "strategy_id": strategy_id,
                "exclusion_type": ex_type,
                "excluded_keys": ",".join(sorted(ex_sym | ex_day)),
                "remaining_trades": len(rem),
                "remaining_pnl_yen_100": pnl,
                "remaining_pf": _pf(pnls),
                "beats_baseline_pnl": pnl > baseline_pnl,
                "remains_positive": pnl > 0,
            }
        )
    return rows


def _bar_index_at(bars: Sequence[Bar1m], entry_time: datetime) -> Optional[int]:
    best_i: Optional[int] = None
    best_delta = timedelta(days=999)
    for i, bar in enumerate(bars):
        delta = abs(bar.ts - entry_time)
        if delta < best_delta:
            best_delta = delta
            best_i = i
    if best_i is None or best_delta > timedelta(minutes=2):
        return None
    return best_i


def _session_open_ts(day: str) -> datetime:
    return datetime.strptime(f"{day[:8]} 09:00:00", "%Y%m%d %H:%M:%S").replace(tzinfo=JST)


def _prev_day_high(
    bar_cache: Mapping[tuple[str, str], tuple[list[Bar1m], list[BarIndicatorRow]]],
    sym: str,
    day: str,
    days_sorted: Sequence[str],
) -> Optional[float]:
    sym_t = sym if sym.endswith(".T") else f"{sym}.T"
    d = day[:8]
    if d not in days_sorted:
        return None
    idx = days_sorted.index(d)
    if idx == 0:
        return None
    prev = days_sorted[idx - 1]
    cached = bar_cache.get((sym_t, prev))
    if not cached:
        return None
    bars, _ = cached
    windowed = [b for b in bars if _in_trading_window(b.ts)]
    if not windowed:
        return None
    return max(b.high for b in windowed)


def _high_update_stats(bars: Sequence[Bar1m], entry_i: int, exit_i: int) -> dict[str, Any]:
    if entry_i is None or entry_i < 1:
        return {}
    running_high = bars[0].high
    updates_before = 0
    for j in range(1, entry_i):
        if bars[j].high > running_high:
            updates_before += 1
            running_high = bars[j].high
    intraday_high_before = running_high
    entry_bar = bars[entry_i]
    entry_high = entry_bar.high
    first_breakout = updates_before == 0 and entry_bar.close > intraday_high_before

    continues = False
    ttn_min: Optional[float] = None
    peak = trough = entry_bar.close
    exit_i = min(exit_i, len(bars) - 1)
    for j in range(entry_i + 1, exit_i + 1):
        bar = bars[j]
        peak = max(peak, bar.high)
        trough = min(trough, bar.low)
        if bar.high > entry_high and not continues:
            continues = True
            ttn_min = (bar.ts - entry_bar.ts).total_seconds() / 60.0
    ent_px = entry_bar.close
    mfe = round((peak - ent_px) / ent_px * 100.0, 4) if ent_px > 0 else 0.0
    mae = round((trough - ent_px) / ent_px * 100.0, 4) if ent_px > 0 else 0.0
    ratio = round(mfe / abs(mae), 4) if mae < -1e-9 else (99.0 if mfe > 0 else 0.0)
    return {
        "intraday_high_before_entry": round(intraday_high_before, 4),
        "day_high_update_count_before_entry": updates_before,
        "entry_is_first_breakout": first_breakout,
        "high_update_continues_after_entry": continues,
        "time_to_next_high_update_min": round(ttn_min, 2) if ttn_min is not None else None,
        "mfe_pct": mfe,
        "mae_pct": mae,
        "mfe_mae_ratio": ratio,
    }


def _classify_timing(row: Mapping[str, Any]) -> str:
    mfe = _float(row.get("mfe_pct"))
    mae = _float(row.get("mae_pct"))
    ratio = _float(row.get("mfe_mae_ratio"))
    continues = bool(row.get("high_update_continues_after_entry"))
    late = bool(row.get("entry_is_late_breakout"))
    mins = _float(row.get("minutes_from_open"))
    updates = int(_float(row.get("day_high_update_count_before_entry")))

    if continues and ratio >= 1.2 and mfe > abs(mae):
        return "true_breakout"
    if late or (updates >= 3 and not continues):
        return "late_breakout"
    if ratio < 0.8 or (mae < -0.5 and mfe < 0.3):
        return "high_chase"
    return "noise"


def _timing_rows_for_b005(
    trades: Sequence[Mapping[str, Any]],
    bar_cache: Mapping[tuple[str, str], tuple[list[Bar1m], list[BarIndicatorRow]]],
    days_sorted: Sequence[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for t in trades:
        sym = str(t.get("symbol") or "").replace(".T", "")
        sym_t = f"{sym}.T"
        day = str(t.get("day") or "")[:8]
        ent = _parse_ts(str(t.get("entry_time") or ""))
        ex = _parse_ts(str(t.get("exit_time") or ""))
        if ent is None:
            continue
        cached = bar_cache.get((sym_t, day))
        if not cached:
            continue
        bars, ind_rows = cached
        ei = _bar_index_at(bars, ent)
        xi = _bar_index_at(bars, ex) if ex else len(bars) - 1
        if ei is None:
            continue
        stats = _high_update_stats(bars, ei, xi or ei)
        open_ts = _session_open_ts(day)
        mins_open = round((ent - open_ts).total_seconds() / 60.0, 2)
        prev_hi = _prev_day_high(bar_cache, sym_t, day, days_sorted)
        ent_px = _float(t.get("entry_price")) or bars[ei].close
        prev_dist = (
            round((ent_px - prev_hi) / prev_hi * 100.0, 4) if prev_hi and prev_hi > 0 else None
        )
        ind = ind_rows[ei].values
        vol_avg = statistics.mean(b.volume for b in bars[max(0, ei - 19) : ei + 1]) if ei >= 1 else 1.0
        vol_ratio = round(bars[ei].volume / vol_avg, 4) if vol_avg > 0 else 1.0
        vwap = _f(ind.get("VWAP"))
        vwap_dist = round((ent_px - vwap) / vwap * 100.0, 4) if vwap == vwap and vwap > 0 else None
        late = mins_open > 180 or int(stats.get("day_high_update_count_before_entry") or 0) >= 5
        row = {
            "strategy_id": STRATEGY_B005,
            "symbol": sym,
            "day": day,
            "entry_time": t.get("entry_time"),
            "exit_time": t.get("exit_time"),
            "pnl_yen_100": _float(t.get("pnl_yen_100")),
            "minutes_from_open": mins_open,
            "previous_day_high_distance_pct": prev_dist,
            "entry_is_late_breakout": late,
            "entry_rsi": ind.get("RSI14"),
            "entry_stoch_k": ind.get("STOCH_K"),
            "entry_vwap_distance_pct": vwap_dist,
            "entry_adx": ind.get("ADX"),
            "entry_volume_ratio": vol_ratio,
            **stats,
        }
        row["timing_class"] = _classify_timing(row)
        rows.append(row)
    return rows


def _win_loss_pattern_rows(timing_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    def _bucket(name: str, subset: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        if not subset:
            return {"bucket": name, "trade_count": 0}

        def med(field: str) -> Optional[float]:
            vals = [_float(r.get(field)) for r in subset if r.get(field) is not None]
            return round(statistics.median(vals), 4) if vals else None

        return {
            "bucket": name,
            "trade_count": len(subset),
            "median_minutes_from_open": med("minutes_from_open"),
            "median_mfe_pct": med("mfe_pct"),
            "median_mae_pct": med("mae_pct"),
            "median_mfe_mae_ratio": med("mfe_mae_ratio"),
            "median_entry_rsi": med("entry_rsi"),
            "median_entry_stoch_k": med("entry_stoch_k"),
            "median_entry_vwap_distance_pct": med("entry_vwap_distance_pct"),
            "median_entry_adx": med("entry_adx"),
            "median_entry_volume_ratio": med("entry_volume_ratio"),
            "median_pnl_yen_100": med("pnl_yen_100"),
        }

    winners = [r for r in timing_rows if _float(r.get("pnl_yen_100")) > 0]
    losers = [r for r in timing_rows if _float(r.get("pnl_yen_100")) <= 0]
    return [_bucket("winners", winners), _bucket("losers", losers), _bucket("all", timing_rows)]


def _overlap_analysis(
    baseline_trades: Sequence[Mapping[str, Any]],
    b005_trades: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    def _ent_ts(t: Mapping[str, Any]) -> Optional[datetime]:
        return _parse_ts(str(t.get("entry_time") or ""))

    b005_by_symday: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    base_by_symday: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for t in b005_trades:
        b005_by_symday[(str(t.get("symbol")), str(t.get("day"))[:8])].append(dict(t))
    for t in baseline_trades:
        base_by_symday[(str(t.get("symbol")), str(t.get("day"))[:8])].append(dict(t))

    symdays_b = set(b005_by_symday)
    symdays_a = set(base_by_symday)
    same_symday = symdays_b & symdays_a

    def _match_tol(a: datetime, b: datetime, sec: float) -> bool:
        return abs((a - b).total_seconds()) <= sec

    both_keys: set[str] = set()
    b005_only: list[dict[str, Any]] = []
    base_only: list[dict[str, Any]] = list(baseline_trades)
    used_base: set[str] = set()

    for t in b005_trades:
        sym = str(t.get("symbol"))
        day = str(t.get("day"))[:8]
        et = _ent_ts(t)
        matched = False
        if et and (sym, day) in base_by_symday:
            for bt in base_by_symday[(sym, day)]:
                bt_et = _ent_ts(bt)
                if bt_et and _match_tol(et, bt_et, 60):
                    key = f"{sym}|{day}|{et.isoformat()}"
                    both_keys.add(key)
                    used_base.add(bt.get("position_key", _position_key(bt)))
                    matched = True
                    break
        if not matched:
            b005_only.append(dict(t))

    base_only = [t for t in baseline_trades if t.get("position_key") not in used_base]

    overlap_1m = len(both_keys)
    overlap_5m_count = 0
    for t in b005_trades:
        et = _ent_ts(t)
        if not et:
            continue
        sym = str(t.get("symbol"))
        day = str(t.get("day"))[:8]
        for bt in base_by_symday.get((sym, day), []):
            bt_et = _ent_ts(bt)
            if bt_et and _match_tol(et, bt_et, 300):
                overlap_5m_count += 1
                break

    b005_total = sum(_float(t.get("pnl_yen_100")) for t in b005_trades)

    def _bucket_row(name: str, subset: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        pnls = [_float(t.get("pnl_yen_100")) for t in subset]
        pnl = round(sum(pnls), 2)
        return {
            "bucket": name,
            "trade_count": len(subset),
            "total_pnl_yen_100": pnl,
            "profit_factor": _pf(pnls),
            "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4) if pnls else 0.0,
            "share_of_b005_pnl_pct": round(pnl / b005_total * 100.0, 2) if b005_total else 0.0,
        }

    both_trades = []
    for t in b005_trades:
        sym = str(t.get("symbol"))
        day = str(t.get("day"))[:8]
        et = _ent_ts(t)
        if not et:
            continue
        for bt in base_by_symday.get((sym, day), []):
            bt_et = _ent_ts(bt)
            if bt_et and _match_tol(et, bt_et, 60):
                both_trades.append(t)
                break

    rows = [
        _bucket_row("pbv2_only", base_only),
        _bucket_row("day_high_only", b005_only),
        _bucket_row("both", both_trades),
    ]
    meta = {
        "same_symbol_same_day_overlap_count": len(same_symday),
        "entry_time_pm1min_overlap_count": overlap_1m,
        "entry_time_pm5min_overlap_count": overlap_5m_count,
        "pbv2_only_pnl": rows[0]["total_pnl_yen_100"],
        "day_high_only_pnl": rows[1]["total_pnl_yen_100"],
        "both_pnl": rows[2]["total_pnl_yen_100"],
    }
    return rows, meta


@dataclass
class Phase515BJob:
    repo_root: Path
    parallel: bool = True
    max_workers: int = 4

    def run(self) -> dict[str, Any]:
        kabu = resolve_kabu_root(self.repo_root)
        reports = resolve_reports_dir(self.repo_root)
        max_workers = min(max(1, self.max_workers), MAX_WORKERS_CAP)

        baseline_state, baseline_met = _run_baseline_runtime(self.repo_root)
        baseline_trades = _trade_rows_from_state(baseline_state, BASELINE_STRATEGY_ID)
        baseline_pnl = _float(baseline_met.get("total_pnl_yen_100"))

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

        candidates_by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
        exit_cache: dict[tuple[str, str, str, float], dict[str, Any]] = {}

        jobs = [(spec, day) for spec in CLASSICAL_SPECS for day in days]

        def _job(spec: EntrySpec, day: str) -> tuple[str, str, list[dict[str, Any]]]:
            local: list[dict[str, Any]] = []
            for sym in universe:
                cached = bar_cache.get((sym, day))
                if not cached:
                    continue
                bars, ind_rows = cached
                for tr in scan_entry_pb_exit_day(
                    spec,
                    symbol=sym,
                    day=day,
                    bars=bars,
                    ind_rows=ind_rows,
                    price_idx=price_idx,
                ):
                    cache_key = (
                        str(tr.get("symbol")),
                        day,
                        str(tr.get("entry_time")),
                        round(_float(tr.get("entry_price")), 4),
                    )
                    if cache_key not in exit_cache:
                        exit_cache[cache_key] = tr
                    local.append({**tr, "strategy_id": spec.strategy_id})
            return spec.strategy_id, day, local

        if self.parallel:
            with ThreadPoolExecutor(max_workers=max_workers) as ex:
                futs = [ex.submit(_job, spec, day) for spec, day in jobs]
                for fut in as_completed(futs):
                    sid, _day, cands = fut.result()
                    candidates_by_id[sid].extend(cands)
        else:
            for spec, day in jobs:
                sid, _day, cands = _job(spec, day)
                candidates_by_id[sid].extend(cands)

        states: dict[str, Any] = {BASELINE_STRATEGY_ID: baseline_state}
        all_trades: dict[str, list[dict[str, Any]]] = {BASELINE_STRATEGY_ID: baseline_trades}
        descriptions = {
            BASELINE_STRATEGY_ID: "PBv2 Entry + PBv2 Exit",
            STRATEGY_B005: "day_high breakout + PBv2 Exit",
            STRATEGY_M002: "RSI>45 StochK>D+0 roc + PBv2 Exit",
        }

        for spec in CLASSICAL_SPECS:
            st = _simulate_precomputed_cap(
                candidates_by_id.get(spec.strategy_id, []),
                mode=f"phase515b_{spec.strategy_id}",
            )
            states[spec.strategy_id] = st
            all_trades[spec.strategy_id] = _trade_rows_from_state(st, spec.strategy_id)

        dep_rows: list[dict[str, Any]] = []
        exclusion_rows: list[dict[str, Any]] = []
        for sid in (BASELINE_STRATEGY_ID, STRATEGY_B005, STRATEGY_M002):
            trades = all_trades[sid]
            dep = _dependency_metrics(trades)
            met = _strategy_metrics_safe(
                states[sid],
                strategy_id=sid,
                entry_rule_id=descriptions[sid],
                exit_rule_id="PBv2_EXIT",
            )
            dep_rows.append(
                {
                    "strategy_id": sid,
                    "description": descriptions[sid],
                    "max_drawdown_yen_100": met.get("max_drawdown_yen_100"),
                    "total_pnl_yen_100": dep["total_pnl_yen_100"],
                    "profit_factor": dep["profit_factor"],
                    "trades": dep["trades"],
                    "win_rate": dep["win_rate"],
                    "avg_pnl_yen_100": dep["avg_pnl_yen_100"],
                    "top1_trade_profit_share_pct": dep["top1_trade_profit_share_pct"],
                    "top5_trade_profit_share_pct": dep["top5_trade_profit_share_pct"],
                    "top10_trade_profit_share_pct": dep["top10_trade_profit_share_pct"],
                    "top1_symbol_profit_share_pct": dep["top1_symbol_profit_share_pct"],
                    "top3_symbol_profit_share_pct": dep["top3_symbol_profit_share_pct"],
                    "top1_day_profit_share_pct": dep["top1_day_profit_share_pct"],
                    "top3_day_profit_share_pct": dep["top3_day_profit_share_pct"],
                    "single_symbol_dependency": dep["single_symbol_dependency"],
                    "single_day_dependency": dep["single_day_dependency"],
                    "trade_concentration_dependency": dep["trade_concentration_dependency"],
                    "overall_verdict": dep["overall_verdict"],
                }
            )
            exclusion_rows.extend(_exclusion_rows(sid, trades, baseline_pnl=baseline_pnl))

        b005_trades = all_trades[STRATEGY_B005]
        timing_rows = _timing_rows_for_b005(b005_trades, bar_cache, days)
        win_loss_rows = _win_loss_pattern_rows(timing_rows)
        overlap_rows, overlap_meta = _overlap_analysis(baseline_trades, b005_trades)

        class_counts: dict[str, int] = defaultdict(int)
        for r in timing_rows:
            class_counts[str(r.get("timing_class") or "noise")] += 1
        n_timing = len(timing_rows) or 1
        hi_cont = sum(1 for r in timing_rows if r.get("high_update_continues_after_entry"))

        b005_dep = next(r for r in dep_rows if r["strategy_id"] == STRATEGY_B005)
        b005_excl = {r["exclusion_type"]: r for r in exclusion_rows if r["strategy_id"] == STRATEGY_B005}

        winners = [r for r in timing_rows if _float(r.get("pnl_yen_100")) > 0]
        losers = [r for r in timing_rows if _float(r.get("pnl_yen_100")) <= 0]

        mandatory = {
            "1_b005_verdict": b005_dep.get("overall_verdict"),
            "2_beats_pbv2_after_6976_exclusion": b005_excl.get(f"symbol_{SYMBOL_6976}", {}).get(
                "beats_baseline_pnl", False
            ),
            "3_positive_after_top3_symbol_exclusion": b005_excl.get("top3_symbols", {}).get(
                "remains_positive", False
            ),
            "4_positive_after_top3_day_exclusion": b005_excl.get("top3_days", {}).get(
                "remains_positive", False
            ),
            "5_true_breakout_ratio": round(class_counts.get("true_breakout", 0) / n_timing, 4),
            "6_high_chase_ratio": round(class_counts.get("high_chase", 0) / n_timing, 4),
            "7_winner_common_traits": {
                "median_minutes_from_open": statistics.median(
                    [_float(r.get("minutes_from_open")) for r in winners]
                )
                if winners
                else None,
                "median_first_breakout_pct": round(
                    sum(1 for r in winners if r.get("entry_is_first_breakout")) / max(len(winners), 1), 4
                ),
                "high_update_continues_pct": round(
                    sum(1 for r in winners if r.get("high_update_continues_after_entry"))
                    / max(len(winners), 1),
                    4,
                ),
            },
            "8_loser_common_traits": {
                "median_minutes_from_open": statistics.median(
                    [_float(r.get("minutes_from_open")) for r in losers]
                )
                if losers
                else None,
                "late_breakout_pct": round(
                    sum(1 for r in losers if r.get("entry_is_late_breakout")) / max(len(losers), 1), 4
                ),
                "high_chase_class_pct": round(
                    sum(1 for r in losers if r.get("timing_class") == "high_chase")
                    / max(len(losers), 1),
                    4,
                ),
            },
            "9_same_edge_as_pbv2": overlap_meta.get("both_pnl", 0) > overlap_meta.get("day_high_only_pnl", 0),
            "10_deep_dive_worthy": bool(
                b005_dep.get("total_pnl_yen_100", 0) > baseline_pnl
                and overlap_meta.get("day_high_only_pnl", 0) > 0
            ),
            "timing_class_distribution": dict(class_counts),
            "high_update_continues_after_entry_rate": round(hi_cont / n_timing, 4),
            "late_breakout_ratio": round(class_counts.get("late_breakout", 0) / n_timing, 4),
            "overlap_meta": overlap_meta,
            "adopt_not_allowed": True,
        }

        return {
            "verdict": PHASE515B_VERDICT,
            "generated_at": _now_iso(),
            "period_start": PERIOD_START,
            "period_end": PERIOD_END,
            "dependency_summary": dep_rows,
            "exclusion_audit": exclusion_rows,
            "day_high_timing": timing_rows,
            "win_loss_pattern": win_loss_rows,
            "pbv2_overlap": overlap_rows,
            "mandatory_answers": mandatory,
            "baseline_pnl": baseline_pnl,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        paths = {
            "summary": reports / "phase515b_dependency_summary.csv",
            "exclusion": reports / "phase515b_exclusion_audit.csv",
            "timing": reports / "phase515b_day_high_timing.csv",
            "win_loss": reports / "phase515b_win_loss_pattern.csv",
            "overlap": reports / "phase515b_pbv2_overlap.csv",
            "report": reports / "phase515b_report.json",
            "docs": kabu / "docs" / "operations" / "phase515b_day_high_breakout_dependency_audit.md",
        }
        _write_csv(paths["summary"], DEP_SUMMARY_FIELDS, list(result.get("dependency_summary") or []))
        _write_csv(paths["exclusion"], EXCLUSION_FIELDS, list(result.get("exclusion_audit") or []))
        _write_csv(paths["timing"], TIMING_FIELDS, list(result.get("day_high_timing") or []))
        _write_csv(paths["win_loss"], WIN_LOSS_FIELDS, list(result.get("win_loss_pattern") or []))
        _write_csv(paths["overlap"], OVERLAP_FIELDS, list(result.get("pbv2_overlap") or []))
        paths["report"].write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        paths["docs"].write_text(_render_docs(result), encoding="utf-8")
        return paths


def _render_docs(result: Mapping[str, Any]) -> str:
    ma = result.get("mandatory_answers") or {}
    lines = [
        "# Phase515B — day_high Breakout Dependency Audit",
        "",
        f"**Verdict:** `{result.get('verdict')}`",
        "",
        "## Dependency summary",
        "",
        "| Strategy | PnL | PF | top1 sym% | top1 day% | Verdict |",
        "|----------|-----|-----|-----------|-----------|---------|",
    ]
    for r in result.get("dependency_summary") or []:
        lines.append(
            f"| {r.get('strategy_id')} | {r.get('total_pnl_yen_100')} | {r.get('profit_factor')} | "
            f"{r.get('top1_symbol_profit_share_pct')} | {r.get('top1_day_profit_share_pct')} | "
            f"{r.get('overall_verdict')} |"
        )
    lines.extend(
        [
            "",
            "## Mandatory answers",
            "",
            f"1. B005 robust/fragile: **{ma.get('1_b005_verdict')}**",
            f"2. Beats PBv2 after 6976 exclusion: **{ma.get('2_beats_pbv2_after_6976_exclusion')}**",
            f"3. Positive after top3 symbol exclusion: **{ma.get('3_positive_after_top3_symbol_exclusion')}**",
            f"4. Positive after top3 day exclusion: **{ma.get('4_positive_after_top3_day_exclusion')}**",
            f"5. true_breakout ratio: **{ma.get('5_true_breakout_ratio')}**",
            f"6. high_chase ratio: **{ma.get('6_high_chase_ratio')}**",
            f"7. Winner traits: **{ma.get('7_winner_common_traits')}**",
            f"8. Loser traits: **{ma.get('8_loser_common_traits')}**",
            f"9. Same edge as PBv2: **{ma.get('9_same_edge_as_pbv2')}**",
            f"10. Deep dive worthy: **{ma.get('10_deep_dive_worthy')}**",
        ]
    )
    return "\n".join(lines) + "\n"
