"""
Phase583 — Dynamic Only / Core Mix Universe Grid Search (research only).

Exhaustive grid over Core0/5/10/15 × Dynamic10–60 (total ≤60) to find profit-maximizing
universe composition. No Runtime or universe-generation logic changes.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv, read_jpx_sector_map
from research.phase382_capital_constrained_backtest import _parse_ts
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase451_entry_shape_tournament import JST, _now_iso
from research.phase524_live_reentry_guard_and_stop_low_mfe import _is_stop_low_mfe, _latest_live_day
from research.phase540_no_progress_mfe0_entry_quality import _is_mfe0, _mfe_pct
from research.phase541_guard_v2_full_period_validation import BIG_WINNER_MFE_PCT
from research.phase582_universe_optimization_study import (
    PERIOD_START,
    _chron_pnls,
    _discover_days,
    _filter_accepted,
    _filter_trades,
    _global_dynamic_rank,
    _load_day_accepted,
    _load_day_trades,
    _symbol_pnls,
    _universe_symbols_for_day,
)
from research.phase533_or_profit_source_audit import _num
from research.phase530_winner_capture_research import _sym_key
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from universe.core_watchlist import load_core_watchlist

PHASE583_VERDICT = "phase583_dynamic_only_core_mix_universe_grid_search_done"
BASELINE_CORE = 10
BASELINE_DYNAMIC = 40
BASELINE_ID = "C10_D40"
MAX_WORKERS = 4
BIG_LOSER_PNL = -3000.0

GRID_SUMMARY_FIELDS = [
    "universe_id",
    "universe_label",
    "category",
    "core_count",
    "dynamic_count",
    "total_symbols",
    "trades",
    "accepted",
    "pnl_yen_100",
    "profit_factor",
    "win_rate",
    "max_drawdown_yen_100",
    "avg_pnl_yen_100",
    "stop_hit_count",
    "stop_low_mfe_count",
    "mfe0_count",
    "big_winner_count",
    "big_loser_count",
    "composite_score",
]

DYNAMIC_CURVE_FIELDS = [
    "dynamic_count",
    "universe_id",
    "pnl_yen_100",
    "profit_factor",
    "trades",
    "max_drawdown_yen_100",
    "top3_pnl_share_pct",
    "delta_pf_vs_prev",
    "delta_pnl_vs_prev",
    "rank_by_pnl",
    "rank_by_pf",
    "rank_by_score",
]

CORE_MIX_CURVE_FIELDS = [
    "core_count",
    "dynamic_count",
    "universe_id",
    "total_symbols",
    "pnl_yen_100",
    "profit_factor",
    "trades",
    "max_drawdown_yen_100",
    "top3_pnl_share_pct",
    "delta_pf_vs_core0",
    "delta_pnl_vs_core0",
    "rank_by_pnl",
    "rank_by_pf",
    "rank_by_score",
]

CORE_VS_DYNAMIC_FIELDS = [
    "comparison",
    "universe_id",
    "core_count",
    "dynamic_count",
    "total_symbols",
    "pnl_yen_100",
    "profit_factor",
    "max_drawdown_yen_100",
    "daily_positive_rate",
    "top3_pnl_share_pct",
    "notes",
]

STABILITY_FIELDS = [
    "universe_id",
    "category",
    "total_symbols",
    "daily_positive_rate",
    "trading_days",
    "positive_days",
    "negative_days",
    "worst_day",
    "worst_day_pnl",
    "best_day",
    "best_day_pnl",
    "max_daily_loss",
    "daily_pnl_volatility",
    "improvement_day_rate_vs_baseline",
    "am_pnl_yen_100",
    "pm_pnl_yen_100",
    "am_pf",
    "pm_pf",
    "stability_score",
]

DEPENDENCY_FIELDS = [
    "universe_id",
    "total_symbols",
    "top1_pnl_share_pct",
    "top3_pnl_share_pct",
    "top5_pnl_share_pct",
    "high_price_pnl_share_pct",
    "sector_hhi",
    "top1_symbol",
    "pf_ex_top1_symbol",
    "pf_ex_top3_symbols",
]

MAX50_FIELDS = [
    "rank",
    "universe_id",
    "core_count",
    "dynamic_count",
    "total_symbols",
    "pnl_yen_100",
    "profit_factor",
    "max_drawdown_yen_100",
    "daily_positive_rate",
    "top3_pnl_share_pct",
    "high_price_pnl_share_pct",
    "composite_score",
    "runtime_candidate",
]


def _grid_specs() -> list[tuple[str, str, int, int, str]]:
    specs: list[tuple[str, str, int, int, str]] = []
    for core in (0, 5, 10, 15):
        max_dyn = 60 - core
        for dynamic in range(10, max_dyn + 1, 5):
            uid = f"C{core}_D{dynamic}"
            if core == 0:
                label = f"Dynamic{dynamic}_only"
                category = "dynamic_only"
            else:
                label = f"Core{core}+Dynamic{dynamic}"
                category = "core_mix"
            specs.append((uid, label, core, dynamic, category))
    return specs


def _composite_score(pf: float, pnl: float) -> float:
    return round(float(pf or 0) * 0.6 + (float(pnl or 0) / 100_000.0) * 0.4, 6)


def _is_big_winner(trade: Mapping[str, Any]) -> bool:
    pnl = _num(trade.get("pnl_yen_100"))
    return pnl > 0 and _mfe_pct(trade) >= BIG_WINNER_MFE_PCT


def _is_big_loser(trade: Mapping[str, Any]) -> bool:
    return _num(trade.get("pnl_yen_100")) <= BIG_LOSER_PNL


def _sector_hhi(trades: Sequence[Mapping[str, Any]], sector_map: Mapping[str, str]) -> float:
    sector_pnl: dict[str, float] = defaultdict(float)
    for t in trades:
        sym = _sym_key(t.get("symbol"))
        sector_pnl[sector_map.get(sym, "unknown")] += _num(t.get("pnl_yen_100"))
    total = sum(abs(v) for v in sector_pnl.values()) or 1.0
    shares = [abs(v) / total for v in sector_pnl.values()]
    return round(sum(s * s for s in shares), 4)


def _build_universe_cache(
    *,
    repo_root: Path,
    days: Sequence[str],
    reports_dir: Path,
    all_trades: Sequence[Mapping[str, Any]],
    specs: Sequence[tuple[str, str, int, int, str]],
) -> dict[tuple[int, int], dict[str, set[str]]]:
    try:
        core_raw, _ = load_core_watchlist(repo_root)
    except Exception:
        core_raw = []
    ordered_core = [_sym_key(s) for s in core_raw if _sym_key(s)]
    if not ordered_core:
        all_syms = {_sym_key(t.get("symbol")) for t in all_trades if _sym_key(t.get("symbol"))}
        ordered_core = sorted(all_syms)[:10]
    core_set = set(ordered_core)
    fallback_rank = _global_dynamic_rank(all_trades, core_set)

    unique_pairs = {(core, dynamic) for _, _, core, dynamic, _ in specs}
    cache: dict[tuple[int, int], dict[str, set[str]]] = {}
    for core_slots, dynamic_slots in sorted(unique_pairs):
        by_day: dict[str, set[str]] = {}
        for day in days:
            by_day[day] = _universe_symbols_for_day(
                day,
                core_slots=core_slots,
                dynamic_slots=dynamic_slots,
                reports_dir=reports_dir,
                ordered_core=ordered_core,
                fallback_rank=fallback_rank,
                days=days,
            )
        cache[(core_slots, dynamic_slots)] = by_day
    return cache


def _daily_pnls(trades: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    for t in trades:
        out[str(t.get("day") or "")[:8]] += _num(t.get("pnl_yen_100"))
    return dict(out)


def _pf_excluding_symbols(
    trades: Sequence[Mapping[str, Any]],
    exclude: set[str],
) -> float:
    pnls = [_num(t.get("pnl_yen_100")) for t in trades if _sym_key(t.get("symbol")) not in exclude]
    return round(_pf(pnls) or 0.0, 4) if pnls else 0.0


def _dependency_metrics(
    trades: Sequence[Mapping[str, Any]],
    sector_map: Mapping[str, str],
) -> dict[str, Any]:
    sym_pnls = _symbol_pnls(trades)
    total = sum(sum(v) for v in sym_pnls.values()) or 1.0
    ranked = sorted(((s, sum(v)) for s, v in sym_pnls.items()), key=lambda x: x[1], reverse=True)
    top1_sym = ranked[0][0] if ranked else ""
    top3_syms = {s for s, _ in ranked[:3]}
    top5_syms = {s for s, _ in ranked[:5]}
    top1 = ranked[0][1] if ranked else 0.0
    top3 = sum(p for _, p in ranked[:3])
    top5 = sum(p for _, p in ranked[:5])
    high_price = sum(
        _num(t.get("pnl_yen_100"))
        for t in trades
        if str(t.get("price_band") or "") in ("5000_10000", "gte_10000")
    )
    return {
        "top1_pnl_share_pct": round(100.0 * top1 / total, 2),
        "top3_pnl_share_pct": round(100.0 * top3 / total, 2),
        "top5_pnl_share_pct": round(100.0 * top5 / total, 2),
        "high_price_pnl_share_pct": round(100.0 * high_price / total, 2),
        "sector_hhi": _sector_hhi(trades, sector_map),
        "top1_symbol": top1_sym,
        "pf_ex_top1_symbol": _pf_excluding_symbols(trades, {top1_sym} if top1_sym else set()),
        "pf_ex_top3_symbols": _pf_excluding_symbols(trades, top3_syms),
    }


def _stability_metrics(
    trades: Sequence[Mapping[str, Any]],
    baseline_daily: Mapping[str, float],
) -> dict[str, Any]:
    daily = _daily_pnls(trades)
    vals = list(daily.values())
    pos = sum(1 for v in vals if v > 0)
    neg = sum(1 for v in vals if v < 0)
    trading_days = len(vals)
    worst_day = min(daily, key=lambda d: daily[d]) if daily else ""
    best_day = max(daily, key=lambda d: daily[d]) if daily else ""
    worst_pnl = daily.get(worst_day, 0.0)
    best_pnl = daily.get(best_day, 0.0)
    vol = round(float(math.sqrt(sum((v - (sum(vals) / len(vals))) ** 2 for v in vals) / max(len(vals), 1))), 2) if vals else 0.0
    improve_days = sum(
        1 for d in set(daily) & set(baseline_daily) if daily[d] > baseline_daily[d]
    )
    compare_days = len(set(daily) & set(baseline_daily)) or 1
    am_trades = [t for t in trades if t.get("session_kind") == "am"]
    pm_trades = [t for t in trades if t.get("session_kind") == "pm"]
    am_pnls = [_num(t.get("pnl_yen_100")) for t in am_trades]
    pm_pnls = [_num(t.get("pnl_yen_100")) for t in pm_trades]
    dpr = round(pos / max(trading_days, 1), 4)
    stability_score = round(dpr * 0.4 + (1.0 - min(vol / 100_000.0, 1.0)) * 0.3 + (improve_days / compare_days) * 0.3, 4)
    return {
        "daily_positive_rate": dpr,
        "trading_days": trading_days,
        "positive_days": pos,
        "negative_days": neg,
        "worst_day": worst_day,
        "worst_day_pnl": round(worst_pnl, 2),
        "best_day": best_day,
        "best_day_pnl": round(best_pnl, 2),
        "max_daily_loss": round(worst_pnl, 2),
        "daily_pnl_volatility": vol,
        "improvement_day_rate_vs_baseline": round(improve_days / compare_days, 4),
        "am_pnl_yen_100": round(sum(am_pnls), 2),
        "pm_pnl_yen_100": round(sum(pm_pnls), 2),
        "am_pf": round(_pf(am_pnls) or 0.0, 4),
        "pm_pf": round(_pf(pm_pnls) or 0.0, 4),
        "stability_score": stability_score,
    }


@dataclass
class _VariantInput:
    uid: str
    label: str
    core: int
    dynamic: int
    category: str
    universe_by_day: Mapping[str, set[str]]
    all_trades: Sequence[Mapping[str, Any]]
    all_accepted: Sequence[Mapping[str, Any]]
    sector_map: Mapping[str, str]
    baseline_daily: Mapping[str, float]


def _evaluate_variant(inp: _VariantInput) -> dict[str, Any]:
    trades = _filter_trades(inp.all_trades, inp.universe_by_day)
    accepted = _filter_accepted(inp.all_accepted, inp.universe_by_day)
    pnls = [_num(t.get("pnl_yen_100")) for t in trades]
    total = round(sum(pnls), 2)
    chron = _chron_pnls(trades)
    pf = round(_pf(pnls) or 0.0, 4)
    wins = sum(1 for p in pnls if p > 0)
    dep = _dependency_metrics(trades, inp.sector_map)
    stab = _stability_metrics(trades, inp.baseline_daily)
    score = _composite_score(pf, total)

    summary = {
        "universe_id": inp.uid,
        "universe_label": inp.label,
        "category": inp.category,
        "core_count": inp.core,
        "dynamic_count": inp.dynamic,
        "total_symbols": inp.core + inp.dynamic,
        "trades": len(pnls),
        "accepted": accepted,
        "pnl_yen_100": total,
        "profit_factor": pf,
        "win_rate": round(wins / len(pnls), 4) if pnls else 0.0,
        "max_drawdown_yen_100": round(_max_drawdown_yen(chron) if chron else 0.0, 2),
        "avg_pnl_yen_100": round(total / len(pnls), 2) if pnls else 0.0,
        "stop_hit_count": sum(1 for t in trades if str(t.get("exit_reason") or "").lower() == "stop_hit"),
        "stop_low_mfe_count": sum(1 for t in trades if _is_stop_low_mfe(t)),
        "mfe0_count": sum(1 for t in trades if _is_mfe0(t)),
        "big_winner_count": sum(1 for t in trades if _is_big_winner(t)),
        "big_loser_count": sum(1 for t in trades if _is_big_loser(t)),
        "composite_score": score,
    }

    return {
        "summary": summary,
        "trades": trades,
        "dependency": {
            "universe_id": inp.uid,
            "total_symbols": inp.core + inp.dynamic,
            **dep,
        },
        "stability": {
            "universe_id": inp.uid,
            "category": inp.category,
            "total_symbols": inp.core + inp.dynamic,
            **stab,
        },
    }


def _runtime_candidate(
    row: Mapping[str, Any],
    baseline: Mapping[str, Any],
    dep: Mapping[str, Any],
    stab: Mapping[str, Any],
    baseline_dep: Mapping[str, Any],
    baseline_stab: Mapping[str, Any],
) -> bool:
    return (
        float(row.get("pnl_yen_100") or 0) > float(baseline.get("pnl_yen_100") or 0)
        and float(row.get("profit_factor") or 0) > float(baseline.get("profit_factor") or 0)
        and float(row.get("max_drawdown_yen_100") or 0) <= float(baseline.get("max_drawdown_yen_100") or 0)
        and float(stab.get("daily_positive_rate") or 0) >= float(baseline_stab.get("daily_positive_rate") or 0)
        and float(dep.get("top3_pnl_share_pct") or 999) <= float(baseline_dep.get("top3_pnl_share_pct") or 999)
        and float(dep.get("high_price_pnl_share_pct") or 999) <= float(baseline_dep.get("high_price_pnl_share_pct") or 999)
    )


@dataclass
class Phase583Job:
    repo_root: Path
    workers: int = MAX_WORKERS
    period_end: Optional[str] = None

    def run(self) -> dict[str, Any]:
        specs = _grid_specs()
        days = _discover_days(self.repo_root)
        end = self.period_end or _latest_live_day(self.repo_root)
        days = [d for d in days if d <= end]
        kabu = resolve_kabu_root(self.repo_root)
        reports_dir = resolve_reports_dir(self.repo_root)
        sector_map = read_jpx_sector_map(kabu)

        all_trades: list[dict[str, Any]] = []
        all_accepted: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            for fut in as_completed({ex.submit(_load_day_trades, self.repo_root, d): d for d in days}):
                all_trades.extend(fut.result())
            for fut in as_completed({ex.submit(_load_day_accepted, self.repo_root, d): d for d in days}):
                all_accepted.extend(fut.result())

        all_trades.sort(
            key=lambda t: _parse_ts(str(t.get("entry_time") or ""))
            or datetime.min.replace(tzinfo=JST)
        )

        universe_cache = _build_universe_cache(
            repo_root=self.repo_root,
            days=days,
            reports_dir=reports_dir,
            all_trades=all_trades,
            specs=specs,
        )

        baseline_map = universe_cache[(BASELINE_CORE, BASELINE_DYNAMIC)]
        baseline_trades = _filter_trades(all_trades, baseline_map)
        baseline_daily = _daily_pnls(baseline_trades)

        inputs = [
            _VariantInput(
                uid=uid,
                label=label,
                core=core,
                dynamic=dynamic,
                category=cat,
                universe_by_day=universe_cache[(core, dynamic)],
                all_trades=all_trades,
                all_accepted=all_accepted,
                sector_map=sector_map,
                baseline_daily=baseline_daily,
            )
            for uid, label, core, dynamic, cat in specs
        ]

        results: dict[str, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            futs = {ex.submit(_evaluate_variant, inp): inp.uid for inp in inputs}
            for fut in as_completed(futs):
                uid = futs[fut]
                results[uid] = fut.result()

        summary_rows = [results[s[0]]["summary"] for s in specs]
        dependency_rows = [results[s[0]]["dependency"] for s in specs]
        stability_rows = [results[s[0]]["stability"] for s in specs]

        baseline_summary = next(r for r in summary_rows if r["universe_id"] == BASELINE_ID)
        baseline_dep = next(r for r in dependency_rows if r["universe_id"] == BASELINE_ID)
        baseline_stab = next(r for r in stability_rows if r["universe_id"] == BASELINE_ID)

        # Dynamic-only curve
        dyn_only = [r for r in summary_rows if r["category"] == "dynamic_only"]
        dyn_only.sort(key=lambda r: r["dynamic_count"])
        dyn_rank_pnl = {r["universe_id"]: i + 1 for i, r in enumerate(sorted(dyn_only, key=lambda x: -float(x["pnl_yen_100"])))}
        dyn_rank_pf = {r["universe_id"]: i + 1 for i, r in enumerate(sorted(dyn_only, key=lambda x: -float(x["profit_factor"])))}
        dyn_rank_score = {r["universe_id"]: i + 1 for i, r in enumerate(sorted(dyn_only, key=lambda x: -float(x["composite_score"])))}
        dynamic_curve_rows: list[dict[str, Any]] = []
        prev_dyn: Optional[dict[str, Any]] = None
        for r in dyn_only:
            dep = next(d for d in dependency_rows if d["universe_id"] == r["universe_id"])
            dynamic_curve_rows.append(
                {
                    "dynamic_count": r["dynamic_count"],
                    "universe_id": r["universe_id"],
                    "pnl_yen_100": r["pnl_yen_100"],
                    "profit_factor": r["profit_factor"],
                    "trades": r["trades"],
                    "max_drawdown_yen_100": r["max_drawdown_yen_100"],
                    "top3_pnl_share_pct": dep["top3_pnl_share_pct"],
                    "delta_pf_vs_prev": round(float(r["profit_factor"]) - float(prev_dyn["profit_factor"]), 4) if prev_dyn else 0.0,
                    "delta_pnl_vs_prev": round(float(r["pnl_yen_100"]) - float(prev_dyn["pnl_yen_100"]), 2) if prev_dyn else 0.0,
                    "rank_by_pnl": dyn_rank_pnl[r["universe_id"]],
                    "rank_by_pf": dyn_rank_pf[r["universe_id"]],
                    "rank_by_score": dyn_rank_score[r["universe_id"]],
                }
            )
            prev_dyn = r

        # Core mix curve — best per core count at each dynamic level + overall
        core_mix = [r for r in summary_rows if r["category"] == "core_mix"]
        core_mix_curve_rows: list[dict[str, Any]] = []
        for core in (5, 10, 15):
            subset = [r for r in core_mix if r["core_count"] == core]
            subset.sort(key=lambda r: r["dynamic_count"])
            core0_by_dyn = {r["dynamic_count"]: r for r in summary_rows if r["core_count"] == 0}
            prev_c: Optional[dict[str, Any]] = None
            for r in subset:
                dep = next(d for d in dependency_rows if d["universe_id"] == r["universe_id"])
                c0 = core0_by_dyn.get(r["dynamic_count"])
                core_mix_curve_rows.append(
                    {
                        "core_count": core,
                        "dynamic_count": r["dynamic_count"],
                        "universe_id": r["universe_id"],
                        "total_symbols": r["total_symbols"],
                        "pnl_yen_100": r["pnl_yen_100"],
                        "profit_factor": r["profit_factor"],
                        "trades": r["trades"],
                        "max_drawdown_yen_100": r["max_drawdown_yen_100"],
                        "top3_pnl_share_pct": dep["top3_pnl_share_pct"],
                        "delta_pf_vs_core0": round(float(r["profit_factor"]) - float(c0["profit_factor"]), 4) if c0 else 0.0,
                        "delta_pnl_vs_core0": round(float(r["pnl_yen_100"]) - float(c0["pnl_yen_100"]), 2) if c0 else 0.0,
                        "rank_by_pnl": 0,
                        "rank_by_pf": 0,
                        "rank_by_score": 0,
                    }
                )
                prev_c = r
        mix_rank_pnl = {r["universe_id"]: i + 1 for i, r in enumerate(sorted(core_mix, key=lambda x: -float(x["pnl_yen_100"])))}
        mix_rank_pf = {r["universe_id"]: i + 1 for i, r in enumerate(sorted(core_mix, key=lambda x: -float(x["profit_factor"])))}
        mix_rank_score = {r["universe_id"]: i + 1 for i, r in enumerate(sorted(core_mix, key=lambda x: -float(x["composite_score"])))}
        for row in core_mix_curve_rows:
            row["rank_by_pnl"] = mix_rank_pnl.get(row["universe_id"], 0)
            row["rank_by_pf"] = mix_rank_pf.get(row["universe_id"], 0)
            row["rank_by_score"] = mix_rank_score.get(row["universe_id"], 0)

        # Core vs dynamic comparison rows
        def _pick_best(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
            return max(rows, key=lambda r: float(r.get(key) or 0))

        best_dyn_pnl = _pick_best(dyn_only, "pnl_yen_100")
        best_dyn_pf = _pick_best(dyn_only, "profit_factor")
        best_mix_pnl = _pick_best(core_mix, "pnl_yen_100")
        best_mix_pf = _pick_best(core_mix, "profit_factor")
        core10_rows = [r for r in summary_rows if r["core_count"] == 10]
        core5_rows = [r for r in summary_rows if r["core_count"] == 5]
        core15_rows = [r for r in summary_rows if r["core_count"] == 15]
        core10_only_best = _pick_best(core10_rows, "pnl_yen_100") if core10_rows else {}
        core_vs_dynamic_rows: list[dict[str, Any]] = []

        def _cmp_row(comparison: str, row: Mapping[str, Any], notes: str) -> dict[str, Any]:
            uid = str(row.get("universe_id") or "")
            stab = next((s for s in stability_rows if s["universe_id"] == uid), {})
            dep = next((d for d in dependency_rows if d["universe_id"] == uid), {})
            return {
                "comparison": comparison,
                "universe_id": uid,
                "core_count": row.get("core_count"),
                "dynamic_count": row.get("dynamic_count"),
                "total_symbols": row.get("total_symbols"),
                "pnl_yen_100": row.get("pnl_yen_100"),
                "profit_factor": row.get("profit_factor"),
                "max_drawdown_yen_100": row.get("max_drawdown_yen_100"),
                "daily_positive_rate": stab.get("daily_positive_rate"),
                "top3_pnl_share_pct": dep.get("top3_pnl_share_pct"),
                "notes": notes,
            }

        core_vs_dynamic_rows.extend(
            [
                _cmp_row("best_dynamic_only_pnl", best_dyn_pnl, "dynamic_only best PnL"),
                _cmp_row("best_dynamic_only_pf", best_dyn_pf, "dynamic_only best PF"),
                _cmp_row("best_core_mix_pnl", best_mix_pnl, "core_mix best PnL"),
                _cmp_row("best_core_mix_pf", best_mix_pf, "core_mix best PF"),
                _cmp_row("baseline_current", baseline_summary, "Core10+Dynamic40 production baseline"),
                _cmp_row("core5_best_pnl", _pick_best(core5_rows, "pnl_yen_100"), "best Core5 config"),
                _cmp_row("core10_best_pnl", core10_only_best, "best Core10 mix config"),
                _cmp_row("core15_best_pnl", _pick_best(core15_rows, "pnl_yen_100"), "best Core15 config"),
            ]
        )

        # Max50 ranking
        max50 = [r for r in summary_rows if int(r["total_symbols"]) <= 50]
        max50.sort(key=lambda r: (-float(r["composite_score"]), -float(r["pnl_yen_100"])))
        max50_rows: list[dict[str, Any]] = []
        for i, r in enumerate(max50, 1):
            dep = next(d for d in dependency_rows if d["universe_id"] == r["universe_id"])
            stab = next(s for s in stability_rows if s["universe_id"] == r["universe_id"])
            cand = _runtime_candidate(r, baseline_summary, dep, stab, baseline_dep, baseline_stab)
            max50_rows.append(
                {
                    "rank": i,
                    "universe_id": r["universe_id"],
                    "core_count": r["core_count"],
                    "dynamic_count": r["dynamic_count"],
                    "total_symbols": r["total_symbols"],
                    "pnl_yen_100": r["pnl_yen_100"],
                    "profit_factor": r["profit_factor"],
                    "max_drawdown_yen_100": r["max_drawdown_yen_100"],
                    "daily_positive_rate": stab["daily_positive_rate"],
                    "top3_pnl_share_pct": dep["top3_pnl_share_pct"],
                    "high_price_pnl_share_pct": dep["high_price_pnl_share_pct"],
                    "composite_score": r["composite_score"],
                    "runtime_candidate": cand,
                }
            )

        max60 = [r for r in summary_rows if int(r["total_symbols"]) <= 60]
        best_overall = max(summary_rows, key=lambda r: float(r["composite_score"]))
        best_pnl = max(summary_rows, key=lambda r: float(r["pnl_yen_100"]))
        best_pf = max(summary_rows, key=lambda r: float(r["profit_factor"]))
        best_stab = max(stability_rows, key=lambda r: float(r["stability_score"]))
        best_stab_summary = next(r for r in summary_rows if r["universe_id"] == best_stab["universe_id"])
        lowest_dep = min(dependency_rows, key=lambda d: abs(float(d["top3_pnl_share_pct"])))
        best_max50 = max50_rows[0] if max50_rows else {}
        best_max60 = max(max60, key=lambda r: float(r["composite_score"]))

        dyn_best_score = max(dyn_only, key=lambda r: float(r["composite_score"]))
        mix_best_score = max(core_mix, key=lambda r: float(r["composite_score"]))

        dyn_beats_mix_pnl = float(best_dyn_pnl["pnl_yen_100"]) >= float(best_mix_pnl["pnl_yen_100"])
        core5_needed = float(_pick_best(core5_rows, "pnl_yen_100")["pnl_yen_100"]) > float(best_dyn_pnl["pnl_yen_100"])
        core10_harm = float(core10_only_best.get("pnl_yen_100") or 0) < float(best_dyn_pnl["pnl_yen_100"])
        core15_excess = float(_pick_best(core15_rows, "pnl_yen_100")["pnl_yen_100"]) < float(best_mix_pnl["pnl_yen_100"])

        optimal_dynamic = max(
            dyn_only,
            key=lambda r: float(r["composite_score"]),
        )["dynamic_count"]

        runtime_candidates = [r for r in max50_rows if r["runtime_candidate"]]
        runtime_change_candidate = len(runtime_candidates) > 0

        mandatory = {
            "1_best_universe": f"{best_overall['universe_id']} ({best_overall['universe_label']}) score={best_overall['composite_score']}",
            "2_best_dynamic_only": f"{dyn_best_score['universe_id']} PnL={dyn_best_score['pnl_yen_100']} PF={dyn_best_score['profit_factor']}",
            "3_best_core_mix": f"{mix_best_score['universe_id']} PnL={mix_best_score['pnl_yen_100']} PF={mix_best_score['profit_factor']}",
            "4_core_necessary": not dyn_beats_mix_pnl,
            "5_core10_advantage": not core10_harm,
            "6_optimal_dynamic_count": optimal_dynamic,
            "7_best_total50": f"{best_max50.get('universe_id')} PnL={best_max50.get('pnl_yen_100')} PF={best_max50.get('profit_factor')}",
            "8_best_total60": f"{best_max60['universe_id']} PnL={best_max60['pnl_yen_100']} PF={best_max60['profit_factor']}",
            "9_max_pnl_config": f"{best_pnl['universe_id']} PnL={best_pnl['pnl_yen_100']}",
            "10_max_pf_config": f"{best_pf['universe_id']} PF={best_pf['profit_factor']}",
            "11_max_stability_config": f"{best_stab_summary['universe_id']} stability={best_stab['stability_score']}",
            "12_lowest_dependency_config": f"{lowest_dep['universe_id']} Top3={lowest_dep['top3_pnl_share_pct']}%",
            "13_delta_vs_baseline_pnl": round(float(best_overall["pnl_yen_100"]) - float(baseline_summary["pnl_yen_100"]), 2),
            "13_delta_vs_baseline_pf": round(float(best_overall["profit_factor"]) - float(baseline_summary["profit_factor"]), 4),
            "14_runtime_change_candidate": runtime_change_candidate,
            "14_runtime_candidates": [r["universe_id"] for r in runtime_candidates[:5]],
            "15_next_phase": (
                "phase584_universe_shadow_adoption_review"
                if runtime_change_candidate
                else "phase584_universe_grid_monitor_continue_baseline"
            ),
            "period_start": PERIOD_START,
            "period_end": end,
            "grid_size": len(specs),
            "core5_needed": core5_needed,
            "core10_harmful": core10_harm,
            "core15_excessive": core15_excess,
            "dynamic_only_best_by_pnl": best_dyn_pnl["universe_id"],
            "dynamic_only_best_by_pf": best_dyn_pf["universe_id"],
            "core_mix_best_by_pnl": best_mix_pnl["universe_id"],
            "core_mix_best_by_pf": best_mix_pf["universe_id"],
        }

        return {
            "verdict": PHASE583_VERDICT,
            "all_pass": len(all_trades) > 0 and len(summary_rows) == len(specs),
            "grid_summary_rows": summary_rows,
            "dynamic_curve_rows": dynamic_curve_rows,
            "core_mix_curve_rows": core_mix_curve_rows,
            "core_vs_dynamic_rows": core_vs_dynamic_rows,
            "stability_rows": stability_rows,
            "dependency_rows": dependency_rows,
            "max50_rows": max50_rows,
            "mandatory_answers": mandatory,
            "generated_at": _now_iso(),
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        paths = {
            "grid_summary": reports / "phase583_universe_grid_summary.csv",
            "dynamic_curve": reports / "phase583_dynamic_only_curve.csv",
            "core_mix_curve": reports / "phase583_core_mix_curve.csv",
            "core_vs_dynamic": reports / "phase583_core_vs_dynamic.csv",
            "stability": reports / "phase583_universe_stability.csv",
            "dependency": reports / "phase583_universe_dependency.csv",
            "max50": reports / "phase583_max50_ranking.csv",
            "report": reports / "phase583_report.json",
        }
        _write_csv(paths["grid_summary"], GRID_SUMMARY_FIELDS, list(result.get("grid_summary_rows") or []))
        _write_csv(paths["dynamic_curve"], DYNAMIC_CURVE_FIELDS, list(result.get("dynamic_curve_rows") or []))
        _write_csv(paths["core_mix_curve"], CORE_MIX_CURVE_FIELDS, list(result.get("core_mix_curve_rows") or []))
        _write_csv(paths["core_vs_dynamic"], CORE_VS_DYNAMIC_FIELDS, list(result.get("core_vs_dynamic_rows") or []))
        _write_csv(paths["stability"], STABILITY_FIELDS, list(result.get("stability_rows") or []))
        _write_csv(paths["dependency"], DEPENDENCY_FIELDS, list(result.get("dependency_rows") or []))
        _write_csv(paths["max50"], MAX50_FIELDS, list(result.get("max50_rows") or []))

        slim = {k: v for k, v in result.items() if not k.endswith("_rows")}
        paths["report"].write_text(json.dumps(slim, ensure_ascii=False, indent=2), encoding="utf-8")

        m = result.get("mandatory_answers") or {}
        summary = list(result.get("grid_summary_rows") or [])
        doc = kabu / "docs" / "operations" / "phase583_dynamic_only_core_mix_universe_grid_search.md"
        doc.parent.mkdir(parents=True, exist_ok=True)
        top10 = sorted(summary, key=lambda r: -float(r["composite_score"]))[:10]
        doc.write_text(
            "\n".join(
                [
                    "# Phase583 — Dynamic Only / Core Mix Universe Grid Search",
                    "",
                    f"**Verdict:** `{result.get('verdict')}`",
                    f"**Period:** {m.get('period_start')}–{m.get('period_end')}",
                    f"**Grid size:** {m.get('grid_size')} configurations",
                    "",
                    "## Mandatory answers",
                    "",
                    f"1. Best universe: {m.get('1_best_universe')}",
                    f"2. Best Dynamic only: {m.get('2_best_dynamic_only')}",
                    f"3. Best Core mix: {m.get('3_best_core_mix')}",
                    f"4. Core necessary: {m.get('4_core_necessary')}",
                    f"5. Core10 advantageous: {m.get('5_core10_advantage')}",
                    f"6. Optimal dynamic count: {m.get('6_optimal_dynamic_count')}",
                    f"7. Best total≤50: {m.get('7_best_total50')}",
                    f"8. Best total≤60: {m.get('8_best_total60')}",
                    f"9. Max PnL config: {m.get('9_max_pnl_config')}",
                    f"10. Max PF config: {m.get('10_max_pf_config')}",
                    f"11. Max stability config: {m.get('11_max_stability_config')}",
                    f"12. Lowest dependency config: {m.get('12_lowest_dependency_config')}",
                    f"13. Delta vs baseline — PnL: {m.get('13_delta_vs_baseline_pnl')}, PF: {m.get('13_delta_vs_baseline_pf')}",
                    f"14. Runtime change candidate: {m.get('14_runtime_change_candidate')} {m.get('14_runtime_candidates')}",
                    f"15. Next phase: {m.get('15_next_phase')}",
                    "",
                    "## Top 10 by composite score",
                    "",
                    "| Rank | ID | Label | Total | PnL | PF | MaxDD |",
                    "|------|-----|-------|-------|-----|-----|-------|",
                ]
                + [
                    f"| {i} | {r['universe_id']} | {r['universe_label']} | {r['total_symbols']} | {r['pnl_yen_100']} | {r['profit_factor']} | {r['max_drawdown_yen_100']} |"
                    for i, r in enumerate(top10, 1)
                ]
            ),
            encoding="utf-8",
        )
        paths["doc"] = doc
        return paths
