"""
Phase418: Phase273 / Phase274 full revalidation on no_overlap_replace Baseline B.

Research-only — no Runtime/YAML/Entry/Exit/Order changes.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.equity_dynamic_stop_shadow import (
    PERIOD_END,
    PERIOD_START,
    enrich_trades_with_entry_price,
)
from research.market_sector_heat import _norm_symbol, _write_csv
from research.phase271_leverage_attribution_and_robustness import simulate_audited
from research.phase273_live_config_forward_shadow_logger import (
    LIVE_CONFIG_CANDIDATES,
    build_daily_equity_rows,
    build_trade_event_rows,
    compute_candidate_summary,
    resolve_current_recommendation,
)
from research.phase274_live_config_auto_transition_shadow import (
    compute_adoption_verdict,
    simulate_auto_transition,
)
from research.phase382_capital_constrained_backtest import _float
from research.phase416_post_no_overlap_shadow_rebaseline import (
    load_baseline_a_trades,
    load_baseline_b_trades,
)
from research.structural_trade_normalize import resolve_reports_dir

JST = ZoneInfo("Asia/Tokyo")
PHASE413_TRADES_CSV = "phase413_no_overlap_replace_backfill_trades.csv"

CANDIDATE_FIELDS = [
    "candidate_key",
    "config_id",
    "starting_equity",
    "leverage",
    "shares",
    "cap",
    "stop_policy",
    "final_equity",
    "total_return_pct",
    "max_drawdown_pct",
    "accepted_count",
    "rejected_count",
    "reject_reason_counts_json",
    "profit_factor",
    "win_rate",
    "research_profit_factor",
    "research_total_pnl_yen",
    "verdict",
    "adopt_not_allowed",
    "caution",
]

DAILY_FIELDS = [
    "day",
    "candidate_key",
    "starting_equity",
    "start_equity",
    "end_equity",
    "daily_pnl",
    "cumulative_return_pct",
    "drawdown_pct",
    "accepted_trade_count",
    "rejected_trade_count",
    "active_policy_band_end",
    "phase274_only",
]


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _read_phase413_shadow_trades(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if str(row.get("shadow_included") or "").strip().lower() not in ("true", "1", "yes"):
                continue
            day = str(row.get("day") or "")
            if not (PERIOD_START <= day <= (PERIOD_END or "20260616")):
                continue
            t = dict(row)
            t["symbol"] = _norm_symbol(str(t.get("symbol") or ""))
            t["pnl_yen_100"] = _float(t.get("pnl_yen_100") or t.get("shadow_pnl_yen_100") or 0)
            t["exit_reason"] = t.get("exit_reason") or t.get("shadow_exit_reason") or ""
            rows.append(t)
    return rows


def load_baseline_b_for_revalidation(repo_root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Load Baseline B trades and enrich entry_price for capital simulation.

    Primary source: Phase416 collapse path (681 trades, canonical Baseline B).
    Cross-check: phase413 CSV shadow_included rows when present.
    """
    reports = resolve_reports_dir(repo_root)
    p413 = reports / PHASE413_TRADES_CSV
    raw = load_baseline_b_trades(load_baseline_a_trades(repo_root))
    source = "phase416_collapse"
    p413_count: Optional[int] = None
    if p413.is_file():
        p413_rows = _read_phase413_shadow_trades(p413)
        p413_count = len(p413_rows)

    enriched, enrich_meta = enrich_trades_with_entry_price(raw, repo_root=repo_root)
    period_days = sorted({str(t.get("day") or "") for t in enriched if t.get("day")})
    meta = {
        "source": source,
        "raw_trade_count": len(raw),
        "phase413_shadow_included_count": p413_count,
        "enriched_trade_count": len(enriched),
        "period_days": period_days,
        "period_day_count": len(period_days),
        **enrich_meta,
    }
    return enriched, meta


