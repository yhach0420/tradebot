"""
Phase561 — Trailing shadow validation on full period (research only).

Validates T2 / T3 / T6 vs T0 on Phase558 accepted trades (20260529–20260625).
No Runtime changes.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _float, _parse_ts
from research.phase400_holding_time_audit import normalize_exit_reason
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase451_entry_shape_tournament import JST, _build_price_index_to, _now_iso
from research.phase488_current_runtime_replay import _filter_period
from research.phase507_classic_strategy_battle import _universe_symbols
from research.phase509_t15_t13_signal_audit import _build_bar_cache
from research.phase515b_day_high_breakout_dependency_audit import SYMBOL_6976
from research.phase516_pbv2_best_classical_overlay import (
    OVERLAY_DEFS,
    _merge_or_candidates,
    _pbv2_precomputed_candidates,
    _prepare_runtime_env,
    _scan_overlay_day,
)
from research.phase524_live_reentry_guard_and_stop_low_mfe import _latest_live_day
from research.phase535_or_cap_reality_validation import (
    _cap_scenarios,
    _simulate_cap_audited,
)
from research.phase546_entry_cluster_shadow_replay import _trade_key as _cluster_trade_key
from research.phase560_exit_profit_maximization_study import (
    EARLY_RULES,
    TRAILING_SPECS,
    _load_phase558_accepted,
    _num,
    _pnl_pct,
    _series_from_index,
    _shadow_metrics,
    _stream_states,
    simulate_trailing_shadow_exit,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from replay.pnl_yen import compute_pnl_yen_100

PHASE561_VERDICT = "phase561_trailing_shadow_validation_done"
FULL_START = "20260529"
FULL_END = "20260625"
LIVE_START = "20260616"
LOSS_FOCUS_DAYS = ("20260618", "20260617")
PROFIT_FOCUS_DAYS = ("20260622", "20260624")
SYMBOL_9256 = "9256"
SYMBOL_6981 = "6981"
PHASE561_SPEC_IDS = ("T0", "T2", "T3", "T6")
STOP_HIT_TOLERANCE = 8
WIN_RATE_TOLERANCE = 0.05
IMPROVEMENT_DAY_RATE_MIN = 0.55

PHASE561_SPECS = tuple(s for s in TRAILING_SPECS if s.scenario_id in PHASE561_SPEC_IDS)

SUMMARY_FIELDS = [
    "scenario_id",
    "label",
    "window",
    "trades",
    "pnl_yen_100",
    "profit_factor",
    "max_drawdown_yen_100",
    "win_rate",
    "avg_win_yen_100",
    "avg_loss_yen_100",
    "stop_hit_count",
    "trailing_exit_count",
    "session_close_count",
    "mfe_capture_ratio",
    "opportunity_loss_total_pct",
    "early_profit_take_count",
    "delta_pnl_vs_t0",
    "delta_pf_vs_t0",
    "delta_maxdd_vs_t0",
    "delta_stop_hit_vs_t0",
    "runtime_candidate",
]

DAILY_FIELDS = [
    "window",
    "day",
    "scenario_id",
    "daily_pnl_yen_100",
    "daily_pf",
    "daily_maxdd_yen_100",
    "daily_trades",
    "daily_stop_hit",
    "daily_trailing_exit",
    "delta_pnl_vs_t0",
    "improved_vs_t0",
]

LOSS_DAY_FIELDS = [
    "day",
    "scenario_id",
    "daily_pnl_yen_100",
    "delta_pnl_vs_t0",
    "daily_stop_hit",
    "worse_than_t0",
]

PROFIT_DAY_FIELDS = [
    "day",
    "scenario_id",
    "daily_pnl_yen_100",
    "delta_pnl_vs_t0",
    "pnl_cut_vs_t0",
]

DEPENDENCY_FIELDS = [
    "scenario_id",
    "audit",
    "baseline_remaining_pnl_yen_100",
    "scenario_remaining_pnl_yen_100",
    "delta_vs_t0",
    "dependency_worse_than_t0",
]


def _cap_extension_accepted_trades(
    repo_root: Path,
    *,
    period_start: str,
    period_end: str,
) -> list[dict[str, Any]]:
    replay_pool, runtime_shadows, guard_c_block = _prepare_runtime_env(repo_root)
    pool = _filter_period(replay_pool, start=period_start, end=period_end)
    if not pool:
        return []

    pbv2_candidates = _pbv2_precomputed_candidates(replay_pool, runtime_shadows, guard_c_block)
    pbv2_candidates = [
        t for t in pbv2_candidates if period_start <= str(t.get("day") or "")[:8] <= period_end
    ]

    kabu = resolve_kabu_root(repo_root)
    price_idx = _build_price_index_to(kabu, period_end=period_end)
    bar_cache, days = _build_bar_cache(repo_root)
    days_f = [d for d in days if period_start <= d <= period_end]
    universe = _universe_symbols(pool)
    overlay_def = OVERLAY_DEFS["O_R003"]
    overlay_all: list[dict[str, Any]] = []
    for day in days_f:
        overlay_all.extend(
            _scan_overlay_day(
                overlay_def,
                day=day,
                universe=universe,
                bar_cache=bar_cache,
                price_idx=price_idx,
            )
        )
    candidates = _merge_or_candidates(
        pbv2_candidates,
        overlay_all,
        bar_cache=bar_cache,
        overlay=overlay_def,
        guard_c_block=guard_c_block,
    )
    scenario = next(s for s in _cap_scenarios() if s.scenario_id == "CAP_SPLIT_4_1")
    candidates = [t for t in candidates if period_start <= str(t.get("day") or "")[:8] <= period_end]
    sim = _simulate_cap_audited(candidates, scenario=scenario)

    trades: list[dict[str, Any]] = []
    for log in sim.state.trade_log:
        if not log.get("exit_time"):
            continue
        tr = dict(log.get("trade") or log)
        tr["day"] = str(log.get("day") or tr.get("day") or "")[:8]
        tr["symbol"] = str(tr.get("symbol") or "")
        tr["entry_time"] = tr.get("entry_time")
        tr["exit_time"] = log.get("exit_time")
        tr["exit_price"] = tr.get("exit_price") or tr.get("entry_price")
        tr["pnl_yen_100"] = round(_float(log.get("pnl_yen")), 2)
        tr["exit_reason"] = log.get("exit_reason")
        tr["entry_type"] = "OR" if tr.get("_overlay") or tr.get("_source") == "overlay" else "PBV2"
        tr["_segment"] = "cap_extension"
        trades.append(tr)
    return trades


def _load_full_period_accepted(
    repo: Path,
    *,
    full_start: str,
    live_start: str,
    end: str,
) -> list[dict[str, Any]]:
    cap_end = (datetime.strptime(live_start, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
    cap = _cap_extension_accepted_trades(repo, period_start=full_start, period_end=cap_end)
    live = _load_phase558_accepted(repo, live_start=live_start, end=end)
    for t in live:
        t["_segment"] = "live"
    return cap + live


def _early_profit_take(outcome: Mapping[str, Any]) -> bool:
    mfe = _num(outcome.get("mfe_pct"))
    realized = _num(outcome.get("shadow_pnl_pct") or outcome.get("realized_pnl_pct"))
    for _, mfe_thr, pnl_max in EARLY_RULES:
        if mfe >= mfe_thr and realized < pnl_max:
            return True
    return False


def _metrics_with_early(
    outcomes: Sequence[Mapping[str, Any]],
    *,
    baseline: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    base = _shadow_metrics(outcomes, baseline=baseline)
    base["early_profit_take_count"] = sum(1 for r in outcomes if _early_profit_take(r))
    if baseline:
        base["delta_stop_hit_vs_t0"] = int(base.get("stop_hit_count") or 0) - int(
            baseline.get("stop_hit_count") or 0
        )
    else:
        base["delta_pnl_vs_t0"] = 0.0
        base["delta_pf_vs_t0"] = 0.0
        base["delta_maxdd_vs_t0"] = 0.0
        base["delta_stop_hit_vs_t0"] = 0
    return base


def _run_shadow_replay(
    trades: Sequence[Mapping[str, Any]],
    price_idx: Mapping[tuple[str, str], list[tuple[Any, float]]],
) -> dict[str, list[dict[str, Any]]]:
    shadow_by_spec: dict[str, list[dict[str, Any]]] = {s.scenario_id: [] for s in PHASE561_SPECS}
    for trade in trades:
        sym = str(trade.get("symbol") or "")
        day = str(trade.get("day") or "")[:8]
        series = _series_from_index(price_idx, sym, day)
        streamed = _stream_states(trade, series)
        if streamed is None:
            continue
        states, entry_px, ent_ts, imb = streamed
        trade_key = "|".join(_cluster_trade_key(trade))
        peak_mfe = max(float(s["peak_mfe"]) for s in states)
        for spec in PHASE561_SPECS:
            sim = simulate_trailing_shadow_exit(
                states,
                entry_price=entry_px,
                entry_ts=ent_ts,
                imb_pct=imb,
                spec=spec,
            )
            exit_px = float(sim.get("shadow_exit_price") or entry_px)
            pnl_yen = float(sim.get("shadow_pnl_yen_100") or compute_pnl_yen_100(entry_px, exit_px))
            realized = float(sim.get("shadow_pnl_pct") or _pnl_pct(entry_px, exit_px))
            shadow_by_spec[spec.scenario_id].append(
                {
                    "trade_key": trade_key,
                    "symbol": sym.replace(".T", ""),
                    "day": day,
                    "entry_time": trade.get("entry_time"),
                    "exit_time": datetime.fromtimestamp(
                        float(sim.get("shadow_exit_ts") or ent_ts), tz=JST
                    ).isoformat()
                    if float(sim.get("shadow_exit_ts") or 0) > 0
                    else "",
                    "pnl_yen_100": round(pnl_yen, 2),
                    "mfe_pct": round(peak_mfe, 4),
                    "shadow_pnl_pct": round(realized, 6),
                    "opportunity_loss_pct": round(max(0.0, peak_mfe - realized), 6),
                    "mfe_capture_ratio": round(realized / peak_mfe, 4) if peak_mfe > 0 else None,
                    "shadow_exit_reason": normalize_exit_reason(str(sim.get("shadow_exit_reason") or "")),
                    "exit_reason": normalize_exit_reason(str(sim.get("shadow_exit_reason") or "")),
                    "_segment": trade.get("_segment"),
                }
            )
    return shadow_by_spec


def _filter_window(
    outcomes: Sequence[Mapping[str, Any]],
    *,
    live_start: Optional[str] = None,
) -> list[dict[str, Any]]:
    if live_start is None:
        return list(outcomes)
    return [dict(r) for r in outcomes if str(r.get("day") or "")[:8] >= live_start]


def _daily_rows(
    shadow_by_spec: dict[str, list[dict[str, Any]]],
    *,
    live_start: Optional[str] = None,
) -> list[dict[str, Any]]:
    t0_by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in _filter_window(shadow_by_spec.get("T0") or [], live_start=live_start):
        t0_by_day[str(r.get("day") or "")[:8]].append(r)

    rows: list[dict[str, Any]] = []
    all_days = sorted(
        {str(r.get("day") or "")[:8] for spec in PHASE561_SPEC_IDS for r in shadow_by_spec.get(spec) or []}
    )
    if live_start:
        all_days = [d for d in all_days if d >= live_start]

    for day in all_days:
        t0_day = t0_by_day.get(day, [])
        t0_pnl = round(sum(_num(r.get("pnl_yen_100")) for r in t0_day), 2)
        t0_pnls = [_num(r.get("pnl_yen_100")) for r in t0_day]
        for spec_id in PHASE561_SPEC_IDS:
            day_rows = [
                r
                for r in shadow_by_spec.get(spec_id) or []
                if str(r.get("day") or "")[:8] == day
            ]
            pnls = [_num(r.get("pnl_yen_100")) for r in day_rows]
            pnl = round(sum(pnls), 2)
            reasons = [str(r.get("shadow_exit_reason") or r.get("exit_reason") or "") for r in day_rows]
            rows.append(
                {
                    "window": "full_period" if live_start is None else "live_window",
                    "day": day,
                    "scenario_id": spec_id,
                    "daily_pnl_yen_100": pnl,
                    "daily_pf": _pf(pnls),
                    "daily_maxdd_yen_100": round(_max_drawdown_yen(pnls), 2),
                    "daily_trades": len(day_rows),
                    "daily_stop_hit": sum(1 for x in reasons if x == "stop_hit"),
                    "daily_trailing_exit": sum(
                        1 for x in reasons if x in ("trailing_mfe_exit", "trailing_mfe")
                    ),
                    "delta_pnl_vs_t0": round(pnl - t0_pnl, 2),
                    "improved_vs_t0": pnl > t0_pnl,
                }
            )
    return rows


def _improvement_day_rate(daily_rows: Sequence[Mapping[str, Any]], spec_id: str) -> float:
    days = sorted({str(r.get("day") or "") for r in daily_rows})
    improved = 0
    compared = 0
    for day in days:
        t0 = next(
            (r for r in daily_rows if r.get("day") == day and r.get("scenario_id") == "T0"),
            None,
        )
        spec = next(
            (r for r in daily_rows if r.get("day") == day and r.get("scenario_id") == spec_id),
            None,
        )
        if not t0 or not spec:
            continue
        if int(t0.get("daily_trades") or 0) == 0 and int(spec.get("daily_trades") or 0) == 0:
            continue
        compared += 1
        if spec.get("improved_vs_t0"):
            improved += 1
    return round(improved / compared, 4) if compared else 0.0


def _dependency_audit_rows(
    shadow_by_spec: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    t0 = shadow_by_spec.get("T0") or []
    t0_total = sum(_num(r.get("pnl_yen_100")) for r in t0)

    def _sym(r: Mapping[str, Any]) -> str:
        return str(r.get("symbol") or "").replace(".T", "")

    sym_pnl: dict[str, float] = defaultdict(float)
    day_pnl: dict[str, float] = defaultdict(float)
    for r in t0:
        sym_pnl[_sym(r)] += _num(r.get("pnl_yen_100"))
        day_pnl[str(r.get("day") or "")[:8]] += _num(r.get("pnl_yen_100"))
    top10_keys = {
        str(r.get("trade_key"))
        for r in sorted(t0, key=lambda x: _num(x.get("pnl_yen_100")), reverse=True)[:10]
    }
    top3_sym = {s for s, _ in sorted(sym_pnl.items(), key=lambda x: x[1], reverse=True)[:3]}
    top3_day = {d for d, _ in sorted(day_pnl.items(), key=lambda x: x[1], reverse=True)[:3]}

    audits: list[tuple[str, Callable[[Mapping[str, Any]], bool]]] = [
        ("top10_trade_exclusion", lambda r: str(r.get("trade_key")) in top10_keys),
        ("top3_symbol_exclusion", lambda r: _sym(r) in top3_sym),
        ("top3_day_exclusion", lambda r: str(r.get("day") or "")[:8] in top3_day),
        ("6976_exclusion", lambda r: _sym(r) == SYMBOL_6976),
        ("9256_exclusion", lambda r: _sym(r) == SYMBOL_9256),
        ("6981_exclusion", lambda r: _sym(r) == SYMBOL_6981),
    ]

    for spec_id in PHASE561_SPEC_IDS:
        outcomes = shadow_by_spec.get(spec_id) or []
        for audit_name, exclude_fn in audits:
            t0_rem = round(sum(_num(r.get("pnl_yen_100")) for r in t0 if not exclude_fn(r)), 2)
            spec_rem = round(sum(_num(r.get("pnl_yen_100")) for r in outcomes if not exclude_fn(r)), 2)
            rows.append(
                {
                    "scenario_id": spec_id,
                    "audit": audit_name,
                    "baseline_remaining_pnl_yen_100": t0_rem,
                    "scenario_remaining_pnl_yen_100": spec_rem,
                    "delta_vs_t0": round(spec_rem - t0_rem, 2),
                    "dependency_worse_than_t0": spec_rem < t0_rem,
                }
            )
    rows.append(
        {
            "scenario_id": "meta",
            "audit": "t0_total_pnl",
            "baseline_remaining_pnl_yen_100": round(t0_total, 2),
            "scenario_remaining_pnl_yen_100": round(t0_total, 2),
            "delta_vs_t0": 0.0,
            "dependency_worse_than_t0": False,
        }
    )
    return rows


def _runtime_candidate(
    spec_id: str,
    full_metrics: Mapping[str, Any],
    live_metrics: Mapping[str, Any],
    t0_full: Mapping[str, Any],
    t0_live: Mapping[str, Any],
    daily_full: Sequence[Mapping[str, Any]],
    loss_rows: Sequence[Mapping[str, Any]],
    profit_rows: Sequence[Mapping[str, Any]],
    dependency_rows: Sequence[Mapping[str, Any]],
) -> bool:
    if spec_id == "T0":
        return False
    imp_rate = _improvement_day_rate(daily_full, spec_id)
    dep_worse = sum(
        1
        for r in dependency_rows
        if r.get("scenario_id") == spec_id and r.get("dependency_worse_than_t0")
    )
    loss_worse = sum(
        1 for r in loss_rows if r.get("scenario_id") == spec_id and r.get("worse_than_t0")
    )
    profit_cut = sum(
        1 for r in profit_rows if r.get("scenario_id") == spec_id and r.get("pnl_cut_vs_t0")
    )
    return (
        _num(full_metrics.get("delta_pnl_vs_t0")) > 0
        and _num(live_metrics.get("delta_pnl_vs_t0")) > 0
        and _num(full_metrics.get("profit_factor")) > _num(t0_full.get("profit_factor"))
        and _num(live_metrics.get("profit_factor")) > _num(t0_live.get("profit_factor"))
        and _num(full_metrics.get("max_drawdown_yen_100")) <= _num(t0_full.get("max_drawdown_yen_100"))
        and _num(live_metrics.get("max_drawdown_yen_100")) <= _num(t0_live.get("max_drawdown_yen_100"))
        and imp_rate >= IMPROVEMENT_DAY_RATE_MIN
        and int(full_metrics.get("stop_hit_count") or 0)
        <= int(t0_full.get("stop_hit_count") or 0) + STOP_HIT_TOLERANCE
        and _num(full_metrics.get("win_rate")) >= _num(t0_full.get("win_rate")) - WIN_RATE_TOLERANCE
        and dep_worse <= 2
        and loss_worse == 0
        and profit_cut <= 1
    )


def _mandatory_answers(
    summary: Sequence[Mapping[str, Any]],
    daily_full: Sequence[Mapping[str, Any]],
    loss_rows: Sequence[Mapping[str, Any]],
    profit_rows: Sequence[Mapping[str, Any]],
    dependency_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    def _m(spec_id: str, window: str) -> dict[str, Any]:
        return next(
            (r for r in summary if r.get("scenario_id") == spec_id and r.get("window") == window),
            {},
        )

    t0_full = _m("T0", "full_period")
    t0_live = _m("T0", "live_window")
    t2_full = _m("T2", "full_period")
    t2_live = _m("T2", "live_window")
    t3_full = _m("T3", "full_period")
    t3_live = _m("T3", "live_window")
    t6_full = _m("T6", "full_period")
    t6_live = _m("T6", "live_window")

    candidates = [r.get("scenario_id") for r in summary if r.get("runtime_candidate")]
    best = max(
        (r for r in summary if r.get("window") == "full_period" and r.get("scenario_id") != "T0"),
        key=lambda r: (_num(r.get("delta_pnl_vs_t0")), _num(r.get("profit_factor"))),
        default={},
    )

    dep_t2_worse = sum(
        1 for r in dependency_rows if r.get("scenario_id") == "T2" and r.get("dependency_worse_than_t0")
    )

    return {
        "1_t2_full_period_effective": _num(t2_full.get("delta_pnl_vs_t0")) > 0,
        "1_t2_full_delta_pnl": t2_full.get("delta_pnl_vs_t0"),
        "2_t2_live_window_effective": _num(t2_live.get("delta_pnl_vs_t0")) > 0,
        "2_t2_live_delta_pnl": t2_live.get("delta_pnl_vs_t0"),
        "3_t3_effective": _num(t3_full.get("delta_pnl_vs_t0")) > 0 and _num(t3_live.get("delta_pnl_vs_t0")) > 0,
        "3_t3_full_delta_pnl": t3_full.get("delta_pnl_vs_t0"),
        "4_t6_effective": _num(t6_full.get("delta_pnl_vs_t0")) > 0 and _num(t6_live.get("delta_pnl_vs_t0")) > 0,
        "4_t6_full_delta_pnl": t6_full.get("delta_pnl_vs_t0"),
        "5_best_candidate": best.get("scenario_id"),
        "5_best_full_delta_pnl": best.get("delta_pnl_vs_t0"),
        "6_maxdd_not_worse": (
            _num(t2_full.get("max_drawdown_yen_100")) <= _num(t0_full.get("max_drawdown_yen_100"))
            and _num(t2_live.get("max_drawdown_yen_100")) <= _num(t0_live.get("max_drawdown_yen_100"))
        ),
        "7_stop_hit_not_excessive": int(t2_full.get("stop_hit_count") or 0)
        <= int(t0_full.get("stop_hit_count") or 0) + STOP_HIT_TOLERANCE,
        "7_t2_delta_stop_hit": t2_full.get("delta_stop_hit_vs_t0"),
        "8_profit_days_not_cut": sum(
            1 for r in profit_rows if r.get("scenario_id") == "T2" and r.get("pnl_cut_vs_t0")
        )
        <= 1,
        "9_dependency_not_worse": dep_t2_worse <= 2,
        "9_t2_dependency_worse_count": dep_t2_worse,
        "10_advance_runtime_candidate": "T2" in candidates,
        "10_runtime_candidates": sorted(set(candidates)),
        "11_next_phase": (
            "phase562_trailing_t2_runtime_integration"
            if "T2" in candidates
            else "phase562_exit_observability_refinement"
        ),
        "improvement_day_rate": {
            spec_id: _improvement_day_rate(daily_full, spec_id) for spec_id in ("T2", "T3", "T6")
        },
        "loss_day_t2_worse_count": sum(
            1 for r in loss_rows if r.get("scenario_id") == "T2" and r.get("worse_than_t0")
        ),
    }


@dataclass
class Phase561Job:
    repo_root: Path
    full_start: str = FULL_START
    live_start: str = LIVE_START
    period_end: str = FULL_END

    def run(self) -> dict[str, Any]:
        repo = self.repo_root.resolve()
        kabu = resolve_kabu_root(repo)
        end = min(self.period_end, _latest_live_day(repo))
        accepted = _load_full_period_accepted(
            repo, full_start=self.full_start, live_start=self.live_start, end=end
        )
        if not accepted:
            raise RuntimeError("No accepted trades for Phase561")

        price_idx = _build_price_index_to(kabu, period_end=end)
        shadow_by_spec = _run_shadow_replay(accepted, price_idx)

        summary_rows: list[dict[str, Any]] = []
        t0_full_base: dict[str, Any] = {}
        t0_live_base: dict[str, Any] = {}

        for spec in PHASE561_SPECS:
            spec_id = spec.scenario_id
            full_out = shadow_by_spec.get(spec_id) or []
            live_out = _filter_window(full_out, live_start=self.live_start)
            for window, outcomes in (("full_period", full_out), ("live_window", live_out)):
                baseline = t0_full_base if window == "full_period" else t0_live_base
                metrics = _metrics_with_early(outcomes, baseline=baseline if spec_id != "T0" else None)
                if spec_id == "T0":
                    if window == "full_period":
                        t0_full_base = metrics
                    else:
                        t0_live_base = metrics
                label = next((s.label for s in PHASE561_SPECS if s.scenario_id == spec_id), spec_id)
                summary_rows.append(
                    {
                        "scenario_id": spec_id,
                        "label": label,
                        "window": window,
                        **metrics,
                        "runtime_candidate": False,
                    }
                )

        daily_full = _daily_rows(shadow_by_spec)
        daily_live = _daily_rows(shadow_by_spec, live_start=self.live_start)

        t0_daily_pnl: dict[str, float] = {
            str(r.get("day")): _num(r.get("daily_pnl_yen_100"))
            for r in daily_full
            if r.get("scenario_id") == "T0"
        }

        loss_days = set(LOSS_FOCUS_DAYS)
        loss_days.update(d for d, p in t0_daily_pnl.items() if p < 0)

        loss_rows: list[dict[str, Any]] = []
        for day in sorted(loss_days):
            t0_pnl = t0_daily_pnl.get(day, 0.0)
            for spec_id in PHASE561_SPEC_IDS:
                row = next(
                    (r for r in daily_full if r.get("day") == day and r.get("scenario_id") == spec_id),
                    {},
                )
                pnl = _num(row.get("daily_pnl_yen_100"))
                loss_rows.append(
                    {
                        "day": day,
                        "scenario_id": spec_id,
                        "daily_pnl_yen_100": pnl,
                        "delta_pnl_vs_t0": round(pnl - t0_pnl, 2),
                        "daily_stop_hit": row.get("daily_stop_hit"),
                        "worse_than_t0": pnl < t0_pnl,
                    }
                )

        profit_days = set(PROFIT_FOCUS_DAYS)
        profit_days.update(
            d for d, p in t0_daily_pnl.items() if p > 0 and d < self.live_start and d <= "20260613"
        )

        profit_rows: list[dict[str, Any]] = []
        for day in sorted(profit_days):
            t0_pnl = t0_daily_pnl.get(day, 0.0)
            for spec_id in PHASE561_SPEC_IDS:
                row = next(
                    (r for r in daily_full if r.get("day") == day and r.get("scenario_id") == spec_id),
                    {},
                )
                pnl = _num(row.get("daily_pnl_yen_100"))
                profit_rows.append(
                    {
                        "day": day,
                        "scenario_id": spec_id,
                        "daily_pnl_yen_100": pnl,
                        "delta_pnl_vs_t0": round(pnl - t0_pnl, 2),
                        "pnl_cut_vs_t0": pnl < t0_pnl - 500,
                    }
                )

        dependency_rows = _dependency_audit_rows(shadow_by_spec)

        for row in summary_rows:
            spec_id = str(row.get("scenario_id"))
            window = str(row.get("window"))
            if spec_id == "T0":
                continue
            if window != "full_period":
                continue
            live_m = next(
                (r for r in summary_rows if r.get("scenario_id") == spec_id and r.get("window") == "live_window"),
                {},
            )
            row["runtime_candidate"] = _runtime_candidate(
                spec_id,
                row,
                live_m,
                t0_full_base,
                t0_live_base,
                daily_full,
                loss_rows,
                profit_rows,
                dependency_rows,
            )
            live_row = next(
                (r for r in summary_rows if r.get("scenario_id") == spec_id and r.get("window") == "live_window"),
                None,
            )
            if live_row is not None:
                live_row["runtime_candidate"] = row["runtime_candidate"]

        answers = _mandatory_answers(summary_rows, daily_full, loss_rows, profit_rows, dependency_rows)

        replay_counts = {
            spec_id: len(shadow_by_spec.get(spec_id) or [])
            for spec_id in PHASE561_SPEC_IDS
        }

        return {
            "verdict": PHASE561_VERDICT,
            "generated_at": _now_iso(),
            "period_full": f"{self.full_start}-{end}",
            "period_live": f"{self.live_start}-{end}",
            "accepted_trades_total": len(accepted),
            "accepted_trades_cap": sum(1 for t in accepted if t.get("_segment") == "cap_extension"),
            "accepted_trades_live": sum(1 for t in accepted if t.get("_segment") == "live"),
            "shadow_replay_counts": replay_counts,
            "summary": summary_rows,
            "daily_full": daily_full,
            "daily_live": daily_live,
            "loss_day_impact": loss_rows,
            "profit_day_impact": profit_rows,
            "dependency_audit": dependency_rows,
            "mandatory_answers": answers,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        kabu = resolve_kabu_root(self.repo_root)
        reports = resolve_reports_dir(kabu)
        reports.mkdir(parents=True, exist_ok=True)
        docs = kabu / "docs" / "operations" / "phase561_trailing_shadow_validation.md"

        paths = {
            "summary": reports / "phase561_trailing_shadow_summary.csv",
            "daily": reports / "phase561_trailing_daily.csv",
            "loss_day": reports / "phase561_loss_day_impact.csv",
            "profit_day": reports / "phase561_profit_day_impact.csv",
            "dependency": reports / "phase561_dependency_audit.csv",
            "report": reports / "phase561_report.json",
            "docs": docs,
        }
        _write_csv(paths["summary"], SUMMARY_FIELDS, result.get("summary") or [])
        daily = list(result.get("daily_full") or []) + list(result.get("daily_live") or [])
        _write_csv(paths["daily"], DAILY_FIELDS, daily)
        _write_csv(paths["loss_day"], LOSS_DAY_FIELDS, result.get("loss_day_impact") or [])
        _write_csv(paths["profit_day"], PROFIT_DAY_FIELDS, result.get("profit_day_impact") or [])
        _write_csv(paths["dependency"], DEPENDENCY_FIELDS, result.get("dependency_audit") or [])

        payload = {k: v for k, v in result.items() if k not in ("daily_full", "daily_live")}
        paths["report"].write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
        )

        ma = result.get("mandatory_answers") or {}
        lines = [
            "# Phase561 — Trailing Shadow Validation",
            "",
            f"**Verdict:** `{result.get('verdict')}`",
            f"**Generated:** {result.get('generated_at')}",
            f"**Full period:** {result.get('period_full')}",
            f"**Live window:** {result.get('period_live')}",
            f"**Accepted trades:** {result.get('accepted_trades_total')} "
            f"(cap={result.get('accepted_trades_cap')}, live={result.get('accepted_trades_live')})",
            "",
            "## Mandatory answers",
            "",
            f"1. **T2 full period effective?** {ma.get('1_t2_full_period_effective')} (delta={ma.get('1_t2_full_delta_pnl')})",
            f"2. **T2 live window effective?** {ma.get('2_t2_live_window_effective')} (delta={ma.get('2_t2_live_delta_pnl')})",
            f"3. **T3 effective?** {ma.get('3_t3_effective')} (delta={ma.get('3_t3_full_delta_pnl')})",
            f"4. **T6 effective?** {ma.get('4_t6_effective')} (delta={ma.get('4_t6_full_delta_pnl')})",
            f"5. **Best candidate:** {ma.get('5_best_candidate')} (delta={ma.get('5_best_full_delta_pnl')})",
            f"6. **maxDD not worse (T2)?** {ma.get('6_maxdd_not_worse')}",
            f"7. **stop_hit not excessive?** {ma.get('7_stop_hit_not_excessive')} (delta={ma.get('7_t2_delta_stop_hit')})",
            f"8. **Profit days not cut?** {ma.get('8_profit_days_not_cut')}",
            f"9. **Dependency not worse?** {ma.get('9_dependency_not_worse')} (worse_count={ma.get('9_t2_dependency_worse_count')})",
            f"10. **Advance runtime candidate?** {ma.get('10_advance_runtime_candidate')} {ma.get('10_runtime_candidates')}",
            f"11. **Next phase:** {ma.get('11_next_phase')}",
            "",
            "## Improvement day rate",
            "",
            str(ma.get("improvement_day_rate")),
            "",
            "## Outputs",
            "",
            "- `results/reports/phase561_trailing_shadow_summary.csv`",
            "- `results/reports/phase561_trailing_daily.csv`",
            "- `results/reports/phase561_loss_day_impact.csv`",
            "- `results/reports/phase561_profit_day_impact.csv`",
            "- `results/reports/phase561_dependency_audit.csv`",
            "- `results/reports/phase561_report.json`",
        ]
        docs.parent.mkdir(parents=True, exist_ok=True)
        docs.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return paths
