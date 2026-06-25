"""
Phase514 — PBv2 overlay attribution (research only).

Tests whether classical technical overlays add value ON TOP of PBv2 Runtime.
One overlay at a time. No adoption. No production changes.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

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
from research.phase507_classic_indicators import Bar1m, BarIndicatorRow
from research.phase507_classic_strategy_battle import (
    BASELINE_STRATEGY_ID,
    ENTRY_RULES,
    INITIAL_EQUITY,
    _day_rows,
    _strategy_metrics,
)
from research.phase509_t15_t13_signal_audit import _build_bar_cache, _entry_fn_at_time
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

PHASE514_VERDICT = "phase514_pbv2_overlay_attribution_done"

OVERLAY_SPECS: tuple[tuple[str, Optional[str], str], ...] = (
    ("BASELINE", None, "PBv2 Runtime"),
    ("O1", "T13", "PBv2 + T13 (EMA20 & VWAP & ADX>20)"),
    ("O2", "T15", "PBv2 + T15 (RSI>50 & Stoch K>D)"),
    ("O3", "T4", "PBv2 + VWAP Filter"),
    ("O4", "T6", "PBv2 + ADX Filter"),
    ("O5", "T5", "PBv2 + EMA Filter"),
)

SUMMARY_FIELDS = [
    "scenario_id",
    "description",
    "overlay_rule_id",
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
    "adopted_trades",
    "excluded_trades",
    "substitution_trades",
    "prevented_losses_yen_100",
    "lost_gains_yen_100",
    "net_attribution_delta_yen_100",
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
]


def _float(v: Any) -> float:
    try:
        if v is None or v == "":
            return 0.0
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _trade_rows_from_state(state: CapacityReplayState, scenario_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for log in state.trade_log:
        if not log.get("exit_time"):
            continue
        tr = log.get("trade") or log
        rows.append(
            {
                "scenario_id": scenario_id,
                "symbol": str(tr.get("symbol") or "").replace(".T", ""),
                "day": str(log.get("day") or tr.get("day") or "")[:8],
                "entry_time": tr.get("entry_time"),
                "exit_time": log.get("exit_time"),
                "pnl_yen_100": _float(log.get("pnl_yen")),
                "exit_reason": log.get("exit_reason"),
                "position_key": _position_key(tr),
            }
        )
    return rows


def _pnl_by_key(rows: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    return {str(r["position_key"]): _float(r.get("pnl_yen_100")) for r in rows}


def _attribution_vs_baseline(
    baseline_rows: Sequence[Mapping[str, Any]],
    overlay_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    base_pnl = _pnl_by_key(baseline_rows)
    over_pnl = _pnl_by_key(overlay_rows)
    base_keys = set(base_pnl)
    over_keys = set(over_pnl)
    adopted = base_keys & over_keys
    excluded = base_keys - over_keys
    substitution = over_keys - base_keys

    excluded_pnls = [base_pnl[k] for k in excluded]
    prevented = round(sum(-p for p in excluded_pnls if p < 0), 2)
    lost = round(sum(p for p in excluded_pnls if p > 0), 2)
    overlay_total = round(sum(over_pnl.values()), 2)
    baseline_total = round(sum(base_pnl.values()), 2)

    return {
        "adopted_trades": len(adopted),
        "excluded_trades": len(excluded),
        "substitution_trades": len(substitution),
        "prevented_losses_yen_100": prevented,
        "lost_gains_yen_100": lost,
        "net_attribution_delta_yen_100": round(overlay_total - baseline_total, 2),
    }


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


def _overlay_pass_at_entry(
    trade: Mapping[str, Any],
    *,
    overlay_rule_id: str,
    bar_cache: Mapping[tuple[str, str], tuple[list[Bar1m], list[BarIndicatorRow]]],
) -> bool:
    ent = _parse_ts(str(trade.get("entry_time") or ""))
    if ent is None:
        return False
    return _entry_fn_at_time(
        symbol=str(trade.get("symbol") or ""),
        day=str(trade.get("day") or ""),
        entry_time=ent,
        entry_rule_id=overlay_rule_id,
        bar_cache=bar_cache,
    )


def _run_overlay_replay(
    replay_pool: Sequence[Mapping[str, Any]],
    runtime_shadows: Mapping[str, Any],
    *,
    guard_c_block: Callable[[Mapping[str, Any]], bool],
    bar_cache: Mapping[tuple[str, str], tuple[list[Bar1m], list[BarIndicatorRow]]],
    overlay_rule_id: Optional[str],
    mode_suffix: str,
) -> CapacityReplayState:
    def extra_block(trade: Mapping[str, Any]) -> bool:
        if guard_c_block(trade):
            return True
        if overlay_rule_id is None:
            return False
        return not _overlay_pass_at_entry(trade, overlay_rule_id=overlay_rule_id, bar_cache=bar_cache)

    return _replay_with_extra_block(
        replay_pool,
        runtime_shadows,
        extra_block=extra_block,
        mode_suffix=mode_suffix,
    )


def _mandatory_answers(
    summary_rows: Sequence[Mapping[str, Any]],
    baseline: Mapping[str, Any],
) -> dict[str, Any]:
    b_pnl = _float(baseline.get("total_pnl_yen_100"))
    b_pf = _float(baseline.get("profit_factor"))
    b_dd = _float(baseline.get("max_drawdown_yen_100"))

    overlays = [r for r in summary_rows if r.get("scenario_id") != "BASELINE"]
    pnl_improved = [r["scenario_id"] for r in overlays if _float(r.get("total_pnl_yen_100")) > b_pnl]
    pf_improved = [r["scenario_id"] for r in overlays if _float(r.get("profit_factor") or 0) > b_pf]
    dd_improved = [
        r["scenario_id"]
        for r in overlays
        if _float(r.get("max_drawdown_yen_100")) < b_dd
    ]

    best = max(overlays, key=lambda r: _float(r.get("total_pnl_yen_100")), default={})

    def _overlay_effect(oid: str) -> dict[str, Any]:
        row = next((r for r in overlays if r.get("scenario_id") == oid), {})
        return {
            "pnl_delta": row.get("baseline_diff_pnl"),
            "pf_delta": row.get("baseline_diff_pf"),
            "dd_delta": row.get("baseline_diff_dd"),
            "effective": _float(row.get("total_pnl_yen_100")) > b_pnl
            and _float(row.get("profit_factor") or 0) >= b_pf,
        }

    any_improvement = bool(pnl_improved or pf_improved or dd_improved)
    adopt_candidate = [
        r["scenario_id"]
        for r in overlays
        if _float(r.get("total_pnl_yen_100")) > b_pnl
        and _float(r.get("profit_factor") or 0) > b_pf
        and _float(r.get("max_drawdown_yen_100")) <= b_dd
    ]

    return {
        "1_pnl_improvement_candidates": pnl_improved,
        "2_pf_improvement_candidates": pf_improved,
        "3_dd_improvement_candidates": dd_improved,
        "4_best_overlay": best.get("scenario_id"),
        "4_best_overlay_description": best.get("description"),
        "5_T13_O1_effective": _overlay_effect("O1"),
        "6_T15_O2_effective": _overlay_effect("O2"),
        "7_VWAP_O3_effective": _overlay_effect("O3"),
        "8_ADX_O4_effective": _overlay_effect("O4"),
        "9_EMA_O5_effective": _overlay_effect("O5"),
        "10_pbv2_improvement_room": any_improvement,
        "11_adoption_candidates": adopt_candidate,
        "11_adopt_not_allowed": True,
    }


@dataclass
class Phase514Job:
    repo_root: Path

    def run(self) -> dict[str, Any]:
        bar_cache, _days = _build_bar_cache(self.repo_root)
        replay_pool, runtime_shadows, guard_c_block = _prepare_runtime_env(self.repo_root)

        summary_rows: list[dict[str, Any]] = []
        daily_rows: list[dict[str, Any]] = []
        trade_rows: list[dict[str, Any]] = []
        states: dict[str, CapacityReplayState] = {}

        for scenario_id, rule_id, description in OVERLAY_SPECS:
            state = _run_overlay_replay(
                replay_pool,
                runtime_shadows,
                guard_c_block=guard_c_block,
                bar_cache=bar_cache,
                overlay_rule_id=rule_id,
                mode_suffix=f"phase514_{scenario_id.lower()}",
            )
            states[scenario_id] = state
            entry_rule = rule_id or "PBv2"
            exit_rule = "RUNTIME"
            strat_id = f"PBv2_{scenario_id}" if scenario_id != "BASELINE" else BASELINE_STRATEGY_ID
            met = _strategy_metrics(
                state,
                strategy_id=strat_id,
                entry_rule_id=entry_rule,
                exit_rule_id=exit_rule,
            )
            row = {
                "scenario_id": scenario_id,
                "description": description,
                "overlay_rule_id": rule_id or "",
                **met,
            }
            summary_rows.append(row)
            for dr in _day_rows(state, scenario_id):
                daily_rows.append({"scenario_id": scenario_id, **{k: v for k, v in dr.items() if k != "strategy_id"}})
            trade_rows.extend(_trade_rows_from_state(state, scenario_id))

        baseline_row = next(r for r in summary_rows if r["scenario_id"] == "BASELINE")
        baseline_trades = [t for t in trade_rows if t["scenario_id"] == "BASELINE"]

        for row in summary_rows:
            if row["scenario_id"] == "BASELINE":
                row.update(
                    {
                        "adopted_trades": row["trades"],
                        "excluded_trades": 0,
                        "substitution_trades": 0,
                        "prevented_losses_yen_100": 0.0,
                        "lost_gains_yen_100": 0.0,
                        "net_attribution_delta_yen_100": 0.0,
                    }
                )
                continue
            diff = _strategy_metrics(
                states[row["scenario_id"]],
                strategy_id=row.get("strategy_id", row["scenario_id"]),
                entry_rule_id=row.get("entry_rule_id", ""),
                exit_rule_id="RUNTIME",
                baseline=baseline_row,
            )
            row["baseline_diff_pnl"] = diff["baseline_diff_pnl"]
            row["baseline_diff_pf"] = diff["baseline_diff_pf"]
            row["baseline_diff_dd"] = diff["baseline_diff_dd"]
            overlay_trades = [t for t in trade_rows if t["scenario_id"] == row["scenario_id"]]
            attr = _attribution_vs_baseline(baseline_trades, overlay_trades)
            row.update(attr)

        base_keys = {t["position_key"] for t in baseline_trades}
        annotated: list[dict[str, Any]] = []
        for t in trade_rows:
            out = {k: v for k, v in t.items() if k != "position_key"}
            if t["scenario_id"] == "BASELINE":
                out["attribution"] = "baseline"
            else:
                pk = t["position_key"]
                if pk in base_keys:
                    out["attribution"] = "adopted"
                else:
                    out["attribution"] = "substitution"
            annotated.append(out)

        mandatory = _mandatory_answers(summary_rows, baseline_row)

        return {
            "verdict": PHASE514_VERDICT,
            "generated_at": _now_iso(),
            "period_start": PERIOD_START,
            "period_end": PERIOD_END,
            "overlay_specs": [
                {"scenario_id": s, "rule_id": r, "description": d} for s, r, d in OVERLAY_SPECS
            ],
            "summary_rows": summary_rows,
            "daily_rows": daily_rows,
            "trade_rows": annotated,
            "mandatory_answers": mandatory,
            "baseline": baseline_row,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        paths = {
            "summary": reports / "phase514_overlay_summary.csv",
            "daily": reports / "phase514_overlay_daily.csv",
            "trades": reports / "phase514_overlay_trades.csv",
            "report": reports / "phase514_overlay_report.json",
            "docs": kabu / "docs" / "operations" / "phase514_pbv2_overlay_attribution.md",
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
        "# Phase514 — PBv2 Overlay Attribution",
        "",
        f"**Verdict:** `{result.get('verdict')}`",
        f"**Period:** {result.get('period_start')} – {result.get('period_end')}",
        "",
        "## Summary",
        "",
        "| Scenario | PnL | PF | maxDD | Trades | ΔPnL vs BASE |",
        "|----------|-----|----|-------|--------|--------------|",
    ]
    for row in result.get("summary_rows") or []:
        lines.append(
            f"| {row.get('scenario_id')} | {row.get('total_pnl_yen_100')} | "
            f"{row.get('profit_factor')} | {row.get('max_drawdown_yen_100')} | "
            f"{row.get('trades')} | {row.get('baseline_diff_pnl', 0)} |"
        )
    lines.extend(
        [
            "",
            "## Attribution (vs BASELINE)",
            "",
        ]
    )
    for row in result.get("summary_rows") or []:
        if row.get("scenario_id") == "BASELINE":
            continue
        lines.append(
            f"**{row.get('scenario_id')}**: adopted={row.get('adopted_trades')}, "
            f"excluded={row.get('excluded_trades')}, substitution={row.get('substitution_trades')}, "
            f"prevented_losses={row.get('prevented_losses_yen_100')}, "
            f"lost_gains={row.get('lost_gains_yen_100')}"
        )
    lines.extend(
        [
            "",
            "## Mandatory answers",
            "",
            f"1. PnL improvement candidates: **{ma.get('1_pnl_improvement_candidates')}**",
            f"2. PF improvement candidates: **{ma.get('2_pf_improvement_candidates')}**",
            f"3. DD improvement candidates: **{ma.get('3_dd_improvement_candidates')}**",
            f"4. Best overlay: **{ma.get('4_best_overlay')}** ({ma.get('4_best_overlay_description')})",
            f"5. T13 (O1) effective: **{ma.get('5_T13_O1_effective')}**",
            f"6. T15 (O2) effective: **{ma.get('6_T15_O2_effective')}**",
            f"7. VWAP (O3) effective: **{ma.get('7_VWAP_O3_effective')}**",
            f"8. ADX (O4) effective: **{ma.get('8_ADX_O4_effective')}**",
            f"9. EMA (O5) effective: **{ma.get('9_EMA_O5_effective')}**",
            f"10. PBv2 improvement room: **{ma.get('10_pbv2_improvement_room')}**",
            f"11. Adoption candidates: **{ma.get('11_adoption_candidates')}** (adopt_not_allowed=True)",
        ]
    )
    return "\n".join(lines) + "\n"