def _simulate_phase273(trades: Sequence[Mapping[str, Any]], *, period_days: Sequence[str]) -> dict[str, Any]:
    daily_rows: list[dict[str, Any]] = []
    trade_events: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    sim_by_key: dict[str, dict[str, Any]] = {}

    for candidate in LIVE_CONFIG_CANDIDATES:
        sim = simulate_audited(
            trades,
            starting_equity=int(candidate["starting_equity"]),
            leverage=float(candidate["leverage"]),
            cap=int(candidate["cap"]),
            stop_policy=str(candidate["stop_policy"]),
        )
        key = str(candidate["candidate_key"])
        sim_by_key[key] = sim
        daily_rows.extend(build_daily_equity_rows(sim, candidate=candidate))
        trade_events.extend(build_trade_event_rows(trades, sim, candidate=candidate))
        summaries.append(
            compute_candidate_summary(sim, candidate=candidate, period_days=period_days, trades=trades)
        )

    recommendation = resolve_current_recommendation(summaries)
    return {
        "recommended_candidate_key": recommendation,
        "candidate_summaries": summaries,
        "daily_rows": daily_rows,
        "trade_events": trade_events,
        "sim_by_key": sim_by_key,
    }


def _simulate_phase274(trades: Sequence[Mapping[str, Any]], *, period_days: Sequence[str]) -> dict[str, Any]:
    from research.phase274_live_config_auto_transition_shadow import resolve_policy_band

    sim = simulate_auto_transition(trades)
    adoption = compute_adoption_verdict(metrics=sim, day_count=len(period_days))
    daily_rows: list[dict[str, Any]] = []
    for row in sim.get("_daily_rows") or []:
        end_eq = float(row.get("end_equity") or 1_500_000)
        daily_rows.append(
            {
                **dict(row),
                "candidate_key": "live_config_auto_transition",
                "starting_equity": 1_500_000,
                "phase274_only": True,
                "active_policy_band_end": resolve_policy_band(end_eq)["active_policy_band"],
            }
        )
    return {
        "transition_summary": {
            "current_equity": sim.get("final_equity"),
            "active_policy_band": sim.get("active_policy_band"),
            "transition_day_to_2000k": sim.get("transition_day_to_2000k"),
            "max_drawdown_pct": sim.get("max_drawdown_pct"),
            "profit_factor": sim.get("profit_factor"),
            "accepted_count": sim.get("accepted_trade_count"),
            "rejected_count": sim.get("rejected_trade_count"),
        },
        "adoption_verdict": adoption,
        "daily_rows": daily_rows,
    }


def _unenriched_phase273_snapshot(trades: Sequence[Mapping[str, Any]], *, period_days: Sequence[str]) -> dict[str, Any]:
    """Capture pre-enrichment capital-sim distortion for audit."""
    summaries: list[dict[str, Any]] = []
    invalid_price_total = 0
    for candidate in LIVE_CONFIG_CANDIDATES:
        sim = simulate_audited(
            trades,
            starting_equity=int(candidate["starting_equity"]),
            leverage=float(candidate["leverage"]),
            cap=int(candidate["cap"]),
            stop_policy=str(candidate["stop_policy"]),
        )
        invalid_price_total = max(
            invalid_price_total,
            int((sim.get("reject_reason_counts") or {}).get("invalid_price") or 0),
        )
        summaries.append(
            compute_candidate_summary(sim, candidate=candidate, period_days=period_days, trades=trades)
        )
    return {
        "recommended_candidate_key": resolve_current_recommendation(summaries),
        "candidate_summaries": summaries,
        "max_invalid_price_rejects": invalid_price_total,
    }


def _why_1500k_reject(candidate: Mapping[str, Any], sim: Mapping[str, Any]) -> str:
    if not candidate.get("adopt_not_allowed"):
        return "not_rejected"
    reasons = sim.get("reject_reason_counts") or {}
    final_eq = float(candidate.get("final_equity") or 0)
    start_eq = float(candidate.get("starting_equity") or 0)
    parts: list[str] = []
    if final_eq <= start_eq:
        parts.append(f"final_equity {final_eq} <= starting_equity {start_eq}")
    if int(candidate.get("days_below_50pct") or 0) > 0:
        parts.append("equity_floor_breach")
    if reasons:
        parts.append(f"reject_reason_counts={reasons}")
    return "; ".join(parts) if parts else "adopt_not_allowed"


def _resolve_status(
    *,
    trade_count: int,
    period_day_count: int,
    missing_entry_price: int,
    unenriched_invalid_price: int,
) -> str:
    if trade_count < 600 or period_day_count < 11:
        return "insufficient_inputs"
    if missing_entry_price > 0:
        return "insufficient_inputs"
    if unenriched_invalid_price > 100:
        return "revalidation_complete"
    return "revalidation_complete"


