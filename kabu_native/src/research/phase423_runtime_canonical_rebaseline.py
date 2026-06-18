"""
Phase423 — Canonical Runtime Rebaseline (Post-Phase421).

Formal historical backfill for the latest runtime configuration over 20260529–20260616:
- same_symbol_open_policy = no_overlap_replace (Phase414)
- max_concurrent_positions = 5 (Phase421)
- position_cap_mode = true
- fixed_stop_1p2, Board Dynamic Trailing, Phase314 Entry
- paper_only = true, order_enabled = false

Research / recompute only — no Runtime/YAML/Entry/Exit/Order/Discord changes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.equity_dynamic_stop_shadow import enrich_trades_with_entry_price
from research.market_sector_heat import _pf, _write_csv
from research.phase271_leverage_attribution_and_robustness import simulate_audited
from research.phase382_capital_constrained_backtest import _day_from_ts, _parse_ts
from research.phase400_holding_time_audit import hold_seconds, normalize_exit_reason
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase416_post_no_overlap_shadow_rebaseline import (
    _basic_metrics,
    _counts_by_bucket,
    _ensure_hold_sec,
    compute_phase409_boundary_shadow,
    load_baseline_a_trades,
    load_baseline_b_trades,
)
from research.phase420_cap5_adoption_review import (
    CAP3,
    CAP5,
    LEVERAGE,
    STARTING_EQUITY,
    STOP_POLICY,
    _accepted_trades,
    _best_worst_day,
    _capital_usage,
    _hold_stats_from_accepted,
    _open_position_distribution,
    evaluate_cap,
)
from research.structural_trade_normalize import resolve_reports_dir

JST = ZoneInfo("Asia/Tokyo")

PERIOD_START = "20260529"
PERIOD_END = "20260616"

CANONICAL_RUNTIME = {
    "same_symbol_open_policy": "no_overlap_replace",
    "max_concurrent_positions": 5,
    "position_cap_mode": True,
    "stop_policy": STOP_POLICY,
    "exit_policy": "combined_structural_exit_v1_trailing_mfe_shadow",
    "entry_policy": "phase314_entry_score_v2_min_3",
    "paper_only": True,
    "order_enabled": False,
    "starting_equity_yen": STARTING_EQUITY,
    "leverage": LEVERAGE,
}

TRADES_FIELDS = [
    "logged_at",
    "day",
    "session",
    "symbol",
    "entry_time",
    "exit_time",
    "hold_sec",
    "exit_reason",
    "entry_price",
    "sim_status",
    "reject_reason",
    "pnl_yen",
    "pnl_yen_100",
]

DAILY_FIELDS = [
    "day",
    "start_equity",
    "end_equity",
    "daily_pnl",
    "drawdown_pct",
    "accepted_trade_count",
    "rejected_trade_count",
    "max_gross_position_value",
    "gross_ratio_to_limit",
]

COMPARISON_FIELDS = [
    "phase",
    "label",
    "trade_count",
    "accepted_count",
    "rejected_count",
    "profit_factor",
    "total_pnl_yen",
    "total_pnl_yen_100",
    "max_drawdown_yen",
    "win_rate",
    "avg_hold_sec",
    "median_hold_sec",
    "boundary_eligible_rate",
    "note",
]


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _float(val: Any) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _collapse_overlap_stats(baseline_a: Sequence[Mapping[str, Any]], baseline_b: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    a_counts = _counts_by_bucket(baseline_a)
    b_counts = _counts_by_bucket(baseline_b)
    return {
        "baseline_a_trade_count": len(baseline_a),
        "baseline_b_trade_count": len(baseline_b),
        "collapse_trade_reduction": len(baseline_a) - len(baseline_b),
        "overlap_replaced_review_count_baseline_a": int(a_counts.get("overlap_replaced_review") or 0),
        "overlap_replaced_review_count_baseline_b": int(b_counts.get("overlap_replaced_review") or 0),
        "overlap_replaced_review_reduction": int(a_counts.get("overlap_replaced_review") or 0)
        - int(b_counts.get("overlap_replaced_review") or 0),
    }


def _best_worst_trade(accepted: Sequence[Mapping[str, Any]], *, sim: Mapping[str, Any]) -> dict[str, Any]:
    pnls: list[tuple[str, str, float]] = []
    state = sim.get("_state")
    if state is not None:
        for log in getattr(state, "trade_log", []) or []:
            trade = log.get("trade") or {}
            key = str(log.get("key") or "")
            pnl = float(log.get("pnl_yen") or 0.0)
            pnls.append((key, str(trade.get("symbol") or ""), pnl))
    if not pnls and accepted:
        for t in accepted:
            pnl = _float(t.get("pnl_yen_100_float") or t.get("pnl_yen_100") or 0) * 100.0
            pnls.append((str(t.get("symbol") or ""), str(t.get("day") or ""), pnl))
    if not pnls:
        return {"best_trade_yen": 0.0, "worst_trade_yen": 0.0, "best_trade_key": None, "worst_trade_key": None}
    best = max(pnls, key=lambda x: x[2])
    worst = min(pnls, key=lambda x: x[2])
    return {
        "best_trade_yen": round(best[2], 2),
        "worst_trade_yen": round(worst[2], 2),
        "best_trade_key": best[0],
        "worst_trade_key": worst[0],
    }


def _build_trade_rows(sim: Mapping[str, Any], *, logged_at: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    state = sim.get("_state")
    if state is None:
        return rows
    for log in getattr(state, "trade_log", []) or []:
        trade = dict(log.get("trade") or {})
        pnl = float(log.get("pnl_yen") or 0.0)
        et = str(trade.get("entry_time") or "")
        xt = str(log.get("exit_time") or trade.get("exit_time") or "")
        hs = float(trade.get("hold_sec") or 0.0)
        if hs <= 0:
            hs = float(hold_seconds(et, xt))
        rows.append(
            {
                "logged_at": logged_at,
                "day": str(log.get("day") or trade.get("day") or _day_from_ts(et) or ""),
                "session": str(trade.get("session") or ""),
                "symbol": str(trade.get("symbol") or ""),
                "entry_time": et,
                "exit_time": xt,
                "hold_sec": round(hs, 2),
                "exit_reason": normalize_exit_reason(str(trade.get("exit_reason") or trade.get("close_reason") or "")),
                "entry_price": trade.get("entry_price"),
                "sim_status": "accepted",
                "reject_reason": "",
                "pnl_yen": round(pnl, 2),
                "pnl_yen_100": round(pnl / 100.0, 2),
            }
        )
    for rej in sim.get("reject_log") or []:
        trade = dict(rej.get("trade") or {})
        et = str(trade.get("entry_time") or "")
        rows.append(
            {
                "logged_at": logged_at,
                "day": str(trade.get("day") or _day_from_ts(et) or ""),
                "session": str(trade.get("session") or ""),
                "symbol": str(trade.get("symbol") or ""),
                "entry_time": et,
                "exit_time": str(trade.get("exit_time") or ""),
                "hold_sec": float(trade.get("hold_sec") or 0.0),
                "exit_reason": normalize_exit_reason(str(trade.get("exit_reason") or trade.get("close_reason") or "")),
                "entry_price": trade.get("entry_price"),
                "sim_status": "rejected",
                "reject_reason": str(rej.get("reason") or ""),
                "pnl_yen": round(float(rej.get("counterfactual_pnl") or 0.0), 2),
                "pnl_yen_100": round(float(rej.get("counterfactual_pnl") or 0.0) / 100.0, 2),
            }
        )
    rows.sort(
        key=lambda r: (
            str(r.get("day") or ""),
            _parse_ts(str(r.get("entry_time") or "")) or datetime.min.replace(tzinfo=JST),
            str(r.get("symbol") or ""),
            str(r.get("sim_status") or ""),
        )
    )
    return rows


def _build_daily_rows(sim: Mapping[str, Any]) -> list[dict[str, Any]]:
    limit = float(STARTING_EQUITY) * float(LEVERAGE)
    max_gross = sim.get("_max_gross_by_day") or {}
    out: list[dict[str, Any]] = []
    for r in sim.get("_daily_rows") or []:
        day = str(r.get("day") or "")
        g = float(max_gross.get(day) or 0.0)
        out.append(
            {
                "day": day,
                "start_equity": r.get("start_equity"),
                "end_equity": r.get("end_equity"),
                "daily_pnl": r.get("daily_pnl"),
                "drawdown_pct": r.get("drawdown_pct"),
                "accepted_trade_count": r.get("accepted_trade_count"),
                "rejected_trade_count": r.get("rejected_trade_count"),
                "max_gross_position_value": round(g, 2),
                "gross_ratio_to_limit": round(g / limit, 6) if limit > 0 else 0.0,
            }
        )
    return out


def _phase399_capital_metrics(enriched_a: Sequence[Mapping[str, Any]], *, repo_root: Path, reports_dir: Path) -> dict[str, Any]:
    return evaluate_cap(enriched_a, cap=CAP3, repo_root=repo_root, reports_dir=reports_dir)


def _phase413_structural_metrics(baseline_b: Sequence[Mapping[str, Any]], *, repo_root: Path, reports_dir: Path) -> dict[str, Any]:
    basic = _basic_metrics(baseline_b)
    boundary = compute_phase409_boundary_shadow(list(baseline_b), repo_root=repo_root, reports_dir=reports_dir)
    eligible = int(boundary.get("eligible_count") or 0)
    total = len(baseline_b)
    return {
        "label": "phase413_no_overlap_replace_structural",
        "trade_count": total,
        "accepted_count": total,
        "rejected_count": 0,
        "reject_note": "structural_shadow_all_included",
        "profit_factor": float(basic.get("pf") or 0.0),
        "total_pnl_yen": round(float(basic.get("total_pnl_yen_100") or 0.0), 2),
        "total_pnl_yen_100": round(float(basic.get("total_pnl_yen_100") or 0.0) / 100.0, 2),
        "max_drawdown_yen": float(basic.get("maxdd_yen_100") or 0.0),
        "win_rate": float(basic.get("win_rate") or 0.0) / 100.0,
        "avg_hold_sec": float(basic.get("avg_hold_sec") or 0.0),
        "median_hold_sec": float(basic.get("median_hold_sec") or 0.0),
        "boundary_eligible_count": eligible,
        "boundary_eligible_rate": round(eligible / total, 6) if total else 0.0,
        "boundary_hit_count": int(boundary.get("would_hit_count") or 0),
        "overlap_replaced_review_count": int(basic.get("overlap_replaced_review") or 0),
    }


def _comparison_row(phase: str, label: str, m: Mapping[str, Any], *, note: str = "") -> dict[str, Any]:
    return {
        "phase": phase,
        "label": label,
        "trade_count": int(m.get("trade_count") or 0),
        "accepted_count": int(m.get("accepted_count") or 0),
        "rejected_count": int(m.get("rejected_count") or 0),
        "profit_factor": round(float(m.get("profit_factor") or 0.0), 4),
        "total_pnl_yen": round(float(m.get("total_pnl_yen") or 0.0), 2),
        "total_pnl_yen_100": round(float(m.get("total_pnl_yen_100") or 0.0), 2),
        "max_drawdown_yen": round(float(m.get("max_drawdown_yen") or 0.0), 2),
        "win_rate": round(float(m.get("win_rate") or 0.0), 4),
        "avg_hold_sec": round(float(m.get("avg_hold_sec") or m.get("position_duration", {}).get("avg_hold_sec") or 0.0), 2),
        "median_hold_sec": round(
            float(m.get("median_hold_sec") or m.get("position_duration", {}).get("median_hold_sec") or 0.0), 2
        ),
        "boundary_eligible_rate": round(
            float(
                m.get("boundary_eligible_rate")
                or (m.get("boundary_interaction") or {}).get("eligible_rate")
                or 0.0
            ),
            6,
        ),
        "note": note,
    }


def run_phase423_rebaseline(*, repo_root: Path) -> dict[str, Any]:
    logged_at = _now_iso()
    reports_dir = resolve_reports_dir(repo_root)

    baseline_a = load_baseline_a_trades(repo_root)
    baseline_b = load_baseline_b_trades(baseline_a)
    enriched_a, enrich_meta_a = enrich_trades_with_entry_price([dict(t) for t in baseline_a], repo_root=repo_root)
    enriched_b, enrich_meta_b = enrich_trades_with_entry_price([dict(t) for t in baseline_b], repo_root=repo_root)

    overlap_stats = _collapse_overlap_stats(baseline_a, baseline_b)

    phase399 = _phase399_capital_metrics(enriched_a, repo_root=repo_root, reports_dir=reports_dir)
    phase413 = _phase413_structural_metrics(baseline_b, repo_root=repo_root, reports_dir=reports_dir)
    phase423 = evaluate_cap(enriched_b, cap=CAP5, repo_root=repo_root, reports_dir=reports_dir)
    sim = phase423["_sim"]

    accepted_trades = _accepted_trades(sim)
    holds = _hold_stats_from_accepted(sim)
    bw_trade = _best_worst_trade(accepted_trades, sim=sim)
    bw_day = _best_worst_day(sim)
    open_dist = _open_position_distribution(sim)
    cap_usage = _capital_usage(sim, leverage=LEVERAGE)
    boundary = phase423.get("boundary_interaction") or {}

    period_days = sorted({str(t.get("day") or "") for t in enriched_b if t.get("day")})
    expected_days = 11
    period_ok = PERIOD_START <= min(period_days) and max(period_days) <= PERIOD_END and len(period_days) >= expected_days

    canonical_summary = {
        "phase": "423-Runtime-Canonical-Rebaseline",
        "generated_at": logged_at,
        "verdict": "canonical_baseline_established" if period_ok else "rebaseline_failed",
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "period_days": period_days,
        "period_day_count": len(period_days),
        "canonical_runtime": CANONICAL_RUNTIME,
        "inputs": {
            "baseline_a_trade_count": len(baseline_a),
            "baseline_b_trade_count": len(baseline_b),
            "entry_price_enrichment_a": enrich_meta_a,
            "entry_price_enrichment_b": enrich_meta_b,
        },
        "overlap_policy_stats": {
            **overlap_stats,
            "same_symbol_overlap_reject_count": 0,
            "same_symbol_overlap_reject_note": "no_overlap_replace collapses chains; capital sim does not re-reject",
            "collapse_trade_reduction": overlap_stats["collapse_trade_reduction"],
        },
        "metrics": {
            "trade_count_input_stream": len(enriched_b),
            "trade_count": len(enriched_b),
            "accepted_count": int(phase423.get("accepted_count") or 0),
            "rejected_count": int(phase423.get("rejected_count") or 0),
            "profit_factor": float(phase423.get("profit_factor") or 0.0),
            "total_pnl_yen": float(phase423.get("total_pnl_yen") or 0.0),
            "total_pnl_yen_100": float(phase423.get("total_pnl_yen_100") or 0.0),
            "max_drawdown_yen": float(phase423.get("max_drawdown_yen") or 0.0),
            "max_drawdown_pct": float(phase423.get("max_drawdown_pct") or 0.0),
            "win_rate": float(phase423.get("win_rate") or 0.0),
            "avg_hold_sec": holds.get("avg_hold_sec"),
            "median_hold_sec": holds.get("median_hold_sec"),
            "best_trade_yen": bw_trade.get("best_trade_yen"),
            "worst_trade_yen": bw_trade.get("worst_trade_yen"),
            "best_trade_key": bw_trade.get("best_trade_key"),
            "worst_trade_key": bw_trade.get("worst_trade_key"),
            "overlap_replaced_review_count": overlap_stats["overlap_replaced_review_count_baseline_b"],
            "boundary_eligible_count": int(boundary.get("eligible_count") or 0),
            "boundary_eligible_rate": float(boundary.get("eligible_rate") or 0.0),
            "boundary_hit_count": int(boundary.get("would_hit_count") or 0),
            "max_open_distribution": open_dist,
            "capital_usage": cap_usage,
            "buying_power_reject_count": int(phase423.get("buying_power_reject") or 0),
            "reject_reason_breakdown": dict(phase423.get("reject_reason_counts") or {}),
            "best_worst_day": bw_day,
        },
        "comparison_baseline": {
            "phase399": {
                "label": "phase399_position_cap_cap3_capital_sim",
                "trade_count_input": len(enriched_a),
                "accepted_count": int(phase399.get("accepted_count") or 0),
                "rejected_count": int(phase399.get("rejected_count") or 0),
                "profit_factor": float(phase399.get("profit_factor") or 0.0),
                "total_pnl_yen": float(phase399.get("total_pnl_yen") or 0.0),
                "max_drawdown_yen": float(phase399.get("max_drawdown_yen") or 0.0),
                "avg_hold_sec": (phase399.get("position_duration") or {}).get("avg_hold_sec"),
                "median_hold_sec": (phase399.get("position_duration") or {}).get("median_hold_sec"),
            },
            "phase413": phase413,
            "phase423_vs_phase399": {
                "delta_pnl_yen": round(float(phase423.get("total_pnl_yen") or 0.0) - float(phase399.get("total_pnl_yen") or 0.0), 2),
                "delta_profit_factor": round(float(phase423.get("profit_factor") or 0.0) - float(phase399.get("profit_factor") or 0.0), 6),
                "delta_max_drawdown_yen": round(
                    float(phase423.get("max_drawdown_yen") or 0.0) - float(phase399.get("max_drawdown_yen") or 0.0), 2
                ),
                "delta_accepted": int(phase423.get("accepted_count") or 0) - int(phase399.get("accepted_count") or 0),
                "delta_rejected": int(phase423.get("rejected_count") or 0) - int(phase399.get("rejected_count") or 0),
            },
            "phase423_vs_phase413": {
                "delta_pnl_yen": round(float(phase423.get("total_pnl_yen") or 0.0) - float(phase413.get("total_pnl_yen") or 0.0), 2),
                "delta_profit_factor": round(float(phase423.get("profit_factor") or 0.0) - float(phase413.get("profit_factor") or 0.0), 6),
                "delta_max_drawdown_yen": round(
                    float(phase423.get("max_drawdown_yen") or 0.0) - float(phase413.get("max_drawdown_yen") or 0.0), 2
                ),
                "delta_trade_count": int(phase423.get("accepted_count") or 0) - int(phase413.get("trade_count") or 0),
                "note": "Phase413 is structural shadow (681); Phase423 is CAP5 capital-accepted set",
            },
        },
        "forward_comparison_policy": {
            "use_as_baseline_from": "20260617",
            "compare_forward_shadow_to": "phase423_runtime_canonical_rebaseline",
            "adopt_as_canonical_baseline": period_ok,
        },
        "constraints": {
            "runtime_change_forbidden": True,
            "yaml_change_forbidden": True,
            "entry_exit_order_discord_forbidden": True,
        },
    }

    comparison_rows = [
        _comparison_row(
            "399",
            "phase399_position_cap_cap3",
            {
                **phase399,
                "trade_count": len(enriched_a),
                "win_rate": phase399.get("win_rate"),
            },
            note="CAP3 capital sim on Baseline A (replace-era position-cap stream)",
        ),
        _comparison_row(
            "413",
            "phase413_no_overlap_replace",
            phase413,
            note="Structural shadow collapse (681); no capital-cap rejects",
        ),
        _comparison_row(
            "423",
            "phase423_canonical_runtime_cap5",
            {
                **phase423,
                "trade_count": len(enriched_b),
                "avg_hold_sec": holds.get("avg_hold_sec"),
                "median_hold_sec": holds.get("median_hold_sec"),
                "boundary_eligible_rate": boundary.get("eligible_rate"),
            },
            note="Canonical runtime: no_overlap_replace + CAP5 capital sim",
        ),
    ]

    return {
        "summary": canonical_summary,
        "_trade_rows": _build_trade_rows(sim, logged_at=logged_at),
        "_daily_rows": _build_daily_rows(sim),
        "_comparison_rows": comparison_rows,
    }


def render_report_md(payload: Mapping[str, Any]) -> str:
    s = payload.get("summary") or {}
    m = s.get("metrics") or {}
    cmp_b = s.get("comparison_baseline") or {}
    v399 = cmp_b.get("phase399") or {}
    v413 = cmp_b.get("phase413") or {}
    d399 = cmp_b.get("phase423_vs_phase399") or {}
    d413 = cmp_b.get("phase423_vs_phase413") or {}
    verdict = s.get("verdict") or "rebaseline_failed"
    adopt = (s.get("forward_comparison_policy") or {}).get("adopt_as_canonical_baseline")

    lines = [
        "# Phase423 — Canonical Runtime Rebaseline (Post-Phase421)",
        "",
        f"Generated: {s.get('generated_at')}",
        f"Verdict: **{verdict}**",
        "",
        "## Canonical Runtime (正式Baseline)",
        "",
        f"- same_symbol_open_policy: `{CANONICAL_RUNTIME['same_symbol_open_policy']}`",
        f"- max_concurrent_positions: **{CANONICAL_RUNTIME['max_concurrent_positions']}**",
        f"- position_cap_mode: `{CANONICAL_RUNTIME['position_cap_mode']}`",
        f"- stop: `{CANONICAL_RUNTIME['stop_policy']}`",
        f"- paper_only / order_enabled: `{CANONICAL_RUNTIME['paper_only']}` / `{CANONICAL_RUNTIME['order_enabled']}`",
        "",
        "## 必須回答",
        "",
        f"1. **最新Runtime正式Baseline**: Phase423 canonical (no_overlap_replace + CAP5 + fixed_stop_1p2, 1.5M lev2)",
        f"2. **5/29〜6/16結果**: accepted={m.get('accepted_count')}, rejected={m.get('rejected_count')}, "
        f"PnL={m.get('total_pnl_yen')} yen ({m.get('total_pnl_yen_100')} yen/100), PF={m.get('profit_factor')}, maxDD={m.get('max_drawdown_yen')} yen",
        f"3. **Phase399との差**: ΔPnL={d399.get('delta_pnl_yen')} yen, ΔPF={d399.get('delta_profit_factor')}, "
        f"ΔmaxDD={d399.get('delta_max_drawdown_yen')} yen, Δaccepted={d399.get('delta_accepted')}",
        f"4. **Phase413との差**: ΔPnL={d413.get('delta_pnl_yen')} yen, ΔPF={d413.get('delta_profit_factor')}, "
        f"ΔmaxDD={d413.get('delta_max_drawdown_yen')} yen ({d413.get('note')})",
        f"5. **PF**: {m.get('profit_factor')}",
        f"6. **PnL**: {m.get('total_pnl_yen')} yen",
        f"7. **maxDD**: {m.get('max_drawdown_yen')} yen",
        f"8. **trade_count**: input={m.get('trade_count')}, accepted={m.get('accepted_count')}",
        f"9. **hold時間**: avg={m.get('avg_hold_sec')}s, median={m.get('median_hold_sec')}s",
        f"10. **Boundary対象率**: eligible_rate={m.get('boundary_eligible_rate')} "
        f"({m.get('boundary_eligible_count')}/{m.get('accepted_count')} accepted), hit={m.get('boundary_hit_count')}",
        f"11. **今後の比較基準として採用するか**: {'**採用** (20260617以降のForwardは本Baselineと比較)' if adopt else '**保留** (rebaseline未完了)'}",
        "",
        "## Phase399 → Phase413 → Phase423",
        "",
        "| Phase | trade_count | accepted | rejected | PF | PnL (yen) | maxDD | avg_hold | median_hold |",
        "|-------|-------------|----------|----------|-----|-----------|-------|----------|-------------|",
        f"| 399 | {v399.get('trade_count_input')} | {v399.get('accepted_count')} | {v399.get('rejected_count')} | "
        f"{v399.get('profit_factor')} | {v399.get('total_pnl_yen')} | {v399.get('max_drawdown_yen')} | "
        f"{v399.get('avg_hold_sec')} | {v399.get('median_hold_sec')} |",
        f"| 413 | {v413.get('trade_count')} | {v413.get('accepted_count')} | {v413.get('rejected_count')} | "
        f"{v413.get('profit_factor')} | {v413.get('total_pnl_yen')} | {v413.get('max_drawdown_yen')} | "
        f"{v413.get('avg_hold_sec')} | {v413.get('median_hold_sec')} |",
        f"| 423 | {m.get('trade_count')} | {m.get('accepted_count')} | {m.get('rejected_count')} | "
        f"{m.get('profit_factor')} | {m.get('total_pnl_yen')} | {m.get('max_drawdown_yen')} | "
        f"{m.get('avg_hold_sec')} | {m.get('median_hold_sec')} |",
        "",
        "## Overlap / Reject breakdown (Phase423)",
        "",
        f"- overlap_replaced_review (structural B): {m.get('overlap_replaced_review_count')}",
        f"- collapse reduction (A→B): {(s.get('overlap_policy_stats') or {}).get('collapse_trade_reduction')}",
        f"- buying_power_reject: {m.get('buying_power_reject_count')}",
        f"- reject_reason_breakdown: `{m.get('reject_reason_breakdown')}`",
        "",
        "## Outputs",
        "",
        "- `results/reports/phase423_runtime_canonical_rebaseline_summary.json`",
        "- `results/reports/phase423_runtime_canonical_rebaseline_daily.csv`",
        "- `results/reports/phase423_runtime_canonical_rebaseline_trades.csv`",
        "- `results/reports/phase423_runtime_vs_phase399_phase413.csv`",
        "",
    ]
    return "\n".join(lines)


@dataclass
class Phase423Job:
    repo_root: Path
    reports_dir: Path

    def run(self) -> dict[str, Any]:
        return run_phase423_rebaseline(repo_root=self.repo_root)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = self.reports_dir
        reports.mkdir(parents=True, exist_ok=True)
        summary_path = reports / "phase423_runtime_canonical_rebaseline_summary.json"
        daily_path = reports / "phase423_runtime_canonical_rebaseline_daily.csv"
        trades_path = reports / "phase423_runtime_canonical_rebaseline_trades.csv"
        comparison_path = reports / "phase423_runtime_vs_phase399_phase413.csv"
        report_path = self.repo_root / "docs" / "operations" / "phase423_runtime_canonical_rebaseline_report.md"

        summary_payload = result.get("summary") or {}
        summary_path.write_text(json.dumps(summary_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _write_csv(daily_path, DAILY_FIELDS, result.get("_daily_rows") or [])
        _write_csv(trades_path, TRADES_FIELDS, result.get("_trade_rows") or [])
        _write_csv(comparison_path, COMPARISON_FIELDS, result.get("_comparison_rows") or [])
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(render_report_md(result), encoding="utf-8")

        return {
            "summary": summary_path,
            "daily": daily_path,
            "trades": trades_path,
            "comparison": comparison_path,
            "report": report_path,
        }
