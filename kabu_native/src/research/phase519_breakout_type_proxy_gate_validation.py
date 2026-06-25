"""
Phase519 — Breakout-type proxy gate validation (research only).

Tests ENTRY-time proxy gates on day_high overlay only (not PBv2).
No adoption. No production changes. PBv2 Exit fixed.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, FrozenSet, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _parse_ts, _position_key
from research.phase451_entry_shape_tournament import _build_price_index_to, _now_iso
from research.phase493_global_entry_failure_audit import PERIOD_END, PERIOD_START
from research.phase507_classic_strategy_battle import _day_rows, _run_baseline_runtime, _universe_symbols
from research.phase509_t15_t13_signal_audit import _build_bar_cache
from research.phase510_classic_system_battle import _strategy_metrics_safe
from research.phase515b_day_high_breakout_dependency_audit import (
    SYMBOL_6976,
    _bar_index_at,
    _high_update_stats,
)
from research.phase515c_day_high_breakout_refinement import _timing_ratios
from research.phase516_pbv2_best_classical_overlay import (
    OVERLAY_DEFS,
    _merge_or_candidates,
    _pbv2_precomputed_candidates,
    _prepare_runtime_env,
    _scan_overlay_day,
)
from research.phase517_o_r003_or_robustness_audit import (
    _cap_collision_row,
    _executed_trade_rows,
    _simulate_or_audited,
    _symbol_day_row,
)
from research.phase518_day_high_winner_loser_separation import (
    _build_micro_lookup,
    _extract_entry_features,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

PHASE519_VERDICT = "phase519_breakout_type_proxy_gate_validation_done"
MAX_WORKERS_CAP = 4
BASE_OR_ID = "O_R003_OR_BASE"

SUMMARY_FIELDS = [
    "scenario_id",
    "description",
    "gate_ids",
    "gate_count",
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
    "success_criteria_met",
    "success_criteria_count",
]

DAILY_FIELDS = ["scenario_id", "day", "trade_count", "total_pnl_yen_100", "profit_factor", "win_rate"]

TRADE_FIELDS = [
    "scenario_id",
    "symbol",
    "day",
    "entry_time",
    "exit_time",
    "pnl_yen_100",
    "exit_reason",
    "accepted_by_pbv2",
    "accepted_by_overlay",
]

DEPENDENCY_FIELDS = [
    "scenario_id",
    "symbol_6976_share_pct",
    "top1_symbol_profit_share_pct",
    "top3_symbol_profit_share_pct",
    "top1_day_profit_share_pct",
    "top3_day_profit_share_pct",
    "top10_trade_profit_share_pct",
    "fragile_flag",
    "success_criteria_met",
]

OVERLAY_QUALITY_FIELDS = [
    "scenario_id",
    "overlay_only_trades",
    "overlay_only_pnl",
    "overlay_only_pf",
    "true_breakout_ratio",
    "late_breakout_ratio",
    "high_chase_ratio",
    "high_update_continues_after_entry_ratio",
    "avg_mfe",
    "avg_mae",
    "avg_mfe_mae_ratio",
]

CAP_FIELDS = [
    "scenario_id",
    "cap_block_count",
    "pbv2_trade_lost_by_overlay_count",
    "pbv2_trade_lost_by_overlay_pnl",
    "overlay_trade_added_count",
    "overlay_trade_added_pnl",
    "net_substitution_pnl",
]


def _float(v: Any) -> float:
    try:
        if v is None or v == "":
            return 0.0
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _build_gate_specs() -> list[tuple[str, frozenset[str], str]]:
    singles = [(f"G{i}", frozenset({f"G{i}"}), f"overlay gate G{i}") for i in range(1, 6)]
    pairs = [
        ("G1_G2", frozenset({"G1", "G2"}), "G1+G2"),
        ("G1_G3", frozenset({"G1", "G3"}), "G1+G3"),
        ("G1_G4", frozenset({"G1", "G4"}), "G1+G4"),
        ("G1_G5", frozenset({"G1", "G5"}), "G1+G5"),
        ("G2_G3", frozenset({"G2", "G3"}), "G2+G3"),
        ("G2_G4", frozenset({"G2", "G4"}), "G2+G4"),
        ("G2_G5", frozenset({"G2", "G5"}), "G2+G5"),
        ("G3_G4", frozenset({"G3", "G4"}), "G3+G4"),
        ("G3_G5", frozenset({"G3", "G5"}), "G3+G5"),
        ("G4_G5", frozenset({"G4", "G5"}), "G4+G5"),
    ]
    triples = [
        ("G1_G2_G3", frozenset({"G1", "G2", "G3"}), "G1+G2+G3"),
        ("G1_G2_G4", frozenset({"G1", "G2", "G4"}), "G1+G2+G4"),
        ("G1_G2_G5", frozenset({"G1", "G2", "G5"}), "G1+G2+G5"),
    ]
    base = [
        ("BASELINE", frozenset(), "PBv2 Entry + PBv2 Exit"),
        (BASE_OR_ID, frozenset(), "PBv2 OR day_high updates<=8"),
    ]
    return base + singles + pairs + triples


def _compute_medians(enriched_overlay: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    spreads = [_float(r.get("spread")) for r in enriched_overlay if r.get("spread") is not None]
    boards = [_float(r.get("board_imbalance")) for r in enriched_overlay if r.get("board_imbalance") is not None]
    n = len(enriched_overlay)
    return {
        "spread_median": round(statistics.median(spreads), 4) if spreads else None,
        "board_median": round(statistics.median(boards), 6) if boards else None,
        "spread_non_null": len(spreads),
        "board_non_null": len(boards),
        "board_missing_rate": round(1.0 - len(boards) / n, 4) if n else 0.0,
        "overlay_candidate_count": n,
    }


def _passes_proxy_gate(
    feats: Mapping[str, Any],
    gate_ids: FrozenSet[str],
    *,
    spread_median: Optional[float],
    board_median: Optional[float],
) -> bool:
    if not gate_ids:
        return True
    if "G1" in gate_ids:
        v = feats.get("minutes_from_open")
        if v is None or _float(v) > 120.0:
            return False
    if "G2" in gate_ids:
        v = feats.get("update_count_before_entry")
        if v is None or int(_float(v)) > 5:
            return False
    if "G3" in gate_ids:
        v = feats.get("rolling_volume_percentile")
        if v is None or _float(v) < 80.0:
            return False
    if "G4" in gate_ids:
        v = feats.get("spread")
        if v is None or spread_median is None or _float(v) > spread_median:
            return False
    if "G5" in gate_ids:
        v = feats.get("board_imbalance")
        if v is None or board_median is None or _float(v) > board_median:
            return False
    return True


def _overlay_quality_row(
    scenario_id: str,
    overlay_trades: Sequence[Mapping[str, Any]],
    bar_cache: Mapping[tuple[str, tuple], Any],
) -> dict[str, Any]:
    if not overlay_trades:
        return {
            "scenario_id": scenario_id,
            "overlay_only_trades": 0,
            "overlay_only_pnl": 0.0,
            "overlay_only_pf": 0.0,
            "true_breakout_ratio": 0.0,
            "late_breakout_ratio": 0.0,
            "high_chase_ratio": 0.0,
            "high_update_continues_after_entry_ratio": 0.0,
            "avg_mfe": 0.0,
            "avg_mae": 0.0,
            "avg_mfe_mae_ratio": 0.0,
        }
    pnls = [_float(t.get("pnl_yen_100")) for t in overlay_trades]
    timing = _timing_ratios(overlay_trades, bar_cache)
    mfes: list[float] = []
    maes: list[float] = []
    for t in overlay_trades:
        sym = str(t.get("symbol") or "").replace(".T", "")
        sym_t = f"{sym}.T"
        day = str(t.get("day") or "")[:8]
        ent = _parse_ts(str(t.get("entry_time") or ""))
        ex = _parse_ts(str(t.get("exit_time") or ""))
        cached = bar_cache.get((sym_t, day))
        if not cached or ent is None:
            continue
        bars, _ = cached
        ei = _bar_index_at(bars, ent)
        xi = _bar_index_at(bars, ex) if ex else ei
        if ei is None:
            continue
        stats = _high_update_stats(bars, ei, xi or ei)
        mfes.append(_float(stats.get("mfe_pct")))
        maes.append(_float(stats.get("mae_pct")))
    avg_mfe = round(statistics.mean(mfes), 4) if mfes else 0.0
    avg_mae = round(statistics.mean(maes), 4) if maes else 0.0
    ratio = round(avg_mfe / abs(avg_mae), 4) if avg_mae < -1e-9 else 0.0
    return {
        "scenario_id": scenario_id,
        "overlay_only_trades": len(overlay_trades),
        "overlay_only_pnl": round(sum(pnls), 2),
        "overlay_only_pf": _pf(pnls),
        "true_breakout_ratio": timing.get("true_breakout_ratio", 0.0),
        "late_breakout_ratio": timing.get("late_breakout_ratio", 0.0),
        "high_chase_ratio": timing.get("high_chase_ratio", 0.0),
        "high_update_continues_after_entry_ratio": timing.get("high_update_continues_after_entry_ratio", 0.0),
        "avg_mfe": avg_mfe,
        "avg_mae": avg_mae,
        "avg_mfe_mae_ratio": ratio,
    }


def _success_criteria(
    summary: Mapping[str, Any],
    dependency: Mapping[str, Any],
    overlay_q: Mapping[str, Any],
    *,
    baseline: Mapping[str, Any],
    base_or: Mapping[str, Any],
    base_or_dep: Mapping[str, Any],
    base_or_oq: Mapping[str, Any],
) -> tuple[bool, int]:
    checks = [
        _float(summary.get("total_pnl_yen_100")) >= _float(baseline.get("total_pnl_yen_100")),
        _float(summary.get("profit_factor") or 0) > _float(baseline.get("profit_factor") or 0),
        _float(summary.get("max_drawdown_yen_100")) < _float(baseline.get("max_drawdown_yen_100")),
        _float(dependency.get("symbol_6976_share_pct")) < _float(base_or_dep.get("symbol_6976_share_pct")),
        _float(dependency.get("top10_trade_profit_share_pct")) < _float(base_or_dep.get("top10_trade_profit_share_pct")),
        _float(overlay_q.get("late_breakout_ratio")) < _float(base_or_oq.get("late_breakout_ratio")),
        _float(overlay_q.get("high_chase_ratio")) <= _float(base_or_oq.get("high_chase_ratio")),
    ]
    return all(checks), sum(1 for c in checks if c)


def _mandatory_answers(
    summary_rows: Sequence[Mapping[str, Any]],
    dependency_rows: Sequence[Mapping[str, Any]],
    overlay_rows: Sequence[Mapping[str, Any]],
    *,
    baseline: Mapping[str, Any],
    base_or: Mapping[str, Any],
) -> dict[str, Any]:
    base_or_dep = next(r for r in dependency_rows if r["scenario_id"] == BASE_OR_ID)
    base_or_oq = next(r for r in overlay_rows if r["scenario_id"] == BASE_OR_ID)
    b_pnl = _float(baseline.get("total_pnl_yen_100"))
    b_pf = _float(baseline.get("profit_factor"))
    b_dd = _float(baseline.get("max_drawdown_yen_100"))

    proxy_rows = [r for r in summary_rows if r.get("scenario_id") not in ("BASELINE", BASE_OR_ID)]
    beats_baseline = [r["scenario_id"] for r in proxy_rows if _float(r.get("total_pnl_yen_100")) > b_pnl]
    success_rows = [r for r in summary_rows if r.get("success_criteria_met")]
    pnl_pf_dd = [
        r["scenario_id"]
        for r in proxy_rows
        if _float(r.get("total_pnl_yen_100")) >= b_pnl
        and _float(r.get("profit_factor") or 0) > b_pf
        and _float(r.get("max_drawdown_yen_100")) < b_dd
    ]

    def _best_by(success_only: bool, gate_count: int) -> dict[str, Any]:
        pool = [r for r in proxy_rows if int(r.get("gate_count") or 0) == gate_count]
        if success_only:
            pool = [r for r in pool if r.get("success_criteria_met")]
        if not pool:
            pool = [r for r in proxy_rows if int(r.get("gate_count") or 0) == gate_count]
        best = max(pool, key=lambda r: (_float(r.get("success_criteria_count")), _float(r.get("total_pnl_yen_100"))), default={})
        return {"scenario_id": best.get("scenario_id"), "pnl": best.get("total_pnl_yen_100"), "success": best.get("success_criteria_met")}

    robust_vs_base = [
        r["scenario_id"]
        for r in success_rows
        if r["scenario_id"] != BASE_OR_ID and _float(r.get("total_pnl_yen_100")) >= _float(base_or.get("total_pnl_yen_100")) * 0.5
    ]

    def _delta(metric: str, sid: str) -> Optional[float]:
        row = next((r for r in overlay_rows if r["scenario_id"] == sid), {})
        base = _float(base_or_oq.get(metric))
        return round(_float(row.get(metric)) - base, 4) if row else None

    best_success = max(success_rows, key=lambda r: _float(r.get("total_pnl_yen_100")), default={})

    return {
        "1_pbv2_improving_proxy_gate_exists": bool(beats_baseline),
        "1_improving_candidates": beats_baseline[:10],
        "2_robust_vs_base_or": robust_vs_base,
        "2_success_criteria_candidates": [r["scenario_id"] for r in success_rows if r["scenario_id"] != BASE_OR_ID],
        "3_pnl_pf_dd_improvement_candidates": pnl_pf_dd,
        "4_6976_dependency_reduced": [r["scenario_id"] for r in success_rows if r["scenario_id"] != BASE_OR_ID],
        "5_top10_trade_dependency_reduced": [
            r["scenario_id"]
            for r in dependency_rows
            if r["scenario_id"] not in ("BASELINE", BASE_OR_ID)
            and _float(r.get("top10_trade_profit_share_pct")) < _float(base_or_dep.get("top10_trade_profit_share_pct"))
        ],
        "6_late_breakout_reduced": [
            r["scenario_id"]
            for r in overlay_rows
            if r["scenario_id"] not in ("BASELINE", BASE_OR_ID)
            and _float(r.get("late_breakout_ratio")) < _float(base_or_oq.get("late_breakout_ratio"))
        ],
        "7_high_chase_reduced": [
            r["scenario_id"]
            for r in overlay_rows
            if r["scenario_id"] not in ("BASELINE", BASE_OR_ID)
            and _float(r.get("high_chase_ratio")) <= _float(base_or_oq.get("high_chase_ratio"))
        ],
        "8_best_single_gate": _best_by(True, 1) if any(r.get("success_criteria_met") for r in proxy_rows if r.get("gate_count") == 1) else _best_by(False, 1),
        "9_best_2gate": _best_by(True, 2) if any(r.get("success_criteria_met") for r in proxy_rows if r.get("gate_count") == 2) else _best_by(False, 2),
        "10_best_3gate": _best_by(True, 3) if any(r.get("success_criteria_met") for r in proxy_rows if r.get("gate_count") == 3) else _best_by(False, 3),
        "11_proxy_gate_raises_adoption_potential": bool(success_rows),
        "12_next_phase_candidates": [r["scenario_id"] for r in success_rows if r["scenario_id"] != BASE_OR_ID][:5],
        "12_best_success_candidate": best_success.get("scenario_id"),
        "13_production_adopt_ok": False,
        "13_adopt_not_allowed": True,
    }


@dataclass
class Phase519Job:
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
        micro_lookup = _build_micro_lookup(replay_pool)
        overlay = OVERLAY_DEFS["O_R003"]
        gate_specs = _build_gate_specs()

        baseline_state, baseline_met = _run_baseline_runtime(self.repo_root)
        baseline_executed = _executed_trade_rows(baseline_state, "BASELINE")
        pbv2_candidates = _pbv2_precomputed_candidates(replay_pool, runtime_shadows, guard_c_block)

        overlay_by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)

        def _scan_day(day: str) -> tuple[str, list[dict[str, Any]]]:
            return day, _scan_overlay_day(
                overlay,
                day=day,
                universe=universe,
                bar_cache=bar_cache,
                price_idx=price_idx,
            )

        if self.parallel:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(_scan_day, day): day for day in days}
                for fut in as_completed(futs):
                    day, chunk = fut.result()
                    overlay_by_day[day].extend(chunk)
        else:
            for day in days:
                _, chunk = _scan_day(day)
                overlay_by_day[day].extend(chunk)

        enriched_pool: list[dict[str, Any]] = []

        def _enrich_day(day: str) -> list[dict[str, Any]]:
            rows: list[dict[str, Any]] = []
            for trade in overlay_by_day.get(day, []):
                feats = _extract_entry_features(trade, bar_cache=bar_cache, micro_lookup=micro_lookup)
                rows.append({**dict(trade), "_entry_feats": feats})
            return rows

        enrich_jobs = list(days)
        if self.parallel and enrich_jobs:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(_enrich_day, day): day for day in enrich_jobs}
                for fut in as_completed(futs):
                    enriched_pool.extend(fut.result())
        else:
            for day in enrich_jobs:
                enriched_pool.extend(_enrich_day(day))

        medians = _compute_medians([t.get("_entry_feats") or {} for t in enriched_pool])
        spread_med = medians.get("spread_median")
        board_med = medians.get("board_median")

        def _strip_overlay(trade: Mapping[str, Any]) -> dict[str, Any]:
            return {k: v for k, v in trade.items() if not str(k).startswith("_")}

        def _filter_overlay(gate_ids: frozenset[str]) -> list[dict[str, Any]]:
            out: list[dict[str, Any]] = []
            for trade in enriched_pool:
                feats = trade.get("_entry_feats") or {}
                if not _passes_proxy_gate(
                    feats,
                    gate_ids,
                    spread_median=spread_med,
                    board_median=board_med,
                ):
                    continue
                out.append(_strip_overlay(trade))
            return out

        summary_rows: list[dict[str, Any]] = []
        daily_rows: list[dict[str, Any]] = []
        trade_rows: list[dict[str, Any]] = []
        dependency_rows: list[dict[str, Any]] = []
        overlay_quality_rows: list[dict[str, Any]] = []
        cap_rows: list[dict[str, Any]] = []
        sim_results: dict[str, Any] = {}

        baseline_row = {
            "scenario_id": "BASELINE",
            "description": "PBv2 Entry + PBv2 Exit",
            "gate_ids": "",
            "gate_count": 0,
            **baseline_met,
            "success_criteria_met": False,
            "success_criteria_count": 0,
        }
        summary_rows.append(baseline_row)
        for dr in _day_rows(baseline_state, "BASELINE"):
            daily_rows.append({"scenario_id": "BASELINE", **{k: v for k, v in dr.items() if k != "strategy_id"}})
        trade_rows.extend(baseline_executed)
        dependency_rows.append(_symbol_day_row("BASELINE", baseline_executed))
        overlay_quality_rows.append(_overlay_quality_row("BASELINE", [], bar_cache))
        cap_rows.append(
            {
                "scenario_id": "BASELINE",
                "cap_block_count": baseline_state.rejected_trade_count,
                "pbv2_trade_lost_by_overlay_count": 0,
                "pbv2_trade_lost_by_overlay_pnl": 0.0,
                "overlay_trade_added_count": 0,
                "overlay_trade_added_pnl": 0.0,
                "net_substitution_pnl": 0.0,
            }
        )

        or_specs = [s for s in gate_specs if s[0] != "BASELINE"]

        def _run_scenario(spec: tuple[str, frozenset[str], str]) -> dict[str, Any]:
            sid, gate_ids, desc = spec
            if sid == BASE_OR_ID:
                filtered = [_strip_overlay(t) for t in enriched_pool]
            else:
                filtered = _filter_overlay(gate_ids)
            merged = _merge_or_candidates(
                pbv2_candidates,
                filtered,
                bar_cache=bar_cache,
                overlay=overlay,
                guard_c_block=guard_c_block,
            )
            result = _simulate_or_audited(merged, mode=f"phase519_{sid.lower()}")
            executed = _executed_trade_rows(result.state, sid)
            met = _strategy_metrics_safe(
                result.state,
                strategy_id=sid,
                entry_rule_id="PBv2+OR" if sid != "BASELINE" else "PBv2",
                exit_rule_id="RUNTIME/PB" if sid != "BASELINE" else "RUNTIME",
                baseline=baseline_met,
            )
            overlay_only = [t for t in executed if t.get("accepted_by_overlay") and not t.get("accepted_by_pbv2")]
            dep = _symbol_day_row(sid, executed)
            oq = _overlay_quality_row(sid, overlay_only, bar_cache)
            cap = _cap_collision_row(
                scenario_id=sid,
                baseline_state=baseline_state,
                or_result=result,
                baseline_executed=baseline_executed,
                or_executed=executed,
            )
            days_out = [
                {"scenario_id": sid, **{k: v for k, v in dr.items() if k != "strategy_id"}}
                for dr in _day_rows(result.state, sid)
            ]
            return {
                "scenario_id": sid,
                "description": desc,
                "gate_ids": ",".join(sorted(gate_ids)),
                "gate_count": len(gate_ids),
                "metrics": met,
                "executed": executed,
                "dependency": dep,
                "overlay_quality": oq,
                "cap": cap,
                "daily": days_out,
                "result": result,
                "filtered_overlay_count": len(filtered),
            }

        if self.parallel:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(_run_scenario, spec): spec[0] for spec in or_specs}
                for fut in as_completed(futs):
                    sim_results[fut.result()["scenario_id"]] = fut.result()
        else:
            for spec in or_specs:
                res = _run_scenario(spec)
                sim_results[res["scenario_id"]] = res

        for sid, _, desc in or_specs:
            res = sim_results[sid]
            met = res["metrics"]
            row = {
                "scenario_id": sid,
                "description": desc,
                "gate_ids": res["gate_ids"],
                "gate_count": res["gate_count"],
                **met,
                "success_criteria_met": False,
                "success_criteria_count": 0,
            }
            summary_rows.append(row)
            daily_rows.extend(res["daily"])
            trade_rows.extend(res["executed"])
            dependency_rows.append(res["dependency"])
            overlay_quality_rows.append(res["overlay_quality"])
            cap_rows.append(res["cap"])

        baseline_summary = next(r for r in summary_rows if r["scenario_id"] == "BASELINE")
        base_or_summary = next(r for r in summary_rows if r["scenario_id"] == BASE_OR_ID)
        base_or_dep = next(r for r in dependency_rows if r["scenario_id"] == BASE_OR_ID)
        base_or_oq = next(r for r in overlay_quality_rows if r["scenario_id"] == BASE_OR_ID)

        for row in summary_rows:
            if row["scenario_id"] == "BASELINE":
                continue
            dep = next(r for r in dependency_rows if r["scenario_id"] == row["scenario_id"])
            oq = next(r for r in overlay_quality_rows if r["scenario_id"] == row["scenario_id"])
            ok, count = _success_criteria(
                row,
                dep,
                oq,
                baseline=baseline_summary,
                base_or=base_or_summary,
                base_or_dep=base_or_dep,
                base_or_oq=base_or_oq,
            )
            row["success_criteria_met"] = ok
            row["success_criteria_count"] = count
            dep["success_criteria_met"] = ok

        mandatory = _mandatory_answers(
            summary_rows,
            dependency_rows,
            overlay_quality_rows,
            baseline=baseline_summary,
            base_or=base_or_summary,
        )

        return {
            "verdict": PHASE519_VERDICT,
            "generated_at": _now_iso(),
            "period_start": PERIOD_START,
            "period_end": PERIOD_END,
            "parallel_workers": workers,
            "median_thresholds": medians,
            "gate_specs": [{"scenario_id": s, "gate_ids": sorted(g), "description": d} for s, g, d in gate_specs],
            "summary_rows": summary_rows,
            "daily_rows": daily_rows,
            "trade_rows": trade_rows,
            "dependency_rows": dependency_rows,
            "overlay_quality_rows": overlay_quality_rows,
            "cap_collision_rows": cap_rows,
            "mandatory_answers": mandatory,
            "baseline": baseline_summary,
            "base_or": base_or_summary,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        paths = {
            "summary": reports / "phase519_proxy_gate_summary.csv",
            "daily": reports / "phase519_proxy_gate_daily.csv",
            "trades": reports / "phase519_proxy_gate_trades.csv",
            "dependency": reports / "phase519_proxy_gate_dependency.csv",
            "overlay_quality": reports / "phase519_proxy_gate_overlay_quality.csv",
            "cap_collision": reports / "phase519_proxy_gate_cap_collision.csv",
            "report": reports / "phase519_report.json",
            "docs": kabu / "docs" / "operations" / "phase519_breakout_type_proxy_gate_validation.md",
        }
        _write_csv(paths["summary"], SUMMARY_FIELDS, list(result.get("summary_rows") or []))
        _write_csv(paths["daily"], DAILY_FIELDS, list(result.get("daily_rows") or []))
        _write_csv(paths["trades"], TRADE_FIELDS, list(result.get("trade_rows") or []))
        _write_csv(paths["dependency"], DEPENDENCY_FIELDS, list(result.get("dependency_rows") or []))
        _write_csv(paths["overlay_quality"], OVERLAY_QUALITY_FIELDS, list(result.get("overlay_quality_rows") or []))
        _write_csv(paths["cap_collision"], CAP_FIELDS, list(result.get("cap_collision_rows") or []))
        paths["report"].write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        paths["docs"].write_text(_render_docs(result), encoding="utf-8")
        return paths


def _render_docs(result: Mapping[str, Any]) -> str:
    ma = result.get("mandatory_answers") or {}
    med = result.get("median_thresholds") or {}
    lines = [
        "# Phase519 — Breakout-Type Proxy Gate Validation",
        "",
        f"**Verdict:** `{result.get('verdict')}`",
        f"**Period:** {result.get('period_start')} – {result.get('period_end')}",
        f"**Spread median:** {med.get('spread_median')} | **Board median:** {med.get('board_median')} "
        f"(board missing {med.get('board_missing_rate')})",
        "",
        "## Top success-criteria candidates",
        "",
    ]
    success = [r for r in result.get("summary_rows") or [] if r.get("success_criteria_met")]
    for row in sorted(success, key=lambda r: -_float(r.get("total_pnl_yen_100")))[:10]:
        lines.append(
            f"- **{row.get('scenario_id')}**: PnL={row.get('total_pnl_yen_100')}, "
            f"PF={row.get('profit_factor')}, maxDD={row.get('max_drawdown_yen_100')}, gates={row.get('gate_ids')}"
        )
    if not success:
        lines.append("- None met all success criteria")
    lines.extend(["", "## Summary (BASE vs BASE_OR vs best)", ""])
    for sid in ("BASELINE", BASE_OR_ID, ma.get("12_best_success_candidate")):
        row = next((r for r in result.get("summary_rows") or [] if r.get("scenario_id") == sid), None)
        if row:
            lines.append(
                f"- **{sid}**: PnL={row.get('total_pnl_yen_100')} PF={row.get('profit_factor')} "
                f"maxDD={row.get('max_drawdown_yen_100')} success={row.get('success_criteria_met')}"
            )
    lines.extend(["", "## Mandatory answers", ""])
    for k in sorted(ma.keys(), key=lambda x: int(x.split("_")[0]) if x[0].isdigit() else 99):
        lines.append(f"- **{k}**: {ma.get(k)}")
    return "\n".join(lines) + "\n"
