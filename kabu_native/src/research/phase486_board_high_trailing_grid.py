"""
Phase486 — Board High Trailing Grid Search (research only).

Grid search board_high activate_mfe × giveback on PBv2 accepted trades (256).
Hard Stop / No Progress / board_low fixed.
"""

from __future__ import annotations

import json
import statistics
import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _position_key
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase443_full_runtime_combined_capital_sim import simulate_capacity_replay
from research.phase451_entry_shape_tournament import PERIOD_END, PERIOD_START, _now_iso
from research.phase463_trend_pullback_population_tournament import _fill_close_proxy_shadows, _filter_replay_pool
from research.phase473_trend_entry_architecture import _entry_block, pass_pbv2
from research.phase476_pre_breakout_gate_replay import _load_replay_pool
from research.phase485_fine_tiered_giveback_tournament import (
    GivebackVariantSpec,
    TradeOutcome,
    _concentration,
    _load_day_all_series,
    _metrics,
    _mfe_band_rows,
    _outcome_from_sim,
    _stream_tick_states,
    _symbol_day_rows,
    simulate_tiered_giveback_exit,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

MAX_WORKERS_CAP = 2
BASELINE_ACTIVATE = 1.0
BASELINE_GIVEBACK = 0.60

ACTIVATE_GRID = (0.6, 0.8, 1.0, 1.2, 1.5, 2.0)
GIVEBACK_GRID = (0.40, 0.50, 0.60, 0.70, 0.80)

GRID_FIELDS = [
    "variant_id",
    "activate_mfe_pct",
    "giveback_pct",
    "is_baseline",
    "total_pnl_yen",
    "profit_factor",
    "max_drawdown_yen",
    "win_rate",
    "avg_winner",
    "avg_loser",
    "best_trade",
    "worst_trade",
    "accepted_count",
    "trailing_exit_count",
    "hard_stop_count",
    "no_progress_count",
    "delta_pnl_vs_baseline",
    "delta_pf_vs_baseline",
    "delta_maxdd_vs_baseline",
    "rank_by_pnl",
]

MFE_BAND_FIELDS = [
    "variant_id",
    "activate_mfe_pct",
    "giveback_pct",
    "mfe_band",
    "trade_count",
    "baseline_pnl",
    "variant_pnl",
    "delta_pnl",
    "winner_cut_count",
    "winner_extended_count",
]

SYMBOL_DAY_FIELDS = [
    "variant_id",
    "symbol",
    "day",
    "accepted_count",
    "total_pnl_yen",
    "trailing_exit_count",
    "delta_pnl_vs_baseline",
]

ROBUSTNESS_FIELDS = [
    "test",
    "variant_id",
    "activate_mfe_pct",
    "giveback_pct",
    "total_pnl_yen",
    "profit_factor",
    "max_drawdown_yen",
    "accepted_count",
    "delta_pnl_vs_full",
    "top_day_share",
    "top_symbol_share",
]


def _variant_id(activate: float, giveback: float) -> str:
    a = int(round(activate * 10))
    g = int(round(giveback * 100))
    return f"A{a}_G{g}"


def _build_grid() -> list[GivebackVariantSpec]:
    specs: list[GivebackVariantSpec] = []
    for act in ACTIVATE_GRID:
        for gb in GIVEBACK_GRID:
            vid = _variant_id(act, gb)
            is_base = act == BASELINE_ACTIVATE and abs(gb - BASELINE_GIVEBACK) < 1e-9
            label = "Baseline" if is_base else f"act={act}% gb={int(gb * 100)}%"
            specs.append(
                GivebackVariantSpec(
                    vid,
                    label,
                    f"board_high activate {act}% giveback {int(gb * 100)}%",
                    ((act, gb),),
                    act,
                )
            )
    return specs


def _load_accepted(replay_pool: Sequence[Mapping[str, Any]], runtime_shadows: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    st = simulate_capacity_replay(
        replay_pool,
        runtime_shadows,
        mode="phase486_pbv2",
        entry_block_fn=_entry_block(pass_pbv2),
        baseline_accepted_keys=set(),
    )
    return [{"trade": dict(log.get("trade") or log), "log": log} for log in st.trade_log]


def _parse_grid_meta(variant_id: str) -> tuple[float, float]:
    # A10_G60 -> 1.0, 0.60
    parts = variant_id.split("_")
    act = float(parts[0][1:]) / 10.0
    gb = float(parts[1][1:]) / 100.0
    return act, gb


def _verdict(*, best: Mapping[str, Any], baseline_id: str, robust_rows: Sequence[Mapping[str, Any]]) -> str:
    if str(best.get("variant_id")) == baseline_id or float(best.get("delta_pnl_vs_baseline") or 0) <= 0:
        return "keep_current_exit"
    loo = [float(r.get("delta_pnl_vs_full") or 0) for r in robust_rows if str(r.get("test", "")).startswith("LOO_")]
    if loo and min(loo) < -30000:
        return "overfit_exit"
    if float(best.get("top_day_share") or 0) > 0.45:
        return "overfit_exit"
    return "board_high_grid_candidate"


def run_phase486(*, repo_root: Path, parallel: bool = False, max_workers: int = 2) -> dict[str, Any]:
    max_workers = min(max(1, max_workers), MAX_WORKERS_CAP)
    kabu = resolve_kabu_root(repo_root)
    reports = resolve_reports_dir(repo_root)
    replay_pool, runtime_shadows = _load_replay_pool(reports)
    runtime_shadows = _fill_close_proxy_shadows(replay_pool, runtime_shadows, price_idx={})
    replay_pool = _filter_replay_pool(replay_pool, runtime_shadows)

    accepted = _load_accepted(replay_pool, runtime_shadows)
    print(f"phase486 accepted trades {len(accepted)} grid variants {len(ACTIVATE_GRID) * len(GIVEBACK_GRID)}", flush=True)

    specs = _build_grid()
    baseline_id = _variant_id(BASELINE_ACTIVATE, BASELINE_GIVEBACK)
    day_cache: dict[str, dict[str, list[tuple[float, float]]]] = {}
    cache_lock = threading.Lock()

    def _series(sym: str, day: str) -> list[tuple[float, float]]:
        with cache_lock:
            if day not in day_cache:
                day_cache[day] = _load_day_all_series(kabu, day)
                if len(day_cache) > 3:
                    del day_cache[min(day_cache)]
            return list(day_cache.get(day, {}).get(sym, []))

    def _process_one(item: Mapping[str, Any]) -> dict[str, TradeOutcome]:
        tr = item["trade"]
        sym = str(tr.get("symbol") or "")
        day = str(tr.get("day") or "")[:8]
        series = _series(sym, day)
        streamed = _stream_tick_states(tr, series)
        out: dict[str, TradeOutcome] = {}
        if streamed is None:
            pnl = round(float(item["log"].get("pnl_yen") or 0), 2)
            for spec in specs:
                out[spec.variant_id] = TradeOutcome(
                    position_key=_position_key(tr),
                    symbol=sym.replace(".T", ""),
                    day=day,
                    entry_time=str(tr.get("entry_time") or ""),
                    exit_time="",
                    exit_reason="other",
                    raw_exit_reason="no_ticks",
                    pnl_yen=pnl,
                    peak_mfe_pct=0.0,
                    hold_sec=0.0,
                )
            return out
        states, entry_px, entry_ts, imb = streamed
        for spec in specs:
            sim = simulate_tiered_giveback_exit(
                states,
                entry_price=entry_px,
                entry_ts=entry_ts,
                imb_pct=imb,
                spec=spec,
            )
            out[spec.variant_id] = _outcome_from_sim(tr, sim, states)
        return out

    all_results: dict[str, dict[str, TradeOutcome]] = {}
    if parallel and len(accepted) > 1:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = {ex.submit(_process_one, item): item for item in accepted}
            for fut in as_completed(futs):
                all_results[_position_key(futs[fut]["trade"])] = fut.result()
    else:
        for item in accepted:
            all_results[_position_key(item["trade"])] = _process_one(item)

    by_variant: dict[str, list[TradeOutcome]] = {s.variant_id: [] for s in specs}
    for key in sorted(all_results):
        for vid, o in all_results[key].items():
            by_variant[vid].append(o)

    baseline_outcomes = by_variant[baseline_id]
    baseline_metrics = _metrics(baseline_outcomes)

    grid_rows: list[dict[str, Any]] = []
    mfe_band_rows: list[dict[str, Any]] = []
    symbol_day_rows: list[dict[str, Any]] = []

    for spec in specs:
        act, gb = _parse_grid_meta(spec.variant_id)
        outs = by_variant[spec.variant_id]
        met = _metrics(outs)
        grid_rows.append(
            {
                "variant_id": spec.variant_id,
                "activate_mfe_pct": act,
                "giveback_pct": round(gb, 4),
                "is_baseline": act == BASELINE_ACTIVATE and abs(gb - BASELINE_GIVEBACK) < 1e-9,
                "total_pnl_yen": met["total_pnl_yen"],
                "profit_factor": met["profit_factor"],
                "max_drawdown_yen": met["max_drawdown_yen"],
                "win_rate": met["win_rate"],
                "avg_winner": met["avg_winner"],
                "avg_loser": met["avg_loser"],
                "best_trade": met["best_trade"],
                "worst_trade": met["worst_trade"],
                "accepted_count": met["accepted_count"],
                "trailing_exit_count": met["trailing_exit_count"],
                "hard_stop_count": met["hard_stop_count"],
                "no_progress_count": met["no_progress_count"],
                "delta_pnl_vs_baseline": round(float(met["total_pnl_yen"]) - float(baseline_metrics["total_pnl_yen"]), 2),
                "delta_pf_vs_baseline": round((met["profit_factor"] or 0) - (baseline_metrics["profit_factor"] or 0), 4),
                "delta_maxdd_vs_baseline": round(float(met["max_drawdown_yen"]) - float(baseline_metrics["max_drawdown_yen"]), 2),
            }
        )
        for band_row in _mfe_band_rows(spec.variant_id, outs, baseline_outcomes):
            band_row["activate_mfe_pct"] = act
            band_row["giveback_pct"] = round(gb, 4)
            mfe_band_rows.append(band_row)
        symbol_day_rows.extend(_symbol_day_rows(spec.variant_id, outs, baseline_outcomes))

    grid_rows.sort(key=lambda r: float(r.get("total_pnl_yen") or -1e18), reverse=True)
    for i, r in enumerate(grid_rows, start=1):
        r["rank_by_pnl"] = i

    best = grid_rows[0]
    best_id = str(best.get("variant_id"))
    best_outcomes = by_variant[best_id]
    best_act, best_gb = _parse_grid_meta(best_id)
    full_pnl = float(best.get("total_pnl_yen") or 0)
    top_day, top_sym = _concentration(best_outcomes)
    best["top_day_share"] = top_day
    best["top_symbol_share"] = top_sym

    robust_rows: list[dict[str, Any]] = []
    days = sorted({o.day for o in baseline_outcomes if o.day})

    def _subset(exclude_day: Optional[str] = None, exclude_sym: Optional[str] = None) -> list[TradeOutcome]:
        return [
            o
            for o in best_outcomes
            if (exclude_day is None or o.day != exclude_day) and (exclude_sym is None or o.symbol != exclude_sym)
        ]

    for day in days:
        outs = _subset(exclude_day=day)
        pnl = round(sum(o.pnl_yen for o in outs), 2)
        td, ts = _concentration(outs)
        robust_rows.append(
            {
                "test": f"LOO_{day}",
                "variant_id": best_id,
                "activate_mfe_pct": best_act,
                "giveback_pct": best_gb,
                "total_pnl_yen": pnl,
                "profit_factor": _pf([o.pnl_yen for o in outs]),
                "max_drawdown_yen": _max_drawdown_yen([o.pnl_yen for o in outs]),
                "accepted_count": len(outs),
                "delta_pnl_vs_full": round(pnl - full_pnl, 2),
                "top_day_share": td,
                "top_symbol_share": ts,
            }
        )
    robust_rows.append(
        {
            "test": "full",
            "variant_id": best_id,
            "activate_mfe_pct": best_act,
            "giveback_pct": best_gb,
            "total_pnl_yen": full_pnl,
            "profit_factor": best.get("profit_factor"),
            "max_drawdown_yen": best.get("max_drawdown_yen"),
            "accepted_count": len(best_outcomes),
            "delta_pnl_vs_full": 0.0,
            "top_day_share": top_day,
            "top_symbol_share": top_sym,
        }
    )
    for sym in ("6976", "4062"):
        outs = _subset(exclude_sym=sym)
        pnl = round(sum(o.pnl_yen for o in outs), 2)
        robust_rows.append(
            {
                "test": f"exclude_{sym}",
                "variant_id": best_id,
                "activate_mfe_pct": best_act,
                "giveback_pct": best_gb,
                "total_pnl_yen": pnl,
                "profit_factor": _pf([o.pnl_yen for o in outs]),
                "max_drawdown_yen": _max_drawdown_yen([o.pnl_yen for o in outs]),
                "accepted_count": len(outs),
                "delta_pnl_vs_full": round(pnl - full_pnl, 2),
                "top_day_share": top_day,
                "top_symbol_share": top_sym,
            }
        )

    verdict = _verdict(best=best, baseline_id=baseline_id, robust_rows=robust_rows)
    sym6976 = next((r for r in symbol_day_rows if r["variant_id"] == best_id and r["symbol"] == "6976" and r["day"] == "ALL"), {})
    sym4062 = next((r for r in symbol_day_rows if r["variant_id"] == best_id and r["symbol"] == "4062" and r["day"] == "ALL"), {})
    base_row = next(r for r in grid_rows if r.get("is_baseline"))

    loo_deltas = [float(r.get("delta_pnl_vs_full") or 0) for r in robust_rows if str(r.get("test", "")).startswith("LOO_")]
    overfit_risk = "high" if loo_deltas and min(loo_deltas) < -40000 else "moderate" if loo_deltas and statistics.pstdev(loo_deltas) > 25000 else "low"

    mandatory = {
        "1_best_grid": best_id,
        "2_optimal_activate_mfe_pct": best_act,
        "3_optimal_giveback_pct": best_gb,
        "4_delta_pnl_vs_baseline": best.get("delta_pnl_vs_baseline"),
        "5_delta_pf_vs_baseline": best.get("delta_pf_vs_baseline"),
        "6_maxdd_change": best.get("delta_maxdd_vs_baseline"),
        "7_6976_impact": sym6976,
        "8_4062_impact": sym4062,
        "9_overfit_risk": overfit_risk,
        "10_runtime_candidate": verdict == "board_high_grid_candidate",
        "11_next_actions": _next_actions(verdict, best, base_row),
        "verdict": verdict,
        "baseline_variant": baseline_id,
        "baseline_pnl": baseline_metrics["total_pnl_yen"],
        "grid_size": len(grid_rows),
        "top5_grid": grid_rows[:5],
    }

    return {
        "generated_at": _now_iso(),
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "verdict": verdict,
        "mandatory_answers": mandatory,
        "_grid_rows": grid_rows,
        "_mfe_band_rows": mfe_band_rows,
        "_symbol_day_rows": symbol_day_rows,
        "_robustness_rows": robust_rows,
    }


def _next_actions(verdict: str, best: Mapping[str, Any], base: Mapping[str, Any]) -> list[str]:
    actions = [f"Verdict: {verdict}"]
    if verdict == "board_high_grid_candidate":
        actions.append(
            f"Shadow board_high activate={best.get('activate_mfe_pct')}% giveback={float(best.get('giveback_pct', 0)) * 100:.0f}%"
        )
        actions.append(f"Delta PnL vs baseline: {best.get('delta_pnl_vs_baseline')}")
    elif verdict == "overfit_exit":
        actions.append("Grid optimum unstable under LOO - do not adopt")
    else:
        actions.append(f"Baseline {base.get('variant_id')} remains optimal at {base.get('total_pnl_yen')} PnL")
    return actions


@dataclass
class Phase486Job:
    repo_root: Path
    parallel: bool = False
    max_workers: int = 2

    def run(self) -> dict[str, Any]:
        return run_phase486(repo_root=self.repo_root, parallel=self.parallel, max_workers=self.max_workers)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "grid": reports / "phase486_board_high_trailing_grid.csv",
            "mfe_bands": reports / "phase486_board_high_grid_mfe_bands.csv",
            "symbol_day": reports / "phase486_board_high_grid_symbol_day.csv",
            "robustness": reports / "phase486_board_high_grid_robustness.csv",
            "summary": reports / "phase486_summary.json",
        }
        _write_csv(paths["grid"], GRID_FIELDS, list(result.get("_grid_rows") or []))
        _write_csv(paths["mfe_bands"], MFE_BAND_FIELDS, list(result.get("_mfe_band_rows") or []))
        _write_csv(paths["symbol_day"], SYMBOL_DAY_FIELDS, list(result.get("_symbol_day_rows") or []))
        _write_csv(paths["robustness"], ROBUSTNESS_FIELDS, list(result.get("_robustness_rows") or []))
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

        doc_root = self.repo_root / "kabu_native"
        if not (doc_root / "docs").is_dir():
            doc_root = self.repo_root
        report = doc_root / "docs" / "operations" / "phase486_board_high_trailing_grid.md"
        self._write_report(report, result)
        paths["report"] = report
        return paths

    def _write_report(self, report: Path, result: Mapping[str, Any]) -> None:
        m = result.get("mandatory_answers") or {}
        top5 = m.get("top5_grid") or []
        lines = [
            "# Phase486 — Board High Trailing Grid Search",
            "",
            f"**Verdict:** `{result.get('verdict')}`",
            f"**Period:** {result.get('period_start')}-{result.get('period_end')}",
            f"**Grid:** {len(ACTIVATE_GRID)} activate x {len(GIVEBACK_GRID)} giveback = {len(ACTIVATE_GRID) * len(GIVEBACK_GRID)}",
            "",
            "## Mandatory answers",
            "",
            f"1. Best grid: **{m.get('1_best_grid')}**",
            f"2. Optimal activate: **{m.get('2_optimal_activate_mfe_pct')}%**",
            f"3. Optimal giveback: **{float(m.get('3_optimal_giveback_pct') or 0) * 100:.0f}%**",
            f"4. Delta PnL: **{m.get('4_delta_pnl_vs_baseline')}**",
            f"5. Delta PF: **{m.get('5_delta_pf_vs_baseline')}**",
            f"6. maxDD change: **{m.get('6_maxdd_change')}**",
            f"7. 6976: **{m.get('7_6976_impact')}**",
            f"8. 4062: **{m.get('8_4062_impact')}**",
            f"9. Overfit risk: **{m.get('9_overfit_risk')}**",
            f"10. Runtime candidate: **{m.get('10_runtime_candidate')}**",
            f"11. Next actions: {m.get('11_next_actions')}",
            "",
            "## Top 5 grid cells",
            "",
        ]
        for r in top5:
            lines.append(
                f"- **{r.get('variant_id')}** act={r.get('activate_mfe_pct')}% gb={float(r.get('giveback_pct', 0)) * 100:.0f}% "
                f"PnL {r.get('total_pnl_yen')} dPnL {r.get('delta_pnl_vs_baseline')}"
            )
        lines.extend(["", f"**Verdict:** `{result.get('verdict')}`", ""])
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("\n".join(lines), encoding="utf-8")
