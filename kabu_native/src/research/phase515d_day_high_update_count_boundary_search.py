"""
Phase515D — day_high update-count boundary search (research only).

Narrows the updates<=5..8 boundary from Phase515C. PBv2 Exit fixed. No adoption.
"""

from __future__ import annotations

import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase451_entry_shape_tournament import _build_price_index_to, _now_iso
from research.phase476_pre_breakout_gate_replay import _load_replay_pool
from research.phase488_current_runtime_replay import _filter_period
from research.phase493_global_entry_failure_audit import PERIOD_END, PERIOD_START
from research.phase507_classic_indicators import (
    BarIndicatorRow,
    Bar1m,
    compute_bar_indicators,
    ticks_to_1m_bars,
)
from research.phase507_classic_strategy_battle import (
    BASELINE_STRATEGY_ID,
    MIN_BARS_WARMUP,
    _day_rows,
    _run_baseline_runtime,
    _simulate_precomputed_cap,
    _universe_symbols,
)
from research.phase510_classic_system_battle import _strategy_metrics_safe
from research.phase515c_day_high_breakout_refinement import (
    BASE_ID,
    RefinementSpec,
    _concentration,
    _exclusion_for_top,
    _rule_label,
    _timing_ratios,
    _trade_rows_from_log,
    scan_day_high_refined,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

PHASE515D_VERDICT = "phase515d_day_high_update_count_boundary_search_done"
MAX_WORKERS_CAP = 4

REF_R002 = "P515C_R_002"
REF_R003 = "P515C_R_003"
REF_R022 = "P515C_R_022"

SUMMARY_FIELDS = [
    "strategy_id",
    "refinement_description",
    "phase_group",
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
    "symbol_6976_share_pct",
    "top1_day_profit_share_pct",
    "top3_day_profit_share_pct",
    "fragile",
    "revenue_type",
    "dispersion_type",
    "balance_type",
    "beats_baseline_pnl",
    "baseline_diff_pnl",
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


def _build_strategy_grid() -> list[RefinementSpec]:
    specs: list[RefinementSpec] = [
        RefinementSpec(BASE_ID, (), "day_high only (BASE)"),
        RefinementSpec(REF_R002, (("r1_max", 5),), f"day_high + {_rule_label((('r1_max', 5),))}"),
        RefinementSpec(REF_R003, (("r1_max", 8),), f"day_high + {_rule_label((('r1_max', 8),))}"),
        RefinementSpec(
            REF_R022,
            (("r1_max", 5), ("r2_max_vwap", 3)),
            f"day_high + {_rule_label((('r1_max', 5), ('r2_max_vwap', 3)))}",
        ),
    ]
    for v in (6, 7):
        rules = (("r1_max", v),)
        specs.append(
            RefinementSpec(f"P515D_D1_{v:02d}", rules, f"day_high + {_rule_label(rules)}")
        )
    for u in (6, 7):
        for w in (3, 5, 8):
            rules = (("r1_max", u), ("r2_max_vwap", w))
            specs.append(
                RefinementSpec(f"P515D_D2_{u}{w:02d}", rules, f"day_high + {_rule_label(rules)}")
            )
    for v in (6, 7):
        rules = (("r1_max", v), ("r3_min_vol", 1.0))
        specs.append(
            RefinementSpec(f"P515D_D3_{v:02d}", rules, f"day_high + {_rule_label(rules)}")
        )
    for v in (6, 7):
        rules = (("r1_max", v), ("r6_max_rsi", 90))
        specs.append(
            RefinementSpec(f"P515D_D4_{v:02d}", rules, f"day_high + {_rule_label(rules)}")
        )
    for v in (6, 7):
        rules = (("r1_max", v), ("r4_min_adx", 15))
        specs.append(
            RefinementSpec(f"P515D_D5_{v:02d}", rules, f"day_high + {_rule_label(rules)}")
        )
    return specs


def _phase_group(sid: str) -> str:
    if sid == BASELINE_STRATEGY_ID:
        return "BASELINE"
    if sid == BASE_ID:
        return "BASE"
    if sid.startswith("P515C_"):
        return "515C_REF"
    if sid.startswith("P515D_D1"):
        return "D1"
    if sid.startswith("P515D_D2"):
        return "D2"
    if sid.startswith("P515D_D3"):
        return "D3"
    if sid.startswith("P515D_D4"):
        return "D4"
    if sid.startswith("P515D_D5"):
        return "D5"
    return "OTHER"


def _classify_types(
    row: Mapping[str, Any],
    *,
    baseline: Mapping[str, Any],
    r003: Mapping[str, Any],
) -> dict[str, bool]:
    b_pnl = _float(baseline.get("total_pnl_yen_100"))
    b_pf = _float(baseline.get("profit_factor"))
    b_dd = _float(baseline.get("max_drawdown_yen_100"))
    b_stab = _float(baseline.get("daily_stability_score"))
    pnl = _float(row.get("total_pnl_yen_100"))
    pf = _float(row.get("profit_factor"))
    dd = _float(row.get("max_drawdown_yen_100"))
    stab = _float(row.get("daily_stability_score"))
    fragile = bool(row.get("fragile"))

    revenue = pnl > b_pnl and pf > b_pf and dd < b_dd
    if fragile:
        revenue = revenue  # still revenue type but noted fragile in flag

    dispersion = (
        _float(row.get("top1_symbol_profit_share_pct"))
        < _float(baseline.get("top1_symbol_profit_share_pct"))
        and _float(row.get("symbol_6976_share_pct")) < 50
        and dd < b_dd
    )

    balance = (
        pnl > b_pnl
        and pf > b_pf
        and dd < b_dd
        and _float(row.get("symbol_6976_share_pct")) < _float(r003.get("symbol_6976_share_pct"))
        and _float(row.get("top1_symbol_profit_share_pct"))
        < _float(r003.get("top1_symbol_profit_share_pct"))
        and stab >= b_stab
    )
    return {"revenue_type": revenue, "dispersion_type": dispersion, "balance_type": balance}


def _mandatory_answers(
    summary_rows: Sequence[Mapping[str, Any]],
    robustness_rows: Sequence[Mapping[str, Any]],
    *,
    baseline: Mapping[str, Any],
    r002: Mapping[str, Any],
    r003: Mapping[str, Any],
) -> dict[str, Any]:
    b_pnl = _float(baseline.get("total_pnl_yen_100"))
    d_new = [r for r in summary_rows if str(r.get("strategy_id", "")).startswith("P515D_")]
    d16 = [r for r in d_new if "D1_" in str(r.get("strategy_id")) or str(r.get("strategy_id", "")).endswith("_06") or "_D1_" in str(r.get("strategy_id"))]
    d16_7 = [r for r in d_new if str(r.get("strategy_id", "")).startswith("P515D_D1")]

    promising_d16 = [
        r["strategy_id"]
        for r in d16_7
        if _float(r.get("total_pnl_yen_100")) > b_pnl * 0.5
    ]

    beat_r003_dispersed = [
        r["strategy_id"]
        for r in summary_rows
        if r.get("strategy_id") not in (BASELINE_STRATEGY_ID, BASE_ID, REF_R003)
        and _float(r.get("total_pnl_yen_100")) > b_pnl
        and _float(r.get("top1_symbol_profit_share_pct"))
        < _float(r003.get("top1_symbol_profit_share_pct"))
        and _float(r.get("symbol_6976_share_pct")) < _float(r003.get("symbol_6976_share_pct"))
    ]

    better_than_r002 = [
        r["strategy_id"]
        for r in summary_rows
        if r.get("strategy_id") not in (BASELINE_STRATEGY_ID, BASE_ID, REF_R002)
        and _float(r.get("total_pnl_yen_100")) > _float(r002.get("total_pnl_yen_100"))
        and not r.get("fragile")
    ]

    revenue_rows = [r for r in summary_rows if r.get("revenue_type")]
    dispersion_rows = [r for r in summary_rows if r.get("dispersion_type")]
    balance_rows = [r for r in summary_rows if r.get("balance_type")]

    best_rev = max(revenue_rows, key=lambda r: _float(r.get("total_pnl_yen_100")), default={})
    best_disp = min(
        dispersion_rows,
        key=lambda r: _float(r.get("top1_symbol_profit_share_pct")),
        default={},
    )
    best_bal = max(balance_rows, key=lambda r: _float(r.get("total_pnl_yen_100")), default={})

    min_6976 = min(
        (r for r in summary_rows if r.get("strategy_id") != BASELINE_STRATEGY_ID),
        key=lambda r: _float(r.get("symbol_6976_share_pct")),
        default={},
    )

    rob_by_id: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in robustness_rows:
        rob_by_id[str(row["strategy_id"])].append(row)

    top3_sym_pos = [
        sid
        for sid, rows in rob_by_id.items()
        if any(r.get("exclusion_type") == "top3_symbols" and r.get("remains_positive") for r in rows)
    ]
    top3_day_pos = [
        sid
        for sid, rows in rob_by_id.items()
        if any(r.get("exclusion_type") == "top3_days" and r.get("remains_positive") for r in rows)
    ]

    return {
        "1_updates_6_7_promising": promising_d16,
        "2_beat_r003_dispersed_and_pbv2": beat_r003_dispersed[:10],
        "3_better_than_r002_acceptable_dependency": better_than_r002[:10],
        "4_best_revenue_type": {
            "strategy_id": best_rev.get("strategy_id"),
            "pnl": best_rev.get("total_pnl_yen_100"),
            "description": best_rev.get("refinement_description"),
        },
        "5_best_dispersion_type": {
            "strategy_id": best_disp.get("strategy_id"),
            "top1_symbol_share": best_disp.get("top1_symbol_profit_share_pct"),
            "symbol_6976_share": best_disp.get("symbol_6976_share_pct"),
        },
        "6_best_balance_type": {
            "strategy_id": best_bal.get("strategy_id"),
            "description": best_bal.get("refinement_description"),
            "pnl": best_bal.get("total_pnl_yen_100"),
        },
        "7_lowest_symbol_6976_share": {
            "strategy_id": min_6976.get("strategy_id"),
            "share_pct": min_6976.get("symbol_6976_share_pct"),
        },
        "8_top3_symbol_positive_candidates": top3_sym_pos[:10],
        "9_top3_day_positive_candidates": top3_day_pos[:10],
        "10_continue_research": bool(balance_rows) or bool(beat_r003_dispersed),
        "adopt_not_allowed": True,
    }


@dataclass
class Phase515DJob:
    repo_root: Path
    parallel: bool = True
    max_workers: int = 4

    def run(self) -> dict[str, Any]:
        specs = _build_strategy_grid()
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

        bl_trades = _trade_rows_from_log(baseline_state, BASELINE_STRATEGY_ID)
        bl_conc = _concentration(bl_trades)
        bl_timing = _timing_ratios(bl_trades, bar_cache)
        baseline_row = {
            "strategy_id": BASELINE_STRATEGY_ID,
            "refinement_description": "PBv2 Entry + PBv2 Exit",
            "phase_group": "BASELINE",
            **baseline_met,
            **bl_conc,
            **bl_timing,
            "fragile": bl_conc.get("fragile"),
            "beats_baseline_pnl": True,
            "baseline_diff_pnl": 0.0,
            "revenue_type": False,
            "dispersion_type": False,
            "balance_type": False,
        }
        summary_rows.append(baseline_row)

        for spec in specs:
            st = _simulate_precomputed_cap(
                candidates_by_id.get(spec.strategy_id, []),
                mode=f"phase515d_{spec.strategy_id}",
            )
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
                daily_rows.append(
                    {"strategy_id": spec.strategy_id, **{k: v for k, v in dr.items() if k != "strategy_id"}}
                )
            summary_rows.append(
                {
                    "strategy_id": spec.strategy_id,
                    "refinement_description": spec.description,
                    "phase_group": _phase_group(spec.strategy_id),
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
                    "fragile": conc.get("fragile"),
                    "revenue_type": False,
                    "dispersion_type": False,
                    "balance_type": False,
                }
            )

        r003_row = next(r for r in summary_rows if r["strategy_id"] == REF_R003)
        r002_row = next(r for r in summary_rows if r["strategy_id"] == REF_R002)
        for row in summary_rows:
            if row["strategy_id"] == BASELINE_STRATEGY_ID:
                continue
            types = _classify_types(row, baseline=baseline_row, r003=r003_row)
            row.update(types)

        top_ids = set()
        for key in ("balance_type", "revenue_type", "dispersion_type"):
            for r in summary_rows:
                if r.get(key):
                    top_ids.add(r["strategy_id"])
        for r in sorted(
            [x for x in summary_rows if x["strategy_id"].startswith("P515D_")],
            key=lambda x: _float(x.get("total_pnl_yen_100")),
            reverse=True,
        )[:5]:
            top_ids.add(r["strategy_id"])
        for ref in (REF_R002, REF_R003, REF_R022, BASE_ID):
            top_ids.add(ref)

        robustness_rows: list[dict[str, Any]] = []
        for sid in top_ids:
            spec = next(s for s in specs if s.strategy_id == sid)
            trades = [t for t in trade_rows if t["strategy_id"] == sid]
            conc = _concentration(trades)
            for ex in _exclusion_for_top(sid, trades, conc, baseline_pnl=baseline_pnl):
                ex["refinement_description"] = spec.description
                robustness_rows.append(ex)

        mandatory = _mandatory_answers(
            summary_rows,
            robustness_rows,
            baseline=baseline_row,
            r002=r002_row,
            r003=r003_row,
        )

        return {
            "verdict": PHASE515D_VERDICT,
            "generated_at": _now_iso(),
            "period_start": PERIOD_START,
            "period_end": PERIOD_END,
            "strategy_count": len(specs) + 1,
            "summary_rows": summary_rows,
            "daily_rows": daily_rows,
            "trade_rows": trade_rows,
            "robustness_rows": robustness_rows,
            "mandatory_answers": mandatory,
            "baseline": baseline_met,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        paths = {
            "summary": reports / "phase515d_boundary_summary.csv",
            "daily": reports / "phase515d_boundary_daily.csv",
            "trades": reports / "phase515d_boundary_trades.csv",
            "robustness": reports / "phase515d_boundary_robustness.csv",
            "report": reports / "phase515d_report.json",
            "docs": kabu / "docs" / "operations" / "phase515d_day_high_update_count_boundary_search.md",
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
        "# Phase515D — day_high Update-Count Boundary Search",
        "",
        f"**Verdict:** `{result.get('verdict')}`",
        "",
        "## Mandatory answers",
        "",
        f"1. updates 6/7 promising: **{ma.get('1_updates_6_7_promising')}**",
        f"2. Beat R_003 dispersed + PBv2: **{ma.get('2_beat_r003_dispersed_and_pbv2')}**",
        f"3. Better than R_002: **{ma.get('3_better_than_r002_acceptable_dependency')}**",
        f"4. Best revenue: **{ma.get('4_best_revenue_type')}**",
        f"5. Best dispersion: **{ma.get('5_best_dispersion_type')}**",
        f"6. Best balance: **{ma.get('6_best_balance_type')}**",
        f"7. Lowest 6976 share: **{ma.get('7_lowest_symbol_6976_share')}**",
        f"8. top3 symbol positive: **{ma.get('8_top3_symbol_positive_candidates')}**",
        f"9. top3 day positive: **{ma.get('9_top3_day_positive_candidates')}**",
        f"10. Continue research: **{ma.get('10_continue_research')}**",
    ]
    return "\n".join(lines) + "\n"
