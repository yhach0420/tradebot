"""
Phase269-Portfolio-Configuration-Optimization.

Grid search over starting equity, leverage, position cap, and stop policy under
full capital-path simulation. Research only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from itertools import product
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from zoneinfo import ZoneInfo

from research.equity_curve_shadow import (
    FIXED_SPEC,
    PERIOD_START,
    load_period_trades,
    pnl_for_actual_fixed_stop,
    pnl_for_dynamic_stop_risk_1p0,
    simulate_equity_curve_scenario,
)
from research.phase382_capital_constrained_backtest import _write_csv
from research.research_output_layers import (
    COMMON_RESEARCH_CONSTRAINTS,
    build_dual_layer_bundle,
    build_live_simulation_layer_from_equity_metrics,
    build_research_layer,
)

JST = ZoneInfo("Asia/Tokyo")

STARTING_EQUITIES: tuple[int, ...] = (1_000_000, 1_500_000, 2_000_000, 2_500_000, 3_000_000)
LEVERAGES: tuple[float, ...] = (1.0, 1.5, 2.0)
CAP_LEVELS: tuple[int, ...] = (1, 2, 3, 4, 5)
STOP_POLICIES: tuple[str, ...] = ("fixed_stop_1p2", "dynamic_stop_risk_1p0")
SHARES = 100
DD_WARNING_PCT = 20.0

STOP_RESOLVERS: dict[str, Callable[..., float]] = {
    "fixed_stop_1p2": pnl_for_actual_fixed_stop,
    "dynamic_stop_risk_1p0": pnl_for_dynamic_stop_risk_1p0,
}

GRID_FIELDS = [
    "config_id",
    "starting_equity",
    "leverage",
    "cap",
    "stop_policy",
    "research_profit_factor",
    "research_total_pnl_yen",
    "research_win_rate",
    "research_entry_count",
    "final_equity",
    "total_return_pct",
    "max_drawdown_pct",
    "days_below_50pct",
    "accepted_count",
    "rejected_count",
    "realized_pnl",
    "profit_factor",
    "win_rate",
    "min_equity",
    "equity_floor_breached",
    "adoptable_by_final_equity",
    "safe_configuration",
    "rank_final_equity",
]

DUAL_LAYER_FIELDS = [
    "config_id",
    "starting_equity",
    "leverage",
    "cap",
    "stop_policy",
    "research_profit_factor",
    "research_total_pnl_yen",
    "research_win_rate",
    "research_entry_count",
    "live_final_equity",
    "live_total_return_pct",
    "live_max_drawdown_pct",
    "live_days_below_50pct",
    "live_accepted_count",
    "live_rejected_count",
    "live_realized_pnl",
    "live_profit_factor",
    "live_win_rate",
    "live_min_equity",
    "live_equity_floor_breached",
    "adoptable",
    "safe_configuration",
]


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def config_id(*, starting_equity: int, leverage: float, cap: int, stop_policy: str) -> str:
    eq_k = int(starting_equity // 1000)
    lev = str(leverage).replace(".", "p")
    return f"eq{eq_k}k_lev{lev}_cap{cap}_{stop_policy}"


def build_spec(*, leverage: float, cap: int, stop_policy: str) -> dict[str, Any]:
    return {
        **FIXED_SPEC,
        "label": config_id(starting_equity=0, leverage=leverage, cap=cap, stop_policy=stop_policy).replace("eq0k_", ""),
        "leverage_limit": leverage,
        "sizing": "fixed_100_only",
    }


def build_research_layer_for_config(
    trades: Sequence[Mapping[str, Any]],
    *,
    stop_policy: str,
    starting_equity: float,
) -> dict[str, Any]:
    resolver = STOP_RESOLVERS[stop_policy]
    pnls = [
        float(resolver(t, shares=SHARES, entry_equity=starting_equity))
        for t in trades
    ]
    layer = build_research_layer(
        pnls,
        trade_count=len(trades),
        label=f"unconstrained_static_{stop_policy}",
    )
    layer["entry_count"] = len(trades)
    return layer


def simulate_configuration(
    trades: Sequence[Mapping[str, Any]],
    *,
    starting_equity: int,
    leverage: float,
    cap: int,
    stop_policy: str,
) -> dict[str, Any]:
    resolver = STOP_RESOLVERS[stop_policy]
    cid = config_id(
        starting_equity=starting_equity,
        leverage=leverage,
        cap=cap,
        stop_policy=stop_policy,
    )
    equity_floor = starting_equity * 0.5
    sim = simulate_equity_curve_scenario(
        trades,
        scenario_id=cid,
        pnl_resolver=resolver,
        initial_equity=float(starting_equity),
        equity_floor=equity_floor,
        cap=cap,
        spec=build_spec(leverage=leverage, cap=cap, stop_policy=stop_policy),
    )
    research = build_research_layer_for_config(
        trades,
        stop_policy=stop_policy,
        starting_equity=float(starting_equity),
    )
    live = build_live_simulation_layer_from_equity_metrics(
        sim,
        cap=cap,
        daily_rows=sim.get("_daily_rows") or [],
        starting_equity=float(starting_equity),
        leverage=leverage,
        shares=SHARES,
    )
    live["realized_pnl"] = round(float(sim.get("final_equity") or 0.0) - starting_equity, 2)
    live["profit_factor"] = sim.get("profit_factor")
    live["win_rate"] = sim.get("win_rate")
    live["min_equity"] = sim.get("min_equity")
    live["equity_floor_breached"] = sim.get("equity_floor_breached")
    dual = build_dual_layer_bundle(research_layer=research, live_simulation_layer=live)
    verdict = dual["adoption_verdict"]
    adoptable = bool(verdict.get("adoptable"))
    days_below = int(live.get("days_below_50pct") or 0)
    max_dd = float(live.get("max_drawdown_pct") or 0.0)
    safe = days_below == 0 and max_dd < DD_WARNING_PCT and adoptable

    return {
        "config_id": cid,
        "starting_equity": starting_equity,
        "leverage": leverage,
        "cap": cap,
        "stop_policy": stop_policy,
        "research_layer": research,
        "live_simulation_layer": live,
        "dual_layer": dual,
        "final_equity": sim.get("final_equity"),
        "total_return_pct": sim.get("total_return_pct"),
        "max_drawdown_pct": sim.get("max_drawdown_pct"),
        "days_below_50pct": sim.get("days_below_50pct"),
        "accepted_count": sim.get("accepted_trade_count"),
        "rejected_count": sim.get("rejected_trade_count"),
        "realized_pnl": live["realized_pnl"],
        "profit_factor": sim.get("profit_factor"),
        "win_rate": sim.get("win_rate"),
        "min_equity": sim.get("min_equity"),
        "equity_floor_breached": sim.get("equity_floor_breached"),
        "adoptable_by_final_equity": adoptable,
        "safe_configuration": safe,
        "dd_warning": max_dd >= DD_WARNING_PCT,
        "_daily_rows": sim.get("_daily_rows") or [],
    }


def grid_row(result: Mapping[str, Any], *, rank: int | None = None) -> dict[str, Any]:
    research = result.get("research_layer") or {}
    row = {
        "config_id": result.get("config_id"),
        "starting_equity": result.get("starting_equity"),
        "leverage": result.get("leverage"),
        "cap": result.get("cap"),
        "stop_policy": result.get("stop_policy"),
        "research_profit_factor": research.get("profit_factor"),
        "research_total_pnl_yen": research.get("total_pnl_yen"),
        "research_win_rate": research.get("win_rate"),
        "research_entry_count": research.get("entry_count"),
        "final_equity": result.get("final_equity"),
        "total_return_pct": result.get("total_return_pct"),
        "max_drawdown_pct": result.get("max_drawdown_pct"),
        "days_below_50pct": result.get("days_below_50pct"),
        "accepted_count": result.get("accepted_count"),
        "rejected_count": result.get("rejected_count"),
        "realized_pnl": result.get("realized_pnl"),
        "profit_factor": result.get("profit_factor"),
        "win_rate": result.get("win_rate"),
        "min_equity": result.get("min_equity"),
        "equity_floor_breached": result.get("equity_floor_breached"),
        "adoptable_by_final_equity": result.get("adoptable_by_final_equity"),
        "safe_configuration": result.get("safe_configuration"),
        "rank_final_equity": rank if rank is not None else "",
    }
    return row


def dual_layer_row(result: Mapping[str, Any]) -> dict[str, Any]:
    research = result.get("research_layer") or {}
    live = result.get("live_simulation_layer") or {}
    verdict = (result.get("dual_layer") or {}).get("adoption_verdict") or {}
    return {
        "config_id": result.get("config_id"),
        "starting_equity": result.get("starting_equity"),
        "leverage": result.get("leverage"),
        "cap": result.get("cap"),
        "stop_policy": result.get("stop_policy"),
        "research_profit_factor": research.get("profit_factor"),
        "research_total_pnl_yen": research.get("total_pnl_yen"),
        "research_win_rate": research.get("win_rate"),
        "research_entry_count": research.get("entry_count"),
        "live_final_equity": live.get("final_equity"),
        "live_total_return_pct": live.get("total_return_pct"),
        "live_max_drawdown_pct": live.get("max_drawdown_pct"),
        "live_days_below_50pct": live.get("days_below_50pct"),
        "live_accepted_count": live.get("accepted_count"),
        "live_rejected_count": live.get("rejected_count"),
        "live_realized_pnl": live.get("realized_pnl"),
        "live_profit_factor": live.get("profit_factor"),
        "live_win_rate": live.get("win_rate"),
        "live_min_equity": live.get("min_equity"),
        "live_equity_floor_breached": live.get("equity_floor_breached"),
        "adoptable": verdict.get("adoptable"),
        "safe_configuration": result.get("safe_configuration"),
    }


def rank_configurations(results: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(
        results,
        key=lambda r: (
            -float(r.get("final_equity") or 0.0),
            float(r.get("max_drawdown_pct") or 999.0),
            int(r.get("days_below_50pct") or 999),
        ),
    )
    rows: list[dict[str, Any]] = []
    for idx, result in enumerate(ordered, start=1):
        rows.append(grid_row(result, rank=idx))
    return rows


def subset_best(
    results: Sequence[Mapping[str, Any]],
    *,
    starting_equity: int | None = None,
    leverage: float | None = None,
    cap: int | None = None,
    stop_policy: str | None = None,
    safe_only: bool = False,
) -> dict[str, Any] | None:
    filtered = list(results)
    if starting_equity is not None:
        filtered = [r for r in filtered if int(r.get("starting_equity") or 0) == starting_equity]
    if leverage is not None:
        filtered = [r for r in filtered if float(r.get("leverage") or 0.0) == leverage]
    if cap is not None:
        filtered = [r for r in filtered if int(r.get("cap") or 0) == cap]
    if stop_policy is not None:
        filtered = [r for r in filtered if str(r.get("stop_policy") or "") == stop_policy]
    if safe_only:
        filtered = [r for r in filtered if r.get("safe_configuration")]
    if not filtered:
        return None
    return max(
        filtered,
        key=lambda r: (
            float(r.get("final_equity") or 0.0),
            -float(r.get("max_drawdown_pct") or 0.0),
            -int(r.get("days_below_50pct") or 0),
        ),
    )


def subset_summary(
    results: Sequence[Mapping[str, Any]],
    *,
    starting_equity: int | None = None,
    leverage: float | None = None,
    cap: int | None = None,
) -> dict[str, Any]:
    filtered = list(results)
    if starting_equity is not None:
        filtered = [r for r in filtered if int(r.get("starting_equity") or 0) == starting_equity]
    if leverage is not None:
        filtered = [r for r in filtered if float(r.get("leverage") or 0.0) == leverage]
    if cap is not None:
        filtered = [r for r in filtered if int(r.get("cap") or 0) == cap]
    if not filtered:
        return {"count": 0}
    best = subset_best(
        filtered,
        starting_equity=starting_equity,
        leverage=leverage,
        cap=cap,
    ) or {}
    safe = [r for r in filtered if r.get("safe_configuration")]
    adoptable = [r for r in filtered if r.get("adoptable_by_final_equity")]
    return {
        "count": len(filtered),
        "best_config_id": best.get("config_id"),
        "best_final_equity": best.get("final_equity"),
        "best_realized_pnl": best.get("realized_pnl"),
        "best_max_drawdown_pct": best.get("max_drawdown_pct"),
        "adoptable_count": len(adoptable),
        "safe_count": len(safe),
        "any_adoptable": len(adoptable) > 0,
        "any_safe": len(safe) > 0,
    }


def build_required_answers(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    ranked = rank_configurations(results)
    top = ranked[0] if ranked else {}
    safe_rows = [r for r in results if r.get("safe_configuration")]
    safe_best = max(
        safe_rows,
        key=lambda r: (
            float(r.get("final_equity") or 0.0),
            -float(r.get("max_drawdown_pct") or 0.0),
        ),
    ) if safe_rows else None

    eq1500 = subset_summary(results, starting_equity=1_500_000)
    lev2 = subset_summary(results, leverage=2.0)
    cap2 = subset_summary(results, cap=2)

    recommended = safe_best or top
    fixed_best = subset_best(results, stop_policy="fixed_stop_1p2", safe_only=bool(safe_rows))
    dynamic_best = subset_best(results, stop_policy="dynamic_stop_risk_1p0", safe_only=bool(safe_rows))

    return {
        "1_max_final_equity_configuration": {
            "config_id": top.get("config_id"),
            "starting_equity": top.get("starting_equity"),
            "leverage": top.get("leverage"),
            "cap": top.get("cap"),
            "stop_policy": top.get("stop_policy"),
            "final_equity": top.get("final_equity"),
            "total_return_pct": top.get("total_return_pct"),
            "max_drawdown_pct": top.get("max_drawdown_pct"),
        },
        "2_dd_adjusted_best_configuration": {
            "config_id": (safe_best or {}).get("config_id"),
            "final_equity": (safe_best or {}).get("final_equity"),
            "max_drawdown_pct": (safe_best or {}).get("max_drawdown_pct"),
            "days_below_50pct": (safe_best or {}).get("days_below_50pct"),
            "note": "Best final_equity among safe_configuration rows (days_below_50pct=0 and max_drawdown_pct<20).",
        },
        "3_is_1500k_start_valid": {
            "summary": eq1500,
            "verdict": "valid" if eq1500.get("any_adoptable") else "not_valid_by_final_equity",
            "note": "Judged by whether any 1.5M configuration achieves final_equity > starting_equity.",
        },
        "4_is_leverage_2x_valid": {
            "summary": lev2,
            "verdict": "valid" if lev2.get("any_adoptable") else "not_valid_by_final_equity",
            "note": "Judged across all starting_equity and cap combinations at leverage=2.0.",
        },
        "5_is_cap2_valid": {
            "summary": cap2,
            "verdict": "valid" if cap2.get("any_adoptable") else "not_valid_by_final_equity",
            "note": "Judged across all starting_equity and leverage combinations at cap=2.",
        },
        "6_recommended_live_configuration": {
            "config_id": recommended.get("config_id"),
            "starting_equity": recommended.get("starting_equity"),
            "leverage": recommended.get("leverage"),
            "cap": recommended.get("cap"),
            "stop_policy": recommended.get("stop_policy"),
            "final_equity": recommended.get("final_equity"),
            "max_drawdown_pct": recommended.get("max_drawdown_pct"),
            "days_below_50pct": recommended.get("days_below_50pct"),
            "selection_rule": (
                "Primary final_equity, secondary max_drawdown_pct, tertiary days_below_50pct; "
                "prefer safe_configuration when available."
            ),
        },
        "stop_policy_comparison": {
            "fixed_best_config_id": (fixed_best or {}).get("config_id"),
            "fixed_best_final_equity": (fixed_best or {}).get("final_equity"),
            "dynamic_best_config_id": (dynamic_best or {}).get("config_id"),
            "dynamic_best_final_equity": (dynamic_best or {}).get("final_equity"),
            "dynamic_changes_optimum": (
                (dynamic_best or {}).get("config_id") != (fixed_best or {}).get("config_id")
                if fixed_best and dynamic_best
                else None
            ),
        },
    }


def build_report(summary: Mapping[str, Any]) -> str:
    answers = summary.get("required_answers") or {}
    a1 = answers.get("1_max_final_equity_configuration") or {}
    a2 = answers.get("2_dd_adjusted_best_configuration") or {}
    a3 = answers.get("3_is_1500k_start_valid") or {}
    a4 = answers.get("4_is_leverage_2x_valid") or {}
    a5 = answers.get("5_is_cap2_valid") or {}
    a6 = answers.get("6_recommended_live_configuration") or {}
    stop_cmp = answers.get("stop_policy_comparison") or {}
    pop = summary.get("population") or {}
    grid = summary.get("grid_stats") or {}

    lines = [
        "# Phase269 Portfolio Configuration Optimization",
        "",
        f"**生成:** {summary.get('generated_at')}",
        "",
        "## 共通制約",
        "",
        "- Runtime / Universe / Entry / Exit / YAML 変更なし",
        "- 採用判定: Primary=final_equity / Secondary=max_drawdown_pct / Tertiary=days_below_50pct",
        "- Research PF 単独では採用禁止（必ず Live Simulation 併記）",
        "",
        f"**対象:** structural_trades {pop.get('period_start')}以降 / {pop.get('input_trade_count')} trades / {grid.get('configuration_count')} configs",
        "",
        "## 必須回答",
        "",
        "### 1. 最終元本最大構成",
        "",
        f"- **{a1.get('config_id')}** → final_equity={a1.get('final_equity')} ({a1.get('total_return_pct')}%)",
        f"- leverage={a1.get('leverage')} cap={a1.get('cap')} stop={a1.get('stop_policy')}",
        "",
        "### 2. DDを考慮した最適構成",
        "",
        f"- **{a2.get('config_id')}** → final_equity={a2.get('final_equity')} max_dd={a2.get('max_drawdown_pct')}% days_below_50={a2.get('days_below_50pct')}",
        "",
        "### 3. 150万円スタートは妥当か",
        "",
        f"- verdict: **{a3.get('verdict')}** (adoptable={((a3.get('summary') or {}).get('adoptable_count'))} / best={((a3.get('summary') or {}).get('best_config_id'))})",
        "",
        "### 4. レバ2倍は妥当か",
        "",
        f"- verdict: **{a4.get('verdict')}** (adoptable={((a4.get('summary') or {}).get('adoptable_count'))} / best={((a4.get('summary') or {}).get('best_config_id'))})",
        "",
        "### 5. CAP=2は妥当か",
        "",
        f"- verdict: **{a5.get('verdict')}** (adoptable={((a5.get('summary') or {}).get('adoptable_count'))} / best={((a5.get('summary') or {}).get('best_config_id'))})",
        "",
        "### 6. ライブ開始推奨構成",
        "",
        f"- **{a6.get('config_id')}**",
        f"- starting_equity={a6.get('starting_equity')} leverage={a6.get('leverage')} cap={a6.get('cap')} stop={a6.get('stop_policy')}",
        f"- final_equity={a6.get('final_equity')} max_dd={a6.get('max_drawdown_pct')}% days_below_50={a6.get('days_below_50pct')}",
        "",
        "## Stop Policy 比較",
        "",
        f"- fixed best: {stop_cmp.get('fixed_best_config_id')} (final={stop_cmp.get('fixed_best_final_equity')})",
        f"- dynamic best: {stop_cmp.get('dynamic_best_config_id')} (final={stop_cmp.get('dynamic_best_final_equity')})",
        f"- dynamic changes optimum: {stop_cmp.get('dynamic_changes_optimum')}",
        "",
        "## グリッド統計",
        "",
        f"- total configurations: {grid.get('configuration_count')}",
        f"- adoptable (final_equity > start): {grid.get('adoptable_count')}",
        f"- safe (days_below_50=0 & max_dd<20%): {grid.get('safe_count')}",
        "",
    ]
    return "\n".join(lines) + "\n"


@dataclass
class Phase269PortfolioConfigurationOptimization:
    repo_root: Path
    reports_dir: Path
    period_start: str = PERIOD_START

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase269_portfolio_configuration_summary.json",
            "grid": self.reports_dir / "phase269_configuration_grid.csv",
            "top20": self.reports_dir / "phase269_top20_configurations.csv",
            "safe": self.reports_dir / "phase269_safe_configurations.csv",
            "dual_layer": self.reports_dir / "phase269_dual_layer_comparison.csv",
            "report": self.reports_dir / "phase269_report.md",
        }

    def run(self) -> dict[str, Any]:
        trades, population = load_period_trades(self.repo_root, period_start=self.period_start)
        results: list[dict[str, Any]] = []
        for starting_equity, leverage, cap, stop_policy in product(
            STARTING_EQUITIES, LEVERAGES, CAP_LEVELS, STOP_POLICIES
        ):
            results.append(
                simulate_configuration(
                    trades,
                    starting_equity=starting_equity,
                    leverage=leverage,
                    cap=cap,
                    stop_policy=stop_policy,
                )
            )

        ranked_rows = rank_configurations(results)
        top20 = ranked_rows[:20]
        safe_rows = [grid_row(r) for r in results if r.get("safe_configuration")]
        safe_rows.sort(key=lambda r: -float(r.get("final_equity") or 0.0))
        dual_rows = [dual_layer_row(r) for r in results]
        answers = build_required_answers(results)

        adoptable_count = sum(1 for r in results if r.get("adoptable_by_final_equity"))
        safe_count = len(safe_rows)

        return {
            "phase": "269-Portfolio-Configuration-Optimization",
            "title": "Portfolio configuration optimization",
            "generated_at": _now_iso(),
            "purpose": "Determine optimal live-start portfolio configuration via capital-path grid search",
            "constraints": dict(COMMON_RESEARCH_CONSTRAINTS),
            "output_standard": {
                "dual_layer_required": True,
                "adoption_primary_metric": "final_equity",
                "adoption_secondary_metric": "max_drawdown_pct",
                "adoption_tertiary_metric": "days_below_50pct",
                "research_pf_not_adoption_basis": True,
            },
            "search_space": {
                "starting_equities": list(STARTING_EQUITIES),
                "leverages": list(LEVERAGES),
                "caps": list(CAP_LEVELS),
                "stop_policies": list(STOP_POLICIES),
                "shares": SHARES,
                "equity_floor_pct": 50.0,
                "safe_max_drawdown_pct": DD_WARNING_PCT,
            },
            "population": population,
            "grid_stats": {
                "configuration_count": len(results),
                "adoptable_count": adoptable_count,
                "safe_count": safe_count,
            },
            "required_answers": answers,
            "top20_config_ids": [r.get("config_id") for r in top20],
            "_grid_rows": ranked_rows,
            "_top20_rows": top20,
            "_safe_rows": safe_rows,
            "_dual_rows": dual_rows,
            "_results": results,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        paths = self.paths()
        paths["grid"].parent.mkdir(parents=True, exist_ok=True)
        _write_csv(paths["grid"], result.get("_grid_rows") or [], GRID_FIELDS)
        _write_csv(paths["top20"], result.get("_top20_rows") or [], GRID_FIELDS)
        _write_csv(paths["safe"], result.get("_safe_rows") or [], GRID_FIELDS)
        _write_csv(paths["dual_layer"], result.get("_dual_rows") or [], DUAL_LAYER_FIELDS)
        public = {k: v for k, v in result.items() if not str(k).startswith("_")}
        paths["summary"].write_text(json.dumps(public, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        paths["report"].write_text(build_report(public), encoding="utf-8")
        return paths


def run_portfolio_configuration_optimization(
    *,
    repo_root: Path,
    reports_dir: Path,
    period_start: str = PERIOD_START,
) -> dict[str, Any]:
    job = Phase269PortfolioConfigurationOptimization(
        repo_root=repo_root,
        reports_dir=reports_dir,
        period_start=period_start,
    )
    result = job.run()
    job.write_outputs(result)
    return {k: v for k, v in result.items() if not str(k).startswith("_")}
