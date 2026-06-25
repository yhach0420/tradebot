"""
Phase515A — Classic entry parameter robustness study (research only).

Classical ENTRY grids with PBv2 Exit fixed. No exit exploration. No adoption.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from itertools import combinations
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase451_entry_shape_tournament import _build_price_index_to, _now_iso
from research.phase476_pre_breakout_gate_replay import _load_replay_pool
from research.phase488_current_runtime_replay import _filter_period, _trade_summary_rows
from research.phase493_global_entry_failure_audit import PERIOD_END, PERIOD_START
from research.phase501_classic_indicator_audit import _ema_series
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
    _rank_summaries,
    _run_baseline_runtime,
    _simulate_precomputed_cap,
    _strategy_metrics,
    _universe_symbols,
    state_trade_logs,
)
from research.phase511_entry_exit_cross_battle import _apply_pb_exit_classical_entry
from research.phase512_classic_indicator_combination_search import _overfit_row
from research.phase510_classic_system_battle import _strategy_metrics_safe
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

PHASE515A_VERDICT = "phase515a_classic_entry_parameter_robustness_done"
MAX_WORKERS_CAP = 4
MAX_MOMENTUM = 150
MAX_TREND = 100
MAX_BREAKOUT = 75
MAX_TOTAL = 325

RSI_THRESHOLDS = (45, 50, 55, 60, 65, 70)
STOCH_MARGINS = (0, 2, 5, 10)

SUMMARY_FIELDS = [
    "strategy_id",
    "family",
    "entry_description",
    "exit_description",
    "total_pnl_yen_100",
    "profit_factor",
    "max_drawdown_yen_100",
    "trades",
    "win_rate",
    "avg_pnl_yen_100",
    "positive_day_count",
    "negative_day_count",
    "daily_stability_score",
    "best_day_pnl",
    "worst_day_pnl",
    "baseline_diff_pnl",
    "baseline_diff_pf",
    "baseline_diff_dd",
    "rank_pnl",
    "rank_pf",
    "rank_dd",
    "rank_stability",
]

DAILY_FIELDS = ["strategy_id", "family", "day", "trade_count", "total_pnl_yen_100", "profit_factor", "win_rate"]

TRADE_FIELDS = [
    "strategy_id",
    "family",
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
    "family",
    "entry_description",
    "parameter_neighborhood_pf",
    "parameter_neighborhood_pnl",
    "robust_band_count",
    "fragile_spike_flag",
    "neighbor_ids",
    "top1_trade_profit_share_pct",
    "top5_trade_profit_share_pct",
    "top10_trade_profit_share_pct",
    "top1_symbol_profit_share_pct",
    "top3_symbol_profit_share_pct",
    "top1_day_profit_share_pct",
    "top3_day_profit_share_pct",
    "single_symbol_dependency",
    "single_day_dependency",
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


def _ema50_at(bars: Sequence[Bar1m], i: int) -> Optional[float]:
    closes = [bars[j].close for j in range(max(0, i - 59), i + 1)]
    if len(closes) < 50:
        return None
    series = _ema_series(closes, 50)
    return series[-1] if series else None


def _donchian_high_at(bars: Sequence[Bar1m], i: int, period: int) -> Optional[float]:
    if i < period:
        return None
    return max(bars[j].high for j in range(i - period, i))


def _vol_above_ma(bars: Sequence[Bar1m], i: int, period: int = 20) -> bool:
    if i < period:
        return False
    avg = statistics.mean(bars[j].volume for j in range(i - period + 1, i + 1))
    return bars[i].volume > avg


def _day_high_break(bars: Sequence[Bar1m], i: int) -> bool:
    if i < 1:
        return False
    dh = max(b.high for b in bars[:i])
    return bars[i].close > dh


@dataclass(frozen=True)
class EntrySpec:
    strategy_id: str
    family: str
    description: str
    params: tuple[tuple[str, Any], ...]

    def eval(
        self,
        ind: Mapping[str, Optional[float]],
        bar: Bar1m,
        bars: Sequence[Bar1m],
        ind_rows: Sequence[BarIndicatorRow],
        i: int,
    ) -> bool:
        p = dict(self.params)
        fam = self.family
        if fam == "momentum":
            rsi = _f(ind.get("RSI14"))
            if rsi != rsi:
                return False
            if rsi <= float(p["rsi_thresh"]):
                return False
            margin = float(p["stoch_margin"])
            if _f(ind.get("STOCH_K")) <= _f(ind.get("STOCH_D")) + margin:
                return False
            extras: frozenset[str] = p["extras"]
            if "roc" in extras and not (_f(ind.get("ROC10")) > 0):
                return False
            if "mom" in extras and not (_f(ind.get("MOMENTUM10")) > 0):
                return False
            if "mfi" in extras and not (_f(ind.get("MFI14")) > 50):
                return False
            return True
        if fam == "trend":
            tags: frozenset[str] = p["tags"]
            checks = {
                "ema20": bar.close > _f(ind.get("EMA20")),
                "ema50": (_ema50 := _ema50_at(bars, i)) is not None and bar.close > _ema50,
                "vwap": bar.close > _f(ind.get("VWAP")),
                "adx15": _f(ind.get("ADX")) > 15,
                "adx20": _f(ind.get("ADX")) > 20,
                "adx25": _f(ind.get("ADX")) > 25,
                "adx30": _f(ind.get("ADX")) > 30,
                "di_bull": _f(ind.get("PLUS_DI")) > _f(ind.get("MINUS_DI")),
            }
            return all(checks[t] for t in tags)
        if fam == "breakout":
            tags = p["tags"]
            checks = {
                "donch10": (_dh := _donchian_high_at(bars, i, 10)) is not None and bar.close > _dh,
                "donch20": (_dh := _donchian_high_at(bars, i, 20)) is not None and bar.close > _dh,
                "donch30": (_dh := _donchian_high_at(bars, i, 30)) is not None and bar.close > _dh,
                "bb_upper": bar.close > _f(ind.get("BB_upper")),
                "day_high": _day_high_break(bars, i),
                "vol_ma": _vol_above_ma(bars, i),
            }
            return all(checks[t] for t in tags)
        return False

    def neighbor_key(self) -> tuple[Any, ...]:
        p = dict(self.params)
        if self.family == "momentum":
            return ("momentum", p["stoch_margin"], p["extras"])
        if self.family == "trend":
            return ("trend", p["tags"])
        if self.family == "breakout":
            return ("breakout", p["tags"])
        return (self.strategy_id,)


def _cap(xs: list[EntrySpec], n: int) -> list[EntrySpec]:
    return xs[:n]


def _build_momentum_grid() -> list[EntrySpec]:
    extra_sets: list[frozenset[str]] = [
        frozenset(),
        frozenset({"roc"}),
        frozenset({"mom"}),
        frozenset({"mfi"}),
        frozenset({"roc", "mom"}),
        frozenset({"roc", "mfi"}),
        frozenset({"mom", "mfi"}),
        frozenset({"roc", "mom", "mfi"}),
    ]
    specs: list[EntrySpec] = []
    idx = 0
    for rsi in RSI_THRESHOLDS:
        for margin in STOCH_MARGINS:
            for extras in extra_sets:
                idx += 1
                extra_label = "+".join(sorted(extras)) if extras else "base"
                desc = f"RSI>{rsi} StochK>D+{margin}" + (f" {extra_label}" if extras else "")
                specs.append(
                    EntrySpec(
                        strategy_id=f"P515A_M_{idx:03d}",
                        family="momentum",
                        description=desc,
                        params=(
                            ("rsi_thresh", rsi),
                            ("stoch_margin", margin),
                            ("extras", extras),
                        ),
                    )
                )
    return _cap(specs, MAX_MOMENTUM)


def _build_trend_grid() -> list[EntrySpec]:
    atoms = ("ema20", "ema50", "vwap", "adx15", "adx20", "adx25", "adx30", "di_bull")
    price_atoms = ("ema20", "ema50", "vwap")
    specs: list[EntrySpec] = []
    idx = 0

    def _add(tags: tuple[str, ...]) -> None:
        nonlocal idx
        idx += 1
        specs.append(
            EntrySpec(
                strategy_id=f"P515A_T_{idx:03d}",
                family="trend",
                description=" & ".join(tags),
                params=(("tags", frozenset(tags)),),
            )
        )

    for a in atoms:
        _add((a,))
    for a, b in combinations(atoms, 2):
        if a in price_atoms or b in price_atoms:
            _add((a, b))
    for a, b, c in combinations(atoms, 3):
        if any(x in price_atoms for x in (a, b, c)):
            _add((a, b, c))
    for a, b, c, d in combinations(atoms, 4):
        if sum(1 for x in (a, b, c, d) if x in price_atoms) >= 2 and "di_bull" in (a, b, c, d):
            _add((a, b, c, d))
    return _cap(specs, MAX_TREND)


def _build_breakout_grid() -> list[EntrySpec]:
    atoms = ("donch10", "donch20", "donch30", "bb_upper", "day_high", "vol_ma")
    specs: list[EntrySpec] = []
    idx = 0

    def _add(tags: tuple[str, ...]) -> None:
        nonlocal idx
        idx += 1
        specs.append(
            EntrySpec(
                strategy_id=f"P515A_B_{idx:03d}",
                family="breakout",
                description=" & ".join(tags),
                params=(("tags", frozenset(tags)),),
            )
        )

    for a in atoms:
        _add((a,))
    for a, b in combinations(atoms, 2):
        _add((a, b))
    for a, b, c in combinations(atoms, 3):
        _add((a, b, c))
    return _cap(specs, MAX_BREAKOUT)


def build_entry_grid() -> list[EntrySpec]:
    mom = _build_momentum_grid()
    trend = _build_trend_grid()
    brk = _build_breakout_grid()
    total = mom + trend + brk
    return total[:MAX_TOTAL]


def scan_all_specs_symbol_day(
    specs: Sequence[EntrySpec],
    *,
    symbol: str,
    day: str,
    bars: Sequence[Bar1m],
    ind_rows: Sequence[BarIndicatorRow],
    price_idx: Mapping[tuple[str, str], list[tuple[datetime, float]]],
    exit_cache: dict[tuple[str, str, str, float], dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """One bar pass evaluates every entry spec; PB exit results are cached by entry key."""
    out: dict[str, list[dict[str, Any]]] = {s.strategy_id: [] for s in specs}
    last_entry: dict[str, Optional[datetime]] = {s.strategy_id: None for s in specs}
    sym = symbol if symbol.endswith(".T") else f"{symbol}.T"
    for i in range(MIN_BARS_WARMUP, len(bars)):
        bar = bars[i]
        if not _in_trading_window(bar.ts):
            continue
        ind = ind_rows[i].values
        for spec in specs:
            if spec.family == "momentum" and ind.get("RSI14") is None:
                continue
            last = last_entry[spec.strategy_id]
            if last and (bar.ts - last).total_seconds() < ENTRY_COOLDOWN_SEC:
                continue
            if not spec.eval(ind, bar, bars, ind_rows, i):
                continue
            ent_iso = bar.ts.isoformat()
            ent_px = bar.close
            cache_key = (sym, day, ent_iso, round(ent_px, 4))
            applied = exit_cache.get(cache_key)
            if applied is None:
                candidate = {"symbol": sym, "day": day, "entry_time": ent_iso, "entry_price": ent_px}
                applied = _apply_pb_exit_classical_entry(candidate, price_idx=price_idx)
                if applied:
                    exit_cache[cache_key] = applied
            if applied:
                out[spec.strategy_id].append({**applied, "strategy_id": spec.strategy_id, "family": spec.family})
            last_entry[spec.strategy_id] = bar.ts
    return out


def scan_entry_pb_exit_day(
    spec: EntrySpec,
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
        ind = ind_rows[i].values
        if ind.get("RSI14") is None and spec.family == "momentum":
            continue
        if last_entry and (bar.ts - last_entry).total_seconds() < ENTRY_COOLDOWN_SEC:
            continue
        if not spec.eval(ind, bar, bars, ind_rows, i):
            continue
        candidate = {
            "symbol": sym,
            "day": day,
            "entry_time": bar.ts.isoformat(),
            "entry_price": bar.close,
        }
        applied = _apply_pb_exit_classical_entry(candidate, price_idx=price_idx)
        if applied:
            trades.append(applied)
        last_entry = bar.ts
    return trades


def _symbol_day_overfit(trade_log: Sequence[Mapping[str, Any]], total: float) -> dict[str, float]:
    sym_pnl: dict[str, float] = defaultdict(float)
    day_pnl: dict[str, float] = defaultdict(float)
    for t in trade_log:
        tr = t.get("trade") or t
        sym = str(tr.get("symbol") or "").replace(".T", "")
        d = str(t.get("day") or "")[:8]
        p = _float(t.get("pnl_yen"))
        sym_pnl[sym] += p
        day_pnl[d] += p
    sym_rank = sorted(sym_pnl.items(), key=lambda x: x[1], reverse=True)
    day_rank = sorted(day_pnl.items(), key=lambda x: x[1], reverse=True)
    top1_sym = round(sym_rank[0][1] / total * 100.0, 2) if total and sym_rank else 0.0
    top3_sym = round(sum(v for _, v in sym_rank[:3]) / total * 100.0, 2) if total and sym_rank else 0.0
    top1_day = round(day_rank[0][1] / total * 100.0, 2) if total and day_rank else 0.0
    top3_day = round(sum(v for _, v in day_rank[:3]) / total * 100.0, 2) if total and day_rank else 0.0
    return {
        "top1_symbol_profit_share_pct": top1_sym,
        "top3_symbol_profit_share_pct": top3_sym,
        "top1_day_profit_share_pct": top1_day,
        "top3_day_profit_share_pct": top3_day,
        "single_symbol_dependency": bool(sym_rank and top1_sym >= 40 and sym_rank[0][1] > 0),
        "single_day_dependency": bool(day_rank and top1_day >= 35 and day_rank[0][1] > 0),
    }


def _neighbors_for(spec: EntrySpec, all_specs: Sequence[EntrySpec]) -> list[EntrySpec]:
    if spec.family == "momentum":
        p = dict(spec.params)
        rsi = int(p["rsi_thresh"])
        margin = p["stoch_margin"]
        extras = p["extras"]
        rsi_i = RSI_THRESHOLDS.index(rsi) if rsi in RSI_THRESHOLDS else -1
        out: list[EntrySpec] = []
        for other in all_specs:
            if other.family != "momentum":
                continue
            op = dict(other.params)
            if op["stoch_margin"] != margin or op["extras"] != extras:
                continue
            oi = RSI_THRESHOLDS.index(int(op["rsi_thresh"])) if int(op["rsi_thresh"]) in RSI_THRESHOLDS else -1
            if rsi_i >= 0 and oi >= 0 and abs(oi - rsi_i) <= 1 and other.strategy_id != spec.strategy_id:
                out.append(other)
        return out
    return [s for s in all_specs if s.neighbor_key() == spec.neighbor_key() and s.strategy_id != spec.strategy_id]


def _robustness_row(
    spec: EntrySpec,
    summary_by_id: Mapping[str, Mapping[str, Any]],
    all_specs: Sequence[EntrySpec],
    trade_log: Sequence[Mapping[str, Any]],
    *,
    baseline_pf: float,
) -> dict[str, Any]:
    neighbors = _neighbors_for(spec, all_specs)
    neighbor_ids = [n.strategy_id for n in neighbors]
    n_pfs = [_float(summary_by_id[nid].get("profit_factor")) for nid in neighbor_ids if nid in summary_by_id]
    n_pnls = [_float(summary_by_id[nid].get("total_pnl_yen_100")) for nid in neighbor_ids if nid in summary_by_id]
    self_pf = _float(summary_by_id[spec.strategy_id].get("profit_factor"))
    self_pnl = _float(summary_by_id[spec.strategy_id].get("total_pnl_yen_100"))
    med_pf = round(statistics.median(n_pfs), 4) if n_pfs else self_pf
    med_pnl = round(statistics.median(n_pnls), 2) if n_pnls else self_pnl
    robust_band = sum(1 for pf in n_pfs if pf > baseline_pf and pf > 0)
    fragile = bool(
        n_pnls
        and self_pnl > 2.0 * med_pnl
        and med_pnl < self_pnl * 0.5
        and (self_pf > 1.5 * med_pf if med_pf > 0 else False)
    )
    overfit = _overfit_row(spec.strategy_id, spec.family, trade_log)
    sym_day = _symbol_day_overfit(trade_log, sum(_float(t.get("pnl_yen")) for t in trade_log))
    return {
        "strategy_id": spec.strategy_id,
        "family": spec.family,
        "entry_description": spec.description,
        "parameter_neighborhood_pf": med_pf,
        "parameter_neighborhood_pnl": med_pnl,
        "robust_band_count": robust_band,
        "fragile_spike_flag": fragile,
        "neighbor_ids": ",".join(neighbor_ids[:10]),
        **{k: overfit[k] for k in (
            "top1_trade_profit_share_pct",
            "top5_trade_profit_share_pct",
            "top10_trade_profit_share_pct",
        )},
        **sym_day,
    }


def _mandatory_answers(
    summary_rows: Sequence[Mapping[str, Any]],
    robustness_rows: Sequence[Mapping[str, Any]],
    baseline: Mapping[str, Any],
    specs: Sequence[EntrySpec],
) -> dict[str, Any]:
    b_pnl = _float(baseline.get("total_pnl_yen_100"))
    b_pf = _float(baseline.get("profit_factor"))
    b_dd = _float(baseline.get("max_drawdown_yen_100"))
    classical = [r for r in summary_rows if r.get("strategy_id") != BASELINE_STRATEGY_ID]

    beat_pnl = [r["strategy_id"] for r in classical if _float(r.get("total_pnl_yen_100")) > b_pnl]
    beat_pf = [r["strategy_id"] for r in classical if _float(r.get("profit_factor") or 0) > b_pf]
    beat_dd = [r["strategy_id"] for r in classical if _float(r.get("max_drawdown_yen_100")) < b_dd]

    def _best_family(fam: str) -> dict[str, Any]:
        fam_rows = [r for r in classical if r.get("family") == fam]
        if not fam_rows:
            return {}
        best = max(fam_rows, key=lambda r: _float(r.get("total_pnl_yen_100")))
        return {"strategy_id": best.get("strategy_id"), "description": best.get("entry_description"), "pnl": best.get("total_pnl_yen_100")}

    def _rsi_band_stability() -> dict[str, Any]:
        by_rsi: dict[int, list[float]] = defaultdict(list)
        for r in classical:
            if r.get("family") != "momentum":
                continue
            spec = next((s for s in specs if s.strategy_id == r["strategy_id"]), None)
            if spec is None:
                continue
            by_rsi[int(dict(spec.params)["rsi_thresh"])].append(_float(r.get("profit_factor")))
        bands = {}
        for rsi, pfs in sorted(by_rsi.items()):
            bands[str(rsi)] = {
                "median_pf": round(statistics.median(pfs), 4) if pfs else 0.0,
                "count": len(pfs),
            }
        stable = sorted(bands.items(), key=lambda x: x[1]["median_pf"], reverse=True)
        return {"bands": bands, "most_stable_rsi": stable[0][0] if stable else None}

    def _stoch_stability() -> dict[str, Any]:
        by_margin: dict[int, list[float]] = defaultdict(list)
        for r in classical:
            if r.get("family") != "momentum":
                continue
            spec = next((s for s in specs if s.strategy_id == r["strategy_id"]), None)
            if spec is None:
                continue
            by_margin[int(dict(spec.params)["stoch_margin"])].append(_float(r.get("profit_factor")))
        bands = {str(m): round(statistics.median(pfs), 4) if pfs else 0.0 for m, pfs in sorted(by_margin.items())}
        best = max(bands.items(), key=lambda x: x[1])[0] if bands else None
        return {"bands": bands, "most_stable_margin": best}

    def _adx_stability() -> dict[str, Any]:
        by_adx: dict[str, list[float]] = defaultdict(list)
        for r in classical:
            if r.get("family") != "trend":
                continue
            spec = next((s for s in specs if s.strategy_id == r["strategy_id"]), None)
            if spec is None:
                continue
            tags = dict(spec.params)["tags"]
            for t in tags:
                if t.startswith("adx"):
                    by_adx[t].append(_float(r.get("profit_factor")))
        bands = {k: round(statistics.median(v), 4) if v else 0.0 for k, v in sorted(by_adx.items())}
        best = max(bands.items(), key=lambda x: x[1])[0] if bands else None
        return {"bands": bands, "most_stable_adx": best}

    robust_non_fragile = [
        r["strategy_id"]
        for r in robustness_rows
        if not r.get("fragile_spike_flag")
        and not r.get("single_symbol_dependency")
        and not r.get("single_day_dependency")
    ]
    neighborhood_robust = [r["strategy_id"] for r in robustness_rows if int(r.get("robust_band_count") or 0) >= 2]

    best_classical = max(classical, key=lambda r: _float(r.get("total_pnl_yen_100")), default={})
    promising_family = max(
        ("momentum", "trend", "breakout"),
        key=lambda f: _float((_best_family(f) or {}).get("pnl") or 0),
    )

    return {
        "1_beats_pbv2_entry_any": bool(beat_pnl or beat_pf),
        "2_pnl_beats_baseline": beat_pnl[:10],
        "3_pf_beats_baseline": beat_pf[:10],
        "4_dd_beats_baseline": beat_dd[:10],
        "5_best_momentum": _best_family("momentum"),
        "6_best_trend": _best_family("trend"),
        "7_best_breakout": _best_family("breakout"),
        "8_rsi_threshold_stability": _rsi_band_stability(),
        "9_stoch_condition_stability": _stoch_stability(),
        "10_adx_threshold_stability": _adx_stability(),
        "11_promising_family": promising_family,
        "12_neighborhood_robust_entries": neighborhood_robust[:10],
        "13_non_fragile_candidates": robust_non_fragile[:10],
        "14_next_deep_dive": best_classical.get("strategy_id"),
        "14_next_deep_dive_description": best_classical.get("entry_description"),
        "adopt_not_allowed": True,
    }


@dataclass
class Phase515AJob:
    repo_root: Path
    parallel: bool = True
    max_workers: int = 4

    def run(self) -> dict[str, Any]:
        specs = build_entry_grid()
        kabu = resolve_kabu_root(self.repo_root)
        reports = resolve_reports_dir(self.repo_root)
        max_workers = min(max(1, self.max_workers), MAX_WORKERS_CAP)

        baseline_state, baseline_met = _run_baseline_runtime(self.repo_root)
        baseline_row = {
            **baseline_met,
            "strategy_id": BASELINE_STRATEGY_ID,
            "family": "BASELINE",
            "entry_description": "PBv2 Entry",
            "exit_description": "PBv2 Exit (board_trailing, hard_stop, session_close)",
        }

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

        def _scan_symbol_day(sym: str, day: str) -> dict[str, list[dict[str, Any]]]:
            cached = bar_cache.get((sym, day))
            if not cached:
                return {}
            bars, ind_rows = cached
            return scan_all_specs_symbol_day(
                specs,
                symbol=sym,
                day=day,
                bars=bars,
                ind_rows=ind_rows,
                price_idx=price_idx,
                exit_cache=exit_cache,
            )

        sym_day_jobs = [(sym, day) for sym in universe for day in days if (sym, day) in bar_cache]

        if self.parallel:
            with ThreadPoolExecutor(max_workers=max_workers) as ex:
                futs = [ex.submit(_scan_symbol_day, sym, day) for sym, day in sym_day_jobs]
                for fut in as_completed(futs):
                    batch = fut.result()
                    for sid, cands in batch.items():
                        candidates_by_id[sid].extend(cands)
        else:
            for sym, day in sym_day_jobs:
                batch = _scan_symbol_day(sym, day)
                for sid, cands in batch.items():
                    candidates_by_id[sid].extend(cands)

        summary_rows: list[dict[str, Any]] = [baseline_row]
        daily_rows: list[dict[str, Any]] = []
        trade_rows: list[dict[str, Any]] = []
        strategy_states: dict[str, Any] = {"BASELINE": baseline_state}

        for dr in _day_rows(baseline_state, BASELINE_STRATEGY_ID):
            daily_rows.append({**dr, "family": "BASELINE"})
        for log in _trade_summary_rows(baseline_state):
            trade_rows.append(
                {
                    "strategy_id": BASELINE_STRATEGY_ID,
                    "family": "BASELINE",
                    "symbol": str(log.get("symbol") or "").replace(".T", ""),
                    "day": str(log.get("day") or "")[:8],
                    "entry_time": log.get("entry_time"),
                    "exit_time": log.get("exit_time"),
                    "entry_price": "",
                    "exit_price": "",
                    "pnl_yen_100": log.get("pnl_yen"),
                    "exit_reason": log.get("exit_reason"),
                }
            )

        spec_by_id = {s.strategy_id: s for s in specs}
        for spec in specs:
            cands = candidates_by_id.get(spec.strategy_id, [])
            st = _simulate_precomputed_cap(cands, mode=f"phase515a_{spec.strategy_id}")
            strategy_states[spec.strategy_id] = st
            met = _strategy_metrics_safe(
                st,
                strategy_id=spec.strategy_id,
                entry_rule_id=spec.description,
                exit_rule_id="PBv2_EXIT",
                baseline=baseline_row,
            )
            summary_rows.append(
                {
                    **met,
                    "family": spec.family,
                    "entry_description": spec.description,
                    "exit_description": "PBv2 Exit",
                }
            )
            for dr in _day_rows(st, spec.strategy_id):
                daily_rows.append({**dr, "family": spec.family})
            log_spec = {
                "strategy_id": spec.strategy_id,
                "entry_rule_id": spec.description,
                "exit_rule_id": "PBv2_EXIT",
            }
            for log in state_trade_logs(st, log_spec):
                trade_rows.append({**log, "family": spec.family})

        _rank_summaries(summary_rows)

        summary_by_id = {r["strategy_id"]: r for r in summary_rows}
        classical_sorted = sorted(
            [r for r in summary_rows if r["strategy_id"] != BASELINE_STRATEGY_ID],
            key=lambda r: _float(r.get("total_pnl_yen_100")),
            reverse=True,
        )
        top_for_robust = classical_sorted[:15]
        robustness_rows: list[dict[str, Any]] = []
        b_pf = _float(baseline_row.get("profit_factor"))
        for row in top_for_robust:
            sid = row["strategy_id"]
            spec = spec_by_id[sid]
            st = strategy_states[sid]
            robustness_rows.append(
                _robustness_row(spec, summary_by_id, specs, st.trade_log, baseline_pf=b_pf)
            )

        mandatory = _mandatory_answers(summary_rows, robustness_rows, baseline_row, specs)

        return {
            "verdict": PHASE515A_VERDICT,
            "generated_at": _now_iso(),
            "period_start": PERIOD_START,
            "period_end": PERIOD_END,
            "exit_fixed": "PBv2 Exit (board_dynamic_trailing, hard_stop -1.2%, session_close)",
            "strategy_count": len(specs) + 1,
            "classical_count": len(specs),
            "grid_sizes": {
                "momentum": sum(1 for s in specs if s.family == "momentum"),
                "trend": sum(1 for s in specs if s.family == "trend"),
                "breakout": sum(1 for s in specs if s.family == "breakout"),
            },
            "summary_rows": summary_rows,
            "daily_rows": daily_rows,
            "trade_rows": trade_rows,
            "robustness_rows": robustness_rows,
            "mandatory_answers": mandatory,
            "baseline": baseline_row,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        paths = {
            "summary": reports / "phase515a_entry_param_summary.csv",
            "daily": reports / "phase515a_entry_param_daily.csv",
            "trades": reports / "phase515a_entry_param_trades.csv",
            "robustness": reports / "phase515a_entry_param_robustness.csv",
            "report": reports / "phase515a_entry_param_report.json",
            "docs": kabu / "docs" / "operations" / "phase515a_classic_entry_parameter_robustness.md",
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
    lines = [
        "# Phase515A — Classic Entry Parameter Robustness",
        "",
        f"**Verdict:** `{result.get('verdict')}`",
        f"**Exit fixed:** PBv2 Exit",
        f"**Classical strategies:** {result.get('classical_count')}",
        f"**Grid:** {result.get('grid_sizes')}",
        "",
        "## Top 10 by PnL (classical)",
        "",
        "| ID | Family | PnL | PF | maxDD | ΔPnL |",
        "|----|--------|-----|----|-------|------|",
    ]
    classical = sorted(
        [r for r in result.get("summary_rows") or [] if r.get("strategy_id") != BASELINE_STRATEGY_ID],
        key=lambda r: _float(r.get("total_pnl_yen_100")),
        reverse=True,
    )[:10]
    baseline = result.get("baseline") or {}
    lines.append(
        f"| BASELINE | PBv2 | {baseline.get('total_pnl_yen_100')} | "
        f"{baseline.get('profit_factor')} | {baseline.get('max_drawdown_yen_100')} | — |"
    )
    for r in classical:
        lines.append(
            f"| {r.get('strategy_id')} | {r.get('family')} | {r.get('total_pnl_yen_100')} | "
            f"{r.get('profit_factor')} | {r.get('max_drawdown_yen_100')} | {r.get('baseline_diff_pnl')} |"
        )
    lines.extend(
        [
            "",
            "## Mandatory answers",
            "",
            f"1. Beats PBv2 entry: **{ma.get('1_beats_pbv2_entry_any')}**",
            f"2. PnL beats: **{ma.get('2_pnl_beats_baseline')}**",
            f"3. PF beats: **{ma.get('3_pf_beats_baseline')}**",
            f"4. DD beats: **{ma.get('4_dd_beats_baseline')}**",
            f"5. Best momentum: **{ma.get('5_best_momentum')}**",
            f"6. Best trend: **{ma.get('6_best_trend')}**",
            f"7. Best breakout: **{ma.get('7_best_breakout')}**",
            f"8. RSI stability: **{ma.get('8_rsi_threshold_stability')}**",
            f"9. Stoch stability: **{ma.get('9_stoch_condition_stability')}**",
            f"10. ADX stability: **{ma.get('10_adx_threshold_stability')}**",
            f"11. Promising family: **{ma.get('11_promising_family')}**",
            f"12. Neighborhood robust: **{ma.get('12_neighborhood_robust_entries')}**",
            f"13. Non-fragile: **{ma.get('13_non_fragile_candidates')}**",
            f"14. Next deep dive: **{ma.get('14_next_deep_dive')}** — {ma.get('14_next_deep_dive_description')}",
        ]
    )
    return "\n".join(lines) + "\n"
