"""
Phase406: Portfolio-level adoption re-evaluation.

Re-ranks Phase402–405 policies on portfolio metrics only (no per-symbol gates).
Research only — no Runtime / YAML / Entry / Exit changes.
"""

from __future__ import annotations

import csv
import json
import statistics
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import _pf, _win_rate
from research.phase382_capital_constrained_backtest import _float, _parse_ts, _write_csv
from research.phase400_holding_time_audit import enrich_trade, hold_seconds, load_phase399_trades
from research.phase402_time_decay_exit_shadow import (
    HARD_STOP_PCT,
    PolicySpec,
    POLICY_MFE,
    _max_drawdown_yen,
    _normalize_shadow_exit,
    _prepare_trade_context,
    _saved_lost_yen,
    simulate_time_decay_exit,
)
from research.phase403_gradual_time_decay_shadow import (
    POLICY_LINEAR,
    GradualPolicySpec,
    simulate_gradual_decay_exit,
)
from research.phase404_no_progress_exit_shadow import (
    NoProgressPolicySpec,
    build_tick_states,
    simulate_no_progress_exit,
)
from research.phase405_time_boundary_inference import TIME_BUCKETS_MIN, TRAIL_GIVEBACK_FRAC
from small_paper.board_dynamic_trailing_shadow import trailing_params_for_board_tier

JST = ZoneInfo("Asia/Tokyo")
PERIOD_START = "20260529"
PERIOD_END = "20260615"
INITIAL_EQUITY_YEN = 1_500_000.0
MAX_DD_TOLERANCE_YEN = 500.0

PHASE402_BEST = PolicySpec(POLICY_MFE, 900.0, 0.3, None, True, False)
PHASE403_BEST = GradualPolicySpec(POLICY_LINEAR, 900.0, 0.6, 0.2, linear_decay_per_min=0.05)
PHASE404_BEST = NoProgressPolicySpec(900.0, 0.8, 0.2, "none", "none")

COMPARISON_FIELDS = [
    "policy_label",
    "source_phase",
    "tier",
    "rank",
    "total_pnl_yen_100",
    "profit_factor",
    "final_equity_yen",
    "max_drawdown_yen_100",
    "calmar_like",
    "expectancy_yen_per_trade",
    "trade_count",
    "win_rate",
    "avg_hold_sec",
    "stop_hit_count",
    "session_close_count",
    "trailing_mfe_count",
    "no_progress_exit_count",
    "boundary_exit_count",
    "long_hold_loser_count",
    "saved_loss_yen",
    "lost_upside_yen",
    "net_delta_yen",
    "pnl_improvement_pct",
    "pf_improvement_pct",
    "maxdd_improvement_yen",
    "risk_adjusted_score",
]

RANKING_FIELDS = [
    "rank",
    "policy_label",
    "source_phase",
    "tier",
    "recommendation",
    "total_pnl_yen_100",
    "profit_factor",
    "max_drawdown_yen_100",
    "calmar_like",
    "expectancy_yen_per_trade",
    "risk_adjusted_score",
    "pnl_improvement_pct",
    "pf_improvement_pct",
]


@dataclass(frozen=True)
class BoundaryBucketRule:
    mfe_exit: float
    stop: float
    trail: float


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _pnl_pct(entry: float, px: float) -> float:
    if entry <= 0:
        return 0.0
    return round((px - entry) / entry * 100.0, 4)


def load_phase405_boundary_policy(path: Path) -> dict[int, BoundaryBucketRule]:
    rules: dict[int, BoundaryBucketRule] = {}
    if not path.is_file():
        return _default_boundary_rules()
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            b = int(float(row["time_bucket_min"]))
            rules[b] = BoundaryBucketRule(
                mfe_exit=float(row["recommended_mfe_exit_threshold"]),
                stop=float(row["recommended_stop_threshold"]),
                trail=float(row["recommended_mfe_trail_threshold"]),
            )
    return rules or _default_boundary_rules()


def _default_boundary_rules() -> dict[int, BoundaryBucketRule]:
    return {
        5: BoundaryBucketRule(1.0, -0.6, 0.5),
        10: BoundaryBucketRule(1.0, -1.0, 0.4),
        15: BoundaryBucketRule(1.0, -1.0, 0.4),
        20: BoundaryBucketRule(1.0, -0.8, 0.3),
        30: BoundaryBucketRule(0.6, -0.8, 0.3),
        45: BoundaryBucketRule(0.6, -0.2, 0.3),
        60: BoundaryBucketRule(0.6, -0.2, 0.3),
    }


