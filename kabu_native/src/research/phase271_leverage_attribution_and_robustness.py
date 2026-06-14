"""
Phase271-Leverage-Attribution-and-Robustness.

Test whether Phase270 lev=1.5 advantage at 2.5M/4.5M/5M/10M is robust or sample noise.
Research only.
"""

from __future__ import annotations

import json
import random
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from research.equity_curve_shadow import (
    EquityCurveCapState,
    PERIOD_START,
    build_daily_equity_rows,
    compute_scenario_metrics,
    load_period_trades,
)
from research.phase269_portfolio_configuration_optimization import (
    STOP_RESOLVERS,
    build_spec,
)
from research.phase382_capital_constrained_backtest import (
    _day_from_ts,
    _position_key,
    _trade_pnl_yen,
    _write_csv,
)
from research.phase383_realistic_credit_sizing_backtest import build_event_timeline
from research.research_output_layers import (
    COMMON_RESEARCH_CONSTRAINTS,
    build_dual_layer_bundle,
    build_live_simulation_layer_from_equity_metrics,
    build_research_layer,
)

JST = ZoneInfo("Asia/Tokyo")

FOCUS_EQUITIES: tuple[int, ...] = (2_500_000, 4_500_000, 5_000_000, 10_000_000)
LEVERAGES: tuple[float, ...] = (1.0, 1.5, 2.0)
REFERENCE_LEVERAGE = 2.0
CHALLENGER_LEVERAGE = 1.5
BOOTSTRAP_ITERATIONS = 1000
ECONOMIC_SIGNIFICANCE_PCT = 1.0
ROBUST_LOO_THRESHOLD = 0.67
ROBUST_DAY_WIN_THRESHOLD = 0.67

PHASE270_RECOMMENDATIONS: dict[int, dict[str, Any]] = {
    2_500_000: {"cap": 5, "stop_policy": "dynamic_stop_risk_1p0"},
    4_500_000: {"cap": 5, "stop_policy": "dynamic_stop_risk_1p0"},
    5_000_000: {"cap": 5, "stop_policy": "dynamic_stop_risk_1p0"},
    10_000_000: {"cap": 5, "stop_policy": "dynamic_stop_risk_1p0"},
}

REJECT_CATEGORY_MAP = {
    "max_concurrent_positions": "cap_constraint",
    "insufficient_buying_power": "buying_power",
    "invalid_size": "buying_power",
    "invalid_price": "buying_power",
    "maintenance_ratio_stop": "leverage_maintenance",
    "maintenance_ratio_force_exit": "leverage_maintenance",
}

ATTRIBUTION_FIELDS = [
    "equity_yen",
    "baseline_leverage",
    "challenger_leverage",
    "delta_final_equity_yen",
    "delta_final_equity_pct",
    "accepted_trade_effect_yen",
    "buying_power_effect_yen",
    "cap_constraint_effect_yen",
    "dynamic_stop_effect_yen",
    "dd_effect_yen",
    "residual_yen",
    "baseline_final_equity",
    "challenger_final_equity",
    "baseline_accepted_count",
    "challenger_accepted_count",
    "baseline_max_drawdown_pct",
    "challenger_max_drawdown_pct",
]

DAY_ROBUSTNESS_FIELDS = [
    "equity_yen",
    "day",
    "daily_pnl_lev1p0",
    "daily_pnl_lev1p5",
    "daily_pnl_lev2p0",
    "winner_leverage",
    "lev1p5_beats_lev2p0",
    "lev1p5_beats_lev1p0",
]

BOOTSTRAP_FIELDS = [
    "equity_yen",
    "comparison",
    "metric",
    "point_estimate",
    "ci_low_95",
    "ci_high_95",
    "bootstrap_n",
    "lev1p5_win_rate",
]

EXPOSURE_CURVE_FIELDS = [
    "equity_yen",
    "leverage",
    "day",
    "end_equity",
    "daily_pnl",
    "gross_position_value",
    "max_gross_in_day",
]


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _trade_key(trade: Mapping[str, Any]) -> str:
    return _position_key(trade)