def run_phase418_revalidation(repo_root: Path) -> dict[str, Any]:
    reports_dir = resolve_reports_dir(repo_root)
    trades, load_meta = load_baseline_b_for_revalidation(repo_root)
    period_days = list(load_meta.get("period_days") or [])

    raw_b = load_baseline_b_trades(load_baseline_a_trades(repo_root))
    unenriched = _unenriched_phase273_snapshot(raw_b, period_days=period_days)

    p273 = _simulate_phase273(trades, period_days=period_days)
    p274 = _simulate_phase274(trades, period_days=period_days)

    by_key = {str(c.get("candidate_key")): c for c in p273.get("candidate_summaries") or []}
    c1500 = by_key.get("live_start_candidate_1500k") or {}
    c2000 = by_key.get("scale_candidate_2000k_plus") or {}
    c3000 = by_key.get("scale_candidate_3000k") or {}
    sim1500 = (p273.get("sim_by_key") or {}).get("live_start_candidate_1500k") or {}

    status = _resolve_status(
        trade_count=len(trades),
        period_day_count=len(period_days),
        missing_entry_price=int(load_meta.get("missing_entry_price_count") or 0),
        unenriched_invalid_price=int(unenriched.get("max_invalid_price_rejects") or 0),
    )

    # Phase416 Baseline A/B snapshots (from prior rebaseline — unenriched for B)
    phase416_a_1500 = {
        "final_equity": 1_513_300.0,
        "accepted_count": 546,
        "rejected_count": 983,
        "verdict": "adopt",
        "recommended": "scale_candidate_3000k",
    }
    phase416_b_1500_unenriched = {
        "final_equity": 1_472_500.0,
        "accepted_count": 20,
        "rejected_count": 661,
        "verdict": "reject",
    }
    phase416_p274_b = {"adoption_verdict": "reject", "final_equity": 1_472_500.0}

    candidate_rows: list[dict[str, Any]] = []
    for summary in p273.get("candidate_summaries") or []:
        key = str(summary.get("candidate_key") or "")
        sim = (p273.get("sim_by_key") or {}).get(key) or {}
        candidate_rows.append(
            {
                **{k: summary.get(k) for k in CANDIDATE_FIELDS if k != "reject_reason_counts_json"},
                "reject_reason_counts_json": json.dumps(sim.get("reject_reason_counts") or {}, ensure_ascii=False),
            }
        )

    daily_rows: list[dict[str, Any]] = []
    for row in p273.get("daily_rows") or []:
        daily_rows.append({**row, "active_policy_band_end": "", "phase274_only": False})
    for row in p274.get("daily_rows") or []:
        daily_rows.append(row)

    mandatory_answers = {
        "phase273_recommendation": {
            "value": p273.get("recommended_candidate_key"),
            "vs_phase416_baseline_b": "maintained"
            if p273.get("recommended_candidate_key") == "scale_candidate_3000k"
            else "changed",
        },
        "phase274_adoption": (p274.get("adoption_verdict") or {}).get("adoption_verdict"),
        "phase274_vs_phase416_b": (
            "changed_reject_to_adopt"
            if (p274.get("adoption_verdict") or {}).get("adoption_verdict") == "adopt"
            and phase416_p274_b.get("adoption_verdict") == "reject"
            else "unchanged"
        ),
        "continue_1500k_operations": c1500.get("verdict") == "adopt",
        "scale_candidates_meaningful": (
            c2000.get("verdict") == "adopt" or c3000.get("verdict") == "adopt"
        ),
        "tomorrow_capital_shadow_focus": [
            "Phase273 forward daily equity on Baseline B structural stream",
            "Phase274 auto-transition band crossing watch (2M threshold)",
            "accepted/rejected trade events with reject_reason breakdown",
            "no_overlap_replace overlap chain length / hold_sec drift",
        ],
        "invalidate_prior_conclusions": [
            "Phase416 Baseline B Phase274 adoption_verdict=reject (distorted by missing entry_price)",
            "Phase416 Baseline B live_start_candidate_1500k verdict=reject (654 invalid_price rejects)",
            "Any capital-sim metric on Baseline B before entry_price enrichment",
        ],
    }

    return {
        "phase": "418-Live-Config-Revalidation-no-overlap-baseline",
        "generated_at": _now_iso(),
        "status": status,
        "baseline": {
            "name": "Phase413 no_overlap_replace Baseline B",
            "source_csv": str(reports_dir / PHASE413_TRADES_CSV),
            "period": {"start": PERIOD_START, "end": PERIOD_END or "20260616"},
        },
        "input_validation": {
            "trade_count": len(trades),
            "period_day_count": len(period_days),
            "period_days": period_days,
            "load_meta": load_meta,
            "unenriched_baseline_b_audit": {
                "trade_count": len(raw_b),
                "max_invalid_price_rejects": unenriched.get("max_invalid_price_rejects"),
                "live_start_1500k": next(
                    (
                        c
                        for c in unenriched.get("candidate_summaries") or []
                        if c.get("candidate_key") == "live_start_candidate_1500k"
                    ),
                    {},
                ),
                "note": "Phase416 Baseline B capital sim ran without entry_price enrichment; 654 invalid_price rejects",
            },
        },
        "phase273": {
            "recommended_candidate_key": p273.get("recommended_candidate_key"),
            "candidate_summaries": p273.get("candidate_summaries"),
            "live_start_1500k_reject_reason": _why_1500k_reject(c1500, sim1500),
        },
        "phase274": {
            "transition_summary": p274.get("transition_summary"),
            "adoption_verdict": p274.get("adoption_verdict"),
        },
        "mandatory_checks": {
            "1_trade_count_681": len(trades) == 681,
            "2_period_days_11": len(period_days) == 11,
            "3_live_start_1500k": c1500,
            "4_scale_2000k": c2000,
            "5_scale_3000k": c3000,
            "6_phase273_recommendation": p273.get("recommended_candidate_key"),
            "7_phase274_verdict": (p274.get("adoption_verdict") or {}).get("adoption_verdict"),
            "8_why_1500k_reject": _why_1500k_reject(c1500, sim1500),
            "9_accepted_rejected": {
                key: {
                    "accepted_count": (by_key.get(key) or {}).get("accepted_count"),
                    "rejected_count": (by_key.get(key) or {}).get("rejected_count"),
                    "reject_reason_counts": ((p273.get("sim_by_key") or {}).get(key) or {}).get("reject_reason_counts"),
                }
                for key in (
                    "live_start_candidate_1500k",
                    "scale_candidate_2000k_plus",
                    "scale_candidate_3000k",
                )
            },
            "10_adoption_verdict_change": {
                "phase273_recommendation": {
                    "baseline_a_pre_no_overlap": phase416_a_1500.get("recommended"),
                    "baseline_b_post_no_overlap": p273.get("recommended_candidate_key"),
                },
                "phase274": {
                    "baseline_a_pre_no_overlap": "adopt",
                    "baseline_b_unenriched_phase416": phase416_p274_b.get("adoption_verdict"),
                    "baseline_b_enriched_phase418": (p274.get("adoption_verdict") or {}).get("adoption_verdict"),
                },
                "live_start_1500k": {
                    "baseline_a": phase416_a_1500,
                    "baseline_b_unenriched": phase416_b_1500_unenriched,
                    "baseline_b_enriched": {
                        "final_equity": c1500.get("final_equity"),
                        "accepted_count": c1500.get("accepted_count"),
                        "rejected_count": c1500.get("rejected_count"),
                        "verdict": c1500.get("verdict"),
                    },
                },
            },
        },
        "mandatory_answers": mandatory_answers,
        "_candidate_rows": candidate_rows,
        "_daily_rows": daily_rows,
        "reports_dir": reports_dir,
    }