def _exit_result(entry_price: float, px: float, ts: float, pnl: float, reason: str) -> dict[str, Any]:
    from replay.pnl_yen import compute_pnl_yen_100

    return {
        "shadow_exit_reason": reason,
        "shadow_exit_ts": ts,
        "shadow_pnl_pct": pnl,
        "shadow_pnl_yen_100": round(compute_pnl_yen_100(entry_price, px), 2),
        "shadow_exit_price": round(px, 4),
    }


def simulate_boundary_policy(
    states: Sequence[Mapping[str, Any]],
    *,
    entry_price: float,
    entry_ts: float,
    imb_pct: Optional[float],
    buckets: Mapping[int, BoundaryBucketRule],
) -> dict[str, Any]:
    activate_base, giveback_frac, _tier = trailing_params_for_board_tier(imb_pct)
    hard_stop_px = entry_price * (1.0 - HARD_STOP_PCT / 100.0)
    bucket_mins = sorted(buckets.keys())

    if not states:
        return _exit_result(entry_price, entry_price, entry_ts, 0.0, "no_ticks")

    for state in states:
        ts = float(state["ts"])
        px = float(state["px"])
        elapsed = float(state["elapsed"])
        pnl = float(state["pnl"])
        peak_mfe = float(state["peak_mfe"])

        if px <= hard_stop_px:
            return _exit_result(entry_price, px, ts, pnl, "stop_hit")

        active_bucket: Optional[int] = None
        for b in bucket_mins:
            if elapsed >= b * 60.0:
                active_bucket = b
        if active_bucket is not None:
            rule = buckets[active_bucket]
            if peak_mfe < rule.mfe_exit:
                return _exit_result(entry_price, px, ts, pnl, "boundary_mfe_exit")
            if pnl < rule.stop:
                return _exit_result(entry_price, px, ts, pnl, "boundary_stop_exit")
            if peak_mfe >= rule.trail and pnl <= peak_mfe * TRAIL_GIVEBACK_FRAC:
                return _exit_result(entry_price, px, ts, pnl, "boundary_trail_exit")

        if peak_mfe >= activate_base and pnl <= peak_mfe * giveback_frac:
            return _exit_result(entry_price, px, ts, pnl, "trailing_mfe_exit")

    last = states[-1]
    return _exit_result(
        entry_price,
        float(last["px"]),
        float(last["ts"]),
        float(last["pnl"]),
        "session_close",
    )


def _enrich_with_tick_states(
    ctx: Mapping[str, Any],
    *,
    entry_vwap_dev_pct: Optional[float],
) -> dict[str, Any]:
    states = build_tick_states(
        ctx["price_series"],
        entry_ts=float(ctx["entry_ts"]),
        entry_price=float(ctx["entry_price"]),
        session_end_ts=float(ctx["session_end_ts"]),
        entry_vwap_dev_pct=entry_vwap_dev_pct,
    )
    return {**dict(ctx), "tick_states": states, "entry_vwap_dev_pct": entry_vwap_dev_pct}


