"""
Phase496 — MST Near High Threshold Optimization (research only).

Soft threshold grid on distance_from_day_high_pct / MST_near_day_high_score.
PBv2 CAP replay 20260529–20260622. No Runtime changes.
"""

from __future__ import annotations

import json
import math
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
from research.phase465b_trend_gate_redesign import _day_high_distance
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
    PERIOD_END,
    PERIOD_START,
    _enrich_trade_row,
    _is_loser,
    _medians_from_losers,
    _replay_with_extra_block,
)
from research.phase495_new_feature_guard_replay import _counterfactual_row, _rows_from_state, _session_bucket
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

REJECT_GRID = (5, 10, 15, 20, 25, 30, 40, 50)
HARD_REJECT_DISTANCE = 1.0

GRID_FIELDS = [
    "reject_rate_target_pct", "threshold_distance_pct", "threshold_score",
    "actual_pool_reject_pct", "metric_used", "scenario",
    "total_pnl_yen_100", "profit_factor", "maxDD_yen_100", "delta_maxDD_yen_100",
    "delta_pnl_yen_100", "accepted", "blocked_total",
    "blocked_winners", "blocked_losers", "winner_loser_ratio",
    "impact_6976", "impact_4062", "impact_AM", "impact_PM",
]

ROBUSTNESS_FIELDS = [
    "test", "reject_rate_target_pct", "threshold_distance_pct",
    "total_pnl_yen_100", "profit_factor", "delta_pnl_vs_baseline", "blocked_winners",
]


def _float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _distance_from_day_high(trade: Mapping[str, Any]) -> Optional[float]:
    d = _day_high_distance(trade)
    return round(d, 6) if d is not None else None


def _mst_near_high_score(distance: Optional[float]) -> Optional[float]:
    if distance is None:
        return None
    return round(1.0 / max(distance, 0.05), 6)


def _pool_distances(replay_pool: Sequence[Mapping[str, Any]]) -> list[float]:
    out: list[float] = []
    for trade in replay_pool:
        if not pass_pbv2(trade):
            continue
        d = _distance_from_day_high(trade)
        if d is not None:
            out.append(d)
    return out


def _bottom_pct_threshold(values: Sequence[float], pct: float) -> float:
    if not values:
        return 0.0
    ranked = sorted(values)
    idx = min(len(ranked) - 1, max(0, int(math.ceil(pct / 100.0 * len(ranked)) - 1)))
    return ranked[idx]


def _actual_reject_rate(pool: Sequence[Mapping[str, Any]], thr: float) -> float:
    dists = _pool_distances(pool)
    if not dists:
        return 0.0
    blocked = sum(1 for d in dists if d <= thr)
    return round(100.0 * blocked / len(dists), 4)


def _block_near_high(thr_distance: float) -> Callable[[Mapping[str, Any]], bool]:
    def _b(trade: Mapping[str, Any]) -> bool:
        d = _distance_from_day_high(trade)
        return d is not None and d <= thr_distance
    return _b


def _winner_loser_ratio(blocked_winners: int, blocked_losers: int) -> Optional[float]:
    if blocked_winners == 0:
        return None if blocked_losers == 0 else float("inf")
    return round(blocked_losers / blocked_winners, 4)


def _verdict(
    *,
    best: Mapping[str, Any],
    hard: Mapping[str, Any],
    robustness: Sequence[Mapping[str, Any]],
) -> str:
    delta = float(best.get("delta_pnl_yen_100") or 0)
    bw = int(best.get("blocked_winners") or 0)
    hard_delta = float(hard.get("delta_pnl_yen_100") or 0)
    loo_rows = [r for r in robustness if str(r.get("test", "")).startswith("LOO_day_")]
    loo_pos = sum(1 for r in loo_rows if float(r.get("delta_pnl_vs_baseline") or 0) > 0)
    loo_ratio = loo_pos / max(1, len(loo_rows))

    if loo_ratio < 0.5 and len(loo_rows) >= 5:
        return "overfit_threshold"
    if delta < 3000:
        return "overfit_threshold"
    if bw <= 5 and delta >= 15000 and float(best.get("profit_factor") or 0) > float(hard.get("baseline_PF") or 0) + 0.1:
        return "runtime_candidate"
    if delta >= 5000 or (delta >= hard_delta * 0.85 and bw < int(hard.get("blocked_winners") or 999)):
        return "needs_forward_shadow"
    return "overfit_threshold"


