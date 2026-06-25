"""
Phase495 — New Feature Guard Replay (research only).

CAP replay counterfactuals for Phase494 top features. No Runtime/YAML/Entry/Exit/Order/Discord changes.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _parse_ts, _position_key
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase443_full_runtime_combined_capital_sim import CapacityReplayState
from research.phase451_entry_shape_tournament import _build_price_index_to, _now_iso
from research.phase463_trend_pullback_population_tournament import _fill_close_proxy_shadows
from research.phase473_trend_entry_architecture import _entry_block, pass_pbv2
from research.phase476_pre_breakout_gate_replay import _ensure_enriched, _load_replay_pool
from research.phase488_current_runtime_replay import (
    REPLAY_MODE,
    _filter_period,
    _filter_replay_pool_safe,
    _simulate_runtime_replay,
    _summary_metrics,
)
from research.phase493_global_entry_failure_audit import (
    DAY_622,
    PERIOD_END,
    PERIOD_START,
    _classify_cluster,
    _enrich_trade_row,
    _exit_reason,
    _is_loser,
    _is_winner,
    _medians_from_losers,
    _replay_with_extra_block,
    _top_pct_threshold,
)
from research.phase494_new_feature_discovery import _compute_new_features
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

FOCUS_SYMBOLS = ("6976", "6981", "4062", "6522", "6838", "3449", "5367")

GUARD_REPLAY_FIELDS = [
    "scenario", "total_pnl_yen_100", "profit_factor", "maxDD_yen_100", "delta_maxDD_yen_100",
    "accepted", "blocked_total", "blocked_winners", "blocked_losers", "blocked_pnl_yen_100",
    "delta_pnl_yen_100", "baseline_pnl_yen_100", "baseline_PF", "baseline_maxDD_yen_100",
    "stop_hit_baseline", "stop_hit_guard", "stop_hit_reduction",
    "no_progress_baseline", "no_progress_guard", "no_progress_reduction",
    "falling_knife_baseline", "falling_knife_guard", "falling_knife_reduction",
    "high_price_extension_baseline", "high_price_extension_guard", "high_price_extension_reduction",
    "impact_6976", "impact_6981", "impact_4062", "impact_6522", "impact_6838", "impact_3449", "impact_5367",
    "impact_20260622", "impact_AM", "impact_PM",
]

SYMBOL_DAY_FIELDS = [
    "scenario", "symbol", "day", "focus_symbol", "baseline_pnl_yen_100", "guard_pnl_yen_100",
    "delta_pnl_yen_100", "blocked_count", "blocked_pnl_yen_100",
]

ROBUSTNESS_FIELDS = [
    "test", "scenario", "total_pnl_yen_100", "profit_factor", "maxDD_yen_100",
    "delta_pnl_vs_baseline", "trade_count", "accepted",
]


def _float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _session_bucket(entry_time: Any) -> str:
    dt = _parse_ts(str(entry_time or ""))
    if dt is None:
        return "unknown"
    return "PM" if dt.hour >= 12 else "AM"


def _build_feature_environment(
    replay_pool: Sequence[Mapping[str, Any]],
) -> tuple[Callable[[Mapping[str, Any]], dict[str, Any]], dict[str, float]]:
    pb_rows: list[dict[str, Any]] = []
    for trade in replay_pool:
        if not pass_pbv2(trade):
            continue
        day = str(trade.get("day") or "")[:8]
        row = _enrich_trade_row({"trade": trade, "day": day, "pnl_yen": 0, "exit_reason": ""})
        pb_rows.append(row)

    sym_r5: dict[str, list[float]] = defaultdict(list)
    day_r10: dict[str, list[float]] = defaultdict(list)
    for row in pb_rows:
        v5 = _float(row.get("r5"))
        v10 = _float(row.get("r10"))
        if v5 is not None:
            sym_r5[str(row["symbol"])].append(v5)
        if v10 is not None:
            day_r10[str(row["day"])].append(v10)

    sym_median = {s: statistics.median(v) for s, v in sym_r5.items() if v}
    day_stats = {
        d: (statistics.mean(v), statistics.pstdev(v) or 1e-9)
        for d, v in day_r10.items()
        if len(v) >= 2
    }

    def feature_row(trade: Mapping[str, Any]) -> dict[str, Any]:
        day = str(trade.get("day") or "")[:8]
        base = _enrich_trade_row({"trade": trade, "day": day, "pnl_yen": 0, "exit_reason": ""})
        feats = _compute_new_features(
            base,
            symbol_r5_median=sym_median,
            day_r10_stats=day_stats,
            composite_pct={},
        )
        base.update(feats)
        return base

    enriched = [feature_row(t) for t in replay_pool if pass_pbv2(t)]

    def vals(key: str) -> list[float]:
        return [float(r[key]) for r in enriched if _float(r.get(key)) is not None]

    thresholds = {
        "RSY_r5_minus_symbol_median": _top_pct_threshold(vals("RSY_r5_minus_symbol_median"), 80.0),
        "RSY_r10_zscore_in_day": _top_pct_threshold(vals("RSY_r10_zscore_in_day"), 80.0),
        "EXH_chase_intensity": _top_pct_threshold(vals("EXH_chase_intensity"), 80.0),
    }
    return feature_row, thresholds


def _rows_from_state(state: CapacityReplayState) -> list[dict[str, Any]]:
    return [_enrich_trade_row(log) for log in state.trade_log]


def _cluster_count(rows: Sequence[Mapping[str, Any]], cluster: str, *, medians: Mapping[str, float]) -> int:
    return sum(1 for r in rows if _is_loser(r) and _classify_cluster(r, medians=medians) == cluster)


def _exit_count(rows: Sequence[Mapping[str, Any]], reason: str) -> int:
    return sum(1 for r in rows if _exit_reason(r) == reason)


def _counterfactual_row(
    state: CapacityReplayState,
    baseline_state: CapacityReplayState,
    *,
    scenario: str,
    baseline_pnl: float,
    baseline_pf: Any,
    baseline_max_dd: float,
    baseline_rows: Sequence[Mapping[str, Any]],
    medians: Mapping[str, float],
) -> dict[str, Any]:
    met = _summary_metrics(state, initial_equity=1_500_000.0)
    guard_rows = _rows_from_state(state)
    remain_pnl = float(met["total_pnl_yen"])
    delta = remain_pnl - baseline_pnl
    max_dd = float(met["max_drawdown_yen"])
    dd_delta = max_dd - baseline_max_dd

    base_keys = {_position_key(log.get("trade") or {}) for log in baseline_state.trade_log}
    remain_keys = {_position_key(log.get("trade") or {}) for log in state.trade_log}
    blocked_keys = base_keys - remain_keys
    blocked = [r for r in baseline_rows if r["position_key"] in blocked_keys]
    bw = sum(1 for r in blocked if float(r["pnl_yen"]) > 0)
    bl = sum(1 for r in blocked if float(r["pnl_yen"]) < 0)
    bp = sum(float(r["pnl_yen"]) for r in blocked)

    def _sym_pnl(sym: str) -> float:
        return sum(float(r["pnl_yen"]) for r in blocked if r["symbol"] == sym)

    day622 = sum(float(r["pnl_yen"]) for r in blocked if r.get("day") == DAY_622)
    am_block = sum(float(r["pnl_yen"]) for r in blocked if r.get("session_bucket") == "AM")
    pm_block = sum(float(r["pnl_yen"]) for r in blocked if r.get("session_bucket") == "PM")

    sh_b = _exit_count(baseline_rows, "stop_hit")
    sh_g = _exit_count(guard_rows, "stop_hit")
    np_b = _exit_count(baseline_rows, "no_progress_exit")
    np_g = _exit_count(guard_rows, "no_progress_exit")
    fk_b = _cluster_count(baseline_rows, "falling_knife", medians=medians)
    fk_g = _cluster_count(guard_rows, "falling_knife", medians=medians)
    hpe_b = _cluster_count(baseline_rows, "high_price_extension", medians=medians)
    hpe_g = _cluster_count(guard_rows, "high_price_extension", medians=medians)

    return {
        "scenario": scenario,
        "total_pnl_yen_100": round(remain_pnl, 2),
        "profit_factor": met["profit_factor"],
        "maxDD_yen_100": round(max_dd, 2),
        "delta_maxDD_yen_100": round(dd_delta, 2),
        "accepted": met["trade_count"],
        "blocked_total": len(blocked),
        "blocked_winners": bw,
        "blocked_losers": bl,
        "blocked_pnl_yen_100": round(bp, 2),
        "delta_pnl_yen_100": round(delta, 2),
        "baseline_pnl_yen_100": round(baseline_pnl, 2),
        "baseline_PF": baseline_pf,
        "baseline_maxDD_yen_100": round(baseline_max_dd, 2),
        "stop_hit_baseline": sh_b,
        "stop_hit_guard": sh_g,
        "stop_hit_reduction": sh_b - sh_g,
        "no_progress_baseline": np_b,
        "no_progress_guard": np_g,
        "no_progress_reduction": np_b - np_g,
        "falling_knife_baseline": fk_b,
        "falling_knife_guard": fk_g,
        "falling_knife_reduction": fk_b - fk_g,
        "high_price_extension_baseline": hpe_b,
        "high_price_extension_guard": hpe_g,
        "high_price_extension_reduction": hpe_b - hpe_g,
        "impact_6976": round(_sym_pnl("6976"), 2),
        "impact_6981": round(_sym_pnl("6981"), 2),
        "impact_4062": round(_sym_pnl("4062"), 2),
        "impact_6522": round(_sym_pnl("6522"), 2),
        "impact_6838": round(_sym_pnl("6838"), 2),
        "impact_3449": round(_sym_pnl("3449"), 2),
        "impact_5367": round(_sym_pnl("5367"), 2),
        "impact_20260622": round(day622, 2),
        "impact_AM": round(am_block, 2),
        "impact_PM": round(pm_block, 2),
    }


def _symbol_day_rows(
    scenario: str,
    baseline_rows: Sequence[Mapping[str, Any]],
    guard_state: CapacityReplayState,
    *,
    baseline_pnl_by: Mapping[tuple[str, str], float],
) -> list[dict[str, Any]]:
    guard_rows = _rows_from_state(guard_state)
    guard_pnl_by: dict[tuple[str, str], float] = defaultdict(float)
    for r in guard_rows:
        guard_pnl_by[(str(r["symbol"]), str(r["day"]))] += float(r["pnl_yen"])

    guard_keys = {_position_key(log.get("trade") or {}) for log in guard_state.trade_log}
    baseline_keys = {r["position_key"] for r in baseline_rows}
    blocked_keys = baseline_keys - guard_keys
    blocked_by: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for r in baseline_rows:
        if r["position_key"] in blocked_keys:
            blocked_by[(str(r["symbol"]), str(r["day"]))].append(r)

    keys = {k for k in baseline_pnl_by if k[0] in FOCUS_SYMBOLS}
    keys |= {k for k in guard_pnl_by if k[0] in FOCUS_SYMBOLS}
    keys |= {k for k in blocked_by if k[0] in FOCUS_SYMBOLS}
    out: list[dict[str, Any]] = []
    for sym, day in sorted(keys):
        bpnl = baseline_pnl_by.get((sym, day), 0.0)
        gpnl = guard_pnl_by.get((sym, day), 0.0)
        blocked = blocked_by.get((sym, day), [])
        out.append(
            {
                "scenario": scenario,
                "symbol": sym,
                "day": day,
                "focus_symbol": sym in FOCUS_SYMBOLS,
                "baseline_pnl_yen_100": round(bpnl, 2),
                "guard_pnl_yen_100": round(gpnl, 2),
                "delta_pnl_yen_100": round(gpnl - bpnl, 2),
                "blocked_count": len(blocked),
                "blocked_pnl_yen_100": round(sum(float(x["pnl_yen"]) for x in blocked), 2),
            }
        )
    return out


def _verdict(
    *,
    best_cf: Mapping[str, Any],
    baseline_pnl: float,
    day622_share: float,
    robustness_rows: Sequence[Mapping[str, Any]],
) -> str:
    delta = float(best_cf.get("delta_pnl_yen_100") or 0)
    blocked_w = int(best_cf.get("blocked_winners") or 0)
    remaining_pf = float(best_cf.get("profit_factor") or 0)
    baseline_pf = float(best_cf.get("baseline_PF") or 0)

    if delta < 3000:
        return "no_replay_edge"

    loo_rows = [r for r in robustness_rows if str(r.get("test", "")).startswith("LOO_day_")]
    loo_positive = sum(1 for r in loo_rows if float(r.get("delta_pnl_vs_baseline") or 0) > 0)
    loo_ratio = loo_positive / max(1, len(loo_rows))

    ex622 = next((r for r in robustness_rows if r.get("test") == "exclude_6_22"), None)
    ex622_delta = float(ex622.get("delta_pnl_vs_baseline") or 0) if ex622 else 0.0

    if day622_share > 0.35 and float(best_cf.get("impact_20260622") or 0) < -15000 and ex622_delta < delta * 0.3:
        return "overfit_guard"

    if delta > 10000 and remaining_pf > baseline_pf and blocked_w <= 8 and loo_ratio >= 0.55:
        return "new_feature_guard_candidate"

    if delta > 5000 and blocked_w > 10:
        return "needs_forward_shadow"

    if delta > 5000:
        return "needs_forward_shadow"

    return "no_replay_edge"


def run_phase495(*, repo_root: Path, parallel: bool = False, max_workers: int = 2) -> dict[str, Any]:
    max_workers = min(max(2, max_workers), 4)
    kabu = resolve_kabu_root(repo_root)
    reports = resolve_reports_dir(repo_root)
    price_idx = _build_price_index_to(kabu, period_end=PERIOD_END)

    replay_pool, runtime_shadows = _load_replay_pool(reports)
    replay_pool = _filter_period(replay_pool, start=PERIOD_START, end=PERIOD_END)
    runtime_shadows = _fill_close_proxy_shadows(replay_pool, runtime_shadows, price_idx=price_idx)
    replay_pool = _filter_replay_pool_safe(replay_pool, runtime_shadows)
    _ensure_enriched(replay_pool, price_idx=price_idx)

    feature_row, thresholds = _build_feature_environment(replay_pool)

    baseline_state = _simulate_runtime_replay(
        replay_pool,
        runtime_shadows,
        mode=f"{REPLAY_MODE}_phase495_base",
        entry_block_fn=_entry_block(pass_pbv2),
        initial_equity=1_500_000.0,
    )
    baseline_met = _summary_metrics(baseline_state, initial_equity=1_500_000.0)
    baseline_pnl = float(baseline_met["total_pnl_yen"])
    baseline_pf = baseline_met["profit_factor"]
    baseline_max_dd = float(baseline_met["max_drawdown_yen"])
    baseline_rows = _rows_from_state(baseline_state)
    losers = [r for r in baseline_rows if _is_loser(r)]
    medians = _medians_from_losers(losers)

    baseline_pnl_by: dict[tuple[str, str], float] = defaultdict(float)
    for r in baseline_rows:
        baseline_pnl_by[(str(r["symbol"]), str(r["day"]))] += float(r["pnl_yen"])

    def flag_eq(key: str, val: float) -> Callable[[Mapping[str, Any]], bool]:
        def _b(trade: Mapping[str, Any]) -> bool:
            row = feature_row(trade)
            v = _float(row.get(key))
            return v is not None and v == val
        return _b

    def flag_top(key: str, thr: float) -> Callable[[Mapping[str, Any]], bool]:
        def _b(trade: Mapping[str, Any]) -> bool:
            row = feature_row(trade)
            v = _float(row.get(key))
            return v is not None and v >= thr
        return _b

    def block_and(*fns: Callable[[Mapping[str, Any]], bool]) -> Callable[[Mapping[str, Any]], bool]:
        def _b(trade: Mapping[str, Any]) -> bool:
            return all(fn(trade) for fn in fns)
        return _b

    guard_a = flag_eq("PBQ_negative_r5_board_midhigh", 1.0)
    guard_b = flag_top("RSY_r5_minus_symbol_median", thresholds["RSY_r5_minus_symbol_median"])
    guard_c = flag_top("RSY_r10_zscore_in_day", thresholds["RSY_r10_zscore_in_day"])
    guard_d = flag_eq("MST_near_day_high_flag", 1.0)
    guard_e = flag_top("EXH_chase_intensity", thresholds["EXH_chase_intensity"])
    guard_f = block_and(guard_a, guard_d)
    guard_g = block_and(guard_a, guard_b)
    guard_h = block_and(guard_d, guard_e)

    uni_specs = [
        ("A", guard_a),
        ("B", guard_b),
        ("C", guard_c),
        ("D", guard_d),
        ("E", guard_e),
    ]
    uni_deltas: list[tuple[str, float, Callable[[Mapping[str, Any]], bool]]] = []
    for name, fn in uni_specs:
        blocked_pnl = 0.0
        for r in baseline_rows:
            tr = dict(r.get("_trade") or r)
            if fn(tr):
                blocked_pnl += float(r["pnl_yen"])
        uni_deltas.append((name, -blocked_pnl, fn))
    uni_deltas.sort(key=lambda x: x[1], reverse=True)
    guard_i = block_and(uni_deltas[0][2], uni_deltas[1][2])

    cf_specs: list[tuple[str, Callable[[Mapping[str, Any]], bool]]] = [
        ("A_PBQ_negative_r5_board_midhigh", guard_a),
        ("B_RSY_r5_minus_symbol_median_top20", guard_b),
        ("C_RSY_r10_zscore_in_day_top20", guard_c),
        ("D_MST_near_day_high_flag", guard_d),
        ("E_EXH_chase_intensity_top20", guard_e),
        ("F_PBQ_and_MST_near_high", guard_f),
        ("G_PBQ_and_RSY_r5_top20", guard_g),
        ("H_MST_and_EXH_chase_top20", guard_h),
        (f"I_conservative_{uni_deltas[0][0]}_{uni_deltas[1][0]}", guard_i),
    ]

    cf_rows: list[dict[str, Any]] = []
    guard_states: dict[str, CapacityReplayState] = {}

    def _run_cf(name: str, block_fn: Callable[[Mapping[str, Any]], bool]) -> dict[str, Any]:
        st = _replay_with_extra_block(replay_pool, runtime_shadows, extra_block=block_fn, mode_suffix=name[:14])
        guard_states[name] = st
        return _counterfactual_row(
            st,
            baseline_state,
            scenario=name,
            baseline_pnl=baseline_pnl,
            baseline_pf=baseline_pf,
            baseline_max_dd=baseline_max_dd,
            baseline_rows=baseline_rows,
            medians=medians,
        )

    if parallel and len(cf_specs) > 1:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = {ex.submit(_run_cf, n, b): n for n, b in cf_specs}
            for fut in as_completed(futs):
                cf_rows.append(fut.result())
    else:
        for name, block_fn in cf_specs:
            cf_rows.append(_run_cf(name, block_fn))

    cf_rows.sort(key=lambda r: float(r.get("delta_pnl_yen_100") or 0), reverse=True)
    best_cf = cf_rows[0] if cf_rows else {}
    best_name = str(best_cf.get("scenario") or cf_specs[0][0])
    best_block_fn = dict(cf_specs).get(best_name, cf_specs[0][1])

    symbol_day_rows: list[dict[str, Any]] = []
    for name, _ in cf_specs:
        st = guard_states.get(name)
        if st is None:
            continue
        symbol_day_rows.extend(_symbol_day_rows(name, baseline_rows, st, baseline_pnl_by=baseline_pnl_by))

    day622_pnl = sum(float(r["pnl_yen"]) for r in baseline_rows if r.get("day") == DAY_622)
    day622_share = abs(day622_pnl / baseline_pnl) if baseline_pnl else 0.0

    robustness_rows: list[dict[str, Any]] = []
    days = sorted({str(r["day"]) for r in baseline_rows})

    def _rob_row(test: str, pool: Sequence[Mapping[str, Any]], *, suffix: str) -> dict[str, Any]:
        st_base = _simulate_runtime_replay(
            pool,
            runtime_shadows,
            mode=f"{REPLAY_MODE}_b_{suffix}",
            entry_block_fn=_entry_block(pass_pbv2),
            initial_equity=1_500_000.0,
        )
        base_met = _summary_metrics(st_base, initial_equity=1_500_000.0)
        st_g = _replay_with_extra_block(pool, runtime_shadows, extra_block=best_block_fn, mode_suffix=f"g_{suffix}")
        g_met = _summary_metrics(st_g, initial_equity=1_500_000.0)
        return {
            "test": test,
            "scenario": best_name,
            "total_pnl_yen_100": round(float(g_met["total_pnl_yen"]), 2),
            "profit_factor": g_met["profit_factor"],
            "maxDD_yen_100": round(float(g_met["max_drawdown_yen"]), 2),
            "delta_pnl_vs_baseline": round(float(g_met["total_pnl_yen"]) - float(base_met["total_pnl_yen"]), 2),
            "trade_count": g_met["trade_count"],
            "accepted": g_met["trade_count"],
        }

    for day in days:
        pool_day = [t for t in replay_pool if str(t.get("day") or "")[:8] != day]
        if len(pool_day) < 50:
            continue
        robustness_rows.append(_rob_row(f"LOO_day_{day}", pool_day, suffix=f"loo_{day}"))

    for test_name, sym in (
        ("exclude_6976", "6976.T"),
        ("exclude_6522", "6522.T"),
        ("exclude_4062", "4062.T"),
        ("exclude_6981", "6981.T"),
    ):
        pool_ex = [t for t in replay_pool if str(t.get("symbol") or "") != sym]
        robustness_rows.append(_rob_row(test_name, pool_ex, suffix=test_name))

    sym_counts = Counter(str(r["symbol"]) for r in baseline_rows)
    top_sym_code = sym_counts.most_common(1)[0][0] if sym_counts else ""
    pool_top = [t for t in replay_pool if str(t.get("symbol") or "").replace(".T", "") != top_sym_code]
    robustness_rows.append(_rob_row("exclude_top_symbol", pool_top, suffix="ex_top_sym"))

    pool_am = [t for t in replay_pool if _session_bucket(t.get("entry_time")) == "AM"]
    pool_pm = [t for t in replay_pool if _session_bucket(t.get("entry_time")) == "PM"]
    pool_622 = [t for t in replay_pool if str(t.get("day") or "")[:8] == DAY_622]
    pool_ex622 = [t for t in replay_pool if str(t.get("day") or "")[:8] != DAY_622]

    if len(pool_am) >= 30:
        robustness_rows.append(_rob_row("AM_only", pool_am, suffix="am_only"))
    if len(pool_pm) >= 30:
        robustness_rows.append(_rob_row("PM_only", pool_pm, suffix="pm_only"))
    if pool_622:
        robustness_rows.append(_rob_row("6_22_only", pool_622, suffix="d622_only"))
    robustness_rows.append(_rob_row("exclude_6_22", pool_ex622, suffix="ex_622"))

    verdict = _verdict(
        best_cf=best_cf,
        baseline_pnl=baseline_pnl,
        day622_share=day622_share,
        robustness_rows=robustness_rows,
    )

    am_rows = [r for r in baseline_rows if r.get("session_bucket") == "AM"]
    pm_rows = [r for r in baseline_rows if r.get("session_bucket") == "PM"]
    am_pf = _pf([float(r["pnl_yen"]) for r in am_rows])
    pm_pf = _pf([float(r["pnl_yen"]) for r in pm_rows])

    loo_rows = [r for r in robustness_rows if str(r.get("test", "")).startswith("LOO_day_")]
    loo_positive = sum(1 for r in loo_rows if float(r.get("delta_pnl_vs_baseline") or 0) > 0)
    ex622_row = next((r for r in robustness_rows if r.get("test") == "exclude_6_22"), {})

    mandatory = {
        "1_best_guard": best_name,
        "2_delta_pnl": best_cf.get("delta_pnl_yen_100"),
        "3_pf_improvement": round(float(best_cf.get("profit_factor") or 0) - float(baseline_pf or 0), 4),
        "4_maxDD_change": best_cf.get("delta_maxDD_yen_100"),
        "5_blocked_winners": best_cf.get("blocked_winners"),
        "6_blocked_losers": best_cf.get("blocked_losers"),
        "7_falling_knife_reduction": best_cf.get("falling_knife_reduction"),
        "8_high_price_extension_reduction": best_cf.get("high_price_extension_reduction"),
        "9_impact_6976": best_cf.get("impact_6976"),
        "10_impact_6522": best_cf.get("impact_6522"),
        "11_hurts_am": float(best_cf.get("impact_AM") or 0) < -5000,
        "12_improves_pm": float(best_cf.get("impact_PM") or 0) > 0,
        "13_day622_dependent": day622_share > 0.30 and abs(float(best_cf.get("impact_20260622") or 0)) > abs(float(best_cf.get("delta_pnl_yen_100") or 0)) * 0.4,
        "14_overfit_risk": (
            "high" if day622_share > 0.35 and float(ex622_row.get("delta_pnl_vs_baseline") or 0) < float(best_cf.get("delta_pnl_yen_100") or 0) * 0.4
            else "moderate" if loo_positive < len(loo_rows) * 0.6
            else "low"
        ),
        "15_runtime_candidate": verdict == "new_feature_guard_candidate" and int(best_cf.get("blocked_winners") or 0) <= 5,
        "16_shadow_candidate": verdict in ("new_feature_guard_candidate", "needs_forward_shadow"),
        "17_next_action": (
            f"Forward-shadow {best_name} on live entries"
            if verdict in ("new_feature_guard_candidate", "needs_forward_shadow")
            else "No guard — continue PBv2 baseline; monitor Phase494 features in shadow only"
        ),
        "verdict": verdict,
        "baseline_pnl_yen_100": baseline_pnl,
        "baseline_PF": baseline_pf,
        "baseline_maxDD_yen_100": baseline_max_dd,
        "thresholds": thresholds,
        "I_conservative_pair": f"{uni_deltas[0][0]}_{uni_deltas[1][0]}",
        "am_pf": am_pf,
        "pm_pf": pm_pf,
        "loo_positive_days": loo_positive,
        "loo_total_days": len(loo_rows),
    }

    return {
        "generated_at": _now_iso(),
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "verdict": verdict,
        "mandatory_answers": mandatory,
        "_guard_replay": cf_rows,
        "_symbol_day": symbol_day_rows,
        "_robustness": robustness_rows,
    }


@dataclass
class Phase495Job:
    repo_root: Path
    parallel: bool = False
    max_workers: int = 2

    def run(self) -> dict[str, Any]:
        return run_phase495(
            repo_root=self.repo_root,
            parallel=self.parallel,
            max_workers=self.max_workers,
        )

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        doc_root = self.repo_root / "kabu_native"
        if not (doc_root / "docs").is_dir():
            doc_root = self.repo_root
        paths = {
            "guard_replay": reports / "phase495_new_feature_guard_replay.csv",
            "symbol_day": reports / "phase495_new_feature_guard_symbol_day.csv",
            "robustness": reports / "phase495_new_feature_guard_robustness.csv",
            "summary": reports / "phase495_summary.json",
            "report": doc_root / "docs" / "operations" / "phase495_new_feature_guard_replay.md",
        }
        _write_csv(paths["guard_replay"], GUARD_REPLAY_FIELDS, list(result.get("_guard_replay") or []))
        _write_csv(paths["symbol_day"], SYMBOL_DAY_FIELDS, list(result.get("_symbol_day") or []))
        _write_csv(paths["robustness"], ROBUSTNESS_FIELDS, list(result.get("_robustness") or []))
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        self._write_report(paths["report"], result)
        return paths

    def _write_report(self, report: Path, result: Mapping[str, Any]) -> None:
        m = result.get("mandatory_answers") or {}
        lines = [
            "# Phase495 — New Feature Guard Replay",
            "",
            f"**Verdict:** `{result.get('verdict')}`",
            f"**Period:** {result.get('period_start')} — {result.get('period_end')}",
            "",
            "## 必須回答",
            "",
        ]
        for key in (
            "1_best_guard", "2_delta_pnl", "3_pf_improvement", "4_maxDD_change",
            "5_blocked_winners", "6_blocked_losers", "7_falling_knife_reduction",
            "8_high_price_extension_reduction", "9_impact_6976", "10_impact_6522",
            "11_hurts_am", "12_improves_pm", "13_day622_dependent", "14_overfit_risk",
            "15_runtime_candidate", "16_shadow_candidate", "17_next_action",
        ):
            lines.append(f"- **{key}:** {m.get(key)}")
        lines.extend(["", "## Guard scenarios", "", "```json"])
        lines.append(json.dumps(result.get("_guard_replay"), indent=2, ensure_ascii=False, default=str))
        lines.append("```")
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("\n".join(lines), encoding="utf-8")