def render_report_md(result: Mapping[str, Any]) -> str:
    checks = result.get("mandatory_checks") or {}
    answers = result.get("mandatory_answers") or {}
    p273 = result.get("phase273") or {}
    p274 = result.get("phase274") or {}
    c1500 = checks.get("3_live_start_1500k") or {}
    c2000 = checks.get("4_scale_2000k") or {}
    c3000 = checks.get("5_scale_3000k") or {}
    lines = [
        "# Phase418 — Phase273 / Phase274 Full Revalidation (no_overlap_replace Baseline B)",
        "",
        f"Generated: {result.get('generated_at')}",
        f"Status: **{result.get('status')}**",
        "",
        "## 必須回答",
        "",
        f"- **Phase273 recommendation**: `{answers.get('phase273_recommendation', {}).get('value')}` "
        f"({answers.get('phase273_recommendation', {}).get('vs_phase416_baseline_b')})",
        f"- **Phase274 adoption**: `{(p274.get('adoption_verdict') or {}).get('adoption_verdict')}` "
        f"({answers.get('phase274_vs_phase416_b')})",
        f"- **150万円運用継続妥当か**: {'妥当（verdict=adopt）' if answers.get('continue_1500k_operations') else '要再検討'}",
        f"- **200万/300万候補の意味**: {'あり（いずれか adopt）' if answers.get('scale_candidates_meaningful') else '限定的'}",
        f"- **明日以降の資金系shadow**: {', '.join(answers.get('tomorrow_capital_shadow_focus') or [])}",
        "- **無効化すべき過去結論**:",
    ]
    for item in answers.get("invalidate_prior_conclusions") or []:
        lines.append(f"  - {item}")
    lines.extend(
        [
            "",
            "## Input validation",
            "",
            f"- trade_count: {(result.get('input_validation') or {}).get('trade_count')}",
            f"- period_days: {(result.get('input_validation') or {}).get('period_day_count')}",
            f"- entry_price enrichment: missing={(result.get('input_validation') or {}).get('load_meta', {}).get('missing_entry_price_count')}",
            "",
            "## Phase273 candidates (Baseline B enriched)",
            "",
            f"- live_start 1500k: final={c1500.get('final_equity')} accepted={c1500.get('accepted_count')} "
            f"rejected={c1500.get('rejected_count')} verdict={c1500.get('verdict')}",
            f"- scale 2000k+: final={c2000.get('final_equity')} accepted={c2000.get('accepted_count')} "
            f"rejected={c2000.get('rejected_count')} verdict={c2000.get('verdict')}",
            f"- scale 3000k: final={c3000.get('final_equity')} accepted={c3000.get('accepted_count')} "
            f"rejected={c3000.get('rejected_count')} verdict={c3000.get('verdict')}",
            f"- recommendation: `{p273.get('recommended_candidate_key')}`",
            "",
            "## Phase274 auto-transition",
            "",
            f"- final_equity: {(p274.get('transition_summary') or {}).get('current_equity')}",
            f"- active_policy_band: {(p274.get('transition_summary') or {}).get('active_policy_band')}",
            f"- transition_day_to_2000k: {(p274.get('transition_summary') or {}).get('transition_day_to_2000k')}",
            f"- adoption_verdict: `{(p274.get('adoption_verdict') or {}).get('adoption_verdict')}`",
            "",
            "## 1500k reject reason (if any)",
            "",
            str(p273.get("live_start_1500k_reject_reason") or "n/a"),
            "",
            "## no_overlap_replace 前後の adoption 変化",
            "",
            json.dumps(checks.get("10_adoption_verdict_change") or {}, ensure_ascii=False, indent=2),
            "",
        ]
    )
    return "\n".join(lines)


@dataclass
class Phase418Job:
    repo_root: Path
    reports_dir: Path

    def run(self) -> dict[str, Any]:
        return run_phase418_revalidation(self.repo_root)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = self.reports_dir
        reports.mkdir(parents=True, exist_ok=True)
        summary_path = reports / "phase418_live_config_revalidation_summary.json"
        candidates_path = reports / "phase418_live_config_revalidation_candidates.csv"
        daily_path = reports / "phase418_live_config_revalidation_daily.csv"
        report_path = self.repo_root / "docs" / "operations" / "phase418_live_config_revalidation_report.md"

        payload = {
            k: (str(v) if k == "reports_dir" else v)
            for k, v in result.items()
            if not k.startswith("_")
        }
        summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _write_csv(candidates_path, CANDIDATE_FIELDS, result.get("_candidate_rows") or [])
        _write_csv(daily_path, DAILY_FIELDS, result.get("_daily_rows") or [])
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(render_report_md(result), encoding="utf-8")
        return {
            "summary": summary_path,
            "candidates": candidates_path,
            "daily": daily_path,
            "report": report_path,
        }
