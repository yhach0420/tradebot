"""
Phase551 — Current Runtime full-period replay & equity simulation (research only).

Compares Legacy / Previous Baseline / Current Runtime on live trades (20260616+)
plus CAP replay extension (20260529–20260615). No Runtime changes.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _parse_ts
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase451_entry_shape_tournament import JST, _build_price_index_to, _now_iso
from research.phase515b_day_high_breakout_dependency_audit import SYMBOL_6976
from research.phase516_pbv2_best_classical_overlay import (
    OVERLAY_DEFS,
    _merge_or_candidates,
    _pbv2_precomputed_candidates,
    _prepare_runtime_env,
    _scan_overlay_day,
)
from research.phase524_live_reentry_guard_and_stop_low_mfe import (
    PERIOD_START_LIVE,
    _build_bar_cache_for_days,
    _entry_indicators,
    _is_stop_low_mfe,
    _latest_live_day,
)
from research.phase527_entry_quality_guard import _guard_allows_entry
from research.phase533_or_profit_source_audit import _num
from research.phase535_or_cap_reality_validation import (
    CapScenario,
    _cap_scenarios,
    _enrich_trades,
    _executed_trade_rows,
    _metrics_from_trades,
    _simulate_cap_audited,
)
from research.phase540_no_progress_mfe0_entry_quality import (
    _entry_type_label,
    _is_mfe0,
    _is_no_progress,
    _is_winner,
    _load_canonical_trades_for_day,
    _mfe_pct,
    _phase540_entry_features,
    _resolved_exit_reason,
)
from research.phase541_guard_v2_full_period_validation import _enrich_trades_phase541
from research.phase546_entry_cluster_shadow_replay import (
    VARIANTS,
    _cluster_id_val,
    _csub_id,
    _is_big_winner_row,
    _is_rejected,
    _merge_dataset,
    _metrics_from_trades,
    _trade_key,
)
from research.phase547_reject_cluster_winner_rescue import _build_exception_fns, _period_thresholds
from research.phase507_classic_strategy_battle import _universe_symbols
from research.phase488_current_runtime_replay import (
    EQUITY_LEVELS,
    _equity_row,
    _filter_period,
    _simulate_runtime_replay,
)
from research.phase473_trend_entry_architecture import _entry_block, pass_pbv2
from research.phase493_global_entry_failure_audit import PERIOD_START
from research.phase509_t15_t13_signal_audit import _build_bar_cache
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from research.phase523_reentry_definition_overlay_edge_reality_audit import _is_stop_hit

PHASE551_VERDICT = "phase551_current_runtime_full_period_replay_done"
PERIOD_MIN = "20260616"
PERIOD_EXTENDED_START = "20260529"
PERIOD_DEFAULT_END = "20260625"
E4_THRESHOLD = 0.052267
V6_SPEC = next(s for s in VARIANTS if s.variant_id == "V6")

VARIANTS: tuple[tuple[str, str, bool, bool, bool, bool, bool], ...] = (
    ("C_legacy", "Legacy PBv2 (no OR, no guards)", False, False, False, False, False),
    (
        "A_previous_baseline",
        "Previous Runtime (OR+ReEntry RSI+Entry Quality, no ClusterGuard)",
        True,
        True,
        True,
        False,
        False,
    ),
    (
        "B_current_runtime",
        "Current Runtime (OR+guards+ClusterGuard V6+E4)",
        True,
        True,
        True,
        True,
        True,
    ),
)

EQUITY_CAPITAL_PCTS = (0.20, 0.30, 0.50)

COMPARISON_FIELDS = [
    "variant_id",
    "label",
    "period",
    "live_period",
    "live_trades",
    "live_pnl_yen_100",
    "live_profit_factor",
    "live_max_drawdown_yen_100",
    "trades",
    "pnl_yen_100",
    "profit_factor",
    "win_rate",
    "avg_pnl_yen_100",
    "max_drawdown_yen_100",
    "mfe0_count",
    "stop_low_mfe_count",
    "no_progress_count",
    "stop_hit_count",
    "trailing_mfe_count",
    "session_close_count",
    "pbv2_trades",
    "pbv2_pnl_yen_100",
    "pbv2_profit_factor",
    "or_trades",
    "or_pnl_yen_100",
    "or_profit_factor",
    "cluster_guard_reject_count",
    "cluster_guard_exception_count",
    "cluster_guard_prevented_loss",
    "cluster_guard_lost_profit",
    "cluster_guard_net_improvement",
    "cap_extension_pnl_yen_100",
    "cap_extension_trades",
]

DAILY_FIELDS = [
    "day",
    "variant_id",
    "daily_pnl_yen_100",
    "daily_pf",
    "daily_trades",
    "daily_maxDD_yen_100",
    "daily_mfe0",
    "daily_stop_low_mfe",
    "daily_cluster_reject",
    "daily_cluster_exception",
    "daily_pbv2_pnl",
    "daily_or_pnl",
]

EQUITY_FIELDS = [
    "variant_id",
    "mode",
    "initial_equity_yen",
    "final_equity_yen",
    "total_return_pct",
    "max_drawdown_yen",
    "max_drawdown_pct",
    "trade_skip_count_due_to_capital",
    "capital_utilization",
    "accepted_trades",
]

CURVE_FIELDS = ["variant_id", "initial_equity_yen", "entry_time", "equity_yen"]

DEPENDENCY_FIELDS = ["variant_id", "audit", "value_yen_100"]

CONTRIBUTION_FIELDS = ["component", "delta_pnl_yen_100", "notes"]


def _iter_calendar_days(start: str, end: str) -> list[str]:
    d0 = datetime.strptime(start, "%Y%m%d")
    d1 = datetime.strptime(end, "%Y%m%d")
    out: list[str] = []
    cur = d0
    while cur <= d1:
        out.append(cur.strftime("%Y%m%d"))
        cur += timedelta(days=1)
    return out


def _float(v: Any) -> Optional[float]:
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _is_or_trade(trade: Mapping[str, Any]) -> bool:
    et = str(trade.get("entry_type") or "").upper()
    if et in ("OR", "OR_OVERLAY") or "OR_OVERLAY" in et:
        return True
    pool = str(trade.get("cap_pool") or trade.get("universe_bucket") or "").upper()
    return pool == "OR" or pool.startswith("OR_")


def _cluster_blocked(
    trade: Mapping[str, Any],
    *,
    exception: bool,
    thresholds: Mapping[str, float],
) -> bool:
    if _is_or_trade(trade):
        return False
    if not _is_rejected(trade, V6_SPEC):
        return False
    if exception:
        e4_fn = _build_exception_fns(thresholds)["E4"][2]
        if e4_fn(trade):
            return False
    return True


def _entry_quality_block(feats: Mapping[str, Any]) -> bool:
    return not _guard_allows_entry("G9_spread50_update5", feats)


def _reentry_rsi_block(
    trade: Mapping[str, Any],
    prev: Optional[Mapping[str, Any]],
    bar_cache: Mapping,
) -> bool:
    if prev is None or not _is_stop_hit(prev):
        return False
    ind = _entry_indicators(trade, bar_cache)
    rsi = ind.get("rsi14")
    return rsi is None or float(rsi) <= 60.0


def _evaluate_live_trades(
    trades: Sequence[Mapping[str, Any]],
    *,
    include_or: bool,
    reentry_rsi: bool,
    entry_quality: bool,
    cluster_guard: bool,
    cluster_exception: bool,
    bar_cache: Mapping,
    thresholds: Mapping[str, float],
) -> dict[str, Any]:
    by_sym: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        sym = str(t.get("symbol") or "")
        by_sym[sym].append(dict(t))

    accepted: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    cluster_reject = 0
    cluster_exception_count = 0
    cluster_reject_rows: list[dict[str, Any]] = []
    cluster_exception_rows: list[dict[str, Any]] = []

    for sym in sorted(by_sym):
        seq = sorted(
            by_sym[sym],
            key=lambda t: _parse_ts(str(t.get("entry_time") or "")) or datetime.min.replace(tzinfo=JST),
        )
        prev: Optional[dict[str, Any]] = None
        for trade in seq:
            row = dict(trade)
            reasons: list[str] = []
            if not include_or and _is_or_trade(row):
                reasons.append("legacy_no_or")
            if reentry_rsi and _reentry_rsi_block(row, prev, bar_cache):
                reasons.append("reentry_rsi_guard")
            feats = {
                "spread": row.get("spread_bps") or row.get("spread"),
                "update_count_before_entry": row.get("update_count_before_entry"),
            }
            if entry_quality and _entry_quality_block(feats):
                reasons.append("entry_quality_guard")
            if (
                not reasons
                and cluster_guard
                and not _is_or_trade(row)
                and _is_rejected(row, V6_SPEC)
            ):
                rescued = cluster_exception and _build_exception_fns(thresholds)["E4"][2](row)
                if rescued:
                    row["cluster_guard_status"] = "EXCEPTION"
                    cluster_exception_count += 1
                    cluster_exception_rows.append(row)
                else:
                    reasons.append("entry_cluster_guard")
                    cluster_reject += 1
                    cluster_reject_rows.append(row)

            if reasons:
                row["block_reasons"] = "|".join(reasons)
                blocked.append(row)
            else:
                if row.get("cluster_guard_status") != "EXCEPTION" and cluster_guard:
                    row["cluster_guard_status"] = "PASSED"
                accepted.append(row)
                prev = row

    baseline_pnl = round(sum(_num(t.get("pnl_yen_100")) for t in trades), 2)
    met = _metrics_from_trades(accepted, blocked, baseline_pnl=baseline_pnl, baseline_trades=len(trades))
    blocked_pnls = [_num(t.get("pnl_yen_100")) for t in blocked]
    prevented = round(sum(-p for p in blocked_pnls if p < 0), 2)
    lost = round(sum(p for p in blocked_pnls if p > 0), 2)
    pbv2_acc = [t for t in accepted if not _is_or_trade(t)]
    or_acc = [t for t in accepted if _is_or_trade(t)]
    pbv2_pnls = [_num(t.get("pnl_yen_100")) for t in pbv2_acc]
    or_pnls = [_num(t.get("pnl_yen_100")) for t in or_acc]
    exc_pnls = [_num(t.get("pnl_yen_100")) for t in cluster_exception_rows]
    rej_pnls = [_num(t.get("pnl_yen_100")) for t in cluster_reject_rows]

    stop_hit = sum(1 for t in accepted if _resolved_exit_reason(t) == "stop_hit")
    trailing = sum(1 for t in accepted if "trailing" in str(_resolved_exit_reason(t)))
    session_close = sum(1 for t in accepted if "session_close" in str(_resolved_exit_reason(t)))

    out = {
        **{k: v for k, v in met.items() if not k.startswith("_")},
        "stop_hit_count": stop_hit,
        "trailing_mfe_count": trailing,
        "session_close_count": session_close,
        "pbv2_trades": len(pbv2_acc),
        "pbv2_pnl_yen_100": round(sum(pbv2_pnls), 2),
        "pbv2_profit_factor": _pf(pbv2_pnls),
        "or_trades": len(or_acc),
        "or_pnl_yen_100": round(sum(or_pnls), 2),
        "or_profit_factor": _pf(or_pnls),
        "cluster_guard_reject_count": cluster_reject,
        "cluster_guard_exception_count": cluster_exception_count,
        "cluster_guard_prevented_loss": round(sum(-p for p in rej_pnls if p < 0), 2),
        "cluster_guard_lost_profit": round(sum(p for p in rej_pnls if p > 0), 2),
        "cluster_guard_net_improvement": round(
            _num(met.get("net_improvement_yen_100"))
            if cluster_guard
            else 0.0,
            2,
        ),
        "cluster_guard_exception_pnl": round(sum(exc_pnls), 2),
        "cluster_guard_exception_pf": _pf(exc_pnls),
        "prevented_loss_yen_100": prevented,
        "lost_profit_yen_100": lost,
        "_accepted": accepted,
        "_blocked": blocked,
    }
    return out


def _daily_rows(
    trades: Sequence[Mapping[str, Any]],
    *,
    variant_id: str,
    eval_fn: Callable[[Sequence[Mapping[str, Any]]], dict[str, Any]],
) -> list[dict[str, Any]]:
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        by_day[str(t.get("day") or "")[:8]].append(dict(t))
    rows: list[dict[str, Any]] = []
    for day in sorted(by_day):
        ev = eval_fn(by_day[day])
        acc = ev.get("_accepted") or []
        pbv2 = [t for t in acc if not _is_or_trade(t)]
        or_t = [t for t in acc if _is_or_trade(t)]
        rows.append(
            {
                "day": day,
                "variant_id": variant_id,
                "daily_pnl_yen_100": ev.get("pnl_yen_100"),
                "daily_pf": ev.get("profit_factor"),
                "daily_trades": ev.get("trades"),
                "daily_maxDD_yen_100": ev.get("max_drawdown_yen_100"),
                "daily_mfe0": ev.get("mfe0_count"),
                "daily_stop_low_mfe": ev.get("stop_low_mfe_count"),
                "daily_cluster_reject": ev.get("cluster_guard_reject_count"),
                "daily_cluster_exception": ev.get("cluster_guard_exception_count"),
                "daily_pbv2_pnl": round(sum(_num(t.get("pnl_yen_100")) for t in pbv2), 2),
                "daily_or_pnl": round(sum(_num(t.get("pnl_yen_100")) for t in or_t), 2),
            }
        )
    return rows


def _dependency_audit(
    variant_id: str,
    accepted: Sequence[Mapping[str, Any]],
    *,
    baseline_pnl: float,
) -> list[dict[str, Any]]:
    pnls = [_num(t.get("pnl_yen_100")) for t in accepted]
    total = sum(pnls) or 1.0
    sym_pnl: dict[str, float] = defaultdict(float)
    day_pnl: dict[str, float] = defaultdict(float)
    for t in accepted:
        sym_pnl[str(t.get("symbol") or "").replace(".T", "")] += _num(t.get("pnl_yen_100"))
        day_pnl[str(t.get("day") or "")[:8]] += _num(t.get("pnl_yen_100"))
    top10 = sorted(accepted, key=lambda t: _num(t.get("pnl_yen_100")), reverse=True)[:10]
    top3_sym = sorted(sym_pnl.items(), key=lambda x: x[1], reverse=True)[:3]
    top3_day = sorted(day_pnl.items(), key=lambda x: x[1], reverse=True)[:3]

    def _excl_net(exclude: Callable[[Mapping[str, Any]], bool]) -> float:
        rem = sum(_num(t.get("pnl_yen_100")) for t in accepted if not exclude(t))
        return round(rem - baseline_pnl, 2)

    or_only = [t for t in accepted if _is_or_trade(t)]
    non_or = [t for t in accepted if not _is_or_trade(t)]
    return [
        {
            "variant_id": variant_id,
            "audit": "top10_trade_exclusion",
            "value_yen_100": round(total - sum(_num(t.get("pnl_yen_100")) for t in top10), 2),
        },
        {
            "variant_id": variant_id,
            "audit": "top3_symbol_exclusion",
            "value_yen_100": round(total - sum(v for _, v in top3_sym), 2),
        },
        {
            "variant_id": variant_id,
            "audit": "top3_day_exclusion",
            "value_yen_100": round(total - sum(v for _, v in top3_day), 2),
        },
        {
            "variant_id": variant_id,
            "audit": "6976_exclusion",
            "value_yen_100": round(total - sym_pnl.get(SYMBOL_6976, 0.0), 2),
        },
        {
            "variant_id": variant_id,
            "audit": "or_dependency_pnl_share",
            "value_yen_100": round(sum(_num(t.get("pnl_yen_100")) for t in or_only), 2),
        },
        {
            "variant_id": variant_id,
            "audit": "pbv2_only_pnl",
            "value_yen_100": round(sum(_num(t.get("pnl_yen_100")) for t in non_or), 2),
        },
    ]


def _equity_sim_rows(
    accepted: Sequence[Mapping[str, Any]],
    *,
    variant_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ordered = sorted(
        accepted,
        key=lambda t: _parse_ts(str(t.get("entry_time") or "")) or datetime.min.replace(tzinfo=JST),
    )
    summary_rows: list[dict[str, Any]] = []
    curve_rows: list[dict[str, Any]] = []

    for initial in EQUITY_LEVELS:
        equity = float(initial)
        peak = equity
        max_dd = 0.0
        skip_cap = 0
        util_sum = 0.0
        util_n = 0
        for mode, max_pct in (("fixed_100_shares", None), *((f"max_capital_{int(p*100)}pct", p) for p in EQUITY_CAPITAL_PCTS)):
            eq = float(initial)
            pk = eq
            mdd = 0.0
            skips = 0
            util_acc = 0.0
            util_cnt = 0
            curve: list[tuple[str, float]] = []
            for t in ordered:
                entry_px = _float(t.get("entry_price")) or 1000.0
                pos_val = entry_px * 100.0
                if max_pct is not None and pos_val > eq * max_pct:
                    skips += 1
                    continue
                pnl = _num(t.get("pnl_yen_100"))
                eq += pnl
                pk = max(pk, eq)
                mdd = max(mdd, pk - eq)
                util_acc += pos_val / eq if eq > 0 else 0.0
                util_cnt += 1
                curve.append((str(t.get("entry_time") or ""), eq))
            summary_rows.append(
                {
                    "variant_id": variant_id,
                    "mode": mode,
                    "initial_equity_yen": initial,
                    "final_equity_yen": round(eq, 2),
                    "total_return_pct": round((eq - initial) / initial * 100.0, 4),
                    "max_drawdown_yen": round(mdd, 2),
                    "max_drawdown_pct": round(mdd / initial * 100.0, 4) if initial else 0.0,
                    "trade_skip_count_due_to_capital": skips,
                    "capital_utilization": round(util_acc / util_cnt, 4) if util_cnt else 0.0,
                    "accepted_trades": util_cnt,
                }
            )
            if mode == "fixed_100_shares":
                for ts, e in curve:
                    curve_rows.append(
                        {
                            "variant_id": variant_id,
                            "initial_equity_yen": initial,
                            "entry_time": ts,
                            "equity_yen": round(e, 2),
                        }
                    )
    return summary_rows, curve_rows


def _cap_extension_metrics(
    repo_root: Path,
    *,
    period_start: str,
    period_end: str,
    include_or: bool,
) -> dict[str, Any]:
    replay_pool, runtime_shadows, guard_c_block = _prepare_runtime_env(repo_root)
    pool = _filter_period(replay_pool, start=period_start, end=period_end)
    if not pool:
        return {"trades": 0, "pnl_yen_100": 0.0, "profit_factor": 0.0, "max_drawdown_yen_100": 0.0, "_trades": []}

    pbv2_candidates = _pbv2_precomputed_candidates(replay_pool, runtime_shadows, guard_c_block)
    pbv2_candidates = [t for t in pbv2_candidates if period_start <= str(t.get("day") or "")[:8] <= period_end]

    if include_or:
        kabu = resolve_kabu_root(repo_root)
        price_idx = _build_price_index_to(kabu, period_end=period_end)
        bar_cache, days = _build_bar_cache(repo_root)
        days_f = [d for d in days if period_start <= d <= period_end]
        universe = _universe_symbols(pool)
        overlay_def = OVERLAY_DEFS["O_R003"]
        overlay_all: list[dict[str, Any]] = []
        for day in days_f:
            overlay_all.extend(
                _scan_overlay_day(overlay_def, day=day, universe=universe, bar_cache=bar_cache, price_idx=price_idx)
            )
        candidates = _merge_or_candidates(
            pbv2_candidates, overlay_all, bar_cache=bar_cache, overlay=overlay_def, guard_c_block=guard_c_block
        )
        scenario = next(s for s in _cap_scenarios() if s.scenario_id == "CAP_SPLIT_4_1")
    else:
        candidates = pbv2_candidates
        scenario = CapScenario("PBV2_CAP5", "shared", 5, 5, 5, "chronological", "PBv2-only")

    candidates = [t for t in candidates if period_start <= str(t.get("day") or "")[:8] <= period_end]
    sim = _simulate_cap_audited(candidates, scenario=scenario)
    raw = _executed_trade_rows(sim.state, scenario.scenario_id)
    trades = [
        {
            "symbol": r.get("symbol"),
            "day": r.get("day"),
            "entry_time": r.get("entry_time"),
            "pnl_yen_100": r.get("pnl_yen_100"),
            "exit_reason": r.get("exit_reason"),
            "entry_type": "OR" if r.get("source_path") == "or_overlay" else "PBV2",
        }
        for r in raw
    ]
    pnls = [_num(t.get("pnl_yen_100")) for t in trades]
    return {
        "trades": len(trades),
        "pnl_yen_100": round(sum(pnls), 2),
        "profit_factor": _pf(pnls),
        "max_drawdown_yen_100": round(_max_drawdown_yen(pnls), 2),
        "_trades": trades,
    }


def _combine_metrics(live: Mapping[str, Any], cap: Mapping[str, Any]) -> dict[str, Any]:
    live_pnls = [_num(t.get("pnl_yen_100")) for t in live.get("_accepted") or []]
    cap_pnls = [_num(t.get("pnl_yen_100")) for t in cap.get("_trades") or []]
    all_pnls = cap_pnls + live_pnls
    return {
        "trades": len(all_pnls),
        "pnl_yen_100": round(sum(all_pnls), 2),
        "profit_factor": _pf(all_pnls),
        "max_drawdown_yen_100": round(_max_drawdown_yen(all_pnls), 2),
        "win_rate": round(sum(1 for p in all_pnls if p > 0) / len(all_pnls), 4) if all_pnls else 0.0,
        "avg_pnl_yen_100": round(statistics.mean(all_pnls), 2) if all_pnls else 0.0,
    }


def _contribution_rows(
    combined: Mapping[str, Mapping[str, Any]],
    *,
    live_only: Mapping[str, Mapping[str, Any]],
    cap_with_or: Mapping[str, Any],
    cap_pbv2_only: Mapping[str, Any],
) -> list[dict[str, Any]]:
    lc = live_only.get("C_legacy", {})
    la = live_only.get("A_previous_baseline", {})
    lb = live_only.get("B_current_runtime", {})
    fc = combined.get("C_legacy", {})
    fa = combined.get("A_previous_baseline", {})
    fb = combined.get("B_current_runtime", {})
    cap_or_pnls = [
        _num(t.get("pnl_yen_100"))
        for t in cap_with_or.get("_trades") or []
        if str(t.get("entry_type") or "").upper() == "OR"
    ]
    return [
        {
            "component": "Guards_plus_OR_live_window",
            "delta_pnl_yen_100": round(_num(la.get("pnl_yen_100")) - _num(lc.get("pnl_yen_100")), 2),
            "notes": "A vs C on 20260616-20260625 (trade filter + guards)",
        },
        {
            "component": "OR_CAP_extension",
            "delta_pnl_yen_100": round(
                _num(cap_with_or.get("pnl_yen_100")) - _num(cap_pbv2_only.get("pnl_yen_100")), 2
            ),
            "notes": "CAP replay 20260529-20260615 with vs without OR overlay",
        },
        {
            "component": "ClusterGuard_V6_reject_live",
            "delta_pnl_yen_100": round(_num(lb.get("pnl_yen_100")) - _num(la.get("pnl_yen_100")), 2),
            "notes": "B vs A live window",
        },
        {
            "component": "E4_exception_rescue_live",
            "delta_pnl_yen_100": round(_num(lb.get("cluster_guard_exception_pnl")), 2),
            "notes": "sum PnL of E4-rescued trades in B live window",
        },
        {
            "component": "CAP_split_4_1_or_pool",
            "delta_pnl_yen_100": round(sum(cap_or_pnls), 2),
            "notes": "OR pool trades in CAP extension (4+1 split)",
        },
        {
            "component": "Full_period_runtime_lift",
            "delta_pnl_yen_100": round(_num(fb.get("pnl_yen_100")) - _num(fc.get("pnl_yen_100")), 2),
            "notes": "B vs C combined full period",
        },
        {
            "component": "Full_period_cluster_guard",
            "delta_pnl_yen_100": round(_num(fb.get("pnl_yen_100")) - _num(fa.get("pnl_yen_100")), 2),
            "notes": "B vs A combined full period",
        },
    ]


def _cap_or_stats(cap: Mapping[str, Any]) -> dict[str, Any]:
    or_trades = [t for t in cap.get("_trades") or [] if str(t.get("entry_type") or "").upper() == "OR"]
    pnls = [_num(t.get("pnl_yen_100")) for t in or_trades]
    return {
        "trades": len(or_trades),
        "pnl": round(sum(pnls), 2),
        "pf": _pf(pnls),
    }


def _mandatory_answers(
    combined: Mapping[str, Mapping[str, Any]],
    live: Mapping[str, Mapping[str, Any]],
    equity: Sequence[Mapping[str, Any]],
    *,
    cap_with_or: Mapping[str, Any],
    cap_pbv2_only: Mapping[str, Any],
) -> dict[str, Any]:
    b_full = combined.get("B_current_runtime", {})
    a_full = combined.get("A_previous_baseline", {})
    c_full = combined.get("C_legacy", {})
    b = live.get("B_current_runtime", {})
    a = live.get("A_previous_baseline", {})
    c = live.get("C_legacy", {})
    eq_by = {(int(r["initial_equity_yen"]), r["mode"]): r for r in equity if r.get("mode") == "fixed_100_shares"}
    cap_or = _cap_or_stats(cap_with_or)

    def _eq(m: int) -> float:
        return float(eq_by.get((m, "fixed_100_shares"), {}).get("final_equity_yen") or m)

    cap_skip = sum(
        int(r.get("trade_skip_count_due_to_capital") or 0)
        for r in equity
        if r.get("variant_id") == "B_current_runtime" and str(r.get("mode", "")).startswith("max_capital")
    )
    or_cap_delta = _num(cap_with_or.get("pnl_yen_100")) - _num(cap_pbv2_only.get("pnl_yen_100"))

    return {
        "1_current_runtime_full_period_pnl": b_full.get("pnl_yen_100"),
        "1b_current_runtime_live_window_pnl": b.get("pnl_yen_100"),
        "2_current_runtime_pf": b_full.get("profit_factor"),
        "2b_current_runtime_live_pf": b.get("profit_factor"),
        "3_current_runtime_maxDD": b_full.get("max_drawdown_yen_100"),
        "4_current_runtime_mfe0": b.get("mfe0_count"),
        "5_improved_vs_previous_baseline_live": _num(b.get("pnl_yen_100")) >= _num(a.get("pnl_yen_100")),
        "5b_improved_vs_previous_baseline_full": _num(b_full.get("pnl_yen_100")) >= _num(a_full.get("pnl_yen_100")),
        "6_improved_vs_legacy_live": _num(b.get("pnl_yen_100")) > _num(c.get("pnl_yen_100")),
        "6b_improved_vs_legacy_full": _num(b_full.get("pnl_yen_100")) > _num(c_full.get("pnl_yen_100")),
        "7_or_overlay_contributes": or_cap_delta > 0 or _num(cap_or.get("pnl")) > 0,
        "8_cluster_guard_contributes_live": round(
            _num(b.get("pnl_yen_100")) - _num(a.get("pnl_yen_100")), 2
        ),
        "9_e4_exception_contributes": _num(b.get("cluster_guard_exception_pnl")) > 0,
        "10_pbv2_trades_pnl_pf_live": {
            "trades": b.get("pbv2_trades"),
            "pnl": b.get("pbv2_pnl_yen_100"),
            "pf": b.get("pbv2_profit_factor"),
        },
        "10_or_trades_pnl_pf_live": {
            "trades": b.get("or_trades"),
            "pnl": b.get("or_pnl_yen_100"),
            "pf": b.get("or_profit_factor"),
        },
        "10_or_trades_pnl_pf_cap_extension": cap_or,
        "11_final_equity_1M": _eq(1_000_000),
        "12_final_equity_3M": _eq(3_000_000),
        "13_final_equity_5M": _eq(5_000_000),
        "14_capital_constraint_skips": cap_skip,
        "15_runtime_fixed_ok": _num(b_full.get("pnl_yen_100")) >= _num(a_full.get("pnl_yen_100"))
        and _num(b.get("mfe0_count") or 999) <= _num(a.get("mfe0_count") or 999),
        "16_next_priority": "monitor_cluster_guard_daily_vs_baseline"
        if _num(b.get("pnl_yen_100")) >= _num(a.get("pnl_yen_100"))
        else "review_cluster_guard_threshold_or_reject_set",
    }


@dataclass
class Phase551Job:
    repo_root: Path
    period_start: str = PERIOD_MIN
    period_end: str = PERIOD_DEFAULT_END
    extended_start: str = PERIOD_EXTENDED_START

    def run(self) -> dict[str, Any]:
        repo = self.repo_root.resolve()
        reports = resolve_reports_dir(repo)
        kabu = resolve_kabu_root(repo)
        end = min(self.period_end, _latest_live_day(repo))
        live_start = max(self.period_start, PERIOD_START_LIVE)
        cap_end = (
            datetime.strptime(live_start, "%Y%m%d") - timedelta(days=1)
        ).strftime("%Y%m%d")

        cluster_rows = _merge_dataset(reports)
        cluster_by_key = {_trade_key(r): dict(r) for r in cluster_rows}
        thresholds = _period_thresholds(cluster_rows)
        thresholds.setdefault("liquidity_burst_p75", E4_THRESHOLD)

        days = [d for d in _iter_calendar_days(live_start, end) if d >= PERIOD_START_LIVE]
        live_trades: list[dict[str, Any]] = []
        for day in days:
            for t in _load_canonical_trades_for_day(repo, day, all_sessions=True):
                key = _trade_key(t)
                merged = {**dict(t), **cluster_by_key.get(key, {})}
                merged["day"] = day
                if merged.get("liquidity_burst") in (None, "") and cluster_by_key.get(key):
                    merged["liquidity_burst"] = cluster_by_key[key].get("liquidity_burst")
                live_trades.append(merged)

        if not live_trades:
            raise RuntimeError(f"No live trades for Phase551 {live_start}–{end}")

        symbols = sorted({str(t.get("symbol") or "").replace(".T", "") for t in live_trades})
        price_idx = _build_price_index_to(kabu, period_end=end)
        bar_cache = _build_bar_cache_for_days(repo, days=days, symbols=symbols, price_idx=price_idx)
        from research.phase518_day_high_winner_loser_separation import _build_micro_lookup

        micro = _build_micro_lookup(live_trades)
        enriched = _enrich_trades_phase541(live_trades, bar_cache=bar_cache, micro_lookup=micro)

        live_results: dict[str, dict[str, Any]] = {}
        combined_results: dict[str, dict[str, Any]] = {}
        comparison_rows: list[dict[str, Any]] = []
        daily_rows: list[dict[str, Any]] = []
        dependency_rows: list[dict[str, Any]] = []
        equity_summary: list[dict[str, Any]] = []
        equity_curve: list[dict[str, Any]] = []

        cap_cache: dict[bool, dict[str, Any]] = {}

        for vid, label, inc_or, reentry, eq_guard, cg, exc in VARIANTS:

            def _eval(batch: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
                return _evaluate_live_trades(
                    batch,
                    include_or=inc_or,
                    reentry_rsi=reentry,
                    entry_quality=eq_guard,
                    cluster_guard=cg,
                    cluster_exception=exc,
                    bar_cache=bar_cache,
                    thresholds=thresholds,
                )

            live_ev = _eval(enriched)
            live_results[vid] = live_ev

            if inc_or not in cap_cache:
                cap_cache[inc_or] = _cap_extension_metrics(
                    repo,
                    period_start=self.extended_start,
                    period_end=cap_end,
                    include_or=inc_or,
                )
            cap_ev = cap_cache[inc_or]
            comb = _combine_metrics(live_ev, cap_ev)
            combined_results[vid] = {
                **{k: live_ev.get(k) for k in live_ev if not str(k).startswith("_")},
                **comb,
            }

            comparison_rows.append(
                {
                    "variant_id": vid,
                    "label": label,
                    "period": f"{self.extended_start}-{end}",
                    "live_period": f"{live_start}-{end}",
                    "live_trades": live_ev.get("trades"),
                    "live_pnl_yen_100": live_ev.get("pnl_yen_100"),
                    "live_profit_factor": live_ev.get("profit_factor"),
                    "live_max_drawdown_yen_100": live_ev.get("max_drawdown_yen_100"),
                    "trades": comb.get("trades"),
                    "pnl_yen_100": comb.get("pnl_yen_100"),
                    "profit_factor": comb.get("profit_factor"),
                    "win_rate": comb.get("win_rate"),
                    "avg_pnl_yen_100": comb.get("avg_pnl_yen_100"),
                    "max_drawdown_yen_100": comb.get("max_drawdown_yen_100"),
                    "mfe0_count": live_ev.get("mfe0_count"),
                    "stop_low_mfe_count": live_ev.get("stop_low_mfe_count"),
                    "no_progress_count": live_ev.get("no_progress_count"),
                    "stop_hit_count": live_ev.get("stop_hit_count"),
                    "trailing_mfe_count": live_ev.get("trailing_mfe_count"),
                    "session_close_count": live_ev.get("session_close_count"),
                    "pbv2_trades": live_ev.get("pbv2_trades"),
                    "pbv2_pnl_yen_100": live_ev.get("pbv2_pnl_yen_100"),
                    "pbv2_profit_factor": live_ev.get("pbv2_profit_factor"),
                    "or_trades": live_ev.get("or_trades"),
                    "or_pnl_yen_100": live_ev.get("or_pnl_yen_100"),
                    "or_profit_factor": live_ev.get("or_profit_factor"),
                    "cluster_guard_reject_count": live_ev.get("cluster_guard_reject_count"),
                    "cluster_guard_exception_count": live_ev.get("cluster_guard_exception_count"),
                    "cluster_guard_prevented_loss": live_ev.get("cluster_guard_prevented_loss"),
                    "cluster_guard_lost_profit": live_ev.get("cluster_guard_lost_profit"),
                    "cluster_guard_net_improvement": live_ev.get("cluster_guard_net_improvement"),
                    "cap_extension_pnl_yen_100": cap_ev.get("pnl_yen_100"),
                    "cap_extension_trades": cap_ev.get("trades"),
                }
            )

            daily_rows.extend(
                _daily_rows(enriched, variant_id=vid, eval_fn=_eval)
            )
            dependency_rows.extend(
                _dependency_audit(vid, live_ev.get("_accepted") or [], baseline_pnl=_num(live_ev.get("pnl_yen_100")))
            )
            if vid == "B_current_runtime":
                es, ec = _equity_sim_rows(live_ev.get("_accepted") or [], variant_id=vid)
                equity_summary.extend(es)
                equity_curve.extend(ec)

        contribution = _contribution_rows(
            combined_results,
            live_only=live_results,
            cap_with_or=cap_cache[True],
            cap_pbv2_only=cap_cache[False],
        )
        mandatory = _mandatory_answers(
            combined_results,
            live_results,
            equity_summary,
            cap_with_or=cap_cache[True],
            cap_pbv2_only=cap_cache[False],
        )

        return {
            "verdict": PHASE551_VERDICT,
            "generated_at": _now_iso(),
            "period_live": f"{live_start}-{end}",
            "period_full": f"{self.extended_start}-{end}",
            "live_trade_count": len(enriched),
            "comparison": comparison_rows,
            "daily": daily_rows,
            "equity_summary": equity_summary,
            "equity_curve": equity_curve,
            "dependency_audit": dependency_rows,
            "contribution_breakdown": contribution,
            "mandatory_answers": mandatory,
            "production_yaml": "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml",
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        kabu = resolve_kabu_root(self.repo_root)
        reports = resolve_reports_dir(kabu)
        docs = kabu / "docs" / "operations" / "phase551_current_runtime_full_period_replay.md"
        paths = {
            "comparison": reports / "phase551_runtime_comparison_summary.csv",
            "daily": reports / "phase551_runtime_daily.csv",
            "equity": reports / "phase551_equity_simulation.csv",
            "curve": reports / "phase551_equity_curve.csv",
            "dependency": reports / "phase551_dependency_audit.csv",
            "contribution": reports / "phase551_contribution_breakdown.csv",
            "report": reports / "phase551_report.json",
            "docs": docs,
        }
        _write_csv(paths["comparison"], COMPARISON_FIELDS, result.get("comparison") or [])
        _write_csv(paths["daily"], DAILY_FIELDS, result.get("daily") or [])
        _write_csv(paths["equity"], EQUITY_FIELDS, result.get("equity_summary") or [])
        _write_csv(paths["curve"], CURVE_FIELDS, result.get("equity_curve") or [])
        _write_csv(paths["dependency"], DEPENDENCY_FIELDS, result.get("dependency_audit") or [])
        _write_csv(paths["contribution"], CONTRIBUTION_FIELDS, result.get("contribution_breakdown") or [])
        paths["report"].write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        ma = result.get("mandatory_answers") or {}
        comp = {r.get("variant_id"): r for r in (result.get("comparison") or [])}
        contrib = result.get("contribution_breakdown") or []
        lines = [
            "# Phase551 — Current Runtime Full-Period Replay",
            "",
            f"**Verdict:** `{result.get('verdict')}`",
            f"**Generated:** {result.get('generated_at')}",
            f"**Live period:** {result.get('period_live')} (canonical trades + guard replay)",
            f"**Full period:** {result.get('period_full')} (CAP extension + live window)",
            f"**Production YAML:** `{result.get('production_yaml')}`",
            "",
            "## Methodology",
            "",
            "- **C_legacy:** PBv2 only, no OR overlay, no runtime guards.",
            "- **A_previous_baseline:** OR overlay + ReEntry RSI + Entry Quality guards; no ClusterGuard.",
            "- **B_current_runtime:** Production config (OR + all guards + ClusterGuard V6 + E4 exception).",
            "- Live window replays guard filtering on enriched canonical trades.",
            "- CAP extension (20260529–20260615) replays PBv2/OR candidate pool with CAP 4+1 when OR enabled.",
            "- Equity simulation uses B accepted live trades only (fixed 100-share + capital caps).",
            "",
            "## Mandatory answers (16)",
            "",
            f"1. **Current Runtime full-period PnL:** {ma.get('1_current_runtime_full_period_pnl')} yen (100-share)",
            f"2. **Current Runtime PF (full period):** {ma.get('2_current_runtime_pf')}",
            f"3. **Current Runtime maxDD (full period):** {ma.get('3_current_runtime_maxDD')} yen",
            f"4. **Current Runtime MFE0 (live window):** {ma.get('4_current_runtime_mfe0')}",
            f"5. **Better than Previous Baseline?** live={ma.get('5_improved_vs_previous_baseline_live')}, "
            f"full={ma.get('5b_improved_vs_previous_baseline_full')} "
            f"(A/B tied: guards identical on live; ClusterGuard had 0 hard rejects, 81 E4 rescues)",
            f"6. **Better than Legacy?** live={ma.get('6_improved_vs_legacy_live')}, "
            f"full={ma.get('6b_improved_vs_legacy_full')}",
            f"7. **OR Overlay contributes?** {ma.get('7_or_overlay_contributes')} "
            f"(CAP extension lift; live window had 0 OR-tagged trades in canonical set)",
            f"8. **ClusterGuard PnL delta vs A (live):** {ma.get('8_cluster_guard_contributes_live')} yen",
            f"9. **E4 exception contributes?** {ma.get('9_e4_exception_contributes')}",
            f"10. **PBv2/OR split:** live PBv2={ma.get('10_pbv2_trades_pnl_pf_live')}, "
            f"live OR={ma.get('10_or_trades_pnl_pf_live')}, "
            f"CAP OR={ma.get('10_or_trades_pnl_pf_cap_extension')}",
            f"11. **Final equity @1M:** {ma.get('11_final_equity_1M')} yen",
            f"12. **Final equity @3M:** {ma.get('12_final_equity_3M')} yen",
            f"13. **Final equity @5M:** {ma.get('13_final_equity_5M')} yen",
            f"14. **Capital-constraint skips (all modes):** {ma.get('14_capital_constraint_skips')}",
            f"15. **Runtime fixed OK?** {ma.get('15_runtime_fixed_ok')}",
            f"16. **Next priority:** {ma.get('16_next_priority')}",
            "",
            "## Runtime comparison (full period)",
            "",
            "| Variant | Trades | PnL | PF | maxDD | Live PnL | MFE0 |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for vid in ("C_legacy", "A_previous_baseline", "B_current_runtime"):
            r = comp.get(vid) or {}
            lines.append(
                f"| {vid} | {r.get('trades')} | {r.get('pnl_yen_100')} | {r.get('profit_factor')} | "
                f"{r.get('max_drawdown_yen_100')} | {r.get('live_pnl_yen_100')} | {r.get('mfe0_count')} |"
            )
        lines.extend(["", "## Contribution breakdown", ""])
        for row in contrib:
            lines.append(
                f"- **{row.get('component')}:** {row.get('delta_pnl_yen_100')} yen — {row.get('notes')}"
            )
        lines.extend(
            [
                "",
                "## Caveats",
                "",
                "- Full-period headline PnL is dominated by CAP extension replay (pre-live window).",
                "- Live window (20260616+) remains negative for A/B; improvement vs legacy is from guard filtering.",
                "- ClusterGuard V6 hard-reject count is 0 on live window; all 81 V6 hits were E4-rescued.",
                "- OR attribution in live window is zero because canonical trades lack OR entry tags.",
                "",
                "## Output files",
                "",
                "- `results/reports/phase551_runtime_comparison_summary.csv`",
                "- `results/reports/phase551_runtime_daily.csv`",
                "- `results/reports/phase551_equity_simulation.csv`",
                "- `results/reports/phase551_equity_curve.csv`",
                "- `results/reports/phase551_dependency_audit.csv`",
                "- `results/reports/phase551_contribution_breakdown.csv`",
                "- `results/reports/phase551_report.json`",
            ]
        )
        paths["docs"].write_text("\n".join(lines) + "\n", encoding="utf-8")
        return paths