def run_phase496(*, repo_root: Path, parallel: bool = False, max_workers: int = 2) -> dict[str, Any]:
    max_workers = min(max(2, max_workers), 4)
    kabu = resolve_kabu_root(repo_root)
    reports = resolve_reports_dir(repo_root)
    price_idx = _build_price_index_to(kabu, period_end=PERIOD_END)

    replay_pool, runtime_shadows = _load_replay_pool(reports)
    replay_pool = _filter_period(replay_pool, start=PERIOD_START, end=PERIOD_END)
    runtime_shadows = _fill_close_proxy_shadows(replay_pool, runtime_shadows, price_idx=price_idx)
    replay_pool = _filter_replay_pool_safe(replay_pool, runtime_shadows)
    _ensure_enriched(replay_pool, price_idx=price_idx)

    pool_dists = _pool_distances(replay_pool)

    baseline_state = _simulate_runtime_replay(
        replay_pool,
        runtime_shadows,
        mode=f"{REPLAY_MODE}_phase496_base",
        entry_block_fn=_entry_block(pass_pbv2),
        initial_equity=1_500_000.0,
    )
    baseline_met = _summary_metrics(baseline_state, initial_equity=1_500_000.0)
    baseline_pnl = float(baseline_met["total_pnl_yen"])
    baseline_pf = baseline_met["profit_factor"]
    baseline_max_dd = float(baseline_met["max_drawdown_yen"])
    baseline_rows = _rows_from_state(baseline_state)
    medians = _medians_from_losers([r for r in baseline_rows if _is_loser(r)])

    grid_specs: list[tuple[int, float, Callable[[Mapping[str, Any]], bool]]] = []
    for rate in REJECT_GRID:
        thr = _bottom_pct_threshold(pool_dists, rate)
        grid_specs.append((rate, thr, _block_near_high(thr)))

    hard_block = _block_near_high(HARD_REJECT_DISTANCE)
    grid_specs.append((-1, HARD_REJECT_DISTANCE, hard_block))  # hard reject reference

    grid_rows: list[dict[str, Any]] = []

    def _run_grid(rate: int, thr: float, block_fn: Callable[[Mapping[str, Any]], bool]) -> dict[str, Any]:
        suffix = f"hard_{HARD_REJECT_DISTANCE}" if rate < 0 else f"rej{rate}"
        st = _replay_with_extra_block(replay_pool, runtime_shadows, extra_block=block_fn, mode_suffix=suffix[:14])
        scenario = (
            f"hard_reject_dhd_lt_{HARD_REJECT_DISTANCE}"
            if rate < 0
            else f"soft_reject_{rate}pct_dhd_lte_{thr:.4f}"
        )
        cf = _counterfactual_row(
            st,
            baseline_state,
            scenario=scenario,
            baseline_pnl=baseline_pnl,
            baseline_pf=baseline_pf,
            baseline_max_dd=baseline_max_dd,
            baseline_rows=baseline_rows,
            medians=medians,
        )
        bw = int(cf.get("blocked_winners") or 0)
        bl = int(cf.get("blocked_losers") or 0)
        target_rate = rate if rate >= 0 else None
        actual_pct = (
            _actual_reject_rate(replay_pool, thr)
            if rate >= 0
            else _actual_reject_rate(replay_pool, HARD_REJECT_DISTANCE)
        )
        return {
            "reject_rate_target_pct": target_rate if rate >= 0 else "hard_lt_1.0",
            "threshold_distance_pct": round(thr, 6),
            "threshold_score": _mst_near_high_score(thr),
            "actual_pool_reject_pct": actual_pct,
            "metric_used": "distance_from_day_high_pct",
            "scenario": scenario,
            "total_pnl_yen_100": cf.get("total_pnl_yen_100"),
            "profit_factor": cf.get("profit_factor"),
            "maxDD_yen_100": cf.get("maxDD_yen_100"),
            "delta_maxDD_yen_100": cf.get("delta_maxDD_yen_100"),
            "delta_pnl_yen_100": cf.get("delta_pnl_yen_100"),
            "accepted": cf.get("accepted"),
            "blocked_total": cf.get("blocked_total"),
            "blocked_winners": bw,
            "blocked_losers": bl,
            "winner_loser_ratio": _winner_loser_ratio(bw, bl),
            "impact_6976": cf.get("impact_6976"),
            "impact_4062": cf.get("impact_4062"),
            "impact_AM": cf.get("impact_AM"),
            "impact_PM": cf.get("impact_PM"),
            "_block_fn": block_fn,
            "_thr": thr,
            "_rate": rate,
        }

    if parallel and len(grid_specs) > 1:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = {ex.submit(_run_grid, r, t, b): r for r, t, b in grid_specs}
            for fut in as_completed(futs):
                row = fut.result()
                row.pop("_block_fn", None)
                row.pop("_thr", None)
                row.pop("_rate", None)
                grid_rows.append(row)
    else:
        for rate, thr, block_fn in grid_specs:
            row = _run_grid(rate, thr, block_fn)
            row.pop("_block_fn", None)
            row.pop("_thr", None)
            row.pop("_rate", None)
            grid_rows.append(row)

    soft_rows = [r for r in grid_rows if r.get("reject_rate_target_pct") != "hard_lt_1.0"]
    hard_row = next(r for r in grid_rows if r.get("reject_rate_target_pct") == "hard_lt_1.0")

    soft_rows.sort(key=lambda r: float(r.get("delta_pnl_yen_100") or 0), reverse=True)
    best_soft = soft_rows[0] if soft_rows else {}

    # Pareto pick: prefer max delta with blocked_winners <= 15, else max delta
    constrained = [r for r in soft_rows if int(r.get("blocked_winners") or 0) <= 15]
    best_constrained = constrained[0] if constrained else best_soft

    best_thr = float(best_soft.get("threshold_distance_pct") or 0)
    best_block = _block_near_high(best_thr)
    best_rate = best_soft.get("reject_rate_target_pct")

    day_pnl = defaultdict(float)
    for r in baseline_rows:
        day_pnl[str(r["day"])] += float(r["pnl_yen"])
    top_day = max(day_pnl, key=lambda d: abs(day_pnl[d])) if day_pnl else ""

    robustness_rows: list[dict[str, Any]] = []
    days = sorted({str(r["day"]) for r in baseline_rows})

    def _rob(test: str, pool: Sequence[Mapping[str, Any]], *, suffix: str) -> None:
        st_base = _simulate_runtime_replay(
            pool,
            runtime_shadows,
            mode=f"{REPLAY_MODE}_b_{suffix}",
            entry_block_fn=_entry_block(pass_pbv2),
            initial_equity=1_500_000.0,
        )
        base_met = _summary_metrics(st_base, initial_equity=1_500_000.0)
        base_rows = _rows_from_state(st_base)
        st_g = _replay_with_extra_block(pool, runtime_shadows, extra_block=best_block, mode_suffix=f"g_{suffix}")
        g_met = _summary_metrics(st_g, initial_equity=1_500_000.0)
        cf = _counterfactual_row(
            st_g,
            st_base,
            scenario=f"rob_{test}",
            baseline_pnl=float(base_met["total_pnl_yen"]),
            baseline_pf=base_met["profit_factor"],
            baseline_max_dd=float(base_met["max_drawdown_yen"]),
            baseline_rows=base_rows,
            medians=medians,
        )
        robustness_rows.append(
            {
                "test": test,
                "reject_rate_target_pct": best_rate,
                "threshold_distance_pct": best_thr,
                "total_pnl_yen_100": round(float(g_met["total_pnl_yen"]), 2),
                "profit_factor": g_met["profit_factor"],
                "delta_pnl_vs_baseline": round(float(g_met["total_pnl_yen"]) - float(base_met["total_pnl_yen"]), 2),
                "blocked_winners": cf.get("blocked_winners"),
            }
        )

    for day in days:
        pool_day = [t for t in replay_pool if str(t.get("day") or "")[:8] != day]
        if len(pool_day) < 50:
            continue
        _rob(f"LOO_day_{day}", pool_day, suffix=f"loo_{day}")

    for test_name, sym in (("exclude_6976", "6976.T"), ("exclude_4062", "4062.T")):
        pool_ex = [t for t in replay_pool if str(t.get("symbol") or "") != sym]
        _rob(test_name, pool_ex, suffix=test_name)

    if top_day:
        pool_ex_day = [t for t in replay_pool if str(t.get("day") or "")[:8] != top_day]
        _rob(f"exclude_top_day_{top_day}", pool_ex_day, suffix="ex_top_day")

    hard_delta = float(hard_row.get("delta_pnl_yen_100") or 0)
    best_delta = float(best_soft.get("delta_pnl_yen_100") or 0)
    improved_vs_hard = (
        best_delta > hard_delta
        or (
            best_delta >= hard_delta * 0.95
            and int(best_soft.get("blocked_winners") or 0) < int(hard_row.get("blocked_winners") or 0)
        )
    )

    verdict = _verdict(best=best_soft, hard=hard_row, robustness=robustness_rows)

    mandatory = {
        "best_threshold_distance_pct": best_soft.get("threshold_distance_pct"),
        "best_reject_rate_target_pct": best_soft.get("reject_rate_target_pct"),
        "best_threshold_score": best_soft.get("threshold_score"),
        "best_delta_pnl": best_soft.get("delta_pnl_yen_100"),
        "best_PF": best_soft.get("profit_factor"),
        "blocked_winners": best_soft.get("blocked_winners"),
        "blocked_losers": best_soft.get("blocked_losers"),
        "winner_loser_ratio": best_soft.get("winner_loser_ratio"),
        "runtime_candidate": verdict == "runtime_candidate",
        "shadow_candidate": verdict in ("runtime_candidate", "needs_forward_shadow"),
        "improved_vs_hard_reject": improved_vs_hard,
        "hard_reject_delta_pnl": hard_row.get("delta_pnl_yen_100"),
        "hard_reject_blocked_winners": hard_row.get("blocked_winners"),
        "hard_reject_PF": hard_row.get("profit_factor"),
        "best_constrained_reject_rate": best_constrained.get("reject_rate_target_pct"),
        "best_constrained_delta_pnl": best_constrained.get("delta_pnl_yen_100"),
        "best_constrained_blocked_winners": best_constrained.get("blocked_winners"),
        "impact_6976": best_soft.get("impact_6976"),
        "impact_4062": best_soft.get("impact_4062"),
        "impact_AM": best_soft.get("impact_AM"),
        "impact_PM": best_soft.get("impact_PM"),
        "verdict": verdict,
        "baseline_pnl_yen_100": baseline_pnl,
        "baseline_PF": baseline_pf,
    }

    grid_rows.sort(
        key=lambda r: (
            0 if r.get("reject_rate_target_pct") == "hard_lt_1.0" else 1,
            float(r.get("delta_pnl_yen_100") or 0),
        ),
        reverse=True,
    )

    return {
        "generated_at": _now_iso(),
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "verdict": verdict,
        "mandatory_answers": mandatory,
        "_grid": grid_rows,
        "_robustness": robustness_rows,
    }


