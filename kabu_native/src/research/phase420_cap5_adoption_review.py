"""
Phase420 — CAP5 Adoption Review & Runtime Alignment (Part A + readiness only).

Baseline: Phase413 no_overlap_replace (Baseline B) over 20260529–20260616.
Compare CAP3 vs CAP5 under:
- 1.5M yen
- leverage 2x
- 100 shares
- fixed_stop_1p2

Research-only — Runtime/YAML/Entry/Exit/Order/Discord changes forbidden in this phase.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.phase271_leverage_attribution_and_robustness import simulate_audited
from research.phase382_capital_constrained_backtest import _day_from_ts
from research.structural_trade_normalize import resolve_reports_dir

JST = ZoneInfo("Asia/Tokyo")

STARTING_EQUITY = 1_500_000
LEVERAGE = 2.0
STOP_POLICY = "fixed_stop_1p2"

CAP3 = 3
CAP5 = 5

DAILY_FIELDS = [
    "day",
    "cap3_start_equity",
    "cap3_end_equity",
    "cap3_daily_pnl",
    "cap3_drawdown_pct",
    "cap3_accepted",
    "cap3_rejected",
    "cap3_max_gross_position_value",
    "cap3_max_gross_ratio_to_limit",
    "cap5_start_equity",
    "cap5_end_equity",
    "cap5_daily_pnl",
    "cap5_drawdown_pct",
    "cap5_accepted",
    "cap5_rejected",
    "cap5_max_gross_position_value",
    "cap5_max_gross_ratio_to_limit",
    "delta_daily_pnl_cap5_minus_cap3",
]


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _pnl_yen(sim: Mapping[str, Any]) -> float:
    start = float(sim.get("starting_equity") or sim.get("initial_equity") or STARTING_EQUITY)
    final = float(sim.get("final_equity") or start)
    return round(final - start, 2)


def _accepted_trades(sim: Mapping[str, Any]) -> list[dict[str, Any]]:
    state = sim.get("_state")
    if state is None:
        return []
    trades: list[dict[str, Any]] = []
    for log in getattr(state, "trade_log", []) or []:
        trade = dict(log.get("trade") or {})
        # keep normalized exit_time when available
        if log.get("exit_time"):
            trade["exit_time"] = log.get("exit_time")
        if log.get("day"):
            trade["day"] = log.get("day")
        trades.append(trade)
    return trades


def _hold_stats_from_accepted(sim: Mapping[str, Any]) -> dict[str, Any]:
    holds: list[float] = []
    for t in _accepted_trades(sim):
        hs = float(t.get("hold_sec") or 0.0)
        if hs > 0:
            holds.append(hs)
    if not holds:
        return {"avg_hold_sec": 0.0, "median_hold_sec": 0.0}
    return {
        "avg_hold_sec": round(statistics.mean(holds), 2),
        "median_hold_sec": round(statistics.median(holds), 2),
    }


def _open_position_distribution(sim: Mapping[str, Any]) -> dict[str, Any]:
    """
    Approximate open position count distribution by replaying equity curve events.
    We increment on 'entry', decrement on 'exit'/'force_exit'. This uses equity_curve ordering.
    """
    curve = sim.get("_equity_curve") or []
    open_count = 0
    hist: dict[int, int] = {}
    peak = 0
    for row in curve:
        et = str(row.get("event_type") or "")
        if et == "entry":
            open_count += 1
        elif et in ("exit", "force_exit"):
            open_count = max(0, open_count - 1)
        peak = max(peak, open_count)
        hist[open_count] = hist.get(open_count, 0) + 1
    # normalize to percentages
    total = sum(hist.values()) or 1
    pct = {str(k): round(v / total, 6) for k, v in sorted(hist.items())}
    return {"peak_open_positions": peak, "open_position_distribution_pct": pct}


def _capital_usage(sim: Mapping[str, Any], *, leverage: float) -> dict[str, Any]:
    max_gross_by_day = sim.get("_max_gross_by_day") or {}
    if not max_gross_by_day:
        return {"max_gross_yen": 0.0, "p90_gross_ratio": 0.0, "p99_gross_ratio": 0.0, "by_day": {}}
    limit = float(STARTING_EQUITY) * float(leverage)
    ratios = []
    by_day: dict[str, Any] = {}
    for day, gross in max_gross_by_day.items():
        g = float(gross or 0.0)
        r = (g / limit) if limit > 0 else 0.0
        ratios.append(r)
        by_day[str(day)] = {
            "max_gross_position_value": round(g, 2),
            "gross_ratio_to_limit": round(r, 6),
        }
    ratios_sorted = sorted(ratios)
    p90 = ratios_sorted[int(max(0, (len(ratios_sorted) - 1) * 0.90))] if ratios_sorted else 0.0
    p99 = ratios_sorted[int(max(0, (len(ratios_sorted) - 1) * 0.99))] if ratios_sorted else 0.0
    return {
        "max_gross_yen": round(max(float(v or 0.0) for v in max_gross_by_day.values()), 2),
        "p90_gross_ratio": round(float(p90), 6),
        "p99_gross_ratio": round(float(p99), 6),
        "by_day": by_day,
    }


def _best_worst_day(sim: Mapping[str, Any]) -> dict[str, Any]:
    rows = sim.get("_daily_rows") or []
    if not rows:
        return {"best_day": None, "best_day_pnl": 0.0, "worst_day": None, "worst_day_pnl": 0.0}
    pnls = [(str(r.get("day") or ""), float(r.get("daily_pnl") or 0.0)) for r in rows]
    best = max(pnls, key=lambda x: x[1])
    worst = min(pnls, key=lambda x: x[1])
    return {
        "best_day": best[0],
        "best_day_pnl": round(best[1], 2),
        "worst_day": worst[0],
        "worst_day_pnl": round(worst[1], 2),
    }


def _boundary_interaction(
    accepted_trades: Sequence[Mapping[str, Any]], *, repo_root: Path, reports_dir: Path
) -> dict[str, Any]:
    """
    Reuse Phase409 boundary shadow evaluator on the accepted set to estimate
    boundary eligible rate and would_hit.
    """
    from research.phase416_post_no_overlap_shadow_rebaseline import compute_phase409_boundary_shadow

    try:
        shadow = compute_phase409_boundary_shadow(list(accepted_trades), repo_root=repo_root, reports_dir=reports_dir)
    except Exception as exc:  # pragma: no cover
        return {"status": "error", "error": str(exc)}
    eligible = int(shadow.get("eligible_count") or 0)
    total = len(accepted_trades)
    return {
        "status": "ok",
        "accepted_trade_count": total,
        "eligible_count": eligible,
        "eligible_rate": round(eligible / total, 6) if total else 0.0,
        "would_hit_count": int(shadow.get("would_hit_count") or 0),
        "eval_failed_count": int(shadow.get("eval_failed_count") or 0),
    }


def _same_symbol_overlap_reject(trades: Sequence[Mapping[str, Any]]) -> int:
    # Baseline is no_overlap_replace; capital sim does not model same-symbol overlap policy.
    # Keep as explicit 0 for Phase420 checklist.
    _ = trades
    return 0


def _daily_compare(sim3: Mapping[str, Any], sim5: Mapping[str, Any]) -> list[dict[str, Any]]:
    by_day3 = {str(r.get("day") or ""): dict(r) for r in (sim3.get("_daily_rows") or [])}
    by_day5 = {str(r.get("day") or ""): dict(r) for r in (sim5.get("_daily_rows") or [])}
    max_gross3 = (sim3.get("_max_gross_by_day") or {}) if isinstance(sim3.get("_max_gross_by_day"), Mapping) else {}
    max_gross5 = (sim5.get("_max_gross_by_day") or {}) if isinstance(sim5.get("_max_gross_by_day"), Mapping) else {}
    limit = float(STARTING_EQUITY) * float(LEVERAGE)
    rows: list[dict[str, Any]] = []
    for day in sorted(set(by_day3) | set(by_day5)):
        r3 = by_day3.get(day) or {}
        r5 = by_day5.get(day) or {}
        g3 = float(max_gross3.get(day) or 0.0)
        g5 = float(max_gross5.get(day) or 0.0)
        row = {
            "day": day,
            "cap3_start_equity": r3.get("start_equity"),
            "cap3_end_equity": r3.get("end_equity"),
            "cap3_daily_pnl": r3.get("daily_pnl"),
            "cap3_drawdown_pct": r3.get("drawdown_pct"),
            "cap3_accepted": r3.get("accepted_trade_count"),
            "cap3_rejected": r3.get("rejected_trade_count"),
            "cap3_max_gross_position_value": round(g3, 2),
            "cap3_max_gross_ratio_to_limit": round(g3 / limit, 6) if limit > 0 else 0.0,
            "cap5_start_equity": r5.get("start_equity"),
            "cap5_end_equity": r5.get("end_equity"),
            "cap5_daily_pnl": r5.get("daily_pnl"),
            "cap5_drawdown_pct": r5.get("drawdown_pct"),
            "cap5_accepted": r5.get("accepted_trade_count"),
            "cap5_rejected": r5.get("rejected_trade_count"),
            "cap5_max_gross_position_value": round(g5, 2),
            "cap5_max_gross_ratio_to_limit": round(g5 / limit, 6) if limit > 0 else 0.0,
            "delta_daily_pnl_cap5_minus_cap3": round(float(r5.get("daily_pnl") or 0.0) - float(r3.get("daily_pnl") or 0.0), 2),
        }
        rows.append(row)
    return rows


def evaluate_cap(trades: Sequence[Mapping[str, Any]], *, cap: int, repo_root: Path, reports_dir: Path) -> dict[str, Any]:
    sim = simulate_audited(
        trades,
        starting_equity=STARTING_EQUITY,
        leverage=LEVERAGE,
        cap=cap,
        stop_policy=STOP_POLICY,
    )
    accepted = int(sim.get("accepted_trade_count") or 0)
    rejected = int(sim.get("rejected_trade_count") or 0)
    reject_counts = dict(sim.get("reject_reason_counts") or {})
    holds = _hold_stats_from_accepted(sim)
    open_dist = _open_position_distribution(sim)
    cap_usage = _capital_usage(sim, leverage=LEVERAGE)
    bw = _best_worst_day(sim)
    boundary = _boundary_interaction(_accepted_trades(sim), repo_root=repo_root, reports_dir=reports_dir)
    return {
        "cap": cap,
        "total_pnl_yen": _pnl_yen(sim),
        "total_pnl_yen_100": round(_pnl_yen(sim) / 100.0, 2),
        "profit_factor": float(sim.get("profit_factor") or 0.0),
        "max_drawdown_yen": float(sim.get("max_drawdown_yen") or 0.0),
        "max_drawdown_pct": float(sim.get("max_drawdown_pct") or 0.0),
        "win_rate": float(sim.get("win_rate") or 0.0),
        "final_equity": float(sim.get("final_equity") or STARTING_EQUITY),
        "accepted_count": accepted,
        "rejected_count": rejected,
        "reject_reason_counts": reject_counts,
        "buying_power_reject": int(reject_counts.get("insufficient_buying_power") or 0),
        "max_open_distribution": open_dist,
        "capital_usage": cap_usage,
        "best_worst_day": bw,
        "position_duration": holds,
        "same_symbol_overlap_reject": _same_symbol_overlap_reject(trades),
        "boundary_interaction": boundary,
        "phase409_interaction": {
            "would_hit_count": (boundary.get("would_hit_count") if isinstance(boundary, Mapping) else None),
        },
        "_sim": sim,
    }


def adoption_conditions(*, cap3: Mapping[str, Any], cap5: Mapping[str, Any], daily_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    cap3_pf = float(cap3.get("profit_factor") or 0.0)
    cap5_pf = float(cap5.get("profit_factor") or 0.0)
    cap3_pnl = float(cap3.get("total_pnl_yen") or 0.0)
    cap5_pnl = float(cap5.get("total_pnl_yen") or 0.0)
    cap3_dd = float(cap3.get("max_drawdown_yen") or 0.0)
    cap5_dd = float(cap5.get("max_drawdown_yen") or 0.0)
    cap5_buy_rej = int(cap5.get("buying_power_reject") or 0)

    cond_a = cap5_pf + 1e-9 >= cap3_pf
    cond_b = cap5_pnl + 1e-9 >= cap3_pnl
    cond_c = cap5_dd <= cap3_dd * 1.10 + 1e-6
    cond_d = cap5_buy_rej <= 10  # explicit guardrail for this period

    # "not 1-day dependent": improvement should not be concentrated in one day.
    deltas = [float(r.get("delta_daily_pnl_cap5_minus_cap3") or 0.0) for r in daily_rows]
    pos = [d for d in deltas if d > 0]
    total_delta = sum(deltas)
    max_day = max(pos) if pos else 0.0
    max_share = (max_day / total_delta) if total_delta > 0 else 1.0
    cond_e = (len(pos) >= 3) and (total_delta > 0) and (max_share <= 0.70)

    return {
        "PF_ge_CAP3": cond_a,
        "PnL_ge_CAP3": cond_b,
        "maxDD_le_CAP3_plus_10pct": cond_c,
        "buying_power_reject_within_guardrail": cond_d,
        "not_single_day_dependent": cond_e,
        "daily_positive_days": len(pos),
        "daily_delta_total_yen": round(total_delta, 2),
        "daily_delta_max_day_yen": round(max_day, 2),
        "daily_delta_max_share": round(max_share, 6),
        "adoption_ready": bool(cond_a and cond_b and cond_c and cond_d and cond_e),
    }


def load_baseline_b_trades(repo_root: Path) -> list[dict[str, Any]]:
    # Canonical Baseline B and entry_price enrichment.
    from research.phase419_cap_sensitivity_post_phase414 import load_baseline_b_trades as _load

    return _load(repo_root)


def run_phase420_review(*, repo_root: Path) -> dict[str, Any]:
    reports_dir = resolve_reports_dir(repo_root)
    trades = load_baseline_b_trades(repo_root)
    period_days = sorted({str(t.get("day") or "") for t in trades if t.get("day")})

    cap3 = evaluate_cap(trades, cap=CAP3, repo_root=repo_root, reports_dir=reports_dir)
    cap5 = evaluate_cap(trades, cap=CAP5, repo_root=repo_root, reports_dir=reports_dir)
    daily_rows = _daily_compare(cap3["_sim"], cap5["_sim"])
    conditions = adoption_conditions(cap3=cap3, cap5=cap5, daily_rows=daily_rows)

    # Part C (alignment) recommendation only, no recompute here.
    phase273_recommendation = "scale_candidate_3000k (cap5 policy band)"
    phase274_recommendation = "auto-transition uses 1500k cap3 -> consider cap5 for 1500k band"

    summary = {
        "phase": "420-CAP5-Adoption-Review",
        "generated_at": _now_iso(),
        "status": "adoption_review_complete",
        "baseline": {
            "name": "Phase413 no_overlap_replace Baseline B",
            "period_days": period_days,
            "period_day_count": len(period_days),
            "trade_count": len(trades),
        },
        "fixed_params": {
            "starting_equity": STARTING_EQUITY,
            "leverage": LEVERAGE,
            "stop_policy": STOP_POLICY,
        },
        "cap3": {k: v for k, v in cap3.items() if not k.startswith("_")},
        "cap5": {k: v for k, v in cap5.items() if not k.startswith("_")},
        "cap3_vs_cap5": {
            "delta_total_pnl_yen": round(float(cap5.get("total_pnl_yen") or 0.0) - float(cap3.get("total_pnl_yen") or 0.0), 2),
            "delta_profit_factor": round(float(cap5.get("profit_factor") or 0.0) - float(cap3.get("profit_factor") or 0.0), 6),
            "delta_max_drawdown_yen": round(float(cap5.get("max_drawdown_yen") or 0.0) - float(cap3.get("max_drawdown_yen") or 0.0), 2),
            "delta_accepted": int(cap5.get("accepted_count") or 0) - int(cap3.get("accepted_count") or 0),
            "delta_rejected": int(cap5.get("rejected_count") or 0) - int(cap3.get("rejected_count") or 0),
        },
        "adoption_conditions": conditions,
        "capital_sim_alignment": {
            "phase273_cap_change": "CAP3 -> CAP5 (1500k band) candidate",
            "phase274_cap_change": "CAP3 -> CAP5 (1500k band) candidate",
            "phase273_recommendation": phase273_recommendation,
            "phase274_recommendation": phase274_recommendation,
        },
    }

    runtime_readiness = {
        "ready_for_part_b_runtime_change": bool(conditions.get("adoption_ready")),
        "recommended_change": {"position_cap": {"from": 3, "to": 5}},
        "constraints": {
            "runtime_change_forbidden_in_part_a": True,
            "yaml_change_forbidden_in_part_a": True,
            "entry_exit_order_discord_forbidden": True,
        },
        "blockers": [] if conditions.get("adoption_ready") else ["adoption_conditions_not_met"],
        "rollback": {"position_cap": {"to": 3}},
    }

    return {
        "summary": summary,
        "runtime_readiness": runtime_readiness,
        "_daily_rows": daily_rows,
    }


def render_report_md(payload: Mapping[str, Any]) -> str:
    s = payload.get("summary") or {}
    cond = (s.get("adoption_conditions") or {}) if isinstance(s, Mapping) else {}
    cap3 = s.get("cap3") or {}
    cap5 = s.get("cap5") or {}
    delta = s.get("cap3_vs_cap5") or {}
    align = s.get("capital_sim_alignment") or {}

    lines = [
        "# Phase420 — CAP5 Adoption Review & Runtime Alignment",
        "",
        f"Generated: {s.get('generated_at')}",
        f"Status: **{s.get('status')}**",
        "",
        "## 必須回答",
        "",
        f"1. **CAP5採用可否**: {'採用候補（条件OK）' if cond.get('adoption_ready') else '保留（条件未達）'}",
        f"2. **CAP3との差**: pnl={delta.get('delta_total_pnl_yen')} yen, pf={delta.get('delta_profit_factor')}, "
        f"maxDD={delta.get('delta_max_drawdown_yen')} yen, acceptedΔ={delta.get('delta_accepted')}, rejectedΔ={delta.get('delta_rejected')}",
        f"3. **買付余力問題**: CAP5 buying_power_reject={cap5.get('buying_power_reject')} (CAP3={cap3.get('buying_power_reject')})",
        f"4. **Boundaryとの相性**: eligible_rate CAP3={((cap3.get('boundary_interaction') or {}).get('eligible_rate'))} "
        f"CAP5={((cap5.get('boundary_interaction') or {}).get('eligible_rate'))}; would_hit CAP3={((cap3.get('phase409_interaction') or {}).get('would_hit_count'))} "
        f"CAP5={((cap5.get('phase409_interaction') or {}).get('would_hit_count'))}",
        f"5. **Runtime変更するべきか**: {'Part B へ進める' if cond.get('adoption_ready') else 'Part B 進行停止'}",
        f"6. **Phase273再推奨値**: {align.get('phase273_recommendation')}",
        f"7. **Phase274再推奨値**: {align.get('phase274_recommendation')}",
        "8. **rollback方法**: position_cap を 5→3 に戻す（他変更なし）",
        "",
        "## Adoption conditions",
        "",
        f"- PF>=CAP3: {cond.get('PF_ge_CAP3')}",
        f"- PnL>=CAP3: {cond.get('PnL_ge_CAP3')}",
        f"- maxDD<=CAP3+10%: {cond.get('maxDD_le_CAP3_plus_10pct')}",
        f"- buying_power_reject within guardrail: {cond.get('buying_power_reject_within_guardrail')}",
        f"- not single-day dependent: {cond.get('not_single_day_dependent')} "
        f"(pos_days={cond.get('daily_positive_days')}, max_share={cond.get('daily_delta_max_share')})",
        "",
        "## Outputs",
        "",
        "- `results/reports/phase420_cap5_adoption_review_summary.json`",
        "- `results/reports/phase420_cap5_vs_cap3_daily.csv`",
        "- `results/reports/phase420_cap5_runtime_readiness.json`",
        "- `docs/operations/phase420_cap5_adoption_review.md`",
        "",
    ]
    return "\n".join(lines)


@dataclass
class Phase420Job:
    repo_root: Path
    reports_dir: Path

    def run(self) -> dict[str, Any]:
        return run_phase420_review(repo_root=self.repo_root)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        from research.market_sector_heat import _write_csv

        reports = self.reports_dir
        reports.mkdir(parents=True, exist_ok=True)
        summary_path = reports / "phase420_cap5_adoption_review_summary.json"
        daily_path = reports / "phase420_cap5_vs_cap3_daily.csv"
        readiness_path = reports / "phase420_cap5_runtime_readiness.json"
        report_path = self.repo_root / "docs" / "operations" / "phase420_cap5_adoption_review.md"

        summary_payload = result.get("summary") or {}
        summary_path.write_text(json.dumps(summary_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        readiness_path.write_text(
            json.dumps(result.get("runtime_readiness") or {}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _write_csv(daily_path, DAILY_FIELDS, result.get("_daily_rows") or [])
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(render_report_md(result), encoding="utf-8")
        return {"summary": summary_path, "daily": daily_path, "runtime_readiness": readiness_path, "report": report_path}