@dataclass
class AuditedEquityCurveCapState(EquityCurveCapState):
    reject_log: list[dict[str, Any]] = field(default_factory=list)
    accepted_pnls: dict[str, float] = field(default_factory=dict)
    reject_reason_counts: Counter[str] = field(default_factory=Counter)
    max_gross_by_day: dict[str, float] = field(default_factory=lambda: defaultdict(float))

    def _reject_entry(self, trade: Mapping[str, Any], reason: str) -> None:
        super()._reject_entry(trade, reason)
        self.reject_reason_counts[reason] += 1
        eq = self.current_equity()
        cf = self.pnl_resolver(trade, shares=100, entry_equity=eq)
        self.reject_log.append(
            {
                "key": _trade_key(trade),
                "reason": reason,
                "category": REJECT_CATEGORY_MAP.get(reason, "other"),
                "counterfactual_pnl": cf,
            }
        )

    def _close_position(self, key: str, ts: str, day: str, *, forced: bool = False, force_reason: str = "") -> None:
        pos = self.open_positions.get(key)
        if pos:
            trade = pos["trade"]
            shares = int(pos["shares"])
            entry_equity = float(pos.get("entry_equity") or self.current_equity())
            pnl = self.pnl_resolver(trade, shares=shares, entry_equity=entry_equity)
            self.accepted_pnls[key] = pnl
        super()._close_position(key, ts, day, forced=forced, force_reason=force_reason)
        gross = float(_gross_position_value(self.open_positions))
        self.max_gross_by_day[day] = max(self.max_gross_by_day[day], gross)


def _gross_position_value(open_positions: Mapping[str, Any]) -> float:
    from research.phase382_capital_constrained_backtest import _gross_position_value as gross_fn

    return gross_fn(open_positions)


def simulate_audited(
    trades: Sequence[Mapping[str, Any]],
    *,
    starting_equity: int,
    leverage: float,
    cap: int,
    stop_policy: str,
) -> dict[str, Any]:
    resolver = STOP_RESOLVERS[stop_policy]
    spec = build_spec(leverage=leverage, cap=cap, stop_policy=stop_policy)
    state = AuditedEquityCurveCapState(
        scenario_id=f"eq{starting_equity}_lev{leverage}",
        max_concurrent_positions=cap,
        spec=spec,
        initial_equity=float(starting_equity),
        equity_floor=float(starting_equity) * 0.5,
        pnl_resolver=resolver,
    )
    events = build_event_timeline(trades)
    if events:
        first_day = _day_from_ts(events[0][0].isoformat())
        state._record_equity(ts="", day=first_day, event_type="start")
    for dt, _, kind, trade in events:
        ts = dt.isoformat()
        day = _day_from_ts(ts)
        if kind == "entry":
            state.try_entry(trade, ts, day)
        else:
            state.process_exit(trade, ts, day)
        gross = _gross_position_value(state.open_positions)
        state.max_gross_by_day[day] = max(state.max_gross_by_day.get(day, 0.0), gross)
    if state.open_positions and events:
        last_ts = events[-1][0].isoformat()
        last_day = _day_from_ts(last_ts)
        state._force_close_all(last_ts, last_day, reason="end_of_period")

    daily_rows = build_daily_equity_rows(state)
    metrics = compute_scenario_metrics(state, daily_rows=daily_rows)
    reject_by_cat: dict[str, list[float]] = defaultdict(list)
    for row in state.reject_log:
        reject_by_cat[str(row.get("category") or "other")].append(float(row.get("counterfactual_pnl") or 0.0))

    return {
        **metrics,
        "leverage": leverage,
        "starting_equity": starting_equity,
        "cap": cap,
        "stop_policy": stop_policy,
        "accepted_pnls": dict(state.accepted_pnls),
        "reject_reason_counts": dict(state.reject_reason_counts),
        "reject_by_category_pnl": {k: round(sum(v), 2) for k, v in reject_by_cat.items()},
        "reject_log": state.reject_log,
        "_daily_rows": daily_rows,
        "_equity_curve": state.equity_curve,
        "_max_gross_by_day": dict(state.max_gross_by_day),
        "_state": state,
    }