@dataclass
class Phase496Job:
    repo_root: Path
    parallel: bool = False
    max_workers: int = 2

    def run(self) -> dict[str, Any]:
        return run_phase496(
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
            "grid": reports / "phase496_mst_threshold_grid.csv",
            "robustness": reports / "phase496_mst_robustness.csv",
            "summary": reports / "phase496_summary.json",
            "report": doc_root / "docs" / "operations" / "phase496_mst_near_high_optimization.md",
        }
        _write_csv(paths["grid"], GRID_FIELDS, list(result.get("_grid") or []))
        _write_csv(paths["robustness"], ROBUSTNESS_FIELDS, list(result.get("_robustness") or []))
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        self._write_report(paths["report"], result)
        return paths

    def _write_report(self, report: Path, result: Mapping[str, Any]) -> None:
        m = result.get("mandatory_answers") or {}
        lines = [
            "# Phase496 — MST Near High Optimization",
            "",
            f"**Verdict:** `{result.get('verdict')}`",
            f"**Period:** {result.get('period_start')} — {result.get('period_end')}",
            "",
            "## 必須回答",
            "",
        ]
        for key in (
            "best_threshold_distance_pct", "best_reject_rate_target_pct", "best_delta_pnl",
            "best_PF", "blocked_winners", "blocked_losers", "runtime_candidate",
            "shadow_candidate", "improved_vs_hard_reject", "hard_reject_delta_pnl",
            "hard_reject_blocked_winners", "best_constrained_reject_rate",
            "best_constrained_delta_pnl", "best_constrained_blocked_winners",
        ):
            lines.append(f"- **{key}:** {m.get(key)}")
        lines.extend(["", "## Threshold grid", "", "```json"])
        lines.append(json.dumps(result.get("_grid"), indent=2, ensure_ascii=False, default=str))
        lines.append("```")
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("\n".join(lines), encoding="utf-8")
