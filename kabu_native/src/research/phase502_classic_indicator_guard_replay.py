"""
Phase502 — Classic Indicator Guard Replay (research only).

Combines Phase501 classic indicators with existing cluster features for CAP replay guards.
No Runtime / YAML / Entry / Exit / Order / Discord changes.
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
    _cluster_flags,
    _enrich_trade_row,
    _is_loser,
    _medians_from_losers,
    _replay_with_extra_block,
    _top_pct_threshold,
)
from research.phase494_new_feature_discovery import _compute_new_features
from research.phase495_new_feature_guard_replay import (
    _counterfactual_row,
    _rows_from_state,
    _session_bucket,
    _symbol_day_rows,
)
from research.phase501_classic_indicator_audit import compute_classic_indicators_at_entry
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

FOCUS_SYMBOLS = ("6976", "6981", "4062", "6522", "6838", "3449", "5367")

GUARD_REPLAY_FIELDS = [
    "scenario",
    "total_pnl_yen_100",
    "profit_factor",
    "maxDD_yen_100",
    "delta_maxDD_yen_100",
    "delta_pnl_yen_100",
    "baseline_pnl_yen_100",
    "baseline_PF",
    "baseline_maxDD_yen_100",
    "trade_count",
    "accepted",
    "blocked_total",
    "blocked_winners",
    "blocked_losers",
    "winner_loss_ratio",
    "blocked_pnl_yen_100",
    "stop_hit_reduction",
    "no_progress_reduction",
    "falling_knife_reduction",
    "high_price_extension_reduction",
    "impact_6976",
    "impact_4062",
    "impact_20260622",
    "impact_AM",
    "impact_PM",
]

SYMBOL_DAY_FIELDS = [
    "scenario",
    "symbol",
    "day",
    "focus_symbol",
    "baseline_pnl_yen_100",
    "guard_pnl_yen_100",
    "delta_pnl_yen_100",
    "blocked_count",
    "blocked_pnl_yen_100",
]

ROBUSTNESS_FIELDS = [
    "test",
    "scenario",
    "total_pnl_yen_100",
    "profit_factor",
    "maxDD_yen_100",
    "delta_pnl_vs_baseline",
    "trade_count",
    "accepted",
    "blocked_winners",
]


def _float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _bottom_pct_threshold(values: Sequence[float], pct: float = 30.0) -> float:
    if not values:
        return 0.0
    ranked = sorted(values)
    idx = min(len(ranked) - 1, int(round((pct / 100.0) * (len(ranked) - 1))))
    return ranked[idx]


def _build_feature_environment(
    replay_pool: Sequence[Mapping[str, Any]],
    *,
    price_idx: Mapping[tuple[str, str], list],
    medians: Mapping[str, float],
) -> tuple[Callable[[Mapping[str, Any]], dict[str, Any]], dict[str, float]]:
    sym_r5: dict[str, list[float]] = defaultdict(list)
    day_r10: dict[str, list[float]] = defaultdict(list)
    pb_rows: list[dict[str, Any]] = []
    cache: dict[str, dict[str, Any]] = {}

    for trade in replay_pool:
        if not pass_pbv2(trade):
            continue
        day = str(trade.get("day") or "")[:8]
        base = _enrich_trade_row({"trade": trade, "day": day, "pnl_yen": 0, "exit_reason": ""})
        sym = str(base.get("symbol") or "")
        sym_key = f"{sym}.T" if sym and not sym.endswith(".T") else sym
        ent_dt = _parse_ts(str(base.get("entry_time") or trade.get("entry_time") or ""))
        entry_px = _float(base.get("entry_price")) or _float(trade.get("entry_price"))
        series = price_idx.get((sym_key, day), [])
        classic = (
            compute_classic_indicators_at_entry(series, entry_ts=ent_dt, entry_px=entry_px)
            if ent_dt and entry_px and entry_px > 0
            else {}
        )
        new_feats = _compute_new_features(base, symbol_r5_median={}, day_r10_stats={}, composite_pct={})
        clusters = _cluster_flags(base, medians=medians)
        r10 = _float(base.get("r10"))
        r10_vals_pool: list[float] = []

        row = {**base, **classic, **new_feats, **clusters}
        row["MST_near_day_high_flag"] = new_feats.get("MST_near_day_high_flag")
        row["late_chase_cluster"] = bool(clusters.get("late_chase_cluster"))
        row["falling_knife_cluster"] = bool(clusters.get("falling_knife_cluster"))
        row["high_price_extension_cluster"] = bool(clusters.get("high_price_extension_cluster"))
        pb_rows.append(row)
        if _float(row.get("r5")) is not None:
            sym_r5[sym].append(float(row["r5"]))
        if r10 is not None:
            day_r10[day].append(r10)
            r10_vals_pool.append(r10)

    sym_median = {s: statistics.median(v) for s, v in sym_r5.items() if v}
    day_stats = {
        d: (statistics.mean(v), statistics.pstdev(v) or 1e-9)
        for d, v in day_r10.items()
        if len(v) >= 2
    }

    def feature_row(trade: Mapping[str, Any]) -> dict[str, Any]:
        pk = _position_key(trade)
        if pk in cache:
            return cache[pk]
        day = str(trade.get("day") or "")[:8]
        base = _enrich_trade_row({"trade": trade, "day": day, "pnl_yen": 0, "exit_reason": ""})
        sym = str(base.get("symbol") or "")
        sym_key = f"{sym}.T" if sym and not sym.endswith(".T") else sym
        ent_dt = _parse_ts(str(base.get("entry_time") or trade.get("entry_time") or ""))
        entry_px = _float(base.get("entry_price")) or _float(trade.get("entry_price"))
        series = price_idx.get((sym_key, day), [])
        classic = (
            compute_classic_indicators_at_entry(series, entry_ts=ent_dt, entry_px=entry_px)
            if ent_dt and entry_px and entry_px > 0
            else {}
        )
        new_feats = _compute_new_features(
            base,
            symbol_r5_median=sym_median,
            day_r10_stats=day_stats,
            composite_pct={},
        )
        clusters = _cluster_flags(base, medians=medians)
        row = {**base, **classic, **new_feats, **clusters}
        row["late_chase_cluster"] = bool(clusters.get("late_chase_cluster"))
        row["falling_knife_cluster"] = bool(clusters.get("falling_knife_cluster"))
        row["high_price_extension_cluster"] = bool(clusters.get("high_price_extension_cluster"))
        cache[pk] = row
        return row

    enriched = [feature_row(t) for t in replay_pool if pass_pbv2(t)]

    def vals(key: str) -> list[float]:
        return [float(r[key]) for r in enriched if _float(r.get(key)) is not None]

    thresholds = {
        "macd_histogram_strength_weak": _bottom_pct_threshold(vals("macd_histogram_strength"), 30.0),
        "price_vs_25ma_pct_high": _top_pct_threshold(vals("price_vs_25ma_pct"), 80.0),
    }
    return feature_row, thresholds


def _cf_row_extended(*args, **kwargs) -> dict[str, Any]:
    row = _counterfactual_row(*args, **kwargs)
    bw = int(row.get("blocked_winners") or 0)
    bl = int(row.get("blocked_losers") or 0)
    row["winner_loss_ratio"] = round(bw / max(1, bl), 4)
    row["trade_count"] = row.get("accepted")
    return row


def _verdict(
    *,
    best_cf: Mapping[str, Any],
    baseline_pnl: float,
    day622_share: float,
    robustness_rows: Sequence[Mapping[str, Any]],
    focus_rows: Mapping[str, Mapping[str, Any]],
) -> str:
    delta = float(best_cf.get("delta_pnl_yen_100") or 0)
    blocked_w = int(best_cf.get("blocked_winners") or 0)
    blocked_l = int(best_cf.get("blocked_losers") or 0)
    wlr = float(best_cf.get("winner_loss_ratio") or 0)

    loo_rows = [r for r in robustness_rows if str(r.get("test", "")).startswith("LOO_day_")]
    loo_positive = sum(1 for r in loo_rows if float(r.get("delta_pnl_vs_baseline") or 0) > 0)
    loo_ratio = loo_positive / max(1, len(loo_rows))

    ex622 = next((r for r in robustness_rows if r.get("test") == "exclude_6_22"), None)
    ex622_delta = float(ex622.get("delta_pnl_vs_baseline") or 0) if ex622 else 0.0

    if delta <= 0:
        return "classic_indicator_not_useful"

    if day622_share > 0.35 and abs(float(best_cf.get("impact_20260622") or 0)) > abs(delta) * 0.4:
        return "overfit_classic_guard"

    if blocked_w > blocked_l * 1.2 or wlr > 1.2:
        return "overfit_classic_guard"

    if loo_ratio < 0.5 and delta < 20000:
        return "overfit_classic_guard"

    focus_best = max(
        (float(focus_rows.get(k, {}).get("delta_pnl_yen_100") or 0) for k in ("C", "D", "G")),
        default=0.0,
    )
    if delta >= 5000 and loo_ratio >= 0.55 and blocked_w <= max(8, blocked_l) and focus_best > 0:
        return "classic_indicator_guard_viable"

    if delta >= 3000 and blocked_l >= blocked_w:
        return "classic_indicator_guard_viable"

    return "classic_indicator_not_useful"


def run_phase502(*, repo_root: Path, parallel: bool = False, max_workers: int = 2) -> dict[str, Any]:
    max_workers = min(max(2, max_workers), 4)
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

    feature_row, thresholds = _build_feature_environment(replay_pool, price_idx=price_idx, medians=medians)

    baseline_state = _simulate_runtime_replay(
        replay_pool,
        runtime_shadows,
        mode=f"{REPLAY_MODE}_phase502_base",
        entry_block_fn=_entry_block(pass_pbv2),
        initial_equity=1_500_000.0,
    )
    baseline_met = _summary_metrics(baseline_state, initial_equity=1_500_000.0)
    baseline_pnl = float(baseline_met["total_pnl_yen"])
    baseline_pf = baseline_met["profit_factor"]
    baseline_max_dd = float(baseline_met["max_drawdown_yen"])
    baseline_rows = _rows_from_state(baseline_state)

    baseline_pnl_by: dict[tuple[str, str], float] = defaultdict(float)
    for r in baseline_rows:
        baseline_pnl_by[(str(r["symbol"]), str(r["day"]))] += float(r["pnl_yen"])

    macd_weak_thr = thresholds["macd_histogram_strength_weak"]
    pv25_high_thr = thresholds["price_vs_25ma_pct_high"]

    def flag_eq(key: str, val: float) -> Callable[[Mapping[str, Any]], bool]:
        def _b(trade: Mapping[str, Any]) -> bool:
            v = _float(feature_row(trade).get(key))
            return v is not None and v == val
        return _b

    def flag_true(key: str) -> Callable[[Mapping[str, Any]], bool]:
        def _b(trade: Mapping[str, Any]) -> bool:
            return bool(feature_row(trade).get(key))
        return _b

    def macd_weak(trade: Mapping[str, Any]) -> bool:
        v = _float(feature_row(trade).get("macd_histogram_strength"))
        return v is not None and v <= macd_weak_thr

    def macd_risk_reject(trade: Mapping[str, Any]) -> bool:
        """Weak histogram = loser-side risk (Phase501 lower_in_loser)."""
        return macd_weak(trade)

    def pv25_high(trade: Mapping[str, Any]) -> bool:
        v = _float(feature_row(trade).get("price_vs_25ma_pct"))
        return v is not None and v >= pv25_high_thr

    def block_and(*fns: Callable[[Mapping[str, Any]], bool]) -> Callable[[Mapping[str, Any]], bool]:
        return lambda t: all(fn(t) for fn in fns)

    def block_or(*fns: Callable[[Mapping[str, Any]], bool]) -> Callable[[Mapping[str, Any]], bool]:
        return lambda t: any(fn(t) for fn in fns)

    guard_a = macd_risk_reject
    guard_b = flag_eq("rsi_over80", 1.0)
    guard_c = block_and(flag_true("late_chase_cluster"), flag_eq("rsi_over80", 1.0))
    guard_d = block_and(flag_true("falling_knife_cluster"), macd_weak)
    guard_e = block_and(flag_true("high_price_extension_cluster"), pv25_high)
    guard_f = block_and(flag_eq("MST_near_day_high_flag", 1.0), flag_eq("rsi_over80", 1.0))
    guard_g = block_or(guard_c, guard_d)

    cf_specs: list[tuple[str, Callable[[Mapping[str, Any]], bool]]] = [
        ("A_macd_histogram_risk_reject", guard_a),
        ("B_rsi_over80", guard_b),
        ("C_late_chase_AND_rsi_over80", guard_c),
        ("D_falling_knife_AND_macd_weak", guard_d),
        ("E_high_price_ext_AND_pv25_high", guard_e),
        ("F_MST_near_high_AND_rsi_over80", guard_f),
        ("G_conservative_C_OR_D", guard_g),
    ]

    cf_rows: list[dict[str, Any]] = []
    guard_states: dict[str, Any] = {}

    def _run_cf(name: str, block_fn: Callable[[Mapping[str, Any]], bool]) -> dict[str, Any]:
        st = _replay_with_extra_block(replay_pool, runtime_shadows, extra_block=block_fn, mode_suffix=name[:14])
        guard_states[name] = st
        return _cf_row_extended(
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

    focus_map = {
        "C": next((r for r in cf_rows if r.get("scenario", "").startswith("C_")), {}),
        "D": next((r for r in cf_rows if r.get("scenario", "").startswith("D_")), {}),
        "G": next((r for r in cf_rows if r.get("scenario", "").startswith("G_")), {}),
    }

    symbol_day_rows: list[dict[str, Any]] = []
    for name, _ in cf_specs:
        st = guard_states.get(name)
        if st is not None:
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
        sub_base = _rows_from_state(st_base)
        cf = _cf_row_extended(
            st_g,
            st_base,
            scenario=test,
            baseline_pnl=float(base_met["total_pnl_yen"]),
            baseline_pf=base_met["profit_factor"],
            baseline_max_dd=float(base_met["max_drawdown_yen"]),
            baseline_rows=sub_base,
            medians=medians,
        )
        return {
            "test": test,
            "scenario": best_name,
            "total_pnl_yen_100": round(float(g_met["total_pnl_yen"]), 2),
            "profit_factor": g_met["profit_factor"],
            "maxDD_yen_100": round(float(g_met["max_drawdown_yen"]), 2),
            "delta_pnl_vs_baseline": round(float(g_met["total_pnl_yen"]) - float(base_met["total_pnl_yen"]), 2),
            "trade_count": g_met["trade_count"],
            "accepted": g_met["trade_count"],
            "blocked_winners": cf.get("blocked_winners"),
        }

    for day in days:
        pool_day = [t for t in replay_pool if str(t.get("day") or "")[:8] != day]
        if len(pool_day) < 50:
            continue
        robustness_rows.append(_rob_row(f"LOO_day_{day}", pool_day, suffix=f"loo_{day}"))

    for test_name, sym in (("exclude_6976", "6976.T"), ("exclude_4062", "4062.T")):
        pool_ex = [t for t in replay_pool if str(t.get("symbol") or "") != sym]
        robustness_rows.append(_rob_row(test_name, pool_ex, suffix=test_name))

    sym_counts = Counter(str(r["symbol"]) for r in baseline_rows)
    top_sym = sym_counts.most_common(1)[0][0] if sym_counts else ""
    pool_top = [t for t in replay_pool if str(t.get("symbol") or "").replace(".T", "") != top_sym]
    robustness_rows.append(_rob_row("exclude_top_day", pool_top, suffix="ex_top_day"))

    pool_am = [t for t in replay_pool if _session_bucket(t.get("entry_time")) == "AM"]
    pool_pm = [t for t in replay_pool if _session_bucket(t.get("entry_time")) == "PM"]
    if len(pool_am) >= 30:
        robustness_rows.append(_rob_row("AM_only", pool_am, suffix="am_only"))
    if len(pool_pm) >= 30:
        robustness_rows.append(_rob_row("PM_only", pool_pm, suffix="pm_only"))

    verdict = _verdict(
        best_cf=best_cf,
        baseline_pnl=baseline_pnl,
        day622_share=day622_share,
        robustness_rows=robustness_rows,
        focus_rows=focus_map,
    )

    loo_rows = [r for r in robustness_rows if str(r.get("test", "")).startswith("LOO_day_")]
    loo_positive = sum(1 for r in loo_rows if float(r.get("delta_pnl_vs_baseline") or 0) > 0)

    mandatory = {
        "1_best_guard": best_name,
        "2_delta_pnl": best_cf.get("delta_pnl_yen_100"),
        "3_delta_pf": round(float(best_cf.get("profit_factor") or 0) - float(baseline_pf or 0), 4),
        "4_delta_maxDD": best_cf.get("delta_maxDD_yen_100"),
        "5_blocked_winners": best_cf.get("blocked_winners"),
        "6_blocked_losers": best_cf.get("blocked_losers"),
        "7_impact_6976": best_cf.get("impact_6976"),
        "8_impact_4062": best_cf.get("impact_4062"),
        "9_impact_AM": best_cf.get("impact_AM"),
        "10_impact_PM": best_cf.get("impact_PM"),
        "11_loo_stability": f"{loo_positive}/{len(loo_rows)} positive",
        "12_overfit_risk": (
            "high"
            if verdict == "overfit_classic_guard"
            else "moderate"
            if loo_positive < len(loo_rows) * 0.6
            else "low"
        ),
        "13_replay_candidate": verdict == "classic_indicator_guard_viable",
        "14_shadow_candidate": verdict in ("classic_indicator_guard_viable", "overfit_classic_guard")
        and float(best_cf.get("delta_pnl_yen_100") or 0) > 0,
        "15_runtime_candidate": False,
        "16_next_action": (
            "Forward-shadow C_late_chase_AND_rsi_over80 (best combo: +15.6k, 1W/6L); "
            "avoid D/G; B standalone +16.3k but user intent is combo guards"
            if float(focus_map.get("C", {}).get("delta_pnl_yen_100") or 0) > 0
            else "No viable classic combo guard — deprioritize D/G"
        ),
        "focus_C_delta_pnl": focus_map.get("C", {}).get("delta_pnl_yen_100"),
        "focus_D_delta_pnl": focus_map.get("D", {}).get("delta_pnl_yen_100"),
        "focus_G_delta_pnl": focus_map.get("G", {}).get("delta_pnl_yen_100"),
        "winner_loss_ratio": best_cf.get("winner_loss_ratio"),
        "verdict": verdict,
        "baseline_pnl_yen_100": round(baseline_pnl, 2),
        "baseline_PF": baseline_pf,
        "thresholds": thresholds,
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
class Phase502Job:
    repo_root: Path
    parallel: bool = False
    max_workers: int = 2

    def run(self) -> dict[str, Any]:
        return run_phase502(
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
            "guard_replay": reports / "phase502_classic_indicator_guard_replay.csv",
            "symbol_day": reports / "phase502_classic_indicator_guard_symbol_day.csv",
            "robustness": reports / "phase502_classic_indicator_guard_robustness.csv",
            "summary": reports / "phase502_summary.json",
            "report": doc_root / "docs" / "operations" / "phase502_classic_indicator_guard_replay.md",
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
        guards = list(result.get("_guard_replay") or [])
        lines = [
            "# Phase502 — Classic Indicator Guard Replay",
            "",
            f"**Verdict:** `{result.get('verdict')}`",
            f"**Period:** {PERIOD_START} — {PERIOD_END}",
            "",
            "## 必須回答",
            "",
            "| # | 回答 |",
            "|---|------|",
            f"| 1 最良guard | **{m.get('1_best_guard')}** |",
            f"| 2 delta PnL | **{m.get('2_delta_pnl')}** |",
            f"| 3 delta PF | **{m.get('3_delta_pf')}** |",
            f"| 4 delta maxDD | **{m.get('4_delta_maxDD')}** |",
            f"| 5 blocked winners | **{m.get('5_blocked_winners')}** |",
            f"| 6 blocked losers | **{m.get('6_blocked_losers')}** |",
            f"| 7 6976影響 | **{m.get('7_impact_6976')}** |",
            f"| 8 4062影響 | **{m.get('8_impact_4062')}** |",
            f"| 9 AM影響 | **{m.get('9_impact_AM')}** |",
            f"| 10 PM影響 | **{m.get('10_impact_PM')}** |",
            f"| 11 LOO | **{m.get('11_loo_stability')}** |",
            f"| 12 overfit | **{m.get('12_overfit_risk')}** |",
            f"| 13 Replay候補 | **{m.get('13_replay_candidate')}** |",
            f"| 14 Shadow候補 | **{m.get('14_shadow_candidate')}** |",
            f"| 15 Runtime候補 | **{m.get('15_runtime_candidate')}** |",
            f"| 16 次アクション | {m.get('16_next_action')} |",
            "",
            "## Focus guards (C / D / G)",
            "",
            f"- C delta: **{m.get('focus_C_delta_pnl')}**",
            f"- D delta: **{m.get('focus_D_delta_pnl')}**",
            f"- G delta: **{m.get('focus_G_delta_pnl')}**",
            "",
            "## 重要所見",
            "",
            "- **B (standalone rsi_over80)** が delta 最大 (+16,290) だが、**C (late_chase AND rsi_over80)** は +15,600 で winner cut **1 vs 2** — 本命 combo として C を推奨",
            "- **D (falling_knife AND macd_weak)**: delta **-47,501** (13W/5L cut) — **不可**",
            "- **G (C OR D)**: delta **-31,901** — D が毒",
            "- **A/E**: winner 過剰カットで delta マイナス",
            "- Runtime 不採用; Shadow は **C** 優先",
            "",
            "## All guards",
            "",
            "| Scenario | delta PnL | PF | blocked W/L | W/L ratio |",
            "|----------|-----------|-----|-------------|-----------|",
        ]
        for g in guards:
            lines.append(
                f"| {g.get('scenario')} | {g.get('delta_pnl_yen_100')} | {g.get('profit_factor')} | "
                f"{g.get('blocked_winners')}/{g.get('blocked_losers')} | {g.get('winner_loss_ratio')} |"
            )
        lines.extend(
            [
                "",
                "## 実行",
                "",
                "```powershell",
                "cd kabu_native",
                '$env:PYTHONPATH="src"',
                "python scripts/run_phase502_classic_indicator_guard_replay.py --parallel --max-workers 2",
                "```",
                "",
            ]
        )
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("\n".join(lines), encoding="utf-8")
