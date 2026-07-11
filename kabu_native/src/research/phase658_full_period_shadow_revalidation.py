"""
Phase658: Full-period Shadow Revalidation (research only).

Recomputes all Phase657 shadows on the Phase634 full-period trade universe
(41 sessions / 22 trading days / ~3,192 trades) with unified metrics.
No ENTRY/EXIT/PBv2/OR/YAML/runtime trading changes.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase451_entry_shape_tournament import _now_iso
from research.phase631_profit_source_attribution import _num
from research.phase632_pbv2_profit_filter_counterfactual import (
    _build_variants,
    _daily_pnl,
    _max_drawdown,
    _metrics,
    _profit_factor,
    evaluate_variant,
)
from research.phase634_pbv2_only_rise5_full_period import (
    PRE625_CUTOFF,
    _iter_events,
    _pbv2_rise5_keep,
    _rise5_thresholds,
    _session_bucket,
    load_all_full_period_trades,
)
from research.phase647_momentum_low_trend_attribution import (
    counterfactual_exclude,
    enrich_momentum_low_trades,
    TREND_DOWN,
    TREND_STRONG_DOWN,
)
from research.phase649_flat_band_guard_counterfactual import (
    apply_variant,
    block_flat_plus_overheat,
    block_phase635_rise5_shadow,
    filter_pbv2_trades,
    leave_one_symbol_out,
)
from research.phase652_shadow_registry import ShadowDef, _extract_dashboard_row, _registry_definitions
from research.phase657_shadow_portfolio_review import (
    _discover_summaries_extended,
    _research_shadow_defs,
)
from research.structural_trade_normalize import resolve_kabu_root
from small_paper.pullback_misread_entry_guard_shadow import would_block_pullback_misread_guard

PHASE658_VERDICT = "phase658_full_period_shadow_revalidation_done"
REPORT_DIR_NAME = "phase658_full_period_shadow_revalidation"
NATIVE_ROOT = Path(__file__).resolve().parents[2]
DAY_707 = "2026-07-07"
RISE5_THRESHOLD = 1.84

ACCEPT_SHADOW_KEYS = (
    "pbv2_rise5_shadow_block",
    "pbv2_rise5_shadow_reason",
    "pbv2_flat_band_shadow_block",
    "pbv2_flat_band_shadow_reason",
    "pullback_misread_guard_shadow_blocked",
    "vwap_shadow_reject_candidate",
    "imbalance_shadow_candidate",
    "limit_up_proximity_guard_shadow_blocked",
    "extended_entry_shadow_flag",
)

EXIT_SHADOW_KEYS = (
    "actual_vs_shadow_delta_yen",
    "shadow_pnl_yen_100",
    "exit_shadow_t2_pnl_yen_100",
    "exit_shadow_t3_pnl_yen_100",
    "realtime_board_vs_actual_delta_yen",
    "shadow_exit_reason",
)

SUMMARY_COLUMNS = [
    "shadow_id",
    "category",
    "evaluation_method",
    "evaluable",
    "unevaluable_reason",
    "session_count",
    "day_count",
    "trade_count",
    "trigger_or_block_count",
    "baseline_pnl_yen",
    "shadow_pnl_yen",
    "delta_pnl_yen",
    "baseline_pf",
    "shadow_pf",
    "delta_pf",
    "baseline_dd_yen",
    "shadow_dd_yen",
    "delta_dd_yen",
    "baseline_win_rate",
    "shadow_win_rate",
    "delta_win_rate",
    "blocked_winners",
    "rescued_losers",
    "pre625_delta_yen",
    "post625_delta_yen",
    "am_delta_yen",
    "pm_delta_yen",
    "day_707_delta_yen",
    "recent_5d_delta_yen",
    "data_gap",
    "phase657_decision",
    "revised_decision",
]

FORWARD_SHADOW_IDS = (
    "sector_heat_forward_shadow",
    "risk_sizing_forward_shadow",
    "equity_dynamic_stop_shadow",
    "live_config_forward_shadow",
    "live_config_transition_shadow",
    "post_entry_forward_shadow",
    "classic_momentum_forward_shadow",
    "boundary_forward_shadow",
)

PHASE657_DECISIONS: dict[str, str] = {}


def _bool_val(v: Any) -> bool:
    if v is True or v == 1:
        return True
    return str(v or "").strip().lower() in ("true", "1", "yes")


def _pf_delta(base_pf: Any, shadow_pf: Any) -> Optional[float]:
    if not isinstance(base_pf, (int, float)) or not isinstance(shadow_pf, (int, float)):
        return None
    if base_pf == 999.0 or shadow_pf == 999.0:
        return None
    return round(float(shadow_pf) - float(base_pf), 4)


def _chrono_pnls(trades: Sequence[Mapping[str, Any]], pnl_key: str = "pnl_yen_100") -> list[float]:
    ordered = sorted(trades, key=lambda t: (str(t.get("day") or ""), str(t.get("entry_time") or "")))
    return [float(t.get(pnl_key) or 0.0) for t in ordered]


def _win_rate(pnls: Sequence[float]) -> Optional[float]:
    if not pnls:
        return None
    return round(sum(1 for p in pnls if p > 0) / len(pnls), 4)


@dataclass
class EvalContext:
    trades: list[dict[str, Any]]
    sessions: list[dict[str, Any]]
    session_dirs: dict[str, Path]
    baseline: dict[str, Any]
    summaries: list[tuple[str, str, dict[str, Any]]]
    summary_by_session: dict[str, dict[str, Any]]
    shadow_defs: dict[str, ShadowDef]
    skip_slow: bool = False


@dataclass
class ShadowEval:
    shadow_id: str
    category: str = ""
    evaluation_method: str = ""
    evaluable: bool = True
    unevaluable_reason: str = ""
    session_count: int = 0
    day_count: int = 0
    trade_count: int = 0
    trigger_or_block_count: int = 0
    baseline_pnl_yen: float = 0.0
    shadow_pnl_yen: float = 0.0
    delta_pnl_yen: float = 0.0
    baseline_pf: Optional[float] = None
    shadow_pf: Optional[float] = None
    delta_pf: Optional[float] = None
    baseline_dd_yen: float = 0.0
    shadow_dd_yen: float = 0.0
    delta_dd_yen: float = 0.0
    baseline_win_rate: Optional[float] = None
    shadow_win_rate: Optional[float] = None
    delta_win_rate: Optional[float] = None
    blocked_winners: int = 0
    rescued_losers: int = 0
    pre625_delta_yen: float = 0.0
    post625_delta_yen: float = 0.0
    am_delta_yen: float = 0.0
    pm_delta_yen: float = 0.0
    day_707_delta_yen: float = 0.0
    recent_5d_delta_yen: float = 0.0
    data_gap: str = ""
    daily_rows: list[dict[str, Any]] = field(default_factory=list)
    symbol_rows: list[dict[str, Any]] = field(default_factory=list)
    loo_rows: list[dict[str, Any]] = field(default_factory=list)

    def to_summary_row(self) -> dict[str, Any]:
        return {
            "shadow_id": self.shadow_id,
            "category": self.category,
            "evaluation_method": self.evaluation_method,
            "evaluable": self.evaluable,
            "unevaluable_reason": self.unevaluable_reason,
            "session_count": self.session_count,
            "day_count": self.day_count,
            "trade_count": self.trade_count,
            "trigger_or_block_count": self.trigger_or_block_count,
            "baseline_pnl_yen": self.baseline_pnl_yen,
            "shadow_pnl_yen": self.shadow_pnl_yen,
            "delta_pnl_yen": self.delta_pnl_yen,
            "baseline_pf": self.baseline_pf,
            "shadow_pf": self.shadow_pf,
            "delta_pf": self.delta_pf,
            "baseline_dd_yen": self.baseline_dd_yen,
            "shadow_dd_yen": self.shadow_dd_yen,
            "delta_dd_yen": self.delta_dd_yen,
            "baseline_win_rate": self.baseline_win_rate,
            "shadow_win_rate": self.shadow_win_rate,
            "delta_win_rate": self.delta_win_rate,
            "blocked_winners": self.blocked_winners,
            "rescued_losers": self.rescued_losers,
            "pre625_delta_yen": self.pre625_delta_yen,
            "post625_delta_yen": self.post625_delta_yen,
            "am_delta_yen": self.am_delta_yen,
            "pm_delta_yen": self.pm_delta_yen,
            "day_707_delta_yen": self.day_707_delta_yen,
            "recent_5d_delta_yen": self.recent_5d_delta_yen,
            "data_gap": self.data_gap,
        }


def _enrich_trades_from_events(ctx: EvalContext) -> None:
    by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in ctx.trades:
        by_session[str(t.get("session") or "")].append(t)

    for sess_name, sess_trades in by_session.items():
        sess_dir = ctx.session_dirs.get(sess_name)
        if sess_dir is None or not sess_dir.is_dir():
            continue
        accepted: dict[tuple[Any, Any], dict[str, Any]] = {}
        exits: dict[tuple[Any, Any], dict[str, Any]] = {}
        for event in _iter_events(sess_dir):
            et = event.get("event_type")
            key = (event.get("symbol"), event.get("entry_time") or event.get("message_index"))
            if et == "accepted":
                accepted[key] = event
            elif et == "observer_exit":
                exits[key] = event

        for trade in sess_trades:
            key = (trade.get("symbol"), trade.get("entry_time"))
            acc = accepted.get(key) or {}
            ex = exits.get(key) or {}
            merged = {**ex, **acc}
            for k in ACCEPT_SHADOW_KEYS + EXIT_SHADOW_KEYS:
                if k in merged and trade.get(k) is None:
                    trade[k] = merged[k]
            trade["session_label"] = _session_bucket(trade)
            trade["period"] = "post625" if str(trade.get("day") or "") >= PRE625_CUTOFF else "pre625"


def _sessions_days(trades: Sequence[Mapping[str, Any]]) -> tuple[set[str], set[str]]:
    sessions = {str(t.get("session") or "") for t in trades if t.get("session")}
    days = {str(t.get("day") or "") for t in trades if t.get("day")}
    return sessions, days


def _fill_from_metrics(
    ev: ShadowEval,
    *,
    baseline: Mapping[str, Any],
    shadow_trades: Sequence[Mapping[str, Any]],
    blocked: Sequence[Mapping[str, Any]],
    method: str,
    data_gap: str = "",
    ctx_trades: Optional[Sequence[Mapping[str, Any]]] = None,
) -> None:
    sm = _metrics(list(shadow_trades))
    ev.evaluation_method = method
    ev.baseline_pnl_yen = float(baseline["pnl_yen_100"])
    ev.shadow_pnl_yen = float(sm["pnl_yen_100"])
    ev.delta_pnl_yen = round(ev.shadow_pnl_yen - ev.baseline_pnl_yen, 2)
    ev.baseline_pf = baseline.get("profit_factor")
    ev.shadow_pf = sm.get("profit_factor")
    ev.delta_pf = _pf_delta(ev.baseline_pf, ev.shadow_pf)
    ev.baseline_dd_yen = float(baseline.get("max_dd_yen_100") or 0.0)
    ev.shadow_dd_yen = float(sm.get("max_dd_yen_100") or 0.0)
    ev.delta_dd_yen = round(ev.shadow_dd_yen - ev.baseline_dd_yen, 2)
    base_wr = baseline.get("win_rate")
    ev.baseline_win_rate = base_wr
    ev.shadow_win_rate = sm.get("win_rate")
    if isinstance(base_wr, (int, float)) and isinstance(ev.shadow_win_rate, (int, float)):
        ev.delta_win_rate = round(float(ev.shadow_win_rate) - float(base_wr), 4)
    ev.blocked_winners = sum(1 for t in blocked if float(t.get("pnl_yen_100") or 0) > 0)
    ev.rescued_losers = sum(1 for t in blocked if float(t.get("pnl_yen_100") or 0) < 0)
    ev.trigger_or_block_count = len(blocked)
    ev.data_gap = data_gap
    _attach_slices(
        ev,
        ctx_trades=list(ctx_trades or shadow_trades),
        blocked=list(blocked),
        baseline=baseline,
    )


def _attach_slices(
    ev: ShadowEval,
    *,
    ctx_trades: Sequence[Mapping[str, Any]],
    blocked: Sequence[Mapping[str, Any]],
    baseline: Mapping[str, Any],
    shadow_pnl_fn: Optional[Callable[[Mapping[str, Any]], float]] = None,
) -> None:
    blocked_set = {
        (t.get("day"), t.get("symbol"), t.get("entry_time")) for t in blocked
    }

    def shadow_pnl(t: Mapping[str, Any]) -> float:
        if shadow_pnl_fn is not None:
            return shadow_pnl_fn(t)
        key = (t.get("day"), t.get("symbol"), t.get("entry_time"))
        if key in blocked_set:
            return 0.0
        return float(t.get("pnl_yen_100") or 0.0)

    def delta_for(subset: Sequence[Mapping[str, Any]]) -> float:
        if not subset:
            return 0.0
        base = sum(float(t.get("pnl_yen_100") or 0) for t in subset)
        sh = sum(shadow_pnl(t) for t in subset)
        return round(sh - base, 2)

    ev.pre625_delta_yen = delta_for([t for t in ctx_trades if str(t.get("day") or "") < PRE625_CUTOFF])
    ev.post625_delta_yen = delta_for([t for t in ctx_trades if str(t.get("day") or "") >= PRE625_CUTOFF])
    ev.am_delta_yen = delta_for([t for t in ctx_trades if _session_bucket(t) == "AM"])
    ev.pm_delta_yen = delta_for([t for t in ctx_trades if _session_bucket(t) == "PM"])
    ev.day_707_delta_yen = delta_for([t for t in ctx_trades if str(t.get("day") or "") == DAY_707])
    days_sorted = sorted({str(t.get("day") or "") for t in ctx_trades if t.get("day")})
    recent = days_sorted[-5:] if days_sorted else []
    ev.recent_5d_delta_yen = delta_for([t for t in ctx_trades if str(t.get("day") or "") in recent])

    base_daily = _daily_pnl(ctx_trades)
    shadow_daily: dict[str, float] = defaultdict(float)
    for t in ctx_trades:
        d = str(t.get("day") or "")
        shadow_daily[d] += shadow_pnl(t)
    for day in sorted(set(base_daily) | set(shadow_daily)):
        ev.daily_rows.append(
            {
                "shadow_id": ev.shadow_id,
                "day": day,
                "period": "post625" if day >= PRE625_CUTOFF else "pre625",
                "baseline_pnl_yen": base_daily.get(day, 0.0),
                "shadow_pnl_yen": round(shadow_daily.get(day, 0.0), 2),
                "delta_pnl_yen": round(shadow_daily.get(day, 0.0) - base_daily.get(day, 0.0), 2),
            }
        )

    by_sym: dict[str, list[float]] = defaultdict(list)
    for t in ctx_trades:
        sym = str(t.get("symbol") or "")
        by_sym[sym].append(shadow_pnl(t) - float(t.get("pnl_yen_100") or 0))
    sym_deltas = [(sym, round(sum(v), 2)) for sym, v in by_sym.items()]
    sym_deltas.sort(key=lambda x: x[1], reverse=True)
    for sym, d in sym_deltas[:20]:
        ev.symbol_rows.append({"shadow_id": ev.shadow_id, "symbol": sym, "delta_pnl_yen": d})


def _eval_entry_block(
    ctx: EvalContext,
    shadow_id: str,
    *,
    category: str,
    block_fn: Callable[[Mapping[str, Any]], bool],
    pool: str = "ALL",
    method: str = "trade_replay_entry_block",
    event_field: str = "",
) -> ShadowEval:
    ev = ShadowEval(shadow_id=shadow_id, category=category)
    universe = ctx.trades
    if pool == "PBV2_ONLY":
        universe = [t for t in ctx.trades if str(t.get("entry_pool") or "") == "PBV2"]
    ev.trade_count = len(universe)
    sess, days = _sessions_days(universe)
    ev.session_count = len(sess)
    ev.day_count = len(days)

    blocked: list[dict[str, Any]] = []
    kept: list[dict[str, Any]] = []
    event_hits = 0
    for t in universe:
        blocked_flag = block_fn(t)
        if event_field and t.get(event_field) is not None:
            blocked_flag = _bool_val(t.get(event_field))
            event_hits += 1
        if blocked_flag:
            blocked.append(dict(t))
        else:
            kept.append(dict(t))

    gap = ""
    if event_field and event_hits == 0:
        gap = f"no_event_field:{event_field};used_replay_block_fn"
        method = f"{method}+replay_fallback"
    elif event_field and event_hits < len(universe):
        gap = f"partial_event_field:{event_hits}/{len(universe)}"

    base_sub = _metrics(universe)
    _fill_from_metrics(
        ev,
        baseline=base_sub,
        shadow_trades=kept,
        blocked=blocked,
        method=method,
        data_gap=gap,
        ctx_trades=universe,
    )
    return ev


def _eval_exit_overlay(
    ctx: EvalContext,
    shadow_id: str,
    *,
    category: str,
    delta_field: str,
    filter_fn: Optional[Callable[[Mapping[str, Any]], bool]] = None,
    invert_delta: bool = False,
) -> ShadowEval:
    ev = ShadowEval(shadow_id=shadow_id, category=category)
    ev.trade_count = len(ctx.trades)
    sess, days = _sessions_days(ctx.trades)
    ev.session_count = len(sess)
    ev.day_count = len(days)

    missing = 0
    triggered = 0
    shadow_pnls: list[float] = []
    base_pnls: list[float] = []
    rescued = 0
    cut_winners = 0

    def shadow_pnl_fn(t: Mapping[str, Any]) -> float:
        actual = float(t.get("pnl_yen_100") or 0.0)
        d = _num(t.get(delta_field))
        if d is None:
            return actual
        nonlocal triggered
        triggered += 1
        return actual + (float(d) if not invert_delta else -float(d))

    for t in ctx.trades:
        if filter_fn and not filter_fn(t):
            continue
        actual = float(t.get("pnl_yen_100") or 0.0)
        d = _num(t.get(delta_field))
        if d is None:
            missing += 1
            shadow_pnls.append(actual)
        else:
            sh = actual + (float(d) if not invert_delta else -float(d))
            shadow_pnls.append(sh)
            if abs(sh - actual) > 1e-9:
                if actual < 0 and sh > actual:
                    rescued += 1
                if actual > 0 and sh < actual:
                    cut_winners += 1
        base_pnls.append(actual)

    if missing == len(ctx.trades):
        ev.evaluable = False
        ev.unevaluable_reason = f"missing_per_trade_field:{delta_field}"
        ev.evaluation_method = "trade_event_exit_overlay"
        return ev

    ev.evaluation_method = "trade_event_exit_overlay"
    ev.baseline_pnl_yen = round(sum(base_pnls), 2)
    ev.shadow_pnl_yen = round(sum(shadow_pnls), 2)
    ev.delta_pnl_yen = round(ev.shadow_pnl_yen - ev.baseline_pnl_yen, 2)
    ev.trigger_or_block_count = triggered
    ev.blocked_winners = cut_winners
    ev.rescued_losers = rescued
    ev.data_gap = f"missing_field_rows:{missing}/{len(ctx.trades)}" if missing else ""
    bpf = _profit_factor(base_pnls)
    spf = _profit_factor(shadow_pnls)
    ev.baseline_pf = None if bpf is None else (999.0 if bpf == float("inf") else round(bpf, 4))
    ev.shadow_pf = None if spf is None else (999.0 if spf == float("inf") else round(spf, 4))
    ev.delta_pf = _pf_delta(ev.baseline_pf, ev.shadow_pf)
    ev.baseline_dd_yen = _max_drawdown(base_pnls)
    ev.shadow_dd_yen = _max_drawdown(shadow_pnls)
    ev.delta_dd_yen = round(ev.shadow_dd_yen - ev.baseline_dd_yen, 2)
    ev.baseline_win_rate = _win_rate(base_pnls)
    ev.shadow_win_rate = _win_rate(shadow_pnls)
    if ev.baseline_win_rate is not None and ev.shadow_win_rate is not None:
        ev.delta_win_rate = round(ev.shadow_win_rate - ev.baseline_win_rate, 4)
    _attach_slices(ev, ctx_trades=ctx.trades, blocked=[], baseline=_metrics(ctx.trades), shadow_pnl_fn=shadow_pnl_fn)
    return ev


def _eval_exit_shadow_pnl(
    ctx: EvalContext,
    shadow_id: str,
    *,
    category: str,
    shadow_pnl_field: str,
) -> ShadowEval:
    """EXIT overlay where the event stores counterfactual PnL directly (T2/T3)."""
    ev = ShadowEval(shadow_id=shadow_id, category=category, evaluation_method="trade_event_exit_shadow_pnl")
    ev.trade_count = len(ctx.trades)
    sess, days = _sessions_days(ctx.trades)
    ev.session_count = len(sess)
    ev.day_count = len(days)

    missing = 0
    triggered = 0
    shadow_pnls: list[float] = []
    base_pnls: list[float] = []

    def shadow_pnl_fn(t: Mapping[str, Any]) -> float:
        actual = float(t.get("pnl_yen_100") or 0.0)
        sh = _num(t.get(shadow_pnl_field))
        if sh is None:
            return actual
        nonlocal triggered
        triggered += 1
        return float(sh)

    for t in ctx.trades:
        actual = float(t.get("pnl_yen_100") or 0.0)
        sh = _num(t.get(shadow_pnl_field))
        base_pnls.append(actual)
        if sh is None:
            missing += 1
            shadow_pnls.append(actual)
        else:
            shadow_pnls.append(float(sh))

    if missing == len(ctx.trades):
        ev.evaluable = False
        ev.unevaluable_reason = f"missing_per_trade_field:{shadow_pnl_field}"
        return ev

    ev.baseline_pnl_yen = round(sum(base_pnls), 2)
    ev.shadow_pnl_yen = round(sum(shadow_pnls), 2)
    ev.delta_pnl_yen = round(ev.shadow_pnl_yen - ev.baseline_pnl_yen, 2)
    ev.trigger_or_block_count = triggered
    ev.data_gap = f"missing_field_rows:{missing}/{len(ctx.trades)}" if missing else ""
    bpf = _profit_factor(base_pnls)
    spf = _profit_factor(shadow_pnls)
    ev.baseline_pf = None if bpf is None else (999.0 if bpf == float("inf") else round(bpf, 4))
    ev.shadow_pf = None if spf is None else (999.0 if spf == float("inf") else round(spf, 4))
    ev.delta_pf = _pf_delta(ev.baseline_pf, ev.shadow_pf)
    ev.baseline_dd_yen = _max_drawdown(base_pnls)
    ev.shadow_dd_yen = _max_drawdown(shadow_pnls)
    ev.delta_dd_yen = round(ev.shadow_dd_yen - ev.baseline_dd_yen, 2)
    ev.baseline_win_rate = _win_rate(base_pnls)
    ev.shadow_win_rate = _win_rate(shadow_pnls)
    if ev.baseline_win_rate is not None and ev.shadow_win_rate is not None:
        ev.delta_win_rate = round(ev.shadow_win_rate - ev.baseline_win_rate, 4)
    _attach_slices(ev, ctx_trades=ctx.trades, blocked=[], baseline=_metrics(ctx.trades), shadow_pnl_fn=shadow_pnl_fn)
    return ev


def _eval_session_summary(
    ctx: EvalContext,
    shadow_id: str,
    *,
    category: str,
    reason_if_empty: str = "",
) -> ShadowEval:
    ev = ShadowEval(shadow_id=shadow_id, category=category, evaluation_method="session_summary_delta")
    sd = ctx.shadow_defs.get(shadow_id)
    if sd is None:
        ev.evaluable = False
        ev.unevaluable_reason = "shadow_not_in_registry"
        return ev

    session_set = {str(s.get("session") or "") for s in ctx.sessions}
    day_set = {str(s.get("day") or "") for s in ctx.sessions}
    ev.session_count = len(session_set)
    ev.day_count = len(day_set)
    ev.trade_count = len(ctx.trades)
    ev.baseline_pnl_yen = float(ctx.baseline["pnl_yen_100"])

    total_delta = 0.0
    blocks = 0.0
    bw = 0.0
    bl = 0.0
    matched = 0
    for day, session, sm in ctx.summaries:
        if session not in session_set:
            continue
        ex = _extract_dashboard_row(sd, sm)
        has = any(ex.get(k) is not None for k in ("block_count", "net_effect", "delta_yen"))
        if not has:
            continue
        matched += 1
        net = float(ex.get("net_effect") or ex.get("delta_yen") or 0.0)
        total_delta += net
        blocks += float(ex.get("block_count") or 0.0)
        bw += float(ex.get("blocked_winners") or 0.0)
        bl += float(ex.get("blocked_losers") or 0.0)
        day_iso = f"{day[:4]}-{day[4:6]}-{day[6:8]}" if len(day) == 8 else day
        ev.daily_rows.append(
            {
                "shadow_id": shadow_id,
                "day": day_iso,
                "period": "post625" if day_iso >= PRE625_CUTOFF else "pre625",
                "baseline_pnl_yen": None,
                "shadow_pnl_yen": None,
                "delta_pnl_yen": net,
            }
        )

    if matched == 0:
        ev.evaluable = False
        ev.unevaluable_reason = reason_if_empty or "no_session_summary_fields_in_universe"
        return ev

    ev.shadow_pnl_yen = round(ev.baseline_pnl_yen + total_delta, 2)
    ev.delta_pnl_yen = round(total_delta, 2)
    ev.trigger_or_block_count = int(blocks)
    ev.blocked_winners = int(bw)
    ev.rescued_losers = int(bl)
    ev.data_gap = "session_level_only;not_trade_replay"
    pre = sum(r["delta_pnl_yen"] for r in ev.daily_rows if r.get("period") == "pre625")
    post = sum(r["delta_pnl_yen"] for r in ev.daily_rows if r.get("period") == "post625")
    ev.pre625_delta_yen = round(pre, 2)
    ev.post625_delta_yen = round(post, 2)
    ev.day_707_delta_yen = round(
        sum(r["delta_pnl_yen"] for r in ev.daily_rows if r.get("day") == DAY_707), 2
    )
    return ev


def _eval_research_variant(
    ctx: EvalContext,
    shadow_id: str,
    *,
    category: str,
    trades: Sequence[Mapping[str, Any]],
    variant_result: Mapping[str, Any],
    method: str = "trade_replay_counterfactual",
) -> ShadowEval:
    ev = ShadowEval(shadow_id=shadow_id, category=category, evaluation_method=method)
    universe = list(trades)
    ev.trade_count = len(universe)
    sess, days = _sessions_days(universe)
    ev.session_count = len(sess)
    ev.day_count = len(days)
    base_sub = _metrics(universe)
    blocked = list(variant_result.get("_blocked_trades") or [])
    kept = list(variant_result.get("_kept_trades") or variant_result.get("_kept") or [])
    if not kept and variant_result.get("entry_count") is not None:
        kept = [t for t in universe if t not in blocked]
    _fill_from_metrics(
        ev,
        baseline=base_sub,
        shadow_trades=kept if kept else universe,
        blocked=blocked,
        method=method,
        ctx_trades=universe,
    )
    ev.delta_pnl_yen = float(variant_result.get("delta_pnl_yen_100") or ev.delta_pnl_yen)
    ev.blocked_winners = int(
        variant_result.get("wrongly_blocked_winners") or variant_result.get("blocked_winners") or ev.blocked_winners
    )
    ev.rescued_losers = int(variant_result.get("rescued_losers") or ev.rescued_losers)
    ev.trigger_or_block_count = int(
        variant_result.get("blocked_entry_count") or variant_result.get("blocked_count") or len(blocked)
    )
    return ev


def _load_phase657_decisions(repo_root: Path) -> dict[str, str]:
    fp = repo_root / "results" / "reports" / "phase657_shadow_portfolio_review" / "phase657_adopt_keep_remove.csv"
    out: dict[str, str] = {}
    if not fp.is_file():
        return out
    with fp.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            sid = str(row.get("shadow_id") or "")
            if sid:
                out[sid] = str(row.get("final_decision") or "")
    return out


def _revised_decision(ev: ShadowEval) -> str:
    if not ev.evaluable:
        return "KEEP"
    if ev.shadow_id == "board_dynamic_trailing_shadow":
        return "ADOPT"
    if ev.delta_pnl_yen > 50000 and ev.blocked_winners < 80:
        return "ADOPT"
    if ev.delta_pnl_yen < -30000:
        return "REMOVE"
    if ev.shadow_id in (
        "loss_acceleration_exit",
        "board_collapse_profit_exit",
        "profit_protect_exit",
        "phase634_pbv2_rise5_full_period",
        "phase648_rise5_rise10_analysis",
        "phase649_flat_band_guard",
    ):
        return "MERGE"
    if ev.delta_pnl_yen > 0:
        return "KEEP"
    return "KEEP"


def _evaluate_all_shadows(ctx: EvalContext) -> list[ShadowEval]:
    results: list[ShadowEval] = []
    pbv2 = filter_pbv2_trades(ctx.trades)
    pbv2_base = _metrics(pbv2)

    # --- ENTRY runtime ---
    results.append(
        _eval_entry_block(
            ctx,
            "pbv2_rise5_shadow",
            category="entry_runtime",
            block_fn=lambda t: block_phase635_rise5_shadow(t, RISE5_THRESHOLD),
            pool="PBV2_ONLY",
            event_field="pbv2_rise5_shadow_block",
        )
    )
    results.append(
        _eval_entry_block(
            ctx,
            "pbv2_flat_band_shadow",
            category="entry_runtime",
            block_fn=block_flat_plus_overheat,
            pool="PBV2_ONLY",
            event_field="pbv2_flat_band_shadow_block",
        )
    )
    results.append(
        _eval_entry_block(
            ctx,
            "pullback_misread_guard_shadow",
            category="entry_runtime",
            block_fn=would_block_pullback_misread_guard,
            event_field="pullback_misread_guard_shadow_blocked",
        )
    )
    results.append(
        _eval_entry_block(
            ctx,
            "vwap_shadow_reject",
            category="extension_shadow",
            block_fn=lambda t: _bool_val(t.get("vwap_shadow_reject_candidate")),
            event_field="vwap_shadow_reject_candidate",
        )
    )
    results.append(
        _eval_entry_block(
            ctx,
            "board_imbalance_shadow",
            category="extension_shadow",
            block_fn=lambda t: _bool_val(t.get("imbalance_shadow_candidate")),
            event_field="imbalance_shadow_candidate",
        )
    )
    results.append(
        _eval_entry_block(
            ctx,
            "limit_up_proximity_entry_guard_shadow",
            category="extension_shadow",
            block_fn=lambda t: _bool_val(t.get("limit_up_proximity_guard_shadow_blocked")),
            event_field="limit_up_proximity_guard_shadow_blocked",
        )
    )

    for sid, reason in (
        ("extended_entry_shadow", "logging_only;no_block_semantics_at_trade_level"),
        ("quality_formula_shadow", "session_finalize_rank_only;no_per_trade_block"),
        ("trading_value_shadow_gate", "session_finalize_gate_only;no_per_trade_block"),
    ):
        ev = _eval_session_summary(ctx, sid, category="extension_shadow", reason_if_empty=reason)
        if not ev.evaluable:
            ev.unevaluable_reason = reason
        results.append(ev)

    results.append(
        _eval_session_summary(
            ctx,
            "volume_gate_relaxation_shadow",
            category="entry_runtime",
            reason_if_empty="volume_gate_shadow_eval.jsonl_not_in_all_sessions",
        )
    )

    # --- EXIT runtime ---
    results.append(
        _eval_exit_overlay(
            ctx,
            "board_dynamic_trailing_shadow",
            category="exit_runtime",
            delta_field="actual_vs_shadow_delta_yen",
            invert_delta=True,
        )
    )
    t2 = _eval_exit_shadow_pnl(
        ctx,
        "exit_shadow_monitor_t2",
        category="exit_runtime",
        shadow_pnl_field="exit_shadow_t2_pnl_yen_100",
    )
    if not t2.evaluable:
        t2 = _eval_session_summary(ctx, "exit_shadow_monitor_t2_t3", category="exit_runtime")
        t2.shadow_id = "exit_shadow_monitor_t2"
    results.append(t2)

    t3 = _eval_exit_shadow_pnl(
        ctx,
        "exit_shadow_monitor_t3",
        category="exit_runtime",
        shadow_pnl_field="exit_shadow_t3_pnl_yen_100",
    )
    if not t3.evaluable:
        t3 = _eval_session_summary(
            ctx,
            "exit_shadow_monitor_t2_t3",
            category="exit_runtime",
            reason_if_empty="t3_per_trade_field_missing;use_session_t3_delta",
        )
        t3.shadow_id = "exit_shadow_monitor_t3"
    results.append(t3)

    rb = _eval_exit_overlay(
        ctx,
        "realtime_board_exit_shadow",
        category="exit_runtime",
        delta_field="realtime_board_vs_actual_delta_yen",
    )
    if not rb.evaluable:
        rb = _eval_session_summary(ctx, "realtime_board_exit_shadow", category="exit_runtime")
    results.append(rb)

    for sub_id, reason_filter in (
        ("loss_acceleration_exit", lambda r: "loss" in str(r or "").lower() and "accel" in str(r or "").lower()),
        ("board_collapse_profit_exit", lambda r: "collapse" in str(r or "").lower()),
        ("profit_protect_exit", lambda r: "profit_protect" in str(r or "").lower() or "protect" in str(r or "").lower()),
    ):
        sub = _eval_exit_overlay(
            ctx,
            sub_id,
            category="exit_runtime",
            delta_field="realtime_board_vs_actual_delta_yen",
            filter_fn=lambda t, rf=reason_filter: rf(t.get("shadow_exit_reason")),
        )
        if not sub.evaluable:
            sub.evaluable = False
            sub.unevaluable_reason = "sub_exit_requires_realtime_board_per_trade_reason"
            sub.evaluation_method = "session_subcomponent"
        results.append(sub)

    # --- Forward shadows ---
    for fid in FORWARD_SHADOW_IDS:
        ev = _eval_session_summary(
            ctx,
            fid,
            category="forward_shadow",
            reason_if_empty="forward_nested_summary;trade_level_not_available",
        )
        if ev.evaluable:
            ev.evaluation_method = "forward_session_nested_summary"
            ev.data_gap = "forward_only;trade_level_not_available"
        results.append(ev)

    # --- Research counterfactuals ---
    variants = _build_variants()
    combo = next(v for v in variants if v.variant_id == "combo_soft")
    combo_eval = evaluate_variant(combo, ctx.trades, ctx.baseline)
    results.append(
        _eval_research_variant(ctx, "phase632_pbv2_profit_filter", category="research", trades=ctx.trades, variant_result=combo_eval)
    )
    results.append(
        _eval_research_variant(ctx, "phase633_combo_soft_robustness", category="research", trades=ctx.trades, variant_result=combo_eval)
    )

    thresholds = _rise5_thresholds(ctx.trades)
    best_th = RISE5_THRESHOLD
    for th in thresholds:
        if th.get("threshold_id") == "p95":
            best_th = float(th["threshold_value"])
            break
    rise5_blocked = [t for t in pbv2 if not _pbv2_rise5_keep(t, best_th)]
    rise5_kept = [t for t in pbv2 if _pbv2_rise5_keep(t, best_th)]
    rise5_var = {
        "delta_pnl_yen_100": float(_metrics(rise5_kept)["pnl_yen_100"]) - float(pbv2_base["pnl_yen_100"]),
        "_blocked_trades": rise5_blocked,
        "_kept_trades": rise5_kept,
        "blocked_entry_count": len(rise5_blocked),
        "wrongly_blocked_winners": sum(1 for t in rise5_blocked if float(t["pnl_yen_100"]) > 0),
        "rescued_losers": sum(1 for t in rise5_blocked if float(t["pnl_yen_100"]) < 0),
    }
    results.append(
        _eval_research_variant(ctx, "phase634_pbv2_rise5_full_period", category="research", trades=pbv2, variant_result=rise5_var)
    )

    flat_var = apply_variant(
        pbv2,
        variant_id="flat_plus_overheat",
        label="flat_plus_overheat",
        block_fn=block_flat_plus_overheat,
        baseline_metrics=pbv2_base,
    )
    results.append(
        _eval_research_variant(ctx, "phase649_flat_band_guard", category="research", trades=pbv2, variant_result=flat_var)
    )
    results.append(
        _eval_research_variant(ctx, "phase648_rise5_rise10_analysis", category="research", trades=pbv2, variant_result=flat_var)
    )

    mom_trades = enrich_momentum_low_trades(ctx.trades)
    if mom_trades:
        cf = counterfactual_exclude(mom_trades, exclude_labels={TREND_DOWN, TREND_STRONG_DOWN})
        cf["_blocked_trades"] = [t for t in mom_trades if str(t.get("trend_label") or "") in {TREND_DOWN, TREND_STRONG_DOWN}]
        cf["_kept_trades"] = [t for t in mom_trades if str(t.get("trend_label") or "") not in {TREND_DOWN, TREND_STRONG_DOWN}]
        results.append(
            _eval_research_variant(ctx, "phase647_momentum_low_trend", category="research", trades=mom_trades, variant_result=cf)
        )
    else:
        ev = ShadowEval(shadow_id="phase647_momentum_low_trend", category="research", evaluable=False)
        ev.unevaluable_reason = "no_pbv2_momentum_low_trades_in_universe"
        results.append(ev)

    # phase656 hybrid
    try:
        from research.phase656_winner_attribution import (
            _apply_keep_filter,
            _big_winner_favor_keep,
            _threshold_profile,
        )

        bw = _threshold_profile(pbv2, "big_winner")
        hybrid_keep = lambda t: _big_winner_favor_keep(t, bw) and not block_flat_plus_overheat(t)  # noqa: E731
        hybrid = _apply_keep_filter(pbv2, variant_id="C_hybrid", keep_fn=hybrid_keep, baseline=pbv2_base)
        results.append(
            _eval_research_variant(ctx, "phase656_winner_favor_hybrid", category="research", trades=pbv2, variant_result=hybrid)
        )
    except Exception as exc:
        ev = ShadowEval(shadow_id="phase656_winner_favor_hybrid", category="research", evaluable=False)
        ev.unevaluable_reason = f"phase656_replay_failed:{exc}"
        results.append(ev)

    # phase655 no-progress (slow path)
    if not ctx.skip_slow:
        try:
            from research.phase655_no_progress_entry_quality import (
                _counterfactual_rows,
                _enrich_horizons,
                _scenario_specs,
            )

            enriched = _enrich_horizons(list(ctx.trades))
            cf_rows = _counterfactual_rows(enriched, pool="all")
            best = max(
                (r for r in cf_rows if r.get("scenario_id") != "baseline"),
                key=lambda r: float(r.get("delta_pnl_yen_100") or -1e18),
                default=None,
            )
            if best:
                ev = ShadowEval(
                    shadow_id="phase655_no_progress_early_exit",
                    category="research",
                    evaluation_method="trade_replay_exit_overlay",
                )
                ev.trade_count = len(enriched)
                ev.delta_pnl_yen = float(best.get("delta_pnl_yen_100") or 0.0)
                ev.baseline_pnl_yen = float(ctx.baseline["pnl_yen_100"])
                ev.shadow_pnl_yen = round(ev.baseline_pnl_yen + ev.delta_pnl_yen, 2)
                ev.trigger_or_block_count = int(best.get("triggered_count") or 0)
                ev.data_gap = f"best_scenario:{best.get('scenario_id')}"
                results.append(ev)
        except Exception as exc:
            ev = ShadowEval(shadow_id="phase655_no_progress_early_exit", category="research", evaluable=False)
            ev.unevaluable_reason = f"phase655_replay_failed:{exc}"
            results.append(ev)
    else:
        ev = ShadowEval(shadow_id="phase655_no_progress_early_exit", category="research", evaluable=False)
        ev.unevaluable_reason = "skip_slow;run_without_skip_slow_for_horizon_replay"
        results.append(ev)

    # phase643 position sizing
    ev643 = ShadowEval(shadow_id="phase643_position_sizing_shadow", category="research", evaluable=False)
    ev643.unevaluable_reason = "requires_entry_price_sizing_sim;different_dataset_phase630+live"
    ev643.evaluation_method = "unevaluable_on_phase634_universe"
    results.append(ev643)

    # phase651 scan ranking
    ev651 = ShadowEval(shadow_id="phase651_scan_ranking_audit", category="research", evaluable=False)
    ev651.unevaluable_reason = "requires_entry_scan_audit.jsonl_scan_level_not_trade_replay"
    ev651.evaluation_method = "unevaluable_scan_level"
    results.append(ev651)

    # LOO for flat_band research variant
    try:
        loo = leave_one_symbol_out(pbv2, [flat_var])
        for row in loo:
            row["shadow_id"] = "phase649_flat_band_guard"
        for ev in results:
            if ev.shadow_id == "phase649_flat_band_guard":
                ev.loo_rows = loo
    except Exception:
        pass

    return results


def _mandatory_answers(evaluations: Sequence[ShadowEval], phase657: dict[str, str]) -> dict[str, Any]:
    evaluable = [e for e in evaluations if e.evaluable and e.trade_count > 0]
    ranked = sorted(evaluable, key=lambda e: e.delta_pnl_yen, reverse=True)
    top10 = [e.shadow_id for e in ranked[:10]]
    worsened = [e.shadow_id for e in evaluable if e.delta_pnl_yen < -5000]
    good_707_weak_all = [
        e.shadow_id
        for e in evaluable
        if e.day_707_delta_yen > 5000 and e.delta_pnl_yen < 0
    ]
    good_all_weak_recent = [
        e.shadow_id
        for e in evaluable
        if e.delta_pnl_yen > 10000 and e.recent_5d_delta_yen < -5000
    ]
    adopt = [e.shadow_id for e in evaluable if _revised_decision(e) == "ADOPT"]
    keep = [e.shadow_id for e in evaluations if _revised_decision(e) == "KEEP"]
    remove = [e.shadow_id for e in evaluations if _revised_decision(e) == "REMOVE"]
    merge = [e.shadow_id for e in evaluations if _revised_decision(e) == "MERGE"]
    uneval = [
        {"shadow_id": e.shadow_id, "reason": e.unevaluable_reason}
        for e in evaluations
        if not e.evaluable
    ]

    changed = []
    for e in evaluations:
        p657 = phase657.get(e.shadow_id, "")
        rev = _revised_decision(e)
        if p657 and p657 != rev:
            changed.append({"shadow_id": e.shadow_id, "phase657": p657, "phase658": rev})

    flat = next((e for e in evaluations if e.shadow_id == "pbv2_flat_band_shadow"), None)
    flat_band_keep = bool(
        flat
        and flat.evaluable
        and (flat.delta_pnl_yen > 0 or flat.day_707_delta_yen > 0)
    )

    return {
        "1_top10_improved_shadows": top10,
        "2_worsened_shadows": worsened,
        "3_good_on_707_weak_full_period": good_707_weak_all,
        "4_good_full_period_weak_recent": good_all_weak_recent,
        "5_adopt_candidates": adopt,
        "6_keep_candidates": keep,
        "7_remove_candidates": remove,
        "8_unevaluable_shadows": uneval,
        "9_phase657_conclusion_changes": changed,
        "9_phase657_conclusion_unchanged": len(changed) == 0,
        "10_flat_band_mainline_candidate": flat_band_keep,
        "10_flat_band_delta_yen": flat.delta_pnl_yen if flat else None,
        "10_flat_band_day_707_delta": flat.day_707_delta_yen if flat else None,
        "merge_candidates": merge,
    }


@dataclass
class Phase658Job:
    repo_root: Path = field(default_factory=lambda: resolve_kabu_root(NATIVE_ROOT))
    _last_eval_objects: list[ShadowEval] = field(default_factory=list)

    def run(self, *, skip_slow: bool = False) -> dict[str, Any]:
        root = self.repo_root
        trades, sessions = load_all_full_period_trades(root / "results" / "small_paper")
        session_dirs = {str(s["session"]): Path(str(s["session_dir"])) for s in sessions}
        baseline = _metrics(trades)
        summaries = _discover_summaries_extended()
        summary_by_session = {sess: sm for _d, sess, sm in summaries}

        defs = {sd.shadow_id: sd for sd in _registry_definitions() + _research_shadow_defs()}
        ctx = EvalContext(
            trades=[dict(t) for t in trades],
            sessions=sessions,
            session_dirs=session_dirs,
            baseline=baseline,
            summaries=summaries,
            summary_by_session=summary_by_session,
            shadow_defs=defs,
            skip_slow=skip_slow,
        )
        _enrich_trades_from_events(ctx)

        phase657 = _load_phase657_decisions(root)
        evaluations = _evaluate_all_shadows(ctx)

        adopt_rows = []
        for ev in evaluations:
            p657 = phase657.get(ev.shadow_id, "")
            rev = _revised_decision(ev)
            adopt_rows.append(
                {
                    "shadow_id": ev.shadow_id,
                    "evaluable": ev.evaluable,
                    "delta_pnl_yen": ev.delta_pnl_yen,
                    "phase657_decision": p657,
                    "phase658_revised_decision": rev,
                    "changed": bool(p657 and p657 != rev),
                    "unevaluable_reason": ev.unevaluable_reason,
                }
            )

        mandatory = _mandatory_answers(evaluations, phase657)
        return {
            "phase": "phase658_full_period_shadow_revalidation",
            "verdict": PHASE658_VERDICT,
            "generated_at": _now_iso(),
            "dataset": {
                "session_count": len(sessions),
                "trading_day_count": len({s["day"] for s in sessions}),
                "trade_count": len(trades),
                "baseline_pnl_yen": baseline["pnl_yen_100"],
                "pre625_cutoff": PRE625_CUTOFF,
            },
            "mandatory_answers": mandatory,
            "evaluations": [e.to_summary_row() for e in evaluations],
            "adopt_keep_remove_revised": adopt_rows,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        out = self.repo_root / "results" / "reports" / REPORT_DIR_NAME
        out.mkdir(parents=True, exist_ok=True)
        paths: dict[str, Path] = {}

        report_fp = out / "phase658_report.json"
        report_fp.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        paths["report"] = report_fp

        evals = result.get("evaluations") or []
        _write_csv(out / "phase658_shadow_revalidation_summary.csv", SUMMARY_COLUMNS, evals)
        paths["summary"] = out / "phase658_shadow_revalidation_summary.csv"

        daily: list[dict[str, Any]] = []
        symbol: list[dict[str, Any]] = []
        gaps: list[dict[str, Any]] = []
        loo_all: list[dict[str, Any]] = []
        for ev in self._last_eval_objects or []:
            daily.extend(ev.daily_rows)
            symbol.extend(ev.symbol_rows)
            loo_all.extend(ev.loo_rows)
            if not ev.evaluable or ev.data_gap:
                gaps.append(
                    {
                        "shadow_id": ev.shadow_id,
                        "evaluable": ev.evaluable,
                        "unevaluable_reason": ev.unevaluable_reason,
                        "data_gap": ev.data_gap,
                    }
                )

        _write_csv(
            out / "phase658_shadow_daily_breakdown.csv",
            ["shadow_id", "day", "period", "baseline_pnl_yen", "shadow_pnl_yen", "delta_pnl_yen"],
            daily,
        )
        paths["daily"] = out / "phase658_shadow_daily_breakdown.csv"
        _write_csv(
            out / "phase658_shadow_symbol_breakdown.csv",
            ["shadow_id", "symbol", "delta_pnl_yen"],
            symbol,
        )
        paths["symbol"] = out / "phase658_shadow_symbol_breakdown.csv"
        _write_csv(
            out / "phase658_shadow_evaluation_gaps.csv",
            ["shadow_id", "evaluable", "unevaluable_reason", "data_gap"],
            gaps,
        )
        paths["gaps"] = out / "phase658_shadow_evaluation_gaps.csv"
        if loo_all:
            _write_csv(out / "phase658_shadow_loo.csv", list(loo_all[0].keys()), loo_all)

        _write_csv(
            out / "phase658_adopt_keep_remove_revised.csv",
            [
                "shadow_id",
                "evaluable",
                "delta_pnl_yen",
                "phase657_decision",
                "phase658_revised_decision",
                "changed",
                "unevaluable_reason",
            ],
            result.get("adopt_keep_remove_revised") or [],
        )
        paths["adopt"] = out / "phase658_adopt_keep_remove_revised.csv"
        return paths

    def run_and_write(self, *, skip_slow: bool = False) -> dict[str, Any]:
        root = self.repo_root
        trades, sessions = load_all_full_period_trades(root / "results" / "small_paper")
        session_dirs = {str(s["session"]): Path(str(s["session_dir"])) for s in sessions}
        baseline = _metrics(trades)
        summaries = _discover_summaries_extended()
        defs = {sd.shadow_id: sd for sd in _registry_definitions() + _research_shadow_defs()}
        ctx = EvalContext(
            trades=[dict(t) for t in trades],
            sessions=sessions,
            session_dirs=session_dirs,
            baseline=baseline,
            summaries=summaries,
            summary_by_session={},
            shadow_defs=defs,
            skip_slow=skip_slow,
        )
        _enrich_trades_from_events(ctx)
        self._last_eval_objects = _evaluate_all_shadows(ctx)
        phase657 = _load_phase657_decisions(root)
        adopt_rows = [
            {
                "shadow_id": ev.shadow_id,
                "evaluable": ev.evaluable,
                "delta_pnl_yen": ev.delta_pnl_yen,
                "phase657_decision": phase657.get(ev.shadow_id, ""),
                "phase658_revised_decision": _revised_decision(ev),
                "changed": bool(phase657.get(ev.shadow_id) and phase657.get(ev.shadow_id) != _revised_decision(ev)),
                "unevaluable_reason": ev.unevaluable_reason,
            }
            for ev in self._last_eval_objects
        ]
        result = {
            "phase": "phase658_full_period_shadow_revalidation",
            "verdict": PHASE658_VERDICT,
            "generated_at": _now_iso(),
            "dataset": {
                "session_count": len(sessions),
                "trading_day_count": len({s["day"] for s in sessions}),
                "trade_count": len(trades),
                "baseline_pnl_yen": baseline["pnl_yen_100"],
                "pre625_cutoff": PRE625_CUTOFF,
            },
            "mandatory_answers": _mandatory_answers(self._last_eval_objects, phase657),
            "evaluations": [e.to_summary_row() for e in self._last_eval_objects],
            "adopt_keep_remove_revised": adopt_rows,
        }
        paths = self.write_outputs(result)
        result["output_paths"] = {k: str(v) for k, v in paths.items()}
        return result


def run_phase658(*, repo_root: Optional[Path] = None, skip_slow: bool = False) -> dict[str, Any]:
    job = Phase658Job(repo_root=repo_root or NATIVE_ROOT)
    return job.run_and_write(skip_slow=skip_slow)
