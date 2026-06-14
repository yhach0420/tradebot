"""
Phase272-Apply-Leverage-Robustness-To-Equity-Bucket-Recommendation.

Recompute Phase270 equity-bucket recommendations with leverage=2.0 fixed per Phase271.
Research only.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime
from itertools import product
from pathlib import Path
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from research.equity_curve_shadow import PERIOD_START, load_period_trades
from research.phase269_portfolio_configuration_optimization import (
    CAP_LEVELS,
    STOP_POLICIES,
    simulate_configuration,
)
from research.phase270_equity_bucket_live_configuration_recommendation import (
    EQUITY_BUCKETS,
    PHASE269_GRID_CSV,
    build_adoption_verdict_label,
    build_caution_reason,
    csv_row_to_result,
    pick_recommended,
)
from research.phase382_capital_constrained_backtest import _write_csv
from research.research_output_layers import (
    COMMON_RESEARCH_CONSTRAINTS,
    build_dual_layer_bundle,
)

JST = ZoneInfo("Asia/Tokyo")
PHASE270_CSV = "phase270_equity_bucket_recommendation.csv"
FIXED_LEVERAGE = 2.0

RECOMMENDATION_FIELDS = [
    "equity_yen",
    "leverage",
    "recommended_cap",
    "recommended_stop_policy",
    "final_equity",
    "total_return_pct",
    "max_drawdown_pct",
    "days_below_50pct",
    "accepted_count",
    "rejected_count",
    "profit_factor",
    "win_rate",
    "adoption_verdict",
    "caution_reason",
    "config_id",
    "selection_basis",
    "research_profit_factor",
    "research_total_pnl_yen",
    "research_win_rate",
    "dual_layer_adoptable",
]

CAP_STOP_FIELDS = [
    "equity_yen",
    "leverage",
    "cap",
    "stop_policy",
    "config_id",
    "final_equity",
    "total_return_pct",
    "max_drawdown_pct",
    "days_below_50pct",
    "accepted_count",
    "rejected_count",
    "profit_factor",
    "win_rate",
    "adoptable_by_final_equity",
    "safe_configuration",
    "is_recommended",
]

def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _int_val(value: Any, default: int = 0) -> int:
    try:
        if value in ("", None):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _float_val(value: Any, default: float = 0.0) -> float:
    try:
        if value in ("", None):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def load_phase269_lev2_grid(reports_dir: Path) -> list[dict[str, Any]]:
    path = reports_dir / PHASE269_GRID_CSV
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8", newline="") as fh:
        for raw in csv.DictReader(fh):
            if _float_val(raw.get("leverage")) != FIXED_LEVERAGE:
                continue
            eq = _int_val(raw.get("starting_equity"))
            if eq not in EQUITY_BUCKETS:
                continue
            rows.append(csv_row_to_result(raw))
    return rows


def load_phase270_recommendations(reports_dir: Path) -> dict[int, dict[str, Any]]:
    path = reports_dir / PHASE270_CSV
    if not path.is_file():
        return {}
    out: dict[int, dict[str, Any]] = {}
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            out[_int_val(row.get("equity_yen"))] = dict(row)
    return out


def build_lev2_grid(
    trades: Sequence[Mapping[str, Any]],
    *,
    reports_dir: Path,
) -> list[dict[str, Any]]:
    loaded = load_phase269_lev2_grid(reports_dir)
    by_equity: dict[int, list[dict[str, Any]]] = {}
    for row in loaded:
        by_equity.setdefault(_int_val(row.get("starting_equity")), []).append(row)

    results: list[dict[str, Any]] = []
    for equity in EQUITY_BUCKETS:
        if equity in by_equity and len(by_equity[equity]) >= len(CAP_LEVELS) * len(STOP_POLICIES):
            results.extend(by_equity[equity])
            continue
        for cap, stop_policy in product(CAP_LEVELS, STOP_POLICIES):
            results.append(
                simulate_configuration(
                    trades,
                    starting_equity=equity,
                    leverage=FIXED_LEVERAGE,
                    cap=cap,
                    stop_policy=stop_policy,
                )
            )
    return results


def recommendation_row(
    equity_yen: int,
    recommended: Mapping[str, Any],
    *,
    selection_basis: str,
) -> dict[str, Any]:
    research = recommended.get("research_layer") or {}
    dual = recommended.get("dual_layer") or build_dual_layer_bundle(
        research_layer=research,
        live_simulation_layer=recommended.get("live_simulation_layer") or {},
    )
    return {
        "equity_yen": equity_yen,
        "leverage": FIXED_LEVERAGE,
        "recommended_cap": recommended.get("cap"),
        "recommended_stop_policy": recommended.get("stop_policy"),
        "final_equity": recommended.get("final_equity"),
        "total_return_pct": recommended.get("total_return_pct"),
        "max_drawdown_pct": recommended.get("max_drawdown_pct"),
        "days_below_50pct": recommended.get("days_below_50pct"),
        "accepted_count": recommended.get("accepted_count"),
        "rejected_count": recommended.get("rejected_count"),
        "profit_factor": recommended.get("profit_factor"),
        "win_rate": recommended.get("win_rate"),
        "adoption_verdict": build_adoption_verdict_label(recommended),
        "caution_reason": build_caution_reason(recommended),
        "config_id": recommended.get("config_id"),
        "selection_basis": selection_basis,
        "research_profit_factor": research.get("profit_factor"),
        "research_total_pnl_yen": research.get("total_pnl_yen"),
        "research_win_rate": research.get("win_rate"),
        "dual_layer_adoptable": (dual.get("adoption_verdict") or {}).get("adoptable"),
    }


def build_phase270_comparison(
    recommendations: Sequence[Mapping[str, Any]],
    phase270: Mapping[int, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for rec in recommendations:
        eq = _int_val(rec.get("equity_yen"))
        p270 = phase270.get(eq) or {}
        p270_final = _float_val(p270.get("final_equity"))
        p272_final = _float_val(rec.get("final_equity"))
        rows.append(
            {
                "equity_yen": eq,
                "phase270_leverage": _float_val(p270.get("recommended_leverage")),
                "phase270_cap": _int_val(p270.get("recommended_cap")),
                "phase270_stop_policy": p270.get("recommended_stop_policy"),
                "phase270_final_equity": p270_final,
                "phase272_leverage": FIXED_LEVERAGE,
                "phase272_cap": rec.get("recommended_cap"),
                "phase272_stop_policy": rec.get("recommended_stop_policy"),
                "phase272_final_equity": p272_final,
                "delta_final_equity_yen": round(p272_final - p270_final, 2),
                "cap_changed": _int_val(p270.get("recommended_cap")) != _int_val(rec.get("recommended_cap")),
                "stop_changed": str(p270.get("recommended_stop_policy") or "") != str(rec.get("recommended_stop_policy") or ""),
                "leverage_changed": _float_val(p270.get("recommended_leverage")) != FIXED_LEVERAGE,
            }
        )
    return rows


def build_required_answers(
    recommendations: Sequence[Mapping[str, Any]],
    comparison: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    by_eq = {int(r["equity_yen"]): r for r in recommendations}
    eq1500 = by_eq.get(1_500_000) or {}
    above_2m = [by_eq[eq] for eq in EQUITY_BUCKETS if eq >= 2_000_000 and eq in by_eq]

    dynamic_equities = [
        int(r["equity_yen"])
        for r in recommendations
        if str(r.get("recommended_stop_policy") or "") == "dynamic_stop_risk_1p0"
    ]
    fixed_equities = [
        int(r["equity_yen"])
        for r in recommendations
        if str(r.get("recommended_stop_policy") or "") == "fixed_stop_1p2"
    ]

    caps_above_2m = sorted({int(r.get("recommended_cap") or 0) for r in above_2m})
    live_start = eq1500 if eq1500.get("adoption_verdict") == "adopt" else None
    if live_start is None:
        live_start = max(
            [r for r in recommendations if r.get("adoption_verdict") == "adopt"],
            key=lambda r: _float_val(r.get("final_equity")),
            default=by_eq.get(1_500_000) or {},
        )

    leverage_changes = sum(1 for c in comparison if c.get("leverage_changed"))
    cap_changes = sum(1 for c in comparison if c.get("cap_changed"))
    stop_changes = sum(1 for c in comparison if c.get("stop_changed"))

    return {
        "1_cap_for_1500k_lev2_fixed": {
            "recommended_cap": eq1500.get("recommended_cap"),
            "recommended_stop_policy": eq1500.get("recommended_stop_policy"),
            "final_equity": eq1500.get("final_equity"),
            "adoption_verdict": eq1500.get("adoption_verdict"),
            "config_id": eq1500.get("config_id"),
        },
        "2_cap_for_2000k_plus_lev2_fixed": {
            "recommended_caps": caps_above_2m,
            "dominant_cap": caps_above_2m[-1] if len(set(caps_above_2m)) == 1 else caps_above_2m,
            "per_equity": [
                {
                    "equity_yen": r.get("equity_yen"),
                    "cap": r.get("recommended_cap"),
                    "stop_policy": r.get("recommended_stop_policy"),
                    "final_equity": r.get("final_equity"),
                }
                for r in above_2m
            ],
        },
        "3_dynamic_stop_equity_bands": {
            "dynamic_stop_equities": dynamic_equities,
            "fixed_stop_equities": fixed_equities,
            "note": "dynamic_stop when it maximizes final_equity at lev=2.0 under safe/adoptable selection.",
        },
        "4_delta_vs_phase270": {
            "leverage_changed_count": leverage_changes,
            "cap_changed_count": cap_changes,
            "stop_changed_count": stop_changes,
            "comparison_rows": list(comparison),
            "summary": (
                f"{leverage_changes} equities changed leverage (250万/450万/500万/1000万: 1.5→2.0); "
                f"{cap_changes} changed CAP; {stop_changes} changed stop."
            ),
        },
        "5_live_start_recommendation": {
            "equity_yen": live_start.get("equity_yen"),
            "leverage": FIXED_LEVERAGE,
            "cap": live_start.get("recommended_cap"),
            "stop_policy": live_start.get("recommended_stop_policy"),
            "final_equity": live_start.get("final_equity"),
            "config_id": live_start.get("config_id"),
            "note": "Primary live-start candidate: 150万円 bucket unless capital allows higher equity tier.",
        },
    }


def build_report(summary: Mapping[str, Any]) -> str:
    answers = summary.get("required_answers") or {}
    a1 = answers.get("1_cap_for_1500k_lev2_fixed") or {}
    a2 = answers.get("2_cap_for_2000k_plus_lev2_fixed") or {}
    a3 = answers.get("3_dynamic_stop_equity_bands") or {}
    a4 = answers.get("4_delta_vs_phase270") or {}
    a5 = answers.get("5_live_start_recommendation") or {}

    lines = [
        "# Phase272 Equity Bucket Recommendation (Leverage 2.0 Fixed)",
        "",
        f"**生成:** {summary.get('generated_at')}",
        "",
        "## 前提",
        "",
        "- Phase271 結論: lev=1.5 優位は非頑健 → **lev=2.0 固定**",
        "- 採用判定: final_equity 主指標",
        "- Research PF 単独禁止",
        "",
        "## 必須回答",
        "",
        "### 1. lev2固定で150万円ならどのCAPか",
        "",
        f"- **CAP={a1.get('recommended_cap')}** / stop={a1.get('recommended_stop_policy')}",
        f"- final_equity={a1.get('final_equity')} / verdict={a1.get('adoption_verdict')}",
        "",
        "### 2. lev2固定で200万円以上ならどのCAPか",
        "",
        f"- 推奨CAP: **{a2.get('dominant_cap')}**",
        "",
    ]
    for row in a2.get("per_equity") or []:
        lines.append(
            f"- {row.get('equity_yen')}円: CAP={row.get('cap')} stop={row.get('stop_policy')} final={row.get('final_equity')}"
        )
    lines.extend(
        [
            "",
            "### 3. dynamic_stopはどの元本帯で採用すべきか",
            "",
            f"- dynamic_stop: {a3.get('dynamic_stop_equities')}",
            f"- fixed_stop: {a3.get('fixed_stop_equities')}",
            "",
            "### 4. Phase270との差分",
            "",
            f"- {a4.get('summary')}",
            "",
        ]
    )
    for row in a4.get("comparison_rows") or []:
        if row.get("leverage_changed") or row.get("cap_changed") or row.get("stop_changed"):
            lines.append(
                f"- {row.get('equity_yen')}円: "
                f"P270 lev={row.get('phase270_leverage')} cap={row.get('phase270_cap')} stop={row.get('phase270_stop_policy')} "
                f"→ P272 cap={row.get('phase272_cap')} stop={row.get('phase272_stop_policy')} "
                f"Δfinal={row.get('delta_final_equity_yen')}円"
            )
    lines.extend(
        [
            "",
            "### 5. ライブ開始推奨構成",
            "",
            f"- **{a5.get('config_id')}**",
            f"- {a5.get('equity_yen')}円 / lev={a5.get('leverage')} / CAP={a5.get('cap')} / stop={a5.get('stop_policy')}",
            f"- final_equity={a5.get('final_equity')}",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


@dataclass
class Phase272ApplyLeverageRobustnessToEquityBucketRecommendation:
    repo_root: Path
    reports_dir: Path
    period_start: str = PERIOD_START

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase272_equity_bucket_recommendation_lev2_fixed_summary.json",
            "recommendation": self.reports_dir / "phase272_equity_bucket_recommendation_lev2_fixed.csv",
            "cap_stop": self.reports_dir / "phase272_cap_stop_by_equity.csv",
            "report": self.reports_dir / "phase272_report.md",
        }

    def run(self) -> dict[str, Any]:
        trades, population = load_period_trades(self.repo_root, period_start=self.period_start)
        grid = build_lev2_grid(trades, reports_dir=self.reports_dir)
        phase270 = load_phase270_recommendations(self.reports_dir)

        recommendations: list[dict[str, Any]] = []
        cap_stop_rows: list[dict[str, Any]] = []
        recommended_keys: set[tuple[int, int, str]] = set()

        for equity_yen in EQUITY_BUCKETS:
            subset = [r for r in grid if _int_val(r.get("starting_equity")) == equity_yen]
            recommended, basis = pick_recommended(subset)
            if not recommended:
                continue
            rec = recommendation_row(equity_yen, recommended, selection_basis=basis)
            recommendations.append(rec)
            recommended_keys.add(
                (equity_yen, _int_val(recommended.get("cap")), str(recommended.get("stop_policy") or ""))
            )

            for row in subset:
                cap_stop_rows.append(
                    {
                        "equity_yen": equity_yen,
                        "leverage": FIXED_LEVERAGE,
                        "cap": row.get("cap"),
                        "stop_policy": row.get("stop_policy"),
                        "config_id": row.get("config_id"),
                        "final_equity": row.get("final_equity"),
                        "total_return_pct": row.get("total_return_pct"),
                        "max_drawdown_pct": row.get("max_drawdown_pct"),
                        "days_below_50pct": row.get("days_below_50pct"),
                        "accepted_count": row.get("accepted_count"),
                        "rejected_count": row.get("rejected_count"),
                        "profit_factor": row.get("profit_factor"),
                        "win_rate": row.get("win_rate"),
                        "adoptable_by_final_equity": row.get("adoptable_by_final_equity"),
                        "safe_configuration": row.get("safe_configuration"),
                        "is_recommended": (
                            equity_yen,
                            _int_val(row.get("cap")),
                            str(row.get("stop_policy") or ""),
                        )
                        in recommended_keys,
                    }
                )

        comparison = build_phase270_comparison(recommendations, phase270)
        answers = build_required_answers(recommendations, comparison)

        return {
            "phase": "272-Apply-Leverage-Robustness-To-Equity-Bucket-Recommendation",
            "title": "Equity bucket recommendation with leverage 2.0 fixed",
            "generated_at": _now_iso(),
            "purpose": "Revise Phase270 recommendations after Phase271 lev1.5 non-robust verdict",
            "constraints": dict(COMMON_RESEARCH_CONSTRAINTS),
            "policy": {
                "leverage_fixed": FIXED_LEVERAGE,
                "shares": 100,
                "cap_range": list(CAP_LEVELS),
                "stop_policies": list(STOP_POLICIES),
                "adoption_primary_metric": "final_equity",
                "phase271_reference": "fixed_leverage_2p0",
            },
            "population": population,
            "equity_buckets": list(EQUITY_BUCKETS),
            "equity_bucket_recommendations": recommendations,
            "phase270_comparison": comparison,
            "required_answers": answers,
            "_recommendation_rows": recommendations,
            "_cap_stop_rows": cap_stop_rows,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        paths = self.paths()
        paths["recommendation"].parent.mkdir(parents=True, exist_ok=True)
        _write_csv(paths["recommendation"], result.get("_recommendation_rows") or [], RECOMMENDATION_FIELDS)
        _write_csv(paths["cap_stop"], result.get("_cap_stop_rows") or [], CAP_STOP_FIELDS)
        public = {k: v for k, v in result.items() if not str(k).startswith("_")}
        paths["summary"].write_text(json.dumps(public, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        paths["report"].write_text(build_report(public), encoding="utf-8")
        return paths


def run_phase272_equity_bucket_recommendation_lev2_fixed(
    *,
    repo_root: Path,
    reports_dir: Path,
    period_start: str = PERIOD_START,
) -> dict[str, Any]:
    job = Phase272ApplyLeverageRobustnessToEquityBucketRecommendation(
        repo_root=repo_root,
        reports_dir=reports_dir,
        period_start=period_start,
    )
    result = job.run()
    job.write_outputs(result)
    return {k: v for k, v in result.items() if not str(k).startswith("_")}