def simulate_policy(
    ctx: Mapping[str, Any],
    *,
    policy_label: str,
    boundary_rules: Optional[Mapping[int, BoundaryBucketRule]] = None,
) -> dict[str, Any]:
    if policy_label == "phase399_baseline":
        return {
            "shadow_exit_reason": ctx.get("baseline_exit_reason") or "baseline",
            "shadow_exit_ts": _parse_ts(str(ctx.get("exit_time") or "")),
            "shadow_pnl_yen_100": float(ctx["baseline_pnl_yen_100"]),
            "shadow_exit_price": None,
        }

    if policy_label == "phase402_best":
        return simulate_time_decay_exit(
            ctx["price_series"],
            entry_ts=float(ctx["entry_ts"]),
            entry_price=float(ctx["entry_price"]),
            session_end_ts=float(ctx["session_end_ts"]),
            imb_pct=ctx.get("imb_pct"),
            policy=PHASE402_BEST,
        )
    if policy_label == "phase403_best":
        return simulate_gradual_decay_exit(
            ctx["price_series"],
            entry_ts=float(ctx["entry_ts"]),
            entry_price=float(ctx["entry_price"]),
            session_end_ts=float(ctx["session_end_ts"]),
            imb_pct=ctx.get("imb_pct"),
            policy=PHASE403_BEST,
        )
    if policy_label == "phase404_best":
        return simulate_no_progress_exit(
            ctx["tick_states"],
            entry_price=float(ctx["entry_price"]),
            entry_ts=float(ctx["entry_ts"]),
            session_end_ts=float(ctx["session_end_ts"]),
            imb_pct=ctx.get("imb_pct"),
            policy=PHASE404_BEST,
        )
    if policy_label == "phase405_boundary":
        assert boundary_rules is not None
        return simulate_boundary_policy(
            ctx["tick_states"],
            entry_price=float(ctx["entry_price"]),
            entry_ts=float(ctx["entry_ts"]),
            imb_pct=ctx.get("imb_pct"),
            buckets=boundary_rules,
        )
    raise ValueError(f"unknown policy: {policy_label}")


def _chronological_pnls(
    trade_results: Sequence[Mapping[str, Any]],
    *,
    pnl_key: str,
    exit_time_key: str,
) -> list[float]:
    sort_keys = [
        (_parse_ts(str(t.get(exit_time_key) or t.get("exit_time") or "")) or datetime.min.replace(tzinfo=JST), i)
        for i, t in enumerate(trade_results)
    ]
    order = [i for _, i in sorted(sort_keys, key=lambda x: (x[0], x[1]))]
    return [float(trade_results[i][pnl_key]) for i in order]


def _equity_curve(pnls: Sequence[float], *, initial: float) -> list[float]:
    equity = initial
    curve = [equity]
    for p in pnls:
        equity += p
        curve.append(equity)
    return curve


def _classify_tier(
    metrics: Mapping[str, Any],
    *,
    baseline: Mapping[str, Any],
) -> str:
    pnl_up = float(metrics["total_pnl_yen_100"]) > float(baseline["total_pnl_yen_100"])
    pf_up = float(metrics["profit_factor"] or 0) > float(baseline["profit_factor"] or 0)
    dd_down = float(metrics["max_drawdown_yen_100"]) < float(baseline["max_drawdown_yen_100"]) - 0.01
    dd_flat = abs(float(metrics["max_drawdown_yen_100"]) - float(baseline["max_drawdown_yen_100"])) <= MAX_DD_TOLERANCE_YEN
    eq_up = float(metrics["final_equity_yen"]) > float(baseline["final_equity_yen"])

    if not pnl_up and not pf_up:
        return "Reject"
    if pnl_up and pf_up and dd_down and eq_up:
        return "Tier S"
    if pnl_up and pf_up and (dd_down or dd_flat):
        return "Tier A"
    return "Tier B"


def _recommendation_for_tier(tier: str, rank: int) -> str:
    if tier == "Tier S" and rank == 1:
        return "A_adopt_candidate"
    if tier in ("Tier S", "Tier A"):
        return "B_shadow_continue"
    if tier == "Tier B" and rank <= 3:
        return "B_shadow_continue"
    return "C_reject"


