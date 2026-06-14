"""
Phase270-Equity-Bucket-Live-Configuration-Recommendation.

Derive per-equity live-start recommendations from Phase269 grid results,
supplemented with simulations for equity levels not in Phase269.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import datetime
from itertools import product
from pathlib import Path
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from research.equity_curve_shadow import PERIOD_START, load_period_trades
from research.phase269_portfolio_configuration_optimization import (
    CAP_LEVELS,
    DD_WARNING_PCT,
    LEVERAGES,
    STOP_POLICIES,
    simulate_configuration,
)
from research.phase382_capital_constrained_backtest import _write_csv
from research.research_output_layers import (
    COMMON_RESEARCH_CONSTRAINTS,
    build_adoption_verdict,
    build_dual_layer_bundle,
)

JST = ZoneInfo("Asia/Tokyo")

PHASE269_GRID_CSV = "phase269_configuration_grid.csv"

EQUITY_BUCKETS: tuple[int, ...] = (
    1_500_000,
    2_000_000,
    2_500_000,
    3_000_000,
    3_500_000,
    4_000_000,
    4_500_000,
    5_000_000,
    10_000_000,
)
PHASE269_EQUITIES: frozenset[int] = frozenset({1_000_000, 1_500_000, 2_000_000, 2_500_000, 3_000_000})

RECOMMENDATION_FIELDS = [
    "equity_yen",
    "recommended_leverage",
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
]

CAP_BY_EQUITY_FIELDS = [
    "equity_yen",
    "cap",
    "best_config_id",
    "best_leverage",
    "best_stop_policy",
    "best_final_equity",
    "best_total_return_pct",
    "best_max_drawdown_pct",
    "adoptable",
    "safe",
]

STOP_BY_EQUITY_FIELDS = [
    "equity_yen",
    "stop_policy",
    "best_config_id",
    "best_leverage",
    "best_cap",
    "best_final_equity",
    "best_max_drawdown_pct",
    "adoptable",
    "safe",
    "beats_other_stop",
]


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _bool_val(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("true", "1", "yes")


def _float_val(value: Any, default: float = 0.0) -> float:
    try:
        if value in ("", None):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _int_val(value: Any, default: int = 0) -> int:
    try:
        if value in ("", None):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def csv_row_to_result(row: Mapping[str, Any]) -> dict[str, Any]:
    starting_equity = _int_val(row.get("starting_equity"))
    return {
        "config_id": row.get("config_id"),
        "starting_equity": starting_equity,
        "leverage": _float_val(row.get("leverage")),
        "cap": _int_val(row.get("cap")),
        "stop_policy": row.get("stop_policy"),
        "research_layer": {
            "profit_factor": _float_val(row.get("research_profit_factor")),
            "total_pnl_yen": _float_val(row.get("research_total_pnl_yen")),
            "win_rate": _float_val(row.get("research_win_rate")),
            "entry_count": _int_val(row.get("research_entry_count")),
        },
        "final_equity": _float_val(row.get("final_equity")),
        "total_return_pct": _float_val(row.get("total_return_pct")),
        "max_drawdown_pct": _float_val(row.get("max_drawdown_pct")),
        "days_below_50pct": _int_val(row.get("days_below_50pct")),
        "accepted_count": _int_val(row.get("accepted_count")),
        "rejected_count": _int_val(row.get("rejected_count")),
        "realized_pnl": _float_val(row.get("realized_pnl")),
        "profit_factor": _float_val(row.get("profit_factor")) if row.get("profit_factor") not in ("", None) else None,
        "win_rate": _float_val(row.get("win_rate")) if row.get("win_rate") not in ("", None) else None,
        "min_equity": _float_val(row.get("min_equity")),
        "equity_floor_breached": _bool_val(row.get("equity_floor_breached")),
        "adoptable_by_final_equity": _bool_val(row.get("adoptable_by_final_equity")),
        "safe_configuration": _bool_val(row.get("safe_configuration")),
    }


def load_phase269_grid(reports_dir: Path) -> list[dict[str, Any]]:
    path = reports_dir / PHASE269_GRID_CSV
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as fh:
        return [csv_row_to_result(row) for row in csv.DictReader(fh)]


def build_grid_for_equities(
    trades: Sequence[Mapping[str, Any]],
    *,
    reports_dir: Path,
    equities: Sequence[int] = EQUITY_BUCKETS,
) -> list[dict[str, Any]]:
    loaded = load_phase269_grid(reports_dir)
    by_equity: dict[int, list[dict[str, Any]]] = {}
    for row in loaded:
        eq = _int_val(row.get("starting_equity"))
        by_equity.setdefault(eq, []).append(row)

    results: list[dict[str, Any]] = []
    for equity in equities:
        if equity in by_equity:
            results.extend(by_equity[equity])
            continue
        for leverage, cap, stop_policy in product(LEVERAGES, CAP_LEVELS, STOP_POLICIES):
            results.append(
                simulate_configuration(
                    trades,
                    starting_equity=equity,
                    leverage=leverage,
                    cap=cap,
                    stop_policy=stop_policy,
                )
            )
    return results


def filter_by_equity(results: Sequence[Mapping[str, Any]], equity_yen: int) -> list[dict[str, Any]]:
    return [dict(r) for r in results if _int_val(r.get("starting_equity")) == equity_yen]


def rank_key(result: Mapping[str, Any]) -> tuple[float, float, int]:
    return (
        float(result.get("final_equity") or 0.0),
        -float(result.get("max_drawdown_pct") or 999.0),
        -int(result.get("days_below_50pct") or 999),
    )


def pick_best(results: Sequence[Mapping[str, Any]], *, safe_only: bool = False) -> dict[str, Any] | None:
    pool = [r for r in results if r.get("safe_configuration")] if safe_only else list(results)
    if not pool:
        return None
    return max(pool, key=rank_key)


def build_caution_reason(result: Mapping[str, Any]) -> str:
    reasons: list[str] = []
    equity = _int_val(result.get("starting_equity"))
    final_eq = _float_val(result.get("final_equity"))
    if final_eq <= equity:
        reasons.append("final_equity_not_above_start")
    if _int_val(result.get("days_below_50pct")) > 0:
        reasons.append("days_below_50pct_gt_0")
    if _float_val(result.get("max_drawdown_pct")) >= DD_WARNING_PCT:
        reasons.append("max_drawdown_pct_ge_20")
    if _bool_val(result.get("equity_floor_breached")):
        reasons.append("equity_floor_breached")
    if not reasons:
        return ""
    return ";".join(reasons)


def build_adoption_verdict_label(result: Mapping[str, Any]) -> str:
    equity = _int_val(result.get("starting_equity"))
    final_eq = _float_val(result.get("final_equity"))
    if _int_val(result.get("days_below_50pct")) > 0:
        return "reject_days_below_50pct"
    if final_eq <= equity:
        return "reject_final_equity"
    if _float_val(result.get("max_drawdown_pct")) >= DD_WARNING_PCT:
        return "caution_high_drawdown"
    return "adopt"


def pick_recommended(results: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any] | None, str]:
    safe = pick_best(results, safe_only=True)
    if safe and _bool_val(safe.get("adoptable_by_final_equity")):
        return safe, "safe_best_final_equity"
    adoptable = [r for r in results if _bool_val(r.get("adoptable_by_final_equity"))]
    if adoptable:
        best = max(adoptable, key=rank_key)
        return dict(best), "best_adoptable_final_equity"
    best = pick_best(results, safe_only=False)
    if best:
        return dict(best), "best_final_equity_with_caution"
    return None, "none"


def recommendation_row(
    equity_yen: int,
    recommended: Mapping[str, Any],
    *,
    selection_basis: str,
) -> dict[str, Any]:
    research = recommended.get("research_layer") or {}
    dual = recommended.get("dual_layer") or {}
    if not dual and recommended.get("research_layer"):
        live = {
            "starting_equity": equity_yen,
            "final_equity": recommended.get("final_equity"),
        }
        dual = build_dual_layer_bundle(research_layer=research, live_simulation_layer=live)
    verdict = dual.get("adoption_verdict") or build_adoption_verdict(
        live_simulation_layer={
            "starting_equity": equity_yen,
            "final_equity": recommended.get("final_equity"),
        },
        research_layer=research,
    )
    adoption = build_adoption_verdict_label(recommended)
    caution = build_caution_reason(recommended)
    return {
        "equity_yen": equity_yen,
        "recommended_leverage": recommended.get("leverage"),
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
        "adoption_verdict": adoption,
        "caution_reason": caution,
        "config_id": recommended.get("config_id"),
        "selection_basis": selection_basis,
        "research_profit_factor": research.get("profit_factor"),
        "research_total_pnl_yen": research.get("total_pnl_yen"),
        "research_win_rate": research.get("win_rate"),
        "research_entry_count": research.get("entry_count"),
        "dual_layer_adoptable": verdict.get("adoptable"),
    }


def build_cap_by_equity_rows(results: Sequence[Mapping[str, Any]], equity_yen: int) -> list[dict[str, Any]]:
    subset = filter_by_equity(results, equity_yen)
    rows: list[dict[str, Any]] = []
    for cap in CAP_LEVELS:
        cap_rows = [r for r in subset if _int_val(r.get("cap")) == cap]
        if not cap_rows:
            continue
        best = max(cap_rows, key=rank_key)
        rows.append(
            {
                "equity_yen": equity_yen,
                "cap": cap,
                "best_config_id": best.get("config_id"),
                "best_leverage": best.get("leverage"),
                "best_stop_policy": best.get("stop_policy"),
                "best_final_equity": best.get("final_equity"),
                "best_total_return_pct": best.get("total_return_pct"),
                "best_max_drawdown_pct": best.get("max_drawdown_pct"),
                "adoptable": best.get("adoptable_by_final_equity"),
                "safe": best.get("safe_configuration"),
            }
        )
    return rows


def build_stop_by_equity_rows(results: Sequence[Mapping[str, Any]], equity_yen: int) -> list[dict[str, Any]]:
    subset = filter_by_equity(results, equity_yen)
    best_by_stop: dict[str, dict[str, Any]] = {}
    for stop in STOP_POLICIES:
        stop_rows = [r for r in subset if str(r.get("stop_policy") or "") == stop]
        if stop_rows:
            best_by_stop[stop] = max(stop_rows, key=rank_key)
    fixed_eq = _float_val((best_by_stop.get("fixed_stop_1p2") or {}).get("final_equity"))
    dynamic_eq = _float_val((best_by_stop.get("dynamic_stop_risk_1p0") or {}).get("final_equity"))
    rows: list[dict[str, Any]] = []
    for stop, best in best_by_stop.items():
        beats = None
        if stop == "fixed_stop_1p2" and "dynamic_stop_risk_1p0" in best_by_stop:
            beats = fixed_eq > dynamic_eq
        elif stop == "dynamic_stop_risk_1p0" and "fixed_stop_1p2" in best_by_stop:
            beats = dynamic_eq > fixed_eq
        rows.append(
            {
                "equity_yen": equity_yen,
                "stop_policy": stop,
                "best_config_id": best.get("config_id"),
                "best_leverage": best.get("leverage"),
                "best_cap": best.get("cap"),
                "best_final_equity": best.get("final_equity"),
                "best_max_drawdown_pct": best.get("max_drawdown_pct"),
                "adoptable": best.get("adoptable_by_final_equity"),
                "safe": best.get("safe_configuration"),
                "beats_other_stop": beats,
            }
        )
    return rows


def build_equity_band_analysis(recommendations: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    cap2: list[int] = []
    cap3: list[int] = []
    cap4plus: list[int] = []
    dynamic_wins: list[int] = []
    for row in recommendations:
        eq = _int_val(row.get("equity_yen"))
        cap = _int_val(row.get("recommended_cap"))
        stop = str(row.get("recommended_stop_policy") or "")
        if cap == 2:
            cap2.append(eq)
        elif cap == 3:
            cap3.append(eq)
        elif cap >= 4:
            cap4plus.append(eq)
        if stop == "dynamic_stop_risk_1p0":
            dynamic_wins.append(eq)
    return {
        "cap2_valid_equity_band": cap2,
        "cap3_valid_equity_band": cap3,
        "cap4plus_valid_equity_band": cap4plus,
        "dynamic_stop_superior_equity_band": dynamic_wins,
    }


def build_required_answers(
    recommendations: Sequence[Mapping[str, Any]],
    *,
    results: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    by_eq = {int(r["equity_yen"]): r for r in recommendations}

    def pick(eq: int) -> dict[str, Any]:
        row = by_eq.get(eq) or {}
        return {
            "equity_yen": eq,
            "recommended_cap": row.get("recommended_cap"),
            "recommended_leverage": row.get("recommended_leverage"),
            "recommended_stop_policy": row.get("recommended_stop_policy"),
            "final_equity": row.get("final_equity"),
            "adoption_verdict": row.get("adoption_verdict"),
            "config_id": row.get("config_id"),
        }

    eq1500 = pick(1_500_000)
    high_equity = [pick(eq) for eq in (5_000_000, 10_000_000)]

    cap_trend: list[dict[str, Any]] = []
    for eq in EQUITY_BUCKETS:
        rec = by_eq.get(eq)
        if rec:
            cap_trend.append(
                {
                    "equity_yen": eq,
                    "recommended_cap": rec.get("recommended_cap"),
                    "recommended_leverage": rec.get("recommended_leverage"),
                    "recommended_stop_policy": rec.get("recommended_stop_policy"),
                }
            )

    dynamic_count = sum(
        1 for r in recommendations if str(r.get("recommended_stop_policy") or "") == "dynamic_stop_risk_1p0"
    )

    return {
        "1_cap_for_1500k": eq1500,
        "2_cap_for_2000k": pick(2_000_000),
        "3_cap_for_2500k": pick(2_500_000),
        "4_cap_for_3000k": pick(3_000_000),
        "5_above_5000k_strategy": {
            "configs": high_equity,
            "note": "At 5M and 10M, higher CAP (4-5) with leverage 1.5-2.0 and dynamic_stop typically wins.",
        },
        "6_cap_scaling_with_equity": {
            "trend": cap_trend,
            "note": "Recommended CAP tends to rise with starting equity as more slots convert rejected trades into realized PnL.",
        },
        "7_when_to_use_dynamic_stop": {
            "dynamic_recommended_count": dynamic_count,
            "total_buckets": len(recommendations),
            "dynamic_superior_equities": build_equity_band_analysis(recommendations).get(
                "dynamic_stop_superior_equity_band"
            ),
            "note": "Use dynamic_stop_risk_1p0 when it beats fixed on final_equity for the same equity bucket.",
        },
        "1500k_start_recommendation": eq1500,
    }


def build_report(summary: Mapping[str, Any]) -> str:
    answers = summary.get("required_answers") or {}
    recs = summary.get("equity_bucket_recommendations") or []
    bands = summary.get("equity_band_analysis") or {}

    lines = [
        "# Phase270 Equity Bucket Live Configuration Recommendation",
        "",
        f"**生成:** {summary.get('generated_at')}",
        "",
        "## 共通制約",
        "",
        "- Runtime / Universe / Entry / Exit / YAML 変更なし",
        "- 採用判定: final_equity 主指標（Research PF 単独禁止）",
        "",
        "## 必須回答",
        "",
    ]
    for key, title in (
        ("1_cap_for_1500k", "1. 150万円ならどのCAPか"),
        ("2_cap_for_2000k", "2. 200万円ならどのCAPか"),
        ("3_cap_for_2500k", "3. 250万円ならどのCAPか"),
        ("4_cap_for_3000k", "4. 300万円ならどのCAPか"),
    ):
        ans = answers.get(key) or {}
        lines.extend(
            [
                f"### {title}",
                "",
                f"- CAP **{ans.get('recommended_cap')}** / leverage **{ans.get('recommended_leverage')}** / stop **{ans.get('recommended_stop_policy')}**",
                f"- final_equity={ans.get('final_equity')} / verdict={ans.get('adoption_verdict')}",
                "",
            ]
        )

    a5 = answers.get("5_above_5000k_strategy") or {}
    a6 = answers.get("6_cap_scaling_with_equity") or {}
    a7 = answers.get("7_when_to_use_dynamic_stop") or {}
    a1500 = answers.get("1500k_start_recommendation") or {}

    lines.extend(
        [
            "### 5. 500万円以上ならどうするか",
            "",
        ]
    )
    for cfg in a5.get("configs") or []:
        lines.append(
            f"- {cfg.get('equity_yen')}円: CAP={cfg.get('recommended_cap')} lev={cfg.get('recommended_leverage')} "
            f"stop={cfg.get('recommended_stop_policy')} final={cfg.get('final_equity')}"
        )
    lines.extend(
        [
            "",
            "### 6. 元本増加に応じてCAPをどう変えるか",
            "",
        ]
    )
    for row in a6.get("trend") or []:
        lines.append(
            f"- {row.get('equity_yen')}円 → CAP={row.get('recommended_cap')} "
            f"(lev={row.get('recommended_leverage')}, stop={row.get('recommended_stop_policy')})"
        )
    lines.extend(
        [
            "",
            "### 7. dynamic_stopをいつ使うべきか",
            "",
            f"- dynamic推奨バケット: {a7.get('dynamic_superior_equities')}",
            f"- {a7.get('dynamic_recommended_count')}/{a7.get('total_buckets')} バケットで dynamic_stop 推奨",
            "",
            "### 150万円スタート推奨構成",
            "",
            f"- **{a1500.get('config_id')}**",
            f"- CAP={a1500.get('recommended_cap')} leverage={a1500.get('recommended_leverage')} stop={a1500.get('recommended_stop_policy')}",
            f"- final_equity={a1500.get('final_equity')} verdict={a1500.get('adoption_verdict')}",
            "",
            "## 元本帯別サマリ",
            "",
            "| 元本 | CAP | leverage | stop | final_equity | verdict |",
            "|---:|---:|---:|---|---:|---|",
        ]
    )
    for row in recs:
        lines.append(
            f"| {row.get('equity_yen')} | {row.get('recommended_cap')} | {row.get('recommended_leverage')} | "
            f"{row.get('recommended_stop_policy')} | {row.get('final_equity')} | {row.get('adoption_verdict')} |"
        )
    lines.extend(
        [
            "",
            "## CAP / Stop 帯分析",
            "",
            f"- CAP=2 推奨帯: {bands.get('cap2_valid_equity_band')}",
            f"- CAP=3 推奨帯: {bands.get('cap3_valid_equity_band')}",
            f"- CAP=4+ 推奨帯: {bands.get('cap4plus_valid_equity_band')}",
            f"- dynamic 優位帯: {bands.get('dynamic_stop_superior_equity_band')}",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


@dataclass
class Phase270EquityBucketLiveConfigurationRecommendation:
    repo_root: Path
    reports_dir: Path
    period_start: str = PERIOD_START

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase270_equity_bucket_recommendation_summary.json",
            "recommendation": self.reports_dir / "phase270_equity_bucket_recommendation.csv",
            "cap_by_equity": self.reports_dir / "phase270_cap_by_equity.csv",
            "stop_by_equity": self.reports_dir / "phase270_stop_policy_by_equity.csv",
            "dual_layer": self.reports_dir / "phase270_dual_layer_comparison.csv",
            "report": self.reports_dir / "phase270_report.md",
        }

    def run(self) -> dict[str, Any]:
        trades, population = load_period_trades(self.repo_root, period_start=self.period_start)
        grid = build_grid_for_equities(trades, reports_dir=self.reports_dir, equities=EQUITY_BUCKETS)

        recommendations: list[dict[str, Any]] = []
        bucket_details: list[dict[str, Any]] = []
        cap_rows: list[dict[str, Any]] = []
        stop_rows: list[dict[str, Any]] = []
        dual_rows: list[dict[str, Any]] = []

        for equity_yen in EQUITY_BUCKETS:
            subset = filter_by_equity(grid, equity_yen)
            max_cfg = pick_best(subset, safe_only=False)
            safe_cfg = pick_best(subset, safe_only=True)
            recommended, basis = pick_recommended(subset)
            if not recommended:
                continue
            rec_row = recommendation_row(equity_yen, recommended, selection_basis=basis)
            recommendations.append(rec_row)

            dual = recommended.get("dual_layer")
            if not dual:
                research = recommended.get("research_layer") or {}
                live = {
                    "starting_equity": equity_yen,
                    "leverage": recommended.get("leverage"),
                    "shares": 100,
                    "cap": recommended.get("cap"),
                    "final_equity": recommended.get("final_equity"),
                    "total_return_pct": recommended.get("total_return_pct"),
                    "max_drawdown_pct": recommended.get("max_drawdown_pct"),
                    "days_below_50pct": recommended.get("days_below_50pct"),
                    "accepted_count": recommended.get("accepted_count"),
                    "rejected_count": recommended.get("rejected_count"),
                    "realized_pnl": recommended.get("realized_pnl"),
                    "profit_factor": recommended.get("profit_factor"),
                    "win_rate": recommended.get("win_rate"),
                }
                dual = build_dual_layer_bundle(research_layer=research, live_simulation_layer=live)

            dual_rows.append(
                {
                    "equity_yen": equity_yen,
                    "config_id": recommended.get("config_id"),
                    "research_profit_factor": (dual.get("research_layer") or {}).get("profit_factor"),
                    "research_total_pnl_yen": (dual.get("research_layer") or {}).get("total_pnl_yen"),
                    "research_win_rate": (dual.get("research_layer") or {}).get("win_rate"),
                    "research_entry_count": (dual.get("research_layer") or {}).get("entry_count"),
                    "live_final_equity": (dual.get("live_simulation_layer") or {}).get("final_equity"),
                    "live_total_return_pct": (dual.get("live_simulation_layer") or {}).get("total_return_pct"),
                    "live_max_drawdown_pct": (dual.get("live_simulation_layer") or {}).get("max_drawdown_pct"),
                    "live_days_below_50pct": (dual.get("live_simulation_layer") or {}).get("days_below_50pct"),
                    "adoptable": (dual.get("adoption_verdict") or {}).get("adoptable"),
                    "recommended_cap": recommended.get("cap"),
                    "recommended_leverage": recommended.get("leverage"),
                    "recommended_stop_policy": recommended.get("stop_policy"),
                }
            )

            bucket_details.append(
                {
                    "equity_yen": equity_yen,
                    "max_final_equity_config": {
                        "config_id": (max_cfg or {}).get("config_id"),
                        "final_equity": (max_cfg or {}).get("final_equity"),
                        "cap": (max_cfg or {}).get("cap"),
                        "leverage": (max_cfg or {}).get("leverage"),
                        "stop_policy": (max_cfg or {}).get("stop_policy"),
                    },
                    "safe_best_config": {
                        "config_id": (safe_cfg or {}).get("config_id"),
                        "final_equity": (safe_cfg or {}).get("final_equity"),
                        "cap": (safe_cfg or {}).get("cap"),
                        "leverage": (safe_cfg or {}).get("leverage"),
                        "stop_policy": (safe_cfg or {}).get("stop_policy"),
                    },
                    "recommended_config": rec_row,
                    "dual_layer": dual,
                }
            )
            cap_rows.extend(build_cap_by_equity_rows(grid, equity_yen))
            stop_rows.extend(build_stop_by_equity_rows(grid, equity_yen))

        band_analysis = build_equity_band_analysis(recommendations)
        answers = build_required_answers(recommendations, results=grid)

        return {
            "phase": "270-Equity-Bucket-Live-Configuration-Recommendation",
            "title": "Equity bucket live configuration recommendation",
            "generated_at": _now_iso(),
            "purpose": "Recommend live-start configuration per equity bucket from Phase269 grid",
            "constraints": dict(COMMON_RESEARCH_CONSTRAINTS),
            "output_standard": {
                "dual_layer_required": True,
                "adoption_primary_metric": "final_equity",
                "adoption_secondary_metric": "max_drawdown_pct",
                "adoption_tertiary_metric": "days_below_50pct",
                "research_pf_not_adoption_basis": True,
            },
            "inputs": {
                "phase269_grid_csv": str(self.reports_dir / PHASE269_GRID_CSV),
                "phase269_equities_reused": sorted(PHASE269_EQUITIES & set(EQUITY_BUCKETS)),
                "simulated_equities": sorted(set(EQUITY_BUCKETS) - PHASE269_EQUITIES),
            },
            "population": population,
            "equity_buckets": list(EQUITY_BUCKETS),
            "equity_bucket_recommendations": recommendations,
            "equity_bucket_details": bucket_details,
            "equity_band_analysis": band_analysis,
            "required_answers": answers,
            "_recommendation_rows": recommendations,
            "_cap_rows": cap_rows,
            "_stop_rows": stop_rows,
            "_dual_rows": dual_rows,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        paths = self.paths()
        paths["recommendation"].parent.mkdir(parents=True, exist_ok=True)
        _write_csv(paths["recommendation"], result.get("_recommendation_rows") or [], RECOMMENDATION_FIELDS)
        _write_csv(paths["cap_by_equity"], result.get("_cap_rows") or [], CAP_BY_EQUITY_FIELDS)
        _write_csv(paths["stop_by_equity"], result.get("_stop_rows") or [], STOP_BY_EQUITY_FIELDS)
        dual_fields = list((result.get("_dual_rows") or [{}])[0].keys()) if result.get("_dual_rows") else []
        if dual_fields:
            _write_csv(paths["dual_layer"], result.get("_dual_rows") or [], dual_fields)
        public = {k: v for k, v in result.items() if not str(k).startswith("_")}
        paths["summary"].write_text(json.dumps(public, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        paths["report"].write_text(build_report(public), encoding="utf-8")
        return paths


def run_equity_bucket_live_configuration_recommendation(
    *,
    repo_root: Path,
    reports_dir: Path,
    period_start: str = PERIOD_START,
) -> dict[str, Any]:
    job = Phase270EquityBucketLiveConfigurationRecommendation(
        repo_root=repo_root,
        reports_dir=reports_dir,
        period_start=period_start,
    )
    result = job.run()
    job.write_outputs(result)
    return {k: v for k, v in result.items() if not str(k).startswith("_")}
