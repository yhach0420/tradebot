"""
Phase516 — PBv2 + best classical entry overlay (research only).

Tests whether Phase515A–D best classical ENTRY candidates add value ON TOP of PBv2 Runtime.
AND and OR overlay modes. PBv2 Exit fixed. No adoption. No production changes.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _parse_ts, _position_key
from research.phase443_full_runtime_combined_capital_sim import CapacityReplayState
from research.phase451_entry_shape_tournament import JST, _build_price_index_to, _now_iso
from research.phase463_trend_pullback_population_tournament import _fill_close_proxy_shadows
from research.phase473_trend_entry_architecture import pass_pbv2
from research.phase476_pre_breakout_gate_replay import _ensure_enriched, _load_replay_pool
from research.phase488_current_runtime_replay import (
    _filter_period,
    _filter_replay_pool_safe,
    _summary_metrics,
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
from research.phase507_classic_indicators import Bar1m, BarIndicatorRow, compute_bar_indicators, ticks_to_1m_bars
from research.phase507_classic_strategy_battle import (
    BASELINE_STRATEGY_ID,
    MIN_BARS_WARMUP,
    _day_rows,
    _run_baseline_runtime,
    _simulate_precomputed_cap,
    _universe_symbols,
)
from research.phase510_classic_system_battle import _strategy_metrics_safe
from research.phase509_t15_t13_signal_audit import _bar_at_entry, _build_bar_cache
from research.phase512_classic_indicator_combination_search import _overfit_row
from research.phase515a_classic_entry_parameter_robustness import (
    EntrySpec,
    _build_momentum_grid,
    _day_high_break,
    scan_entry_pb_exit_day,
)
from research.phase515c_day_high_breakout_refinement import (
    RefinementSpec,
    _entry_context,
    _passes_rules,
    _rule_label,
    scan_day_high_refined,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

PHASE516_VERDICT = "phase516_pbv2_best_classical_overlay_done"
MAX_WORKERS_CAP = 4

OverlayKind = Literal["day_high", "momentum"]
OverlayMode = Literal["AND", "OR"]


@dataclass(frozen=True)
class OverlayDef:
    overlay_id: str
    kind: OverlayKind
    description: str
    refinement: Optional[RefinementSpec] = None
    entry_spec: Optional[EntrySpec] = None


def _overlay_d506() -> RefinementSpec:
    rules = (("r1_max", 6), ("r4_min_adx", 15))
    return RefinementSpec("P515D_D5_06", rules, f"day_high + {_rule_label(rules)}")


def _overlay_r003() -> RefinementSpec:
    rules = (("r1_max", 8),)
    return RefinementSpec("P515C_R_003", rules, f"day_high + {_rule_label(rules)}")


def _overlay_m002() -> EntrySpec:
    for spec in _build_momentum_grid():
        if spec.strategy_id == "P515A_M_002":
            return spec
    return EntrySpec(
        "P515A_M_002",
        "momentum",
        "RSI>45 StochK>D+0 roc",
        (("rsi_thresh", 45), ("stoch_margin", 0), ("extras", frozenset({"roc"}))),
    )


OVERLAY_DEFS: dict[str, OverlayDef] = {
    "O_D506": OverlayDef(
        "O_D506",
        "day_high",
        "day_high AND updates<=6 AND ADX>=15",
        refinement=_overlay_d506(),
    ),
    "O_R003": OverlayDef(
        "O_R003",
        "day_high",
        "day_high AND updates<=8",
        refinement=_overlay_r003(),
    ),
    "O_M002": OverlayDef(
        "O_M002",
        "momentum",
        "RSI>45 AND Stoch K>D AND ROC>0",
        entry_spec=_overlay_m002(),
    ),
}

SCENARIO_SPECS: tuple[tuple[str, Optional[str], Optional[OverlayMode], str], ...] = (
    ("BASELINE", None, None, "PBv2 Runtime"),
    ("O_D506_AND", "O_D506", "AND", "PBv2 AND O_D506"),
    ("O_D506_OR", "O_D506", "OR", "PBv2 OR O_D506"),
    ("O_R003_AND", "O_R003", "AND", "PBv2 AND O_R003"),
    ("O_R003_OR", "O_R003", "OR", "PBv2 OR O_R003"),
    ("O_M002_AND", "O_M002", "AND", "PBv2 AND O_M002"),
    ("O_M002_OR", "O_M002", "OR", "PBv2 OR O_M002"),
)

SUMMARY_FIELDS = [
    "scenario_id",
    "description",
    "overlay_id",
    "overlay_mode",
    "total_pnl_yen_100",
    "profit_factor",
    "max_drawdown_yen_100",
    "trades",
    "win_rate",
    "avg_pnl_yen_100",
    "daily_stability_score",
    "positive_day_count",
    "negative_day_count",
    "baseline_diff_pnl",
    "baseline_diff_pf",
    "baseline_diff_dd",
    "accepted_by_pbv2",
    "accepted_by_overlay",
    "accepted_by_both",
    "overlay_only",
    "pbv2_only",
    "prevented_loss",
    "lost_profit",
    "substitution_profit",
]

DAILY_FIELDS = [
    "scenario_id",
    "day",
    "trade_count",
    "total_pnl_yen_100",
    "profit_factor",
    "win_rate",
]

TRADE_FIELDS = [
    "scenario_id",
    "symbol",
    "day",
    "entry_time",
    "exit_time",
    "pnl_yen_100",
    "exit_reason",
    "attribution",
    "accepted_by_pbv2",
    "accepted_by_overlay",
]

OVERFIT_FIELDS = [
    "scenario_id",
    "top1_trade_profit_share_pct",
    "top10_trade_profit_share_pct",
    "top1_symbol_profit_share_pct",
    "top3_symbol_profit_share_pct",
    "top1_day_profit_share_pct",
    "top3_day_profit_share_pct",
]


def _float(v: Any) -> float:
    try:
        if v is None or v == "":
            return 0.0
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _overlay_pass_at_entry(
    trade: Mapping[str, Any],
    overlay: OverlayDef,
    bar_cache: Mapping[tuple[str, str], tuple[list[Bar1m], list[BarIndicatorRow]]],
) -> bool:
    ent = _parse_ts(str(trade.get("entry_time") or ""))
    if ent is None:
        return False
    sym = str(trade.get("symbol") or "")
    if not sym.endswith(".T"):
        sym = f"{sym}.T"
    day = str(trade.get("day") or "")[:8]
    cached = bar_cache.get((sym, day))
    if not cached:
        return False
    bars, ind_rows = cached
    i = _bar_at_entry(bars, ind_rows, ent)
    if i is None or i < MIN_BARS_WARMUP:
        return False
    if overlay.kind == "day_high":
        assert overlay.refinement is not None
        if not _day_high_break(bars, i):
            return False
        ctx = _entry_context(bars, ind_rows, i)
        return _passes_rules(ctx, overlay.refinement.rules)
    assert overlay.entry_spec is not None
    return overlay.entry_spec.eval(ind_rows[i].values, bars[i], bars, ind_rows, i)


def _prepare_runtime_env(repo_root: Path) -> tuple[list[dict[str, Any]], dict[str, Any], Callable[[Mapping[str, Any]], bool]]:
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

    return list(replay_pool), runtime_shadows, guard_c_block


def _run_and_overlay(
    replay_pool: Sequence[Mapping[str, Any]],
    runtime_shadows: Mapping[str, Any],
    *,
    guard_c_block: Callable[[Mapping[str, Any]], bool],
    bar_cache: Mapping[tuple[str, str], tuple[list[Bar1m], list[BarIndicatorRow]]],
    overlay: OverlayDef,
    mode_suffix: str,
) -> CapacityReplayState:
    def extra_block(trade: Mapping[str, Any]) -> bool:
        if guard_c_block(trade):
            return True
        return not _overlay_pass_at_entry(trade, overlay, bar_cache)

    return _replay_with_extra_block(
        replay_pool,
        runtime_shadows,
        extra_block=extra_block,
        mode_suffix=mode_suffix,
    )


def _pbv2_precomputed_candidates(
    replay_pool: Sequence[Mapping[str, Any]],
    runtime_shadows: Mapping[str, Any],
    guard_c_block: Callable[[Mapping[str, Any]], bool],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for trade in replay_pool:
        if not pass_pbv2(trade) or guard_c_block(trade):
            continue
        key = _position_key(trade)
        si = runtime_shadows.get(key)
        if si is None or not si.eval_ok:
            continue
        ex_dt = datetime.fromtimestamp(si.shadow_exit_ts, tz=JST)
        out.append(
            {
                **dict(trade),
                "exit_time": ex_dt.isoformat(),
                "exit_price": trade.get("exit_price") or trade.get("entry_price"),
                "pnl_yen": si.shadow_pnl_yen,
                "exit_reason": si.shadow_exit_reason,
                "_source": "pbv2",
            }
        )
    return out


def _scan_overlay_day(
    overlay: OverlayDef,
    *,
    day: str,
    universe: Sequence[str],
    bar_cache: Mapping[tuple[str, str], tuple[list[Bar1m], list[BarIndicatorRow]]],
    price_idx: Mapping[tuple[str, str], list[tuple[datetime, float]]],
) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    for sym in universe:
        sym_t = sym if sym.endswith(".T") else f"{sym}.T"
        cached = bar_cache.get((sym_t, day))
        if not cached:
            continue
        bars, ind_rows = cached
        if overlay.kind == "day_high":
            assert overlay.refinement is not None
            trades.extend(
                scan_day_high_refined(
                    overlay.refinement,
                    symbol=sym_t,
                    day=day,
                    bars=bars,
                    ind_rows=ind_rows,
                    price_idx=price_idx,
                )
            )
        else:
            assert overlay.entry_spec is not None
            trades.extend(
                scan_entry_pb_exit_day(
                    overlay.entry_spec,
                    symbol=sym_t,
                    day=day,
                    bars=bars,
                    ind_rows=ind_rows,
                    price_idx=price_idx,
                )
            )
    for t in trades:
        t["_source"] = "overlay"
    return trades


def _merge_or_candidates(
    pbv2_candidates: Sequence[Mapping[str, Any]],
    overlay_trades: Sequence[Mapping[str, Any]],
    *,
    bar_cache: Mapping[tuple[str, str], tuple[list[Bar1m], list[BarIndicatorRow]]],
    overlay: OverlayDef,
    guard_c_block: Callable[[Mapping[str, Any]], bool],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    pbv2_keys: set[str] = set()
    for trade in pbv2_candidates:
        key = _position_key(trade)
        pbv2_keys.add(key)
        merged[key] = {
            **dict(trade),
            "_pbv2": True,
            "_overlay": _overlay_pass_at_entry(trade, overlay, bar_cache),
        }

    for trade in overlay_trades:
        key = _position_key(trade)
        if key in merged:
            merged[key]["_overlay"] = True
            continue
        if key in pbv2_keys:
            continue
        merged[key] = {**dict(trade), "_pbv2": False, "_overlay": True}

    out: list[dict[str, Any]] = []
    for key in sorted(merged, key=lambda k: _parse_ts(str(merged[k].get("entry_time") or "")) or datetime.min.replace(tzinfo=JST)):
        t = merged[key]
        if t.get("_pbv2") and guard_c_block(t):
            continue
        out.append(t)
    return out


def _trade_rows_from_state(
    state: CapacityReplayState,
    scenario_id: str,
    *,
    source_meta: Optional[Mapping[str, Mapping[str, bool]]] = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for log in state.trade_log:
        if not log.get("exit_time"):
            continue
        tr = log.get("trade") or log
        pk = _position_key(tr)
        meta = (source_meta or {}).get(pk, {})
        pbv2 = bool(meta.get("pbv2", True))
        overlay = bool(meta.get("overlay", True))
        if scenario_id.endswith("_AND"):
            pbv2 = True
            overlay = True
        elif scenario_id == "BASELINE":
            pbv2 = True
            overlay = False
        rows.append(
            {
                "scenario_id": scenario_id,
                "symbol": str(tr.get("symbol") or "").replace(".T", ""),
                "day": str(log.get("day") or tr.get("day") or "")[:8],
                "entry_time": tr.get("entry_time"),
                "exit_time": log.get("exit_time"),
                "pnl_yen_100": _float(log.get("pnl_yen")),
                "exit_reason": log.get("exit_reason"),
                "position_key": pk,
                "accepted_by_pbv2": pbv2,
                "accepted_by_overlay": overlay,
            }
        )
    return rows


def _attribution_tag(pbv2: bool, overlay: bool) -> str:
    if pbv2 and overlay:
        return "both"
    if pbv2:
        return "pbv2_only"
    if overlay:
        return "overlay_only"
    return "unknown"


def _attribution_summary(
    scenario_id: str,
    trade_rows: Sequence[Mapping[str, Any]],
    baseline_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    base_pnl = {str(r["position_key"]): _float(r["pnl_yen_100"]) for r in baseline_rows}
    base_keys = set(base_pnl)
    scen_pnl = {str(r["position_key"]): _float(r["pnl_yen_100"]) for r in trade_rows}
    scen_keys = set(scen_pnl)

    accepted_pbv2 = sum(1 for r in trade_rows if r.get("accepted_by_pbv2"))
    accepted_overlay = sum(1 for r in trade_rows if r.get("accepted_by_overlay"))
    accepted_both = sum(1 for r in trade_rows if r.get("accepted_by_pbv2") and r.get("accepted_by_overlay"))
    overlay_only = sum(1 for r in trade_rows if r.get("accepted_by_overlay") and not r.get("accepted_by_pbv2"))
    pbv2_only = sum(1 for r in trade_rows if r.get("accepted_by_pbv2") and not r.get("accepted_by_overlay"))

    excluded = base_keys - scen_keys
    excluded_pnls = [base_pnl[k] for k in excluded]
    prevented = round(sum(-p for p in excluded_pnls if p < 0), 2)
    lost = round(sum(p for p in excluded_pnls if p > 0), 2)
    substitution = round(sum(scen_pnl[k] for k in scen_keys - base_keys), 2)

    return {
        "accepted_by_pbv2": accepted_pbv2,
        "accepted_by_overlay": accepted_overlay,
        "accepted_by_both": accepted_both,
        "overlay_only": overlay_only,
        "pbv2_only": pbv2_only,
        "prevented_loss": prevented,
        "lost_profit": lost,
        "substitution_profit": substitution,
    }


def _overfit_from_trades(scenario_id: str, trade_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    logs = [{"pnl_yen": r["pnl_yen_100"], "day": r["day"], "trade": {"symbol": r["symbol"]}} for r in trade_rows]
    row = _overfit_row(scenario_id, "overlay", logs)
    sym_day = _symbol_day_shares(trade_rows)
    return {
        "scenario_id": scenario_id,
        "top1_trade_profit_share_pct": row.get("top1_trade_profit_share_pct"),
        "top10_trade_profit_share_pct": row.get("top10_trade_profit_share_pct"),
        **sym_day,
    }


def _symbol_day_shares(trade_rows: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    total = sum(_float(r.get("pnl_yen_100")) for r in trade_rows)
    sym_pnl: dict[str, float] = defaultdict(float)
    day_pnl: dict[str, float] = defaultdict(float)
    for r in trade_rows:
        sym_pnl[str(r.get("symbol") or "")] += _float(r.get("pnl_yen_100"))
        day_pnl[str(r.get("day") or "")[:8]] += _float(r.get("pnl_yen_100"))
    sym_rank = sorted(sym_pnl.items(), key=lambda x: x[1], reverse=True)
    day_rank = sorted(day_pnl.items(), key=lambda x: x[1], reverse=True)
    return {
        "top1_symbol_profit_share_pct": round(sym_rank[0][1] / total * 100.0, 2) if total and sym_rank else 0.0,
        "top3_symbol_profit_share_pct": round(sum(v for _, v in sym_rank[:3]) / total * 100.0, 2) if total else 0.0,
        "top1_day_profit_share_pct": round(day_rank[0][1] / total * 100.0, 2) if total and day_rank else 0.0,
        "top3_day_profit_share_pct": round(sum(v for _, v in day_rank[:3]) / total * 100.0, 2) if total else 0.0,
    }


def _mandatory_answers(
    summary_rows: Sequence[Mapping[str, Any]],
    baseline: Mapping[str, Any],
) -> dict[str, Any]:
    b_pnl = _float(baseline.get("total_pnl_yen_100"))
    b_pf = _float(baseline.get("profit_factor"))
    b_dd = _float(baseline.get("max_drawdown_yen_100"))

    overlays = [r for r in summary_rows if r.get("scenario_id") != "BASELINE"]
    beats_pbv2 = [
        r["scenario_id"]
        for r in overlays
        if _float(r.get("total_pnl_yen_100")) > b_pnl
        and _float(r.get("profit_factor") or 0) >= b_pf
        and _float(r.get("max_drawdown_yen_100")) <= b_dd
    ]
    pnl_improved = [r["scenario_id"] for r in overlays if _float(r.get("total_pnl_yen_100")) > b_pnl]
    pf_improved = [r["scenario_id"] for r in overlays if _float(r.get("profit_factor") or 0) > b_pf]
    dd_improved = [r["scenario_id"] for r in overlays if _float(r.get("max_drawdown_yen_100")) < b_dd]

    and_rows = [r for r in overlays if str(r.get("overlay_mode")) == "AND"]
    or_rows = [r for r in overlays if str(r.get("overlay_mode")) == "OR"]
    best_and = max(and_rows, key=lambda r: _float(r.get("total_pnl_yen_100")), default={})
    best_or = max(or_rows, key=lambda r: _float(r.get("total_pnl_yen_100")), default={})

    def _overlay_contrib(oid: str) -> dict[str, Any]:
        and_r = next((r for r in overlays if r.get("overlay_id") == oid and r.get("overlay_mode") == "AND"), {})
        or_r = next((r for r in overlays if r.get("overlay_id") == oid and r.get("overlay_mode") == "OR"), {})
        and_pnl = _float(and_r.get("baseline_diff_pnl"))
        or_pnl = _float(or_r.get("baseline_diff_pnl"))
        return {
            "and_pnl_delta": and_pnl,
            "or_pnl_delta": or_pnl,
            "and_pf_delta": _float(and_r.get("baseline_diff_pf")),
            "or_pf_delta": _float(or_r.get("baseline_diff_pf")),
            "and_dd_delta": _float(and_r.get("baseline_diff_dd")),
            "or_dd_delta": _float(or_r.get("baseline_diff_dd")),
            "helps_pbv2": or_pnl > 0 or (and_pnl > 0 and _float(and_r.get("baseline_diff_dd")) > 0),
        }

    any_value = bool(
        pnl_improved
        or pf_improved
        or dd_improved
        or any(_float(r.get("substitution_profit")) > 0 for r in overlays if r.get("overlay_mode") == "OR")
    )
    adopt = [
        r["scenario_id"]
        for r in overlays
        if _float(r.get("total_pnl_yen_100")) > b_pnl
        and _float(r.get("profit_factor") or 0) > b_pf
        and _float(r.get("max_drawdown_yen_100")) <= b_dd
    ]

    return {
        "1_beats_pbv2_candidates": beats_pbv2,
        "2_pnl_improvement_candidates": pnl_improved,
        "3_pf_improvement_candidates": pf_improved,
        "4_dd_improvement_candidates": dd_improved,
        "5_best_and_candidate": best_and.get("scenario_id"),
        "5_best_and_pnl": best_and.get("total_pnl_yen_100"),
        "6_best_or_candidate": best_or.get("scenario_id"),
        "6_best_or_pnl": best_or.get("total_pnl_yen_100"),
        "7_D506_contributes": _overlay_contrib("O_D506"),
        "8_R003_contributes": _overlay_contrib("O_R003"),
        "9_M002_contributes": _overlay_contrib("O_M002"),
        "10_overlay_has_value": any_value,
        "11_adoption_candidates": adopt,
        "11_adopt_not_allowed": True,
    }


@dataclass
class Phase516Job:
    repo_root: Path
    parallel: bool = True
    max_workers: int = MAX_WORKERS_CAP

    def run(self) -> dict[str, Any]:
        workers = min(max(1, self.max_workers), MAX_WORKERS_CAP)
        bar_cache, days = _build_bar_cache(self.repo_root)
        replay_pool, runtime_shadows, guard_c_block = _prepare_runtime_env(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        price_idx = _build_price_index_to(kabu, period_end=PERIOD_END)
        universe = _universe_symbols(replay_pool)

        summary_rows: list[dict[str, Any]] = []
        daily_rows: list[dict[str, Any]] = []
        trade_rows: list[dict[str, Any]] = []
        states: dict[str, CapacityReplayState] = {}
        source_meta_by_scenario: dict[str, dict[str, dict[str, bool]]] = {}

        baseline_state, baseline_met = _run_baseline_runtime(self.repo_root)
        states["BASELINE"] = baseline_state

        overlay_scan_cache: dict[str, list[dict[str, Any]]] = {oid: [] for oid in OVERLAY_DEFS}
        scan_jobs = [(oid, day) for oid in OVERLAY_DEFS for day in days]

        def _scan_job(oid: str, day: str) -> tuple[str, list[dict[str, Any]]]:
            return oid, _scan_overlay_day(
                OVERLAY_DEFS[oid],
                day=day,
                universe=universe,
                bar_cache=bar_cache,
                price_idx=price_idx,
            )

        if self.parallel and scan_jobs:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(_scan_job, oid, day): (oid, day) for oid, day in scan_jobs}
                for fut in as_completed(futs):
                    oid, chunk = fut.result()
                    overlay_scan_cache[oid].extend(chunk)
        else:
            for oid, day in scan_jobs:
                _, chunk = _scan_job(oid, day)
                overlay_scan_cache[oid].extend(chunk)

        pbv2_candidates = _pbv2_precomputed_candidates(replay_pool, runtime_shadows, guard_c_block)

        for scenario_id, overlay_id, mode, description in SCENARIO_SPECS:
            if scenario_id == "BASELINE":
                state = baseline_state
                source_meta_by_scenario[scenario_id] = {}
            elif mode == "AND":
                assert overlay_id is not None
                overlay = OVERLAY_DEFS[overlay_id]
                state = _run_and_overlay(
                    replay_pool,
                    runtime_shadows,
                    guard_c_block=guard_c_block,
                    bar_cache=bar_cache,
                    overlay=overlay,
                    mode_suffix=f"phase516_{scenario_id.lower()}",
                )
                source_meta_by_scenario[scenario_id] = {}
            else:
                assert overlay_id is not None
                overlay = OVERLAY_DEFS[overlay_id]
                merged = _merge_or_candidates(
                    pbv2_candidates,
                    overlay_scan_cache[overlay_id],
                    bar_cache=bar_cache,
                    overlay=overlay,
                    guard_c_block=guard_c_block,
                )
                meta: dict[str, dict[str, bool]] = {}
                for t in merged:
                    pk = _position_key(t)
                    meta[pk] = {"pbv2": bool(t.get("_pbv2")), "overlay": bool(t.get("_overlay"))}
                source_meta_by_scenario[scenario_id] = meta
                state = _simulate_precomputed_cap(merged, mode=f"phase516_{scenario_id.lower()}")
            states[scenario_id] = state

            entry_rule = "PBv2" if overlay_id is None else f"PBv2+{overlay_id}"
            exit_rule = "RUNTIME" if mode != "OR" else "RUNTIME/PB"
            strat_id = BASELINE_STRATEGY_ID if scenario_id == "BASELINE" else scenario_id
            met = _strategy_metrics_safe(
                state,
                strategy_id=strat_id,
                entry_rule_id=entry_rule,
                exit_rule_id=exit_rule,
            )
            summary_rows.append(
                {
                    "scenario_id": scenario_id,
                    "description": description,
                    "overlay_id": overlay_id or "",
                    "overlay_mode": mode or "",
                    **met,
                }
            )
            for dr in _day_rows(state, scenario_id):
                daily_rows.append({"scenario_id": scenario_id, **{k: v for k, v in dr.items() if k != "strategy_id"}})
            trade_rows.extend(
                _trade_rows_from_state(
                    state,
                    scenario_id,
                    source_meta=source_meta_by_scenario.get(scenario_id),
                )
            )

        baseline_row = next(r for r in summary_rows if r["scenario_id"] == "BASELINE")
        baseline_trades = [t for t in trade_rows if t["scenario_id"] == "BASELINE"]

        for row in summary_rows:
            if row["scenario_id"] == "BASELINE":
                row.update(
                    {
                        "baseline_diff_pnl": 0.0,
                        "baseline_diff_pf": 0.0,
                        "baseline_diff_dd": 0.0,
                        "accepted_by_pbv2": row["trades"],
                        "accepted_by_overlay": 0,
                        "accepted_by_both": 0,
                        "overlay_only": 0,
                        "pbv2_only": row["trades"],
                        "prevented_loss": 0.0,
                        "lost_profit": 0.0,
                        "substitution_profit": 0.0,
                    }
                )
                continue
            diff = _strategy_metrics_safe(
                states[row["scenario_id"]],
                strategy_id=row.get("strategy_id", row["scenario_id"]),
                entry_rule_id=row.get("entry_rule_id", ""),
                exit_rule_id=row.get("exit_rule_id", "RUNTIME"),
                baseline=baseline_row,
            )
            row["baseline_diff_pnl"] = diff["baseline_diff_pnl"]
            row["baseline_diff_pf"] = diff["baseline_diff_pf"]
            row["baseline_diff_dd"] = diff["baseline_diff_dd"]
            scen_trades = [t for t in trade_rows if t["scenario_id"] == row["scenario_id"]]
            row.update(_attribution_summary(row["scenario_id"], scen_trades, baseline_trades))

        annotated: list[dict[str, Any]] = []
        for t in trade_rows:
            pbv2 = bool(t.get("accepted_by_pbv2"))
            overlay = bool(t.get("accepted_by_overlay"))
            annotated.append(
                {
                    **{k: v for k, v in t.items() if k != "position_key"},
                    "attribution": _attribution_tag(pbv2, overlay),
                }
            )

        mandatory = _mandatory_answers(summary_rows, baseline_row)

        top_by_pnl = sorted(
            [r for r in summary_rows if r["scenario_id"] != "BASELINE"],
            key=lambda r: _float(r.get("total_pnl_yen_100")),
            reverse=True,
        )[:3]
        overfit_rows: list[dict[str, Any]] = []
        for row in top_by_pnl:
            sid = row["scenario_id"]
            scen_trades = [t for t in trade_rows if t["scenario_id"] == sid]
            overfit_rows.append(_overfit_from_trades(sid, scen_trades))

        return {
            "verdict": PHASE516_VERDICT,
            "generated_at": _now_iso(),
            "period_start": PERIOD_START,
            "period_end": PERIOD_END,
            "parallel_workers": workers,
            "overlay_defs": {k: {"kind": v.kind, "description": v.description} for k, v in OVERLAY_DEFS.items()},
            "scenario_specs": [
                {"scenario_id": s, "overlay_id": o, "mode": m, "description": d} for s, o, m, d in SCENARIO_SPECS
            ],
            "summary_rows": summary_rows,
            "daily_rows": daily_rows,
            "trade_rows": annotated,
            "overfit_audit": overfit_rows,
            "mandatory_answers": mandatory,
            "baseline": baseline_row,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        paths = {
            "summary": reports / "phase516_overlay_summary.csv",
            "daily": reports / "phase516_overlay_daily.csv",
            "trades": reports / "phase516_overlay_trades.csv",
            "report": reports / "phase516_overlay_report.json",
            "docs": kabu / "docs" / "operations" / "phase516_pbv2_best_classical_overlay.md",
        }
        _write_csv(paths["summary"], SUMMARY_FIELDS, list(result.get("summary_rows") or []))
        _write_csv(paths["daily"], DAILY_FIELDS, list(result.get("daily_rows") or []))
        _write_csv(paths["trades"], TRADE_FIELDS, list(result.get("trade_rows") or []))
        paths["report"].write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        paths["docs"].write_text(_render_docs(result), encoding="utf-8")
        return paths


def _render_docs(result: Mapping[str, Any]) -> str:
    ma = result.get("mandatory_answers") or {}
    lines = [
        "# Phase516 — PBv2 + Best Classical Entry Overlay",
        "",
        f"**Verdict:** `{result.get('verdict')}`",
        f"**Period:** {result.get('period_start')} – {result.get('period_end')}",
        f"**Workers:** {result.get('parallel_workers')}",
        "",
        "## Summary",
        "",
        "| Scenario | Mode | PnL | PF | maxDD | Trades | ΔPnL |",
        "|----------|------|-----|----|-------|--------|------|",
    ]
    for row in result.get("summary_rows") or []:
        lines.append(
            f"| {row.get('scenario_id')} | {row.get('overlay_mode') or '-'} | "
            f"{row.get('total_pnl_yen_100')} | {row.get('profit_factor')} | "
            f"{row.get('max_drawdown_yen_100')} | {row.get('trades')} | "
            f"{row.get('baseline_diff_pnl', 0)} |"
        )
    lines.extend(["", "## Attribution", ""])
    for row in result.get("summary_rows") or []:
        if row.get("scenario_id") == "BASELINE":
            continue
        lines.append(
            f"**{row.get('scenario_id')}**: pbv2={row.get('accepted_by_pbv2')}, "
            f"overlay={row.get('accepted_by_overlay')}, both={row.get('accepted_by_both')}, "
            f"overlay_only={row.get('overlay_only')}, pbv2_only={row.get('pbv2_only')}, "
            f"prevented_loss={row.get('prevented_loss')}, lost_profit={row.get('lost_profit')}, "
            f"substitution_profit={row.get('substitution_profit')}"
        )
    lines.extend(["", "## Overfit audit (top candidates)", ""])
    for row in result.get("overfit_audit") or []:
        lines.append(
            f"**{row.get('scenario_id')}**: top1_trade={row.get('top1_trade_profit_share_pct')}%, "
            f"top10_trade={row.get('top10_trade_profit_share_pct')}%, "
            f"top1_sym={row.get('top1_symbol_profit_share_pct')}%, top3_sym={row.get('top3_symbol_profit_share_pct')}%, "
            f"top1_day={row.get('top1_day_profit_share_pct')}%, top3_day={row.get('top3_day_profit_share_pct')}%"
        )
    d506 = ma.get("7_D506_contributes") or {}
    r003 = ma.get("8_R003_contributes") or {}
    m002 = ma.get("9_M002_contributes") or {}
    lines.extend(
        [
            "",
            "## Mandatory answers",
            "",
            f"1. Beats PBv2 (PnL+PF+DD): **{ma.get('1_beats_pbv2_candidates')}**",
            f"2. PnL improvement: **{ma.get('2_pnl_improvement_candidates')}**",
            f"3. PF improvement: **{ma.get('3_pf_improvement_candidates')}**",
            f"4. DD improvement: **{ma.get('4_dd_improvement_candidates')}**",
            f"5. Best AND: **{ma.get('5_best_and_candidate')}** (PnL {ma.get('5_best_and_pnl')})",
            f"6. Best OR: **{ma.get('6_best_or_candidate')}** (PnL {ma.get('6_best_or_pnl')})",
            f"7. D506 contributes: **{d506.get('helps_pbv2')}** (AND ΔPnL {d506.get('and_pnl_delta')}, OR ΔPnL {d506.get('or_pnl_delta')})",
            f"8. R003 contributes: **{r003.get('helps_pbv2')}** (AND ΔPnL {r003.get('and_pnl_delta')}, OR ΔPnL {r003.get('or_pnl_delta')})",
            f"9. M002 contributes: **{m002.get('helps_pbv2')}** (AND ΔPnL {m002.get('and_pnl_delta')}, OR ΔPnL {m002.get('or_pnl_delta')})",
            f"10. Overlay has value: **{ma.get('10_overlay_has_value')}**",
            f"11. Adoption candidates: **{ma.get('11_adoption_candidates')}** (adopt_not_allowed=True)",
        ]
    )
    return "\n".join(lines) + "\n"
