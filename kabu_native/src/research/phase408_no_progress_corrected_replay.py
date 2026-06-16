"""
Phase408: No Progress Exit corrected replay.

Shadow price path capped at baseline structural exit_time.
Research / shadow only — no Runtime / YAML / Entry / Exit changes.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import _pf, _win_rate
from research.phase382_capital_constrained_backtest import _parse_ts, _write_csv
from research.phase400_holding_time_audit import enrich_trade, load_phase399_trades
from research.phase402_time_decay_exit_shadow import (
    HARD_STOP_PCT,
    POLICY_MFE,
    PolicySpec,
    _max_drawdown_yen,
    _normalize_shadow_exit,
    _saved_lost_yen,
    simulate_time_decay_exit,
)
from research.phase403_gradual_time_decay_shadow import (
    POLICY_LINEAR,
    GradualPolicySpec,
    simulate_gradual_decay_exit,
)
from research.phase404_no_progress_exit_shadow import (
    GRID_FIELDS,
    NoProgressPolicySpec,
    TRADE_FIELDS,
    _baseline_row,
    _prepare_trade_context,
    _symbol_delta,
    build_tick_states,
    iter_policy_grid,
    no_progress_matches,
    simulate_no_progress_exit,
)
from research.phase406_portfolio_adoption import (
    INITIAL_EQUITY_YEN,
    PHASE402_BEST,
    PHASE403_BEST,
    PHASE404_BEST,
    RANKING_FIELDS,
    BoundaryBucketRule,
    _classify_tier,
    _recommendation_for_tier,
    _vs_baseline,
    aggregate_portfolio_metrics,
    load_phase405_boundary_policy,
    simulate_boundary_policy,
)
from research.phase407a_no_progress_lookahead_audit import (
    _find_exit_state,
    _recompute_peak_to_ts,
)

JST = ZoneInfo("Asia/Tokyo")
PERIOD_START = "20260529"
PERIOD_END = "20260615"

PHASE404_UNCORRECTED_NET_DELTA = 274912.4
PHASE407A_CAPPED_NET_DELTA = 67872.4
PHASE407A_CAPPED_TOLERANCE_YEN = 5000.0

SHADOW_TRIGGER_REASONS = frozenset(
    {
        "no_progress_exit",
        "stop_hit",
        "trailing_mfe_exit",
        "boundary_mfe_exit",
        "boundary_stop_exit",
        "boundary_trail_exit",
    }
)

AUDIT_FIELDS = [
    "day",
    "symbol",
    "entry_time",
    "baseline_exit_time",
    "shadow_exit_ts",
    "shadow_exit_reason",
    "post_baseline_violation",
    "peak_mfe_consistent",
    "exit_price_consistent",
    "pnl_at_exit_consistent",
    "single_exit_ok",
    "tick_sparse_at_hold",
]


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def baseline_cap_ts(ctx: Mapping[str, Any]) -> float:
    ex_dt = _parse_ts(str(ctx.get("exit_time") or ""))
    if ex_dt is None:
        return float(ctx["entry_ts"])
    return ex_dt.timestamp()


def cap_price_series(
    series: Sequence[tuple[float, float]],
    cap_ts: float,
) -> list[tuple[float, float]]:
    return [(ts, px) for ts, px in series if float(ts) <= cap_ts + 1e-6]


def prepare_corrected_trade_context(
    trade: Mapping[str, Any],
    *,
    repo_root: Path,
    session_cache: dict[str, Any],
    p90_hold: float,
) -> Optional[dict[str, Any]]:
    ctx = _prepare_trade_context(
        trade,
        repo_root=repo_root,
        session_cache=session_cache,
        p90_hold=p90_hold,
    )
    if ctx is None:
        return None
    cap_ts = baseline_cap_ts(ctx)
    capped_series = cap_price_series(ctx["price_series"], cap_ts)
    capped_states = build_tick_states(
        capped_series,
        entry_ts=float(ctx["entry_ts"]),
        entry_price=float(ctx["entry_price"]),
        session_end_ts=cap_ts,
        entry_vwap_dev_pct=_float_or_none(ctx.get("entry_vwap_dev_pct")),
    )
    return {
        **ctx,
        "baseline_cap_ts": cap_ts,
        "price_series": capped_series,
        "tick_states": capped_states,
        "session_end_ts": cap_ts,
    }


def _float_or_none(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def baseline_fallback_result(ctx: Mapping[str, Any]) -> dict[str, Any]:
    cap_ts = float(ctx["baseline_cap_ts"])
    return {
        "shadow_exit_reason": ctx.get("baseline_exit_reason") or "baseline",
        "shadow_exit_ts": cap_ts,
        "shadow_pnl_pct": None,
        "shadow_pnl_yen_100": float(ctx["baseline_pnl_yen_100"]),
        "shadow_exit_price": None,
        "used_baseline_fallback": True,
    }


def with_baseline_fallback(ctx: Mapping[str, Any], sim: Mapping[str, Any]) -> dict[str, Any]:
    reason = str(sim.get("shadow_exit_reason") or "")
    cap_ts = float(ctx["baseline_cap_ts"])
    exit_ts = float(sim.get("shadow_exit_ts") or cap_ts)
    if reason in SHADOW_TRIGGER_REASONS:
        return {**dict(sim), "used_baseline_fallback": False, "shadow_exit_ts": exit_ts}
    return baseline_fallback_result(ctx)


def simulate_corrected_no_progress(
    ctx: Mapping[str, Any],
    *,
    policy: NoProgressPolicySpec,
) -> dict[str, Any]:
    sim = simulate_no_progress_exit(
        ctx["tick_states"],
        entry_price=float(ctx["entry_price"]),
        entry_ts=float(ctx["entry_ts"]),
        session_end_ts=float(ctx["baseline_cap_ts"]),
        imb_pct=ctx.get("imb_pct"),
        policy=policy,
    )
    return with_baseline_fallback(ctx, sim)


def simulate_corrected_time_decay(
    ctx: Mapping[str, Any],
    *,
    policy: PolicySpec,
) -> dict[str, Any]:
    sim = simulate_time_decay_exit(
        ctx["price_series"],
        entry_ts=float(ctx["entry_ts"]),
        entry_price=float(ctx["entry_price"]),
        session_end_ts=float(ctx["baseline_cap_ts"]),
        imb_pct=ctx.get("imb_pct"),
        policy=policy,
    )
    return with_baseline_fallback(ctx, sim)


def simulate_corrected_gradual(
    ctx: Mapping[str, Any],
    *,
    policy: GradualPolicySpec,
) -> dict[str, Any]:
    sim = simulate_gradual_decay_exit(
        ctx["price_series"],
        entry_ts=float(ctx["entry_ts"]),
        entry_price=float(ctx["entry_price"]),
        session_end_ts=float(ctx["baseline_cap_ts"]),
        imb_pct=ctx.get("imb_pct"),
        policy=policy,
    )
    return with_baseline_fallback(ctx, sim)


def simulate_corrected_boundary(
    ctx: Mapping[str, Any],
    *,
    buckets: Mapping[int, BoundaryBucketRule],
) -> dict[str, Any]:
    sim = simulate_boundary_policy(
        ctx["tick_states"],
        entry_price=float(ctx["entry_price"]),
        entry_ts=float(ctx["entry_ts"]),
        imb_pct=ctx.get("imb_pct"),
        buckets=buckets,
    )
    return with_baseline_fallback(ctx, sim)


def audit_corrected_trade(
    ctx: Mapping[str, Any],
    sim: Mapping[str, Any],
    *,
    policy: Optional[NoProgressPolicySpec] = None,
) -> dict[str, Any]:
    cap_ts = float(ctx["baseline_cap_ts"])
    exit_ts = float(sim.get("shadow_exit_ts") or cap_ts)
    exit_reason = str(sim.get("shadow_exit_reason") or "")
    post_baseline = exit_ts > cap_ts + 1e-6

    peak_consistent = True
    price_consistent = True
    pnl_consistent = True
    single_exit_ok = True
    tick_sparse = False

    if policy is not None and exit_reason == "no_progress_exit" and not sim.get("used_baseline_fallback"):
        states = ctx["tick_states"]
        exit_state = _find_exit_state(states, exit_ts)
        if exit_state is not None:
            recomputed = _recompute_peak_to_ts(states, exit_ts)
            peak_consistent = abs(recomputed - float(exit_state["peak_mfe"])) < 0.001
            price_consistent = abs(float(exit_state["px"]) - float(sim.get("shadow_exit_price") or 0)) < 0.01
            pnl_consistent = abs(float(exit_state["pnl"]) - float(sim.get("shadow_pnl_pct") or 0)) < 0.001

        trigger_count = 0
        first_ts: Optional[float] = None
        for s in states:
            ts = float(s["ts"])
            if ts > exit_ts + 1e-6:
                break
            if no_progress_matches(s, policy):
                trigger_count += 1
                if first_ts is None:
                    first_ts = ts
        single_exit_ok = trigger_count <= 1 or (
            first_ts is not None and abs(first_ts - exit_ts) < 1.0
        )
        target = float(ctx["entry_ts"]) + policy.hold_sec
        tick_sparse = not any(abs(float(s["ts"]) - target) <= 60.0 for s in states)

    return {
        "day": ctx.get("day"),
        "symbol": ctx.get("symbol"),
        "entry_time": ctx.get("entry_time"),
        "baseline_exit_time": ctx.get("exit_time"),
        "shadow_exit_ts": exit_ts,
        "shadow_exit_reason": exit_reason,
        "post_baseline_violation": post_baseline,
        "peak_mfe_consistent": peak_consistent,
        "exit_price_consistent": price_consistent,
        "pnl_at_exit_consistent": pnl_consistent,
        "single_exit_ok": single_exit_ok,
        "tick_sparse_at_hold": tick_sparse,
    }


def aggregate_corrected_policy(
    trade_results: Sequence[Mapping[str, Any]],
    *,
    policy: NoProgressPolicySpec,
    p90_hold: float,
    baseline_metrics: Mapping[str, Any],
    audit_pass: bool,
) -> dict[str, Any]:
    from research.phase404_no_progress_exit_shadow import aggregate_policy_results

    row = aggregate_policy_results(
        trade_results,
        policy=policy,
        p90_hold=p90_hold,
        baseline_metrics=baseline_metrics,
    )
    baseline_total = float(baseline_metrics.get("total_pnl_yen_100") or 0.0)
    baseline_pf = float(baseline_metrics.get("profit_factor") or 0.0)
    baseline_dd = float(baseline_metrics.get("max_drawdown_yen_100") or 0.0)
    total_pnl = float(row["total_pnl_yen_100"])
    shadow_pf = float(row.get("profit_factor") or 0.0)
    max_dd = float(row.get("max_drawdown_yen_100") or 0.0)
    net_delta = float(row.get("net_delta_yen") or 0.0)

    row["adopt_candidate"] = bool(
        audit_pass
        and total_pnl > baseline_total
        and shadow_pf > baseline_pf
        and max_dd <= baseline_dd + 0.01
        and net_delta > 0
    )
    row["corrected_replay"] = True
    return row


def _portfolio_row_from_trades(
    trades: Sequence[Mapping[str, Any]],
    *,
    policy_label: str,
    source_phase: int,
    baseline_pnls: Sequence[float],
    p90_hold: float,
) -> dict[str, Any]:
    metrics = aggregate_portfolio_metrics(
        trades,
        policy_label=policy_label,
        baseline_pnls=baseline_pnls,
        p90_hold=p90_hold,
    )
    metrics["source_phase"] = source_phase
    return metrics


def run_phase408_corrected_replay(
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
        p90_hold = float(
            json.loads(p400_path.read_text(encoding="utf-8"))["hold_duration_sec"]["p90_hold_sec"]
        )

    raw = load_phase399_trades(trades_path)
    accepted = [
        enrich_trade(r)
        for r in raw
        if str(r.get("day") or "") >= period_start
        and str(r.get("day") or "") <= period_end
        and str(r.get("position_cap_accepted") or "").lower() in ("true", "1", "yes")
    ]

    policies = iter_policy_grid()
    boundary_rules = load_phase405_boundary_policy(phase405_policy_path)
    session_cache: dict[str, Any] = {}
    contexts: list[dict[str, Any]] = []

    for trade in accepted:
        trade["_p90_hold"] = p90_hold
        ctx = prepare_corrected_trade_context(
            trade,
            repo_root=repo_root,
            session_cache=session_cache,
            p90_hold=p90_hold,
        )
        if ctx is None:
            continue
        contexts.append(ctx)

    trade_results: list[dict[str, Any]] = []
    for ctx in contexts:
        shadow_by_policy: dict[str, dict[str, Any]] = {}
        for policy in policies:
            shadow_by_policy[policy.grid_key] = simulate_corrected_no_progress(ctx, policy=policy)
        trade_results.append({**ctx, "shadow_by_policy": shadow_by_policy})

    baseline_pnls = [float(t["baseline_pnl_yen_100"]) for t in trade_results]
    sort_keys = [
        (_parse_ts(str(t.get("exit_time") or "")) or datetime.min.replace(tzinfo=JST), i)
        for i, t in enumerate(trade_results)
    ]
    order = [i for _, i in sorted(sort_keys, key=lambda x: (x[0], x[1]))]
    baseline_metrics = {
        "total_pnl_yen_100": round(sum(baseline_pnls), 2),
        "profit_factor": _pf(baseline_pnls),
        "trade_count": len(trade_results),
        "win_rate": _win_rate(baseline_pnls),
        "max_drawdown_yen_100": _max_drawdown_yen([baseline_pnls[i] for i in order]),
        "long_hold_loser_count": sum(1 for t in trade_results if t.get("is_long_hold_loser")),
        "mfe_lt_0p5_loser_count": sum(1 for t in trade_results if t.get("is_mfe_lt_0p5_loser")),
        "final_equity_yen": round(INITIAL_EQUITY_YEN + sum(baseline_pnls), 2),
    }

    post_baseline_count = 0
    for ctx in trade_results:
        cap_ts = float(ctx["baseline_cap_ts"])
        for sim in ctx["shadow_by_policy"].values():
            if float(sim.get("shadow_exit_ts") or cap_ts) > cap_ts + 1e-6:
                post_baseline_count += 1

    audit_rows: list[dict[str, Any]] = []
    audit_policy = PHASE404_BEST
    audit_key = audit_policy.grid_key
    for ctx in trade_results:
        sim = ctx["shadow_by_policy"][audit_key]
        audit_rows.append(audit_corrected_trade(ctx, sim, policy=audit_policy))

    peak_violations = sum(1 for a in audit_rows if not a["peak_mfe_consistent"])
    price_violations = sum(1 for a in audit_rows if not a["exit_price_consistent"])
    pnl_violations = sum(1 for a in audit_rows if not a["pnl_at_exit_consistent"])
    multi_violations = sum(1 for a in audit_rows if not a["single_exit_ok"])
    tick_sparse_count = sum(1 for a in audit_rows if a.get("tick_sparse_at_hold"))

    replay_audit_pass = (
        post_baseline_count == 0
        and peak_violations == 0
        and price_violations == 0
        and pnl_violations == 0
        and multi_violations == 0
    )

    grid_rows: list[dict[str, Any]] = [_baseline_row(baseline_metrics)]
    for policy in policies:
        grid_rows.append(
            aggregate_corrected_policy(
                trade_results,
                policy=policy,
                p90_hold=p90_hold,
                baseline_metrics=baseline_metrics,
                audit_pass=replay_audit_pass,
            )
        )

    adopt_rows = [r for r in grid_rows if r.get("adopt_candidate")]
    adopt_rows.sort(key=lambda r: -float(r.get("net_delta_yen") or 0))
    ranked = sorted(
        [r for r in grid_rows if r.get("hold_sec_threshold") is not None],
        key=lambda r: -float(r.get("net_delta_yen") or 0),
    )
    best_adopt = adopt_rows[0] if adopt_rows else None
    best_overall = ranked[0] if ranked else None
    best = best_adopt or best_overall

    phase404_best_key = PHASE404_BEST.grid_key
    p404_corrected_row = next(
        (r for r in grid_rows if r.get("hold_sec_threshold") == PHASE404_BEST.hold_sec
         and r.get("max_mfe_pct_threshold") == PHASE404_BEST.max_mfe_pct
         and r.get("current_pnl_pct_threshold") == PHASE404_BEST.current_pnl_pct
         and r.get("high_update_mode") == PHASE404_BEST.high_update_mode
         and r.get("vwap_dev_mode") == PHASE404_BEST.vwap_dev_mode),
        None,
    )

    trade_rows: list[dict[str, Any]] = []
    if best:
        pk = (
            f"{best['hold_sec_threshold']}|{best['max_mfe_pct_threshold']}|"
            f"{best['current_pnl_pct_threshold']}|{best['high_update_mode']}|{best['vwap_dev_mode']}"
        )
        best_policy = next(p for p in policies if p.grid_key == pk)
        for t in trade_results:
            if not (t.get("focus_symbol") or t.get("is_long_hold_loser") or t.get("is_mfe_lt_0p5_loser")):
                continue
            sh = t["shadow_by_policy"][pk]
            from research.phase404_no_progress_exit_shadow import _trade_row

            trade_rows.append(
                _trade_row(
                    t,
                    best_policy,
                    sh["shadow_pnl_yen_100"],
                    _normalize_shadow_exit(str(sh.get("shadow_exit_reason") or "")),
                    shadow_exit_ts=sh.get("shadow_exit_ts"),
                )
            )

    per_policy_portfolio: dict[str, list[dict[str, Any]]] = {}

    def _build_portfolio_trades(
        sim_fn: Callable[[Mapping[str, Any]], dict[str, Any]],
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for ctx in contexts:
            sim = sim_fn(ctx)
            exit_ts = float(sim.get("shadow_exit_ts") or ctx["baseline_cap_ts"])
            rows.append(
                {
                    **ctx,
                    "shadow_pnl_yen_100": float(sim["shadow_pnl_yen_100"]),
                    "shadow_exit_reason": _normalize_shadow_exit(str(sim.get("shadow_exit_reason") or "")),
                    "shadow_hold_sec": round(max(0.0, exit_ts - float(ctx["entry_ts"])), 2),
                    "shadow_exit_time": datetime.fromtimestamp(exit_ts, tz=JST).isoformat(timespec="seconds"),
                }
            )
        return rows

    per_policy_portfolio["phase399_baseline"] = [
        {
            **ctx,
            "shadow_pnl_yen_100": float(ctx["baseline_pnl_yen_100"]),
            "shadow_exit_reason": ctx.get("baseline_exit_reason") or "baseline",
            "shadow_hold_sec": float(ctx.get("hold_sec") or 0),
            "shadow_exit_time": ctx.get("exit_time"),
        }
        for ctx in contexts
    ]
    per_policy_portfolio["phase402_corrected"] = _build_portfolio_trades(
        lambda c: simulate_corrected_time_decay(c, policy=PHASE402_BEST)
    )
    per_policy_portfolio["phase403_corrected"] = _build_portfolio_trades(
        lambda c: simulate_corrected_gradual(c, policy=PHASE403_BEST)
    )
    per_policy_portfolio["phase404_corrected"] = _build_portfolio_trades(
        lambda c: simulate_corrected_no_progress(c, policy=PHASE404_BEST)
    )
    per_policy_portfolio["phase405_corrected"] = _build_portfolio_trades(
        lambda c: simulate_corrected_boundary(c, buckets=boundary_rules)
    )

    portfolio_labels = [
        ("phase399_baseline", 399),
        ("phase402_corrected", 402),
        ("phase403_corrected", 403),
        ("phase404_corrected", 404),
        ("phase405_corrected", 405),
    ]
    comparison_rows: list[dict[str, Any]] = []
    for label, phase in portfolio_labels:
        comparison_rows.append(
            _portfolio_row_from_trades(
                per_policy_portfolio[label],
                policy_label=label,
                source_phase=phase,
                baseline_pnls=baseline_pnls,
                p90_hold=p90_hold,
            )
        )

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
    for rank, row in enumerate(shadow_only, start=1):
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
                "net_delta_yen": row["net_delta_yen"],
            }
        )

    best_portfolio = ranking_rows[0] if ranking_rows else None
    best_metrics = best or {}
    corrected_net_delta = float(best_metrics.get("net_delta_yen") or 0) if best else 0.0
    corrected_pf = float(best_metrics.get("profit_factor") or 0) if best else 0.0
    corrected_max_dd = float(best_metrics.get("max_drawdown_yen_100") or 0) if best else 0.0
    corrected_total = float(best_metrics.get("total_pnl_yen_100") or 0) if best else 0.0
    corrected_equity = round(INITIAL_EQUITY_YEN + corrected_total, 2)

    p404_uncorrected_delta = PHASE404_UNCORRECTED_NET_DELTA
    delta_vs_phase404 = round(corrected_net_delta - p404_uncorrected_delta, 2)
    p407a_match = p404_corrected_row is not None and abs(
        float(p404_corrected_row.get("net_delta_yen") or 0) - PHASE407A_CAPPED_NET_DELTA
    ) <= PHASE407A_CAPPED_TOLERANCE_YEN

    mandatory_answers = {
        "1_corrected_best_policy": {
            "hold_sec": best.get("hold_sec_threshold") if best else None,
            "max_mfe_pct": best.get("max_mfe_pct_threshold") if best else None,
            "current_pnl_pct": best.get("current_pnl_pct_threshold") if best else None,
            "high_update_mode": best.get("high_update_mode") if best else None,
            "vwap_dev_mode": best.get("vwap_dev_mode") if best else None,
            "adopt_candidate": best.get("adopt_candidate") if best else False,
        },
        "2_corrected_net_delta_yen": corrected_net_delta,
        "3_corrected_profit_factor": corrected_pf,
        "4_corrected_max_drawdown_yen_100": corrected_max_dd,
        "5_corrected_final_equity_yen": corrected_equity,
        "6_delta_vs_phase404_uncorrected_yen": delta_vs_phase404,
        "7_matches_phase407a_capped_delta": p407a_match,
        "phase404_best_policy_corrected_net_delta_yen": (
            float(p404_corrected_row.get("net_delta_yen") or 0) if p404_corrected_row else None
        ),
        "phase407a_capped_reference_yen": PHASE407A_CAPPED_NET_DELTA,
        "8_adopt_candidate": bool(best_adopt),
        "9_forward_shadow_continue": bool(not best_adopt or best_adopt),
    }

    summary = {
        "phase": 408,
        "generated_at": _now_iso(),
        "period_start": period_start,
        "period_end": period_end,
        "trade_count": len(trade_results),
        "price_path_rule": "entry_time to baseline structural exit_time only",
        "baseline": baseline_metrics,
        "replay_audit": {
            "status": "PASS" if replay_audit_pass else "FAIL",
            "post_baseline_usage_count": post_baseline_count,
            "peak_mfe_violations": peak_violations,
            "exit_price_violations": price_violations,
            "pnl_violations": pnl_violations,
            "multi_exit_violations": multi_violations,
            "tick_sparse_samples": tick_sparse_count,
        },
        "grid_row_count": len(grid_rows),
        "adopt_candidate_count": len(adopt_rows),
        "best_policy": best,
        "best_adopt_policy": best_adopt,
        "phase404_uncorrected_reference": {
            "net_delta_yen": p404_uncorrected_delta,
            "policy": {
                "hold_sec": PHASE404_BEST.hold_sec,
                "max_mfe_pct": PHASE404_BEST.max_mfe_pct,
                "current_pnl_pct": PHASE404_BEST.current_pnl_pct,
            },
        },
        "mandatory_answers": mandatory_answers,
        "portfolio_ranking": ranking_rows,
        "verdict": "adopt_candidate" if best_adopt else "forward_shadow_continue",
        "headline": _headline(best, best_adopt, corrected_net_delta, replay_audit_pass),
    }

    grid_path = output_dir / "phase408_no_progress_corrected_grid.csv"
    trades_path_out = output_dir / "phase408_no_progress_corrected_trades.csv"
    summary_path = output_dir / "phase408_no_progress_corrected_summary.json"
    rank_path = output_dir / "phase408_corrected_portfolio_ranking.csv"
    port_summary_path = output_dir / "phase408_corrected_portfolio_summary.json"

    _write_csv(grid_path, grid_rows, GRID_FIELDS)
    _write_csv(trades_path_out, trade_rows, TRADE_FIELDS)
    _write_csv(rank_path, ranking_rows, RANKING_FIELDS + ["net_delta_yen"])
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    port_summary = {
        "phase": "408_portfolio",
        "generated_at": summary["generated_at"],
        "trade_count": len(contexts),
        "initial_equity_yen": INITIAL_EQUITY_YEN,
        "ranking": ranking_rows,
        "baseline_comparison": {
            "baseline_pnl": baseline_row["total_pnl_yen_100"],
            "baseline_pf": baseline_row["profit_factor"],
            "baseline_maxdd": baseline_row["max_drawdown_yen_100"],
            "best_policy": best_portfolio["policy_label"] if best_portfolio else None,
            "best_pnl": best_portfolio["total_pnl_yen_100"] if best_portfolio else None,
            "best_pf": best_portfolio["profit_factor"] if best_portfolio else None,
            "best_maxdd": best_portfolio["max_drawdown_yen_100"] if best_portfolio else None,
        },
        "comparison_rows": enriched,
    }
    port_summary_path.write_text(json.dumps(port_summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    report_path = repo_root / "docs" / "operations" / "phase408_no_progress_corrected_replay_report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(_render_report(summary, port_summary, enriched), encoding="utf-8")

    return {
        "summary": summary,
        "grid_path": str(grid_path),
        "trades_path": str(trades_path_out),
        "summary_path": str(summary_path),
        "ranking_path": str(rank_path),
        "portfolio_summary_path": str(port_summary_path),
        "report_path": str(report_path),
    }


def _headline(
    best: Optional[Mapping[str, Any]],
    best_adopt: Optional[Mapping[str, Any]],
    net_delta: float,
    audit_pass: bool,
) -> str:
    if not best:
        return "Phase408: corrected replay — no results"
    tag = "ADOPT" if best_adopt else "shadow_continue"
    audit = "audit_PASS" if audit_pass else "audit_FAIL"
    return (
        f"Phase408 corrected: hold={best.get('hold_sec_threshold')}s "
        f"mfe<{best.get('max_mfe_pct_threshold')}% pnl<{best.get('current_pnl_pct_threshold')}% "
        f"delta=¥{net_delta} {tag} {audit}"
    )


def _render_report(
    summary: Mapping[str, Any],
    port_summary: Mapping[str, Any],
    comparison: Sequence[Mapping[str, Any]],
) -> str:
    ma = summary.get("mandatory_answers") or {}
    best = summary.get("best_policy") or {}
    baseline = summary.get("baseline") or {}
    audit = summary.get("replay_audit") or {}
    bp = ma.get("1_corrected_best_policy") or {}

    lines = [
        "# Phase408 — No Progress Exit Corrected Replay",
        "",
        f"Generated: {summary.get('generated_at')}",
        f"Verdict: **{summary.get('verdict')}**",
        "",
        summary.get("headline") or "",
        "",
        "## Price path rule",
        "",
        f"- {summary.get('price_path_rule')}",
        "- If no shadow rule fires before baseline exit → baseline PnL",
        "- Post-baseline candidate prices are **forbidden**",
        "",
        "## Mandatory answers",
        "",
        f"1. **Corrected best policy:** hold={bp.get('hold_sec')}s max_mfe<{bp.get('max_mfe_pct')}% "
        f"pnl<{bp.get('current_pnl_pct')}% hi={bp.get('high_update_mode')} vwap={bp.get('vwap_dev_mode')}",
        f"2. **Corrected net_delta:** ¥{ma.get('2_corrected_net_delta_yen')}",
        f"3. **Corrected PF:** {ma.get('3_corrected_profit_factor')}",
        f"4. **Corrected maxDD:** ¥{ma.get('4_corrected_max_drawdown_yen_100')}",
        f"5. **Corrected final_equity:** ¥{ma.get('5_corrected_final_equity_yen')}",
        f"6. **vs Phase404 uncorrected:** ¥{ma.get('6_delta_vs_phase404_uncorrected_yen')} "
        f"(Phase404 was ¥{PHASE404_UNCORRECTED_NET_DELTA})",
        f"7. **Phase407A capped match:** {ma.get('7_matches_phase407a_capped_delta')} "
        f"(404-best corrected ¥{ma.get('phase404_best_policy_corrected_net_delta_yen')} vs "
        f"407A ref ¥{ma.get('phase407a_capped_reference_yen')})",
        f"8. **Adopt candidate:** {ma.get('8_adopt_candidate')}",
        f"9. **Forward shadow continue:** {ma.get('9_forward_shadow_continue')}",
        "",
        "## Replay audit",
        "",
        f"- Status: **{audit.get('status')}**",
        f"- post_baseline_usage_count: {audit.get('post_baseline_usage_count')}",
        f"- peak_mfe / price / pnl / multi-exit violations: "
        f"{audit.get('peak_mfe_violations')} / {audit.get('exit_price_violations')} / "
        f"{audit.get('pnl_violations')} / {audit.get('multi_exit_violations')}",
        f"- tick_sparse_samples: {audit.get('tick_sparse_samples')}",
        "",
        "## Baseline",
        "",
        f"| total_pnl | ¥{baseline.get('total_pnl_yen_100')} |",
        f"| PF | {baseline.get('profit_factor')} |",
        f"| maxDD | ¥{baseline.get('max_drawdown_yen_100')} |",
        "",
        "## Corrected portfolio ranking (Phase406 redo)",
        "",
        "| Rank | Policy | Tier | Rec | PnL | PF | maxDD | net_delta |",
        "|------|--------|------|-----|-----|----|-------|-----------|",
    ]
    for r in summary.get("portfolio_ranking") or []:
        lines.append(
            f"| {r.get('rank')} | {r.get('policy_label')} | {r.get('tier')} | "
            f"{r.get('recommendation')} | ¥{r.get('total_pnl_yen_100')} | "
            f"{r.get('profit_factor')} | ¥{r.get('max_drawdown_yen_100')} | ¥{r.get('net_delta_yen')} |"
        )

    lines.extend(
        [
            "",
            "## Conclusion",
            "",
            "Phase404 +¥274,912 used post-baseline prices and must not drive adoption. "
            "Corrected replay caps the path at baseline exit; use corrected metrics for decisions.",
            "",
            "- Runtime / YAML / Entry / Exit / Discord unchanged",
            "",
        ]
    )
    return "\n".join(lines)