def aggregate_portfolio_metrics(
    trades: Sequence[Mapping[str, Any]],
    *,
    policy_label: str,
    baseline_pnls: Sequence[float],
    p90_hold: float,
) -> dict[str, Any]:
    shadow_pnls = [float(t["shadow_pnl_yen_100"]) for t in trades]
    shadow_reasons = [_normalize_shadow_exit(str(t.get("shadow_exit_reason") or "")) for t in trades]
    holds = [float(t.get("shadow_hold_sec") or t.get("hold_sec") or 0) for t in trades]

    chron = _chronological_pnls(trades, pnl_key="shadow_pnl_yen_100", exit_time_key="shadow_exit_time")
    max_dd = _max_drawdown_yen(chron)
    total_pnl = round(sum(shadow_pnls), 2)
    final_equity = round(INITIAL_EQUITY_YEN + total_pnl, 2)
    calmar = round(total_pnl / max_dd, 4) if max_dd > 0 else None
    expectancy = round(statistics.mean(shadow_pnls), 2) if shadow_pnls else 0.0
    saved, lost = _saved_lost_yen(list(baseline_pnls), shadow_pnls)

    long_hold_losers = sum(
        1
        for t, p in zip(trades, shadow_pnls)
        if float(t.get("hold_sec") or 0) >= p90_hold and p < 0
    )

    return {
        "policy_label": policy_label,
        "total_pnl_yen_100": total_pnl,
        "profit_factor": _pf(shadow_pnls),
        "final_equity_yen": final_equity,
        "max_drawdown_yen_100": max_dd,
        "calmar_like": calmar,
        "expectancy_yen_per_trade": expectancy,
        "trade_count": len(trades),
        "win_rate": _win_rate(shadow_pnls),
        "avg_hold_sec": round(statistics.mean(holds), 2) if holds else 0.0,
        "stop_hit_count": sum(1 for r in shadow_reasons if r == "stop_hit"),
        "session_close_count": sum(1 for r in shadow_reasons if r == "session_close"),
        "trailing_mfe_count": sum(1 for r in shadow_reasons if r == "trailing_mfe"),
        "no_progress_exit_count": sum(1 for r in shadow_reasons if "no_progress" in r),
        "boundary_exit_count": sum(1 for r in shadow_reasons if "boundary" in r),
        "long_hold_loser_count": long_hold_losers,
        "saved_loss_yen": saved,
        "lost_upside_yen": lost,
        "net_delta_yen": round(total_pnl - sum(baseline_pnls), 2),
        "risk_adjusted_score": round((calmar or 0.0) * 1000 + total_pnl / 10000.0, 4),
    }


def _vs_baseline(row: Mapping[str, Any], baseline: Mapping[str, Any]) -> dict[str, Any]:
    base_pnl = float(baseline["total_pnl_yen_100"])
    base_pf = float(baseline["profit_factor"] or 0)
    base_dd = float(baseline["max_drawdown_yen_100"])
    pnl = float(row["total_pnl_yen_100"])
    pf = float(row["profit_factor"] or 0)
    dd = float(row["max_drawdown_yen_100"])
    return {
        **dict(row),
        "pnl_improvement_pct": round((pnl - base_pnl) / abs(base_pnl) * 100.0, 2) if base_pnl else None,
        "pf_improvement_pct": round((pf - base_pf) / base_pf * 100.0, 2) if base_pf else None,
        "maxdd_improvement_yen": round(base_dd - dd, 2),
    }


def _render_report(summary: Mapping[str, Any]) -> str:
    baseline = summary.get("baseline_comparison") or {}
    ranks = summary.get("ranking") or []
    lines = [
        "# Phase406 — Portfolio-Level Adoption Re-Evaluation",
        "",
        f"Generated: {summary.get('generated_at')}",
        f"Verdict: **{summary.get('verdict')}**",
        "",
        summary.get("headline") or "",
        "",
        "## Ranking (portfolio metrics only)",
        "",
        "| Rank | Policy | Tier | Rec | PnL | PF | maxDD | Calmar |",
        "|------|--------|------|-----|-----|----|-------|--------|",
    ]
    for r in ranks:
        lines.append(
            f"| {r.get('rank')} | {r.get('policy_label')} | {r.get('tier')} | "
            f"{r.get('recommendation')} | ¥{r.get('total_pnl_yen_100')} | "
            f"{r.get('profit_factor')} | ¥{r.get('max_drawdown_yen_100')} | {r.get('calmar_like')} |"
        )
    lines.extend(
        [
            "",
            "## vs Production Stack (Phase399 baseline)",
            "",
            f"| PnL | ¥{baseline.get('baseline_pnl')} → best ¥{baseline.get('best_pnl')} ({baseline.get('pnl_improvement_pct')}%) |",
            f"| PF | {baseline.get('baseline_pf')} → {baseline.get('best_pf')} ({baseline.get('pf_improvement_pct')}%) |",
            f"| maxDD | ¥{baseline.get('baseline_maxdd')} → ¥{baseline.get('best_maxdd')} (Δ¥{baseline.get('maxdd_improvement_yen')}) |",
            "",
            f"**Recommendation:** {summary.get('top_recommendation')}",
            "",
            "## Reference only (not used in tier)",
            "",
            "Per-symbol damage and long_hold_loser deltas excluded from adoption gates.",
            "",
        ]
    )
    return "\n".join(lines)