def filter_trades_exclude_day(trades: Sequence[Mapping[str, Any]], excluded_day: str) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for trade in trades:
        entry_day = _day_from_ts(str(trade.get("entry_time") or ""))
        if entry_day == excluded_day:
            continue
        kept.append(dict(trade))
    return kept


def decompose_leverage_attribution(
    baseline: Mapping[str, Any],
    challenger: Mapping[str, Any],
    *,
    equity_yen: int,
    baseline_leverage: float = REFERENCE_LEVERAGE,
    challenger_leverage: float = CHALLENGER_LEVERAGE,
) -> dict[str, Any]:
    base_pnls = dict(baseline.get("accepted_pnls") or {})
    ch_pnls = dict(challenger.get("accepted_pnls") or {})
    base_keys = set(base_pnls)
    ch_keys = set(ch_pnls)

    only_ch = ch_keys - base_keys
    only_base = base_keys - ch_keys
    both = base_keys & ch_keys

    accepted_trade_effect = round(sum(ch_pnls[k] for k in only_ch) - sum(base_pnls[k] for k in only_base), 2)
    dynamic_stop_effect = round(sum(ch_pnls[k] - base_pnls[k] for k in both), 2)

    base_rejects = baseline.get("reject_log") or []
    ch_rejects = challenger.get("reject_log") or []
    base_reject_keys = {r["key"]: r for r in base_rejects}
    ch_reject_keys = {r["key"]: r for r in ch_rejects}

    buying_power_effect = 0.0
    cap_effect = 0.0
    for key, row in ch_reject_keys.items():
        if key in base_pnls:
            continue
        cat = row.get("category")
        cf = float(row.get("counterfactual_pnl") or 0.0)
        if cat == "buying_power":
            buying_power_effect += cf
        elif cat == "cap_constraint":
            cap_effect += cf
    for key, row in base_reject_keys.items():
        if key in ch_pnls:
            continue
        cat = row.get("category")
        cf = float(row.get("counterfactual_pnl") or 0.0)
        if cat == "buying_power":
            buying_power_effect -= cf
        elif cat == "cap_constraint":
            cap_effect -= cf
    buying_power_effect = round(buying_power_effect, 2)
    cap_effect = round(cap_effect, 2)

    base_final = float(baseline.get("final_equity") or 0.0)
    ch_final = float(challenger.get("final_equity") or 0.0)
    delta_final = round(ch_final - base_final, 2)
    delta_pct = round(delta_final / equity_yen * 100.0, 4) if equity_yen else 0.0

    base_dd = float(baseline.get("max_drawdown_pct") or 0.0)
    ch_dd = float(challenger.get("max_drawdown_pct") or 0.0)
    dd_effect = round((base_dd - ch_dd) / 100.0 * equity_yen, 2)

    explained = accepted_trade_effect + buying_power_effect + cap_effect + dynamic_stop_effect
    residual = round(delta_final - explained - dd_effect, 2)

    return {
        "equity_yen": equity_yen,
        "baseline_leverage": baseline_leverage,
        "challenger_leverage": challenger_leverage,
        "delta_final_equity_yen": delta_final,
        "delta_final_equity_pct": delta_pct,
        "accepted_trade_effect_yen": accepted_trade_effect,
        "buying_power_effect_yen": buying_power_effect,
        "cap_constraint_effect_yen": cap_effect,
        "dynamic_stop_effect_yen": dynamic_stop_effect,
        "dd_effect_yen": dd_effect,
        "residual_yen": residual,
        "baseline_final_equity": base_final,
        "challenger_final_equity": ch_final,
        "baseline_accepted_count": baseline.get("accepted_trade_count"),
        "challenger_accepted_count": challenger.get("accepted_trade_count"),
        "baseline_max_drawdown_pct": base_dd,
        "challenger_max_drawdown_pct": ch_dd,
    }


