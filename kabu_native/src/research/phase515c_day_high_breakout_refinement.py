"""
Phase515C — day_high breakout refinement (research only).

Tests entry-time refinement filters on P515A_B_005 day_high breakout.
PBv2 Exit fixed. No adoption. No future-information filters.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import product
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _parse_ts
from research.phase451_entry_shape_tournament import JST, _build_price_index_to, _now_iso
from research.phase476_pre_breakout_gate_replay import _load_replay_pool
from research.phase488_current_runtime_replay import _filter_period
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
    ENTRY_COOLDOWN_SEC,
    MIN_BARS_WARMUP,
    _day_rows,
    _run_baseline_runtime,
    _simulate_precomputed_cap,
    _universe_symbols,
)
from research.phase510_classic_system_battle import _strategy_metrics_safe
from research.phase511_entry_exit_cross_battle import _apply_pb_exit_classical_entry
from research.phase515a_classic_entry_parameter_robustness import _day_high_break
from research.phase515b_day_high_breakout_dependency_audit import (
    DAY_615,
    SYMBOL_6976,
    _bar_index_at,
    _classify_timing,
    _high_update_stats,
    _session_open_ts,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

PHASE515C_VERDICT = "phase515c_day_high_breakout_refinement_done"
MAX_WORKERS_CAP = 4
MAX_REFINED = 150
BASE_ID = "P515A_B_005_BASE"

SUMMARY_FIELDS = [
    "strategy_id",
    "refinement_description",
    "total_pnl_yen_100",
    "profit_factor",
    "max_drawdown_yen_100",
    "trades",
    "win_rate",
    "avg_pnl_yen_100",
    "daily_stability_score",
    "positive_day_count",
    "negative_day_count",
    "true_breakout_ratio",
    "late_breakout_ratio",
    "high_chase_ratio",
    "high_update_continues_after_entry_ratio",
    "top1_symbol_profit_share_pct",
    "top3_symbol_profit_share_pct",
    "top1_day_profit_share_pct",
    "top3_day_profit_share_pct",
    "baseline_diff_pnl",
    "base_diff_pnl",
    "base_diff_true_breakout_ratio",
    "beats_baseline_pnl",
    "more_robust_than_base",
]

DAILY_FIELDS = ["strategy_id", "day", "trade_count", "total_pnl_yen_100", "profit_factor", "win_rate"]

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
]

ROBUSTNESS_FIELDS = [
    "strategy_id",
    "refinement_description",
    "exclusion_type",
    "remaining_pnl_yen_100",
    "remaining_pf",
    "remains_positive",
    "beats_baseline_pnl",
    "symbol_6976_share_pct",
    "top3_symbol_share_pct",
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


@dataclass(frozen=True)
class RefinementSpec:
    strategy_id: str
    rules: tuple[tuple[str, Any], ...]
    description: str

    @property
    def is_base(self) -> bool:
        return not self.rules


def _entry_context(
    bars: Sequence[Bar1m],
    ind_rows: Sequence[BarIndicatorRow],
    i: int,
) -> dict[str, Any]:
    ind = ind_rows[i].values
    bar = bars[i]
    running_high = bars[0].high
    updates = 0
    for j in range(1, i):
        if bars[j].high > running_high:
            updates += 1
            running_high = bars[j].high
    ent_px = bar.close
    vwap = _f(ind.get("VWAP"))
    vwap_dist = round((ent_px - vwap) / vwap * 100.0, 4) if vwap == vwap and vwap > 0 else 999.0
    vol_slice = bars[max(0, i - 19) : i + 1]
    vol_avg = statistics.mean(b.volume for b in vol_slice) if vol_slice else 1.0
    vol_ratio = round(bar.volume / vol_avg, 4) if vol_avg > 0 else 0.0
    return {
        "updates_before": updates,
        "vwap_dist_pct": vwap_dist,
        "vol_ratio": vol_ratio,
        "adx": ind.get("ADX"),
        "rsi": ind.get("RSI14"),
        "stoch_k": ind.get("STOCH_K"),
    }


def _passes_rules(ctx: Mapping[str, Any], rules: Sequence[tuple[str, Any]]) -> bool:
    for kind, val in rules:
        if kind == "r1_max" and int(ctx["updates_before"]) > int(val):
            return False
        if kind == "r2_max_vwap" and _float(ctx["vwap_dist_pct"]) > float(val):
            return False
        if kind == "r3_min_vol" and _float(ctx["vol_ratio"]) < float(val):
            return False
        if kind == "r4_min_adx" and _f(ctx.get("adx")) < float(val):
            return False
        if kind == "r4_max_adx" and _f(ctx.get("adx")) > float(val):
            return False
        if kind == "r6_max_rsi" and _f(ctx.get("rsi")) > float(val):
            return False
        if kind == "r7_max_stoch" and _f(ctx.get("stoch_k")) > float(val):
            return False
    return True


def _rule_label(rules: Sequence[tuple[str, Any]]) -> str:
    parts: list[str] = []
    for kind, val in rules:
        if kind == "r1_max":
            parts.append(f"updates<={val}")
        elif kind == "r2_max_vwap":
            parts.append(f"vwap_dist<={val}%")
        elif kind == "r3_min_vol":
            parts.append(f"vol_ratio>={val}")
        elif kind == "r4_min_adx":
            parts.append(f"ADX>={val}")
        elif kind == "r4_max_adx":
            parts.append(f"ADX<={val}")
        elif kind == "r6_max_rsi":
            parts.append(f"RSI<={val}")
        elif kind == "r7_max_stoch":
            parts.append(f"StochK<={val}")
    return " & ".join(parts) if parts else "day_high only"


def _build_refinement_grid() -> list[RefinementSpec]:
    specs: list[RefinementSpec] = [RefinementSpec(BASE_ID, (), "day_high only (BASE)")]
    idx = 0
    r1 = [("r1_max", v) for v in (3, 5, 8)]
    r2 = [("r2_max_vwap", v) for v in (3, 5, 8)]
    r3 = [("r3_min_vol", v) for v in (1.0, 1.2, 1.5)]
    r4 = [("r4_min_adx", v) for v in (15, 20, 25)] + [("r4_max_adx", 80)]
    r6 = [("r6_max_rsi", v) for v in (90, 85, 80)]
    r7 = [("r7_max_stoch", v) for v in (98, 95)]

    def _add(rules: tuple[tuple[str, Any], ...]) -> None:
        nonlocal idx
        idx += 1
        specs.append(
            RefinementSpec(
                strategy_id=f"P515C_R_{idx:03d}",
                rules=rules,
                description=f"day_high + {_rule_label(rules)}",
            )
        )

    for rules in (
        r1 + r2 + r3 + r4 + r6 + r7
    ):
        _add((rules,))

    for a, b in product(r1, r2):
        _add((a, b))
    for a, b in product(r1, r3):
        _add((a, b))
    for a, b in product(r1, r6):
        _add((a, b))
    for a, b in product(r2, r3):
        _add((a, b))
    for a, b in product(r2, r6):
        _add((a, b))
    for a, b in product(r3, r4):
        _add((a, b))

    return specs[: 1 + MAX_REFINED]


def scan_day_high_refined(
    spec: RefinementSpec,
    *,
    symbol: str,
    day: str,
    bars: Sequence[Bar1m],
    ind_rows: Sequence[BarIndicatorRow],
    price_idx: Mapping[tuple[str, str], list[tuple[datetime, float]]],
) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    last_entry: Optional[datetime] = None
    sym = symbol if symbol.endswith(".T") else f"{symbol}.T"
    for i in range(MIN_BARS_WARMUP, len(bars)):
        bar = bars[i]
        if not _in_trading_window(bar.ts):
            continue
        if last_entry and (bar.ts - last_entry).total_seconds() < ENTRY_COOLDOWN_SEC:
            continue
        if not _day_high_break(bars, i):
            continue
        ctx = _entry_context(bars, ind_rows, i)
        if not _passes_rules(ctx, spec.rules):
            continue
        candidate = {
            "symbol": sym,
            "day": day,
            "entry_time": bar.ts.isoformat(),
            "entry_price": bar.close,
        }
        applied = _apply_pb_exit_classical_entry(candidate, price_idx=price_idx)
        if applied:
            trades.append({**applied, "strategy_id": spec.strategy_id})
        last_entry = bar.ts
    return trades


def _trade_rows_from_log(state: Any, strategy_id: str) -> list[dict[str, Any]]:
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
            }
        )
    return rows


def _concentration(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    pnls = [_float(t.get("pnl_yen_100")) for t in trades]
    total = sum(pnls)
    sym_pnl: dict[str, float] = defaultdict(float)
    day_pnl: dict[str, float] = defaultdict(float)
    for t in trades:
        sym_pnl[str(t.get("symbol") or "")] += _float(t.get("pnl_yen_100"))
        day_pnl[str(t.get("day") or "")[:8]] += _float(t.get("pnl_yen_100"))
    sym_rank = sorted(sym_pnl.items(), key=lambda x: x[1], reverse=True)
    day_rank = sorted(day_pnl.items(), key=lambda x: x[1], reverse=True)
    top1_sym = round(sym_rank[0][1] / total * 100.0, 2) if total and sym_rank else 0.0
    top3_sym = round(sum(v for _, v in sym_rank[:3]) / total * 100.0, 2) if total and sym_rank else 0.0
    top1_day = round(day_rank[0][1] / total * 100.0, 2) if total and day_rank else 0.0
    top3_day = round(sum(v for _, v in day_rank[:3]) / total * 100.0, 2) if total and day_rank else 0.0
    sym6976 = round(sym_pnl.get(SYMBOL_6976, 0) / total * 100.0, 2) if total else 0.0
    fragile = top1_sym >= 40 or top1_day >= 35
    return {
        "top1_symbol_profit_share_pct": top1_sym,
        "top3_symbol_profit_share_pct": top3_sym,
        "top1_day_profit_share_pct": top1_day,
        "top3_day_profit_share_pct": top3_day,
        "symbol_6976_share_pct": sym6976,
        "fragile": fragile,
        "_sym_rank": sym_rank,
        "_day_rank": day_rank,
    }


def _timing_ratios(
    trades: Sequence[Mapping[str, Any]],
    bar_cache: Mapping[tuple[str, str], tuple[list[Bar1m], list[BarIndicatorRow]]],
) -> dict[str, float]:
    class_counts: dict[str, int] = defaultdict(int)
    hi_cont = 0
    n = 0
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
        late = mins_open > 180 or int(stats.get("day_high_update_count_before_entry") or 0) >= 5
        row = {
            "minutes_from_open": mins_open,
            "entry_is_late_breakout": late,
            **stats,
        }
        row["timing_class"] = _classify_timing(row)
        class_counts[str(row["timing_class"])] += 1
        if row.get("high_update_continues_after_entry"):
            hi_cont += 1
        n += 1
    denom = max(n, 1)
    return {
        "true_breakout_ratio": round(class_counts.get("true_breakout", 0) / denom, 4),
        "late_breakout_ratio": round(class_counts.get("late_breakout", 0) / denom, 4),
        "high_chase_ratio": round(class_counts.get("high_chase", 0) / denom, 4),
        "high_update_continues_after_entry_ratio": round(hi_cont / denom, 4),
        "_timing_n": n,
    }


def _exclusion_for_top(
    strategy_id: str,
    trades: Sequence[Mapping[str, Any]],
    conc: Mapping[str, Any],
    *,
    baseline_pnl: float,
) -> list[dict[str, Any]]:
    sym_rank = list(conc.get("_sym_rank") or [])
    day_rank = list(conc.get("_day_rank") or [])

    def _rem(ex_sym: set[str], ex_day: set[str]) -> list[dict[str, Any]]:
        return [
            t
            for t in trades
            if str(t.get("symbol") or "") not in ex_sym
            and str(t.get("day") or "")[:8] not in ex_day
        ]

    checks = [
        (f"symbol_{SYMBOL_6976}", {SYMBOL_6976}, set()),
        ("top3_symbols", {s for s, _ in sym_rank[:3]}, set()),
        ("top3_days", set(), {d for d, _ in day_rank[:3]}),
        (f"day_{DAY_615}", set(), {DAY_615}),
    ]
    rows: list[dict[str, Any]] = []
    for ex_type, ex_s, ex_d in checks:
        rem = _rem(ex_s, ex_d)
        pnls = [_float(t.get("pnl_yen_100")) for t in rem]
        pnl = round(sum(pnls), 2)
        rows.append(
            {
                "strategy_id": strategy_id,
                "refinement_description": "",
                "exclusion_type": ex_type,
                "remaining_pnl_yen_100": pnl,
                "remaining_pf": _pf(pnls),
                "remains_positive": pnl > 0,
                "beats_baseline_pnl": pnl > baseline_pnl,
                "symbol_6976_share_pct": conc.get("symbol_6976_share_pct"),
                "top3_symbol_share_pct": conc.get("top3_symbol_profit_share_pct"),
            }
        )
    return rows


def _mandatory_answers(
    summary_rows: Sequence[Mapping[str, Any]],
    base_row: Mapping[str, Any],
    baseline_pnl: float,
    robustness_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    refined = [r for r in summary_rows if r.get("strategy_id") != BASELINE_STRATEGY_ID and r.get("strategy_id") != BASE_ID]
    base_pnl = _float(base_row.get("total_pnl_yen_100"))
    base_tb = _float(base_row.get("true_breakout_ratio"))
    base_late = _float(base_row.get("late_breakout_ratio"))
    base_chase = _float(base_row.get("high_chase_ratio"))
    base_top1_sym = _float(base_row.get("top1_symbol_profit_share_pct"))

    beat_baseline = [r["strategy_id"] for r in refined if _float(r.get("total_pnl_yen_100")) > baseline_pnl]
    more_robust = [
        r["strategy_id"]
        for r in refined
        if r.get("more_robust_than_base")
    ]
    improved_tb = [r for r in refined if _float(r.get("true_breakout_ratio")) > base_tb]
    reduced_late = [r for r in refined if _float(r.get("late_breakout_ratio")) < base_late]
    reduced_chase = [r for r in refined if _float(r.get("high_chase_ratio")) < base_chase]
    reduced_6976 = [
        r for r in refined if _float(r.get("top1_symbol_profit_share_pct")) < base_top1_sym
    ]

    rob_by_id = defaultdict(list)
    for row in robustness_rows:
        rob_by_id[row["strategy_id"]].append(row)

    top3_sym_pos = [
        sid
        for sid, rows in rob_by_id.items()
        if any(r["exclusion_type"] == "top3_symbols" and r["remains_positive"] for r in rows)
    ]
    top3_day_pos = [
        sid
        for sid, rows in rob_by_id.items()
        if any(r["exclusion_type"] == "top3_days" and r["remains_positive"] for r in rows)
    ]

    best = max(refined, key=lambda r: (_float(r.get("true_breakout_ratio")), _float(r.get("total_pnl_yen_100"))), default={})

    return {
        "1_refined_beats_pbv2": beat_baseline[:15],
        "2_more_robust_than_base": more_robust[:15],
        "3_true_breakout_improved_count": len(improved_tb),
        "3_true_breakout_improved_ids": [r["strategy_id"] for r in sorted(improved_tb, key=lambda x: -_float(x.get("true_breakout_ratio")))][:10],
        "4_late_breakout_reduced_count": len(reduced_late),
        "5_high_chase_reduced_count": len(reduced_chase),
        "6_symbol_6976_dependency_reduced_count": len(reduced_6976),
        "7_top3_symbol_positive_candidates": top3_sym_pos[:10],
        "8_top3_day_positive_candidates": top3_day_pos[:10],
        "9_best_refinement": {
            "strategy_id": best.get("strategy_id"),
            "description": best.get("refinement_description"),
            "pnl": best.get("total_pnl_yen_100"),
            "true_breakout_ratio": best.get("true_breakout_ratio"),
        },
        "10_continue_research": bool(improved_tb) or bool(beat_baseline),
        "base_metrics": {
            "pnl": base_pnl,
            "true_breakout_ratio": base_tb,
            "late_breakout_ratio": base_late,
            "high_chase_ratio": base_chase,
            "top1_symbol_share": base_top1_sym,
        },
        "adopt_not_allowed": True,
    }


@dataclass
class Phase515CJob:
    repo_root: Path
    parallel: bool = True
    max_workers: int = 4

    def run(self) -> dict[str, Any]:
        specs = _build_refinement_grid()
        kabu = resolve_kabu_root(self.repo_root)
        reports = resolve_reports_dir(self.repo_root)
        max_workers = min(max(1, self.max_workers), MAX_WORKERS_CAP)

        baseline_state, baseline_met = _run_baseline_runtime(self.repo_root)
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
        jobs = [(spec, day) for spec in specs for day in days]

        def _job(spec: RefinementSpec, day: str) -> tuple[str, list[dict[str, Any]]]:
            local: list[dict[str, Any]] = []
            for sym in universe:
                cached = bar_cache.get((sym, day))
                if not cached:
                    continue
                bars, ind_rows = cached
                local.extend(
                    scan_day_high_refined(
                        spec,
                        symbol=sym,
                        day=day,
                        bars=bars,
                        ind_rows=ind_rows,
                        price_idx=price_idx,
                    )
                )
            return spec.strategy_id, local

        if self.parallel:
            with ThreadPoolExecutor(max_workers=max_workers) as ex:
                futs = [ex.submit(_job, spec, day) for spec, day in jobs]
                for fut in as_completed(futs):
                    sid, cands = fut.result()
                    candidates_by_id[sid].extend(cands)
        else:
            for spec, day in jobs:
                sid, cands = _job(spec, day)
                candidates_by_id[sid].extend(cands)

        summary_rows: list[dict[str, Any]] = []
        daily_rows: list[dict[str, Any]] = []
        trade_rows: list[dict[str, Any]] = []
        states: dict[str, Any] = {}

        # Baseline row in summary for reference
        base_trades_bl = _trade_rows_from_log(baseline_state, BASELINE_STRATEGY_ID)
        bl_conc = _concentration(base_trades_bl)
        bl_timing = _timing_ratios(base_trades_bl, bar_cache)
        summary_rows.append(
            {
                "strategy_id": BASELINE_STRATEGY_ID,
                "refinement_description": "PBv2 Entry + PBv2 Exit",
                **baseline_met,
                **bl_conc,
                **bl_timing,
                "base_diff_pnl": 0.0,
                "base_diff_true_breakout_ratio": 0.0,
                "beats_baseline_pnl": True,
                "more_robust_than_base": False,
            }
        )

        for spec in specs:
            st = _simulate_precomputed_cap(
                candidates_by_id.get(spec.strategy_id, []),
                mode=f"phase515c_{spec.strategy_id}",
            )
            states[spec.strategy_id] = st
            trades = _trade_rows_from_log(st, spec.strategy_id)
            conc = _concentration(trades)
            timing = _timing_ratios(trades, bar_cache)
            met = _strategy_metrics_safe(
                st,
                strategy_id=spec.strategy_id,
                entry_rule_id=spec.description,
                exit_rule_id="PBv2_EXIT",
                baseline=baseline_met,
            )
            trade_rows.extend(trades)
            for dr in _day_rows(st, spec.strategy_id):
                daily_rows.append({"strategy_id": spec.strategy_id, **{k: v for k, v in dr.items() if k != "strategy_id"}})
            summary_rows.append(
                {
                    "strategy_id": spec.strategy_id,
                    "refinement_description": spec.description,
                    "total_pnl_yen_100": met.get("total_pnl_yen_100"),
                    "profit_factor": met.get("profit_factor"),
                    "max_drawdown_yen_100": met.get("max_drawdown_yen_100"),
                    "trades": met.get("trades"),
                    "win_rate": met.get("win_rate"),
                    "avg_pnl_yen_100": met.get("avg_pnl_yen_100"),
                    "daily_stability_score": met.get("daily_stability_score"),
                    "positive_day_count": met.get("positive_day_count"),
                    "negative_day_count": met.get("negative_day_count"),
                    "baseline_diff_pnl": met.get("baseline_diff_pnl"),
                    "beats_baseline_pnl": _float(met.get("total_pnl_yen_100")) > baseline_pnl,
                    **conc,
                    **timing,
                    "base_diff_pnl": None,
                    "base_diff_true_breakout_ratio": None,
                    "more_robust_than_base": False,
                }
            )

        base_row = next(r for r in summary_rows if r["strategy_id"] == BASE_ID)
        base_pnl = _float(base_row.get("total_pnl_yen_100"))
        base_tb = _float(base_row.get("true_breakout_ratio"))
        base_fragile = base_row.get("fragile", True)
        base_top1_sym = _float(base_row.get("top1_symbol_profit_share_pct"))

        for row in summary_rows:
            if row["strategy_id"] in (BASELINE_STRATEGY_ID,):
                continue
            row["base_diff_pnl"] = round(_float(row.get("total_pnl_yen_100")) - base_pnl, 2)
            row["base_diff_true_breakout_ratio"] = round(
                _float(row.get("true_breakout_ratio")) - base_tb, 4
            )
            less_fragile = (
                _float(row.get("top1_symbol_profit_share_pct")) < base_top1_sym
                and _float(row.get("top1_day_profit_share_pct"))
                <= _float(base_row.get("top1_day_profit_share_pct"))
            )
            row["more_robust_than_base"] = bool(
                not row.get("fragile")
                or (less_fragile and _float(row.get("true_breakout_ratio")) >= base_tb)
            )

        refined_rows = [r for r in summary_rows if r["strategy_id"] not in (BASELINE_STRATEGY_ID,)]
        top_candidates = sorted(
            [r for r in refined_rows if r["strategy_id"] != BASE_ID],
            key=lambda r: (
                _float(r.get("true_breakout_ratio")),
                _float(r.get("total_pnl_yen_100")),
            ),
            reverse=True,
        )[:15]
        top_ids = {r["strategy_id"] for r in top_candidates} | {BASE_ID}

        robustness_rows: list[dict[str, Any]] = []
        for sid in top_ids:
            spec = next(s for s in specs if s.strategy_id == sid)
            trades = [t for t in trade_rows if t["strategy_id"] == sid]
            conc = _concentration(trades)
            for ex in _exclusion_for_top(sid, trades, conc, baseline_pnl=baseline_pnl):
                ex["refinement_description"] = spec.description
                robustness_rows.append(ex)

        mandatory = _mandatory_answers(summary_rows, base_row, baseline_pnl, robustness_rows)

        return {
            "verdict": PHASE515C_VERDICT,
            "generated_at": _now_iso(),
            "period_start": PERIOD_START,
            "period_end": PERIOD_END,
            "refined_count": len(specs) - 1,
            "grid_count": len(specs),
            "summary_rows": summary_rows,
            "daily_rows": daily_rows,
            "trade_rows": trade_rows,
            "robustness_rows": robustness_rows,
            "mandatory_answers": mandatory,
            "baseline": baseline_met,
            "base_row": base_row,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        paths = {
            "summary": reports / "phase515c_refinement_summary.csv",
            "daily": reports / "phase515c_refinement_daily.csv",
            "trades": reports / "phase515c_refinement_trades.csv",
            "robustness": reports / "phase515c_refinement_robustness.csv",
            "report": reports / "phase515c_report.json",
            "docs": kabu / "docs" / "operations" / "phase515c_day_high_breakout_refinement.md",
        }
        _write_csv(paths["summary"], SUMMARY_FIELDS, list(result.get("summary_rows") or []))
        _write_csv(paths["daily"], DAILY_FIELDS, list(result.get("daily_rows") or []))
        _write_csv(paths["trades"], TRADE_FIELDS, list(result.get("trade_rows") or []))
        _write_csv(paths["robustness"], ROBUSTNESS_FIELDS, list(result.get("robustness_rows") or []))
        paths["report"].write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        paths["docs"].write_text(_render_docs(result), encoding="utf-8")
        return paths


def _render_docs(result: Mapping[str, Any]) -> str:
    ma = result.get("mandatory_answers") or {}
    base = ma.get("base_metrics") or {}
    lines = [
        "# Phase515C — day_high Breakout Refinement",
        "",
        f"**Verdict:** `{result.get('verdict')}`",
        f"**Refined strategies:** {result.get('refined_count')}",
        "",
        f"**BASE true_breakout:** {base.get('true_breakout_ratio')}",
        f"**BASE PnL:** {base.get('pnl')}",
        "",
        "## Mandatory answers",
        "",
        f"1. Refined beats PBv2: **{ma.get('1_refined_beats_pbv2')}**",
        f"2. More robust than BASE: **{ma.get('2_more_robust_than_base')}**",
        f"3. true_breakout improved: **{ma.get('3_true_breakout_improved_count')}** strategies",
        f"4. late_breakout reduced: **{ma.get('4_late_breakout_reduced_count')}**",
        f"5. high_chase reduced: **{ma.get('5_high_chase_reduced_count')}**",
        f"6. 6976 dependency reduced: **{ma.get('6_symbol_6976_dependency_reduced_count')}**",
        f"7. top3 symbol positive: **{ma.get('7_top3_symbol_positive_candidates')}**",
        f"8. top3 day positive: **{ma.get('8_top3_day_positive_candidates')}**",
        f"9. Best refinement: **{ma.get('9_best_refinement')}**",
        f"10. Continue research: **{ma.get('10_continue_research')}**",
    ]
    return "\n".join(lines) + "\n"