def run_phase406_portfolio_adoption(
    *,
    repo_root: Path,
    trades_path: Optional[Path] = None,
    phase405_policy_path: Optional[Path] = None,
    output_dir: Path,
    period_start: str = PERIOD_START,
    period_end: str = PERIOD_END,
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    trades_path = trades_path or (
        repo_root / "results" / "reports" / "phase399_historical_position_cap_backfill_trades.csv"
    )
    phase405_policy_path = phase405_policy_path or (
        repo_root / "results" / "reports" / "phase405_time_boundary_policy.csv"
    )
    p400_path = repo_root / "results" / "reports" / "phase400_holding_time_summary.json"
    p90_hold = 1290.6
    if p400_path.is_file():
        p90_hold = float(json.loads(p400_path.read_text(encoding="utf-8"))["hold_duration_sec"]["p90_hold_sec"])

    raw = load_phase399_trades(trades_path)
    accepted = [
        enrich_trade(r)
        for r in raw
        if str(r.get("day") or "") >= period_start
        and str(r.get("day") or "") <= period_end
        and str(r.get("position_cap_accepted") or "").lower() in ("true", "1", "yes")
    ]

    boundary_rules = load_phase405_boundary_policy(phase405_policy_path)
    session_cache: dict[str, Any] = {}
    contexts: list[dict[str, Any]] = []
    for trade in accepted:
        trade["_p90_hold"] = p90_hold
        ctx = _prepare_trade_context(trade, repo_root=repo_root, session_cache=session_cache)
        if ctx is None:
            continue
        contexts.append(_enrich_with_tick_states(ctx, entry_vwap_dev_pct=None))

    policy_labels = [
        ("phase399_baseline", 399),
        ("phase402_best", 402),
        ("phase403_best", 403),
        ("phase404_best", 404),
        ("phase405_boundary", 405),
    ]

    per_policy_trades: dict[str, list[dict[str, Any]]] = {}
    for label, _phase in policy_labels:
        rows: list[dict[str, Any]] = []
        for ctx in contexts:
            sim = simulate_policy(ctx, policy_label=label, boundary_rules=boundary_rules)
            exit_ts = sim.get("shadow_exit_ts")
            if isinstance(exit_ts, datetime):
                exit_ts_val = exit_ts.timestamp()
            elif exit_ts is not None:
                try:
                    exit_ts_val = float(exit_ts)
                except (TypeError, ValueError):
                    exit_ts_val = None
            else:
                exit_ts_val = None
            shadow_hold = (
                max(0.0, exit_ts_val - float(ctx["entry_ts"]))
                if exit_ts_val and exit_ts_val > float(ctx["entry_ts"])
                else float(ctx.get("hold_sec") or 0)
            )
            rows.append(
                {
                    **ctx,
                    "shadow_pnl_yen_100": float(sim["shadow_pnl_yen_100"]),
                    "shadow_exit_reason": _normalize_shadow_exit(str(sim.get("shadow_exit_reason") or "")),
                    "shadow_hold_sec": round(shadow_hold, 2),
                    "shadow_exit_time": (
                        datetime.fromtimestamp(exit_ts_val, tz=JST).isoformat(timespec="seconds")
                        if exit_ts_val
                        else ctx.get("exit_time")
                    ),
                }
            )
        per_policy_trades[label] = rows

    baseline_pnls = [float(t["baseline_pnl_yen_100"]) for t in contexts]
    comparison_rows: list[dict[str, Any]] = []
    for label, phase in policy_labels:
        metrics = aggregate_portfolio_metrics(
            per_policy_trades[label],
            policy_label=label,
            baseline_pnls=baseline_pnls,
            p90_hold=p90_hold,
        )
        metrics["source_phase"] = phase
        comparison_rows.append(metrics)

    baseline_row = next(r for r in comparison_rows if r["policy_label"] == "phase399_baseline")
    enriched = [_vs_baseline(r, baseline_row) for r in comparison_rows]
    for row in enriched:
        row["tier"] = _classify_tier(row, baseline=baseline_row)

    shadow_only = [r for r in enriched if r["policy_label"] != "phase399_baseline"]
    shadow_only.sort(
        key=lambda r: (
            -float(r.get("risk_adjusted_score") or 0),
            -float(r.get("total_pnl_yen_100") or 0),
            float(r.get("max_drawdown_yen_100") or 1e18),
        )
    )

    ranking_rows: list[dict[str, Any]] = []
    rank = 1
    for row in shadow_only:
        ranking_rows.append(
            {
                "rank": rank,
                "policy_label": row["policy_label"],
                "source_phase": row["source_phase"],
                "tier": row["tier"],
                "recommendation": _recommendation_for_tier(str(row["tier"]), rank),
                "total_pnl_yen_100": row["total_pnl_yen_100"],
                "profit_factor": row["profit_factor"],
                "max_drawdown_yen_100": row["max_drawdown_yen_100"],
                "calmar_like": row["calmar_like"],
                "expectancy_yen_per_trade": row["expectancy_yen_per_trade"],
                "risk_adjusted_score": row["risk_adjusted_score"],
                "pnl_improvement_pct": row["pnl_improvement_pct"],
                "pf_improvement_pct": row["pf_improvement_pct"],
            }
        )
        rank += 1

    for row in enriched:
        if row["policy_label"] == "phase399_baseline":
            row["rank"] = 0
            row["recommendation"] = "baseline"
        else:
            match = next(r for r in ranking_rows if r["policy_label"] == row["policy_label"])
            row["rank"] = match["rank"]
            row["recommendation"] = match["recommendation"]

    best = ranking_rows[0] if ranking_rows else None
    best_full = next((r for r in enriched if best and r["policy_label"] == best["policy_label"]), None)

    mandatory_ranks = {
        f"rank_{i}": ranking_rows[i - 1]["policy_label"] if len(ranking_rows) >= i else None
        for i in range(1, 6)
    }

    top_rec = best["recommendation"] if best else "C_reject"
    verdict = "adopt_candidate" if top_rec == "A_adopt_candidate" else (
        "shadow_continue" if top_rec == "B_shadow_continue" else "reject"
    )

    summary = {
        "phase": 406,
        "generated_at": _now_iso(),
        "period_start": period_start,
        "period_end": period_end,
        "trade_count": len(contexts),
        "initial_equity_yen": INITIAL_EQUITY_YEN,
        "ranking": ranking_rows,
        "mandatory_ranks": mandatory_ranks,
        "baseline_comparison": {
            "baseline_pnl": baseline_row["total_pnl_yen_100"],
            "baseline_pf": baseline_row["profit_factor"],
            "baseline_maxdd": baseline_row["max_drawdown_yen_100"],
            "best_policy": best["policy_label"] if best else None,
            "best_pnl": best["total_pnl_yen_100"] if best else None,
            "best_pf": best["profit_factor"] if best else None,
            "best_maxdd": best["max_drawdown_yen_100"] if best else None,
            "pnl_improvement_pct": best_full.get("pnl_improvement_pct") if best_full else None,
            "pf_improvement_pct": best_full.get("pf_improvement_pct") if best_full else None,
            "maxdd_improvement_yen": best_full.get("maxdd_improvement_yen") if best_full else None,
        },
        "top_recommendation": top_rec,
        "verdict": verdict,
        "headline": (
            f"Phase406: #1 {best['policy_label']} ({best['tier']}) "
            f"PnL ¥{best['total_pnl_yen_100']} PF {best['profit_factor']} "
            f"Calmar {best['calmar_like']} → {top_rec}"
            if best
            else "Phase406: no ranking"
        ),
        "risk_adjusted_winner": best["policy_label"] if best else None,
        "reference_note": "long_hold_loser and per-symbol metrics excluded from tier gates",
    }

    comp_path = output_dir / "phase406_portfolio_adoption_comparison.csv"
    rank_path = output_dir / "phase406_portfolio_ranking.csv"
    _write_csv(comp_path, enriched, COMPARISON_FIELDS)
    _write_csv(rank_path, ranking_rows, RANKING_FIELDS)

    summary_path = output_dir / "phase406_portfolio_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    report_path = repo_root / "docs" / "operations" / "phase406_portfolio_adoption_report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(_render_report(summary), encoding="utf-8")

    return {
        "summary": summary,
        "comparison_path": str(comp_path),
        "ranking_path": str(rank_path),
        "summary_path": str(summary_path),
        "report_path": str(report_path),
    }