def build_day_level_rows(
    daily_by_lev: Mapping[float, Sequence[Mapping[str, Any]]],
    *,
    equity_yen: int,
) -> list[dict[str, Any]]:
    days = sorted(
        {
            str(r.get("day") or "")
            for rows in daily_by_lev.values()
            for r in rows
            if str(r.get("day") or "").isdigit()
        }
    )
    pnl_map: dict[float, dict[str, float]] = {}
    for lev, rows in daily_by_lev.items():
        pnl_map[lev] = {str(r.get("day") or ""): float(r.get("daily_pnl") or 0.0) for r in rows}

    out: list[dict[str, Any]] = []
    for day in days:
        p0 = pnl_map.get(1.0, {}).get(day, 0.0)
        p15 = pnl_map.get(1.5, {}).get(day, 0.0)
        p2 = pnl_map.get(2.0, {}).get(day, 0.0)
        winner = max([(p0, 1.0), (p15, 1.5), (p2, 2.0)], key=lambda x: x[0])[1]
        out.append(
            {
                "equity_yen": equity_yen,
                "day": day,
                "daily_pnl_lev1p0": round(p0, 2),
                "daily_pnl_lev1p5": round(p15, 2),
                "daily_pnl_lev2p0": round(p2, 2),
                "winner_leverage": winner,
                "lev1p5_beats_lev2p0": p15 > p2,
                "lev1p5_beats_lev1p0": p15 > p0,
            }
        )
    return out


def leave_one_day_out_analysis(
    trades: Sequence[Mapping[str, Any]],
    *,
    equity_yen: int,
    cap: int,
    stop_policy: str,
    period_days: Sequence[str],
) -> dict[str, Any]:
    wins = 0
    runs = 0
    rows: list[dict[str, Any]] = []
    for excluded in period_days:
        subset = filter_trades_exclude_day(trades, excluded)
        sim15 = simulate_audited(subset, starting_equity=equity_yen, leverage=1.5, cap=cap, stop_policy=stop_policy)
        sim20 = simulate_audited(subset, starting_equity=equity_yen, leverage=2.0, cap=cap, stop_policy=stop_policy)
        f15 = float(sim15.get("final_equity") or 0.0)
        f20 = float(sim20.get("final_equity") or 0.0)
        win = f15 > f20
        wins += int(win)
        runs += 1
        rows.append(
            {
                "excluded_day": excluded,
                "final_equity_lev1p5": round(f15, 2),
                "final_equity_lev2p0": round(f20, 2),
                "lev1p5_wins": win,
            }
        )
    score = round(wins / runs, 4) if runs else 0.0
    return {"robustness_score": score, "wins": wins, "runs": runs, "rows": rows}


def bootstrap_daily_pnl_ci(
    daily_by_lev: Mapping[float, Sequence[Mapping[str, Any]]],
    *,
    equity_yen: int,
    n_iter: int = BOOTSTRAP_ITERATIONS,
    seed: int = 271,
) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    days = sorted(
        {
            str(r.get("day") or "")
            for rows in daily_by_lev.values()
            for r in rows
            if str(r.get("day") or "").isdigit()
        }
    )
    if not days:
        return []

    pnl_by_lev: dict[float, dict[str, float]] = {}
    for lev, rows in daily_by_lev.items():
        pnl_by_lev[lev] = {str(r.get("day") or ""): float(r.get("daily_pnl") or 0.0) for r in rows}

    results: list[dict[str, Any]] = []
    for comparison, base_lev, ch_lev in (
        ("lev1p5_vs_lev2p0", 2.0, 1.5),
        ("lev1p5_vs_lev1p0", 1.0, 1.5),
    ):
        deltas: list[float] = []
        ch_wins = 0
        for _ in range(n_iter):
            sampled = [days[rng.randrange(len(days))] for _ in range(len(days))]
            base_total = sum(pnl_by_lev.get(base_lev, {}).get(d, 0.0) for d in sampled)
            ch_total = sum(pnl_by_lev.get(ch_lev, {}).get(d, 0.0) for d in sampled)
            deltas.append(ch_total - base_total)
            if ch_total > base_total:
                ch_wins += 1
        deltas.sort()
        lo = deltas[int(0.025 * len(deltas))]
        hi = deltas[int(0.975 * len(deltas))]
        point = statistics.mean(deltas)
        results.append(
            {
                "equity_yen": equity_yen,
                "comparison": comparison,
                "metric": "delta_realized_pnl_yen",
                "point_estimate": round(point, 2),
                "ci_low_95": round(lo, 2),
                "ci_high_95": round(hi, 2),
                "bootstrap_n": n_iter,
                "lev1p5_win_rate": round(ch_wins / n_iter, 4),
            }
        )
        final_deltas = [equity_yen + d for d in deltas]
        final_deltas.sort()
        results.append(
            {
                "equity_yen": equity_yen,
                "comparison": comparison,
                "metric": "challenger_final_equity_yen",
                "point_estimate": round(equity_yen + point, 2),
                "ci_low_95": round(final_deltas[int(0.025 * len(final_deltas))], 2),
                "ci_high_95": round(final_deltas[int(0.975 * len(final_deltas))], 2),
                "bootstrap_n": n_iter,
                "lev1p5_win_rate": round(ch_wins / n_iter, 4),
            }
        )
    return results


def practical_significance(
    *,
    equity_yen: int,
    final_lev15: float,
    final_lev20: float,
) -> dict[str, Any]:
    delta_yen = round(final_lev15 - final_lev20, 2)
    delta_pct = round(delta_yen / equity_yen * 100.0, 4) if equity_yen else 0.0
    insignificant = abs(delta_pct) < ECONOMIC_SIGNIFICANCE_PCT
    return {
        "equity_yen": equity_yen,
        "delta_yen": delta_yen,
        "delta_pct": delta_pct,
        "economically_insignificant": insignificant,
        "threshold_pct": ECONOMIC_SIGNIFICANCE_PCT,
    }


def build_exposure_curve_rows(sim: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    equity_yen = int(sim.get("starting_equity") or 0)
    lev = float(sim.get("leverage") or 0.0)
    daily = {str(r.get("day") or ""): r for r in sim.get("_daily_rows") or []}
    gross = sim.get("_max_gross_by_day") or {}
    for day in sorted(daily):
        row = daily[day]
        rows.append(
            {
                "equity_yen": equity_yen,
                "leverage": lev,
                "day": day,
                "end_equity": row.get("end_equity"),
                "daily_pnl": row.get("daily_pnl"),
                "gross_position_value": round(float(gross.get(day, 0.0)), 2),
                "max_gross_in_day": round(float(gross.get(day, 0.0)), 2),
            }
        )
    return rows


def build_required_answers(
    focus_results: Mapping[int, Mapping[str, Any]],
) -> dict[str, Any]:
    robust_count = 0
    economic_count = 0
    lev2_ok_all = True
    per_equity: list[dict[str, Any]] = []

    for eq, bundle in focus_results.items():
        loo = bundle.get("leave_one_day_out") or {}
        boot = bundle.get("bootstrap") or []
        prac = bundle.get("practical_significance") or {}
        day = bundle.get("day_robustness") or {}
        stat_robust = (
            float(loo.get("robustness_score") or 0.0) >= ROBUST_LOO_THRESHOLD
            and float(day.get("lev1p5_win_rate_vs_lev2p0") or 0.0) >= ROBUST_DAY_WIN_THRESHOLD
        )
        boot_row = next((r for r in boot if r.get("comparison") == "lev1p5_vs_lev2p0" and r.get("metric") == "delta_realized_pnl_yen"), {})
        ci_robust = float(boot_row.get("ci_low_95") or 0.0) > 0
        stat_robust = stat_robust and ci_robust
        econ = not prac.get("economically_insignificant")
        if stat_robust:
            robust_count += 1
        if econ:
            economic_count += 1
        sims = bundle.get("simulations") or {}
        f15 = float((sims.get(1.5) or {}).get("final_equity") or 0.0)
        f20 = float((sims.get(2.0) or {}).get("final_equity") or 0.0)
        if f20 < f15 - abs(EconomicSignificanceThreshold(eq)):
            lev2_ok_all = False
        per_equity.append(
            {
                "equity_yen": eq,
                "statistically_robust": stat_robust,
                "economically_meaningful": econ,
                "delta_pct": prac.get("delta_pct"),
                "loo_score": loo.get("robustness_score"),
                "day_win_rate": day.get("lev1p5_win_rate_vs_lev2p0"),
            }
        )

    n = len(focus_results)
    if robust_count == 0:
        recommendation = "fixed_leverage_2p0"
        rule = "Use leverage=2.0 for all equity buckets; lev1.5 advantage is not robust."
    elif robust_count == n and economic_count >= n // 2 + 1:
        recommendation = "equity_linked_leverage"
        rule = "Use lev=1.5 for 2.5M+ focus buckets where robust; lev=2.0 below 3M otherwise."
    else:
        recommendation = "max_exposure_coefficient"
        rule = "Prefer max gross exposure cap instead of fine-grained leverage tuning; sample too small for stable lev rules."

    return {
        "1_is_lev1p5_statistically_robust": {
            "verdict": robust_count >= 2,
            "robust_equity_count": robust_count,
            "focus_equity_count": n,
            "per_equity": per_equity,
            "note": "Requires LOO score>=67%, day win rate>=67%, bootstrap 95% CI delta PnL > 0.",
        },
        "2_is_lev1p5_economically_meaningful": {
            "verdict": economic_count >= 2,
            "meaningful_equity_count": economic_count,
            "threshold_pct": ECONOMIC_SIGNIFICANCE_PCT,
            "per_equity": [{k: p.get(k) for k in ("equity_yen", "delta_pct", "economically_meaningful")} for p in per_equity],
        },
        "3_is_fixed_lev2_ok_for_all": {
            "verdict": lev2_ok_all or robust_count == 0,
            "note": "If lev1.5 advantage is neither robust nor economic, fixed lev=2.0 is acceptable.",
        },
        "4_need_equity_band_leverage_rules": {
            "verdict": robust_count >= 2 and economic_count >= 2,
            "note": "Only if both statistical and economic tests pass for multiple focus equities.",
        },
        "5_recommended_approach": {
            "choice": recommendation,
            "rule": rule,
            "options": ["fixed_leverage_2p0", "equity_linked_leverage", "max_exposure_coefficient"],
        },
    }


def EconomicSignificanceThreshold(equity_yen: int) -> float:
    return equity_yen * ECONOMIC_SIGNIFICANCE_PCT / 100.0


def build_report(summary: Mapping[str, Any]) -> str:
    answers = summary.get("required_answers") or {}
    a1 = answers.get("1_is_lev1p5_statistically_robust") or {}
    a2 = answers.get("2_is_lev1p5_economically_meaningful") or {}
    a3 = answers.get("3_is_fixed_lev2_ok_for_all") or {}
    a4 = answers.get("4_need_equity_band_leverage_rules") or {}
    a5 = answers.get("5_recommended_approach") or {}

    lines = [
        "# Phase271 Leverage Attribution and Robustness",
        "",
        f"**生成:** {summary.get('generated_at')}",
        "",
        "## 共通制約",
        "",
        "- Runtime / Universe / Entry / Exit / YAML 変更なし",
        "- 採用判定: final_equity 主指標",
        "",
        f"**対象元本:** {summary.get('focus_equities')}",
        f"**営業日数:** {summary.get('period_day_count')}",
        "",
        "## 必須回答",
        "",
        "### 1. レバ1.5優位は統計的に頑健か",
        "",
        f"- verdict: **{'はい' if a1.get('verdict') else 'いいえ'}** ({a1.get('robust_equity_count')}/{a1.get('focus_equity_count')} 元本)",
        f"- 条件: LOO>={ROBUST_LOO_THRESHOLD}, 日次勝率>={ROBUST_DAY_WIN_THRESHOLD}, bootstrap 95% CI > 0",
        "",
        "### 2. レバ1.5優位は経済的に意味があるか",
        "",
        f"- verdict: **{'はい' if a2.get('verdict') else 'いいえ'}** (閾値 {ECONOMIC_SIGNIFICANCE_PCT}% 超)",
        "",
        "### 3. 全元本でレバ2固定で問題ないか",
        "",
        f"- verdict: **{'はい' if a3.get('verdict') else 'いいえ'}**",
        "",
        "### 4. 元本帯別レバ変更ルールが必要か",
        "",
        f"- verdict: **{'はい' if a4.get('verdict') else 'いいえ'}**",
        "",
        "### 5. 推奨方式",
        "",
        f"- **{a5.get('choice')}**",
        f"- {a5.get('rule')}",
        "",
        "## 分析サマリ",
        "",
    ]
    for row in summary.get("focus_summaries") or []:
        lines.append(
            f"- {row.get('equity_yen')}円: Δfinal(1.5-2.0)={row.get('delta_pct')}% "
            f"LOO={row.get('loo_score')} day_win={row.get('day_win_rate')} "
            f"econ={'insignificant' if row.get('economically_insignificant') else 'significant'}"
        )
    lines.append("")
    return "\n".join(lines) + "\n"


@dataclass
class Phase271LeverageAttributionAndRobustness:
    repo_root: Path
    reports_dir: Path
    period_start: str = PERIOD_START
    bootstrap_iterations: int = BOOTSTRAP_ITERATIONS

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase271_summary.json",
            "attribution": self.reports_dir / "phase271_leverage_attribution.csv",
            "day_robustness": self.reports_dir / "phase271_day_level_robustness.csv",
            "bootstrap": self.reports_dir / "phase271_bootstrap_confidence.csv",
            "exposure_curve": self.reports_dir / "phase271_equity_exposure_curve.csv",
            "report": self.reports_dir / "phase271_report.md",
        }

    def run(self) -> dict[str, Any]:
        trades, population = load_period_trades(self.repo_root, period_start=self.period_start)
        period_days = list(population.get("period_days") or [])

        attribution_rows: list[dict[str, Any]] = []
        day_rows: list[dict[str, Any]] = []
        bootstrap_rows: list[dict[str, Any]] = []
        exposure_rows: list[dict[str, Any]] = []
        focus_results: dict[int, dict[str, Any]] = {}
        focus_summaries: list[dict[str, Any]] = []

        for equity_yen in FOCUS_EQUITIES:
            cfg = PHASE270_RECOMMENDATIONS[equity_yen]
            cap = int(cfg["cap"])
            stop_policy = str(cfg["stop_policy"])
            sims: dict[float, dict[str, Any]] = {}
            daily_by_lev: dict[float, list[dict[str, Any]]] = {}

            for lev in LEVERAGES:
                sim = simulate_audited(
                    trades,
                    starting_equity=equity_yen,
                    leverage=lev,
                    cap=cap,
                    stop_policy=stop_policy,
                )
                sims[lev] = sim
                daily_by_lev[lev] = list(sim.get("_daily_rows") or [])
                exposure_rows.extend(build_exposure_curve_rows(sim))

                research = build_research_layer(
                    list((sim.get("accepted_pnls") or {}).values()),
                    trade_count=int(sim.get("accepted_trade_count") or 0),
                )
                live = build_live_simulation_layer_from_equity_metrics(
                    sim,
                    cap=cap,
                    daily_rows=sim.get("_daily_rows") or [],
                    starting_equity=float(equity_yen),
                    leverage=lev,
                )
                build_dual_layer_bundle(research_layer=research, live_simulation_layer=live)

            attr = decompose_leverage_attribution(
                sims[REFERENCE_LEVERAGE],
                sims[CHALLENGER_LEVERAGE],
                equity_yen=equity_yen,
            )
            attribution_rows.append(attr)

            drows = build_day_level_rows(daily_by_lev, equity_yen=equity_yen)
            day_rows.extend(drows)
            lev15_wins = sum(1 for r in drows if r.get("lev1p5_beats_lev2p0"))
            day_total = len(drows)

            loo = leave_one_day_out_analysis(
                trades,
                equity_yen=equity_yen,
                cap=cap,
                stop_policy=stop_policy,
                period_days=period_days,
            )
            boot = bootstrap_daily_pnl_ci(
                daily_by_lev,
                equity_yen=equity_yen,
                n_iter=self.bootstrap_iterations,
            )
            bootstrap_rows.extend(boot)

            prac = practical_significance(
                equity_yen=equity_yen,
                final_lev15=float(sims[1.5].get("final_equity") or 0.0),
                final_lev20=float(sims[2.0].get("final_equity") or 0.0),
            )

            focus_results[equity_yen] = {
                "simulations": sims,
                "attribution": attr,
                "leave_one_day_out": loo,
                "bootstrap": boot,
                "practical_significance": prac,
                "day_robustness": {
                    "lev1p5_win_rate_vs_lev2p0": round(lev15_wins / day_total, 4) if day_total else 0.0,
                    "lev1p5_wins": lev15_wins,
                    "day_count": day_total,
                },
            }
            focus_summaries.append(
                {
                    "equity_yen": equity_yen,
                    "delta_pct": prac.get("delta_pct"),
                    "delta_yen": prac.get("delta_yen"),
                    "loo_score": loo.get("robustness_score"),
                    "day_win_rate": round(lev15_wins / day_total, 4) if day_total else 0.0,
                    "economically_insignificant": prac.get("economically_insignificant"),
                    "attribution": attr,
                }
            )

        answers = build_required_answers(focus_results)

        return {
            "phase": "271-Leverage-Attribution-and-Robustness",
            "title": "Leverage attribution and robustness",
            "generated_at": _now_iso(),
            "purpose": "Validate Phase270 lev=1.5 advantage at focus equity buckets",
            "constraints": dict(COMMON_RESEARCH_CONSTRAINTS),
            "output_standard": {
                "dual_layer_required": True,
                "adoption_primary_metric": "final_equity",
            },
            "focus_equities": list(FOCUS_EQUITIES),
            "fixed_cap": 5,
            "fixed_stop_policy": "dynamic_stop_risk_1p0",
            "leverages_compared": list(LEVERAGES),
            "period_day_count": len(period_days),
            "period_days": period_days,
            "bootstrap_iterations": self.bootstrap_iterations,
            "economic_significance_threshold_pct": ECONOMIC_SIGNIFICANCE_PCT,
            "robustness_thresholds": {
                "loo_score": ROBUST_LOO_THRESHOLD,
                "day_win_rate": ROBUST_DAY_WIN_THRESHOLD,
            },
            "population": population,
            "focus_summaries": focus_summaries,
            "required_answers": answers,
            "focus_results": {
                str(k): {
                    "attribution": v.get("attribution"),
                    "leave_one_day_out": {
                        "robustness_score": (v.get("leave_one_day_out") or {}).get("robustness_score"),
                        "wins": (v.get("leave_one_day_out") or {}).get("wins"),
                        "runs": (v.get("leave_one_day_out") or {}).get("runs"),
                    },
                    "practical_significance": v.get("practical_significance"),
                    "day_robustness": v.get("day_robustness"),
                    "final_equity_by_leverage": {
                        str(lev): (v.get("simulations") or {}).get(lev, {}).get("final_equity")
                        for lev in LEVERAGES
                    },
                }
                for k, v in focus_results.items()
            },
            "_attribution_rows": attribution_rows,
            "_day_rows": day_rows,
            "_bootstrap_rows": bootstrap_rows,
            "_exposure_rows": exposure_rows,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        paths = self.paths()
        paths["attribution"].parent.mkdir(parents=True, exist_ok=True)
        _write_csv(paths["attribution"], result.get("_attribution_rows") or [], ATTRIBUTION_FIELDS)
        _write_csv(paths["day_robustness"], result.get("_day_rows") or [], DAY_ROBUSTNESS_FIELDS)
        _write_csv(paths["bootstrap"], result.get("_bootstrap_rows") or [], BOOTSTRAP_FIELDS)
        _write_csv(paths["exposure_curve"], result.get("_exposure_rows") or [], EXPOSURE_CURVE_FIELDS)
        public = {k: v for k, v in result.items() if not str(k).startswith("_")}
        paths["summary"].write_text(json.dumps(public, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        paths["report"].write_text(build_report(public), encoding="utf-8")
        return paths


def run_leverage_attribution_and_robustness(
    *,
    repo_root: Path,
    reports_dir: Path,
    period_start: str = PERIOD_START,
    bootstrap_iterations: int = BOOTSTRAP_ITERATIONS,
) -> dict[str, Any]:
    job = Phase271LeverageAttributionAndRobustness(
        repo_root=repo_root,
        reports_dir=reports_dir,
        period_start=period_start,
        bootstrap_iterations=bootstrap_iterations,
    )
    result = job.run()
    job.write_outputs(result)
    return {k: v for k, v in result.items() if not str(k).startswith("_")}
