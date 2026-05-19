"""
Phase 58: structural_observer_v1 — structure-based pseudo-trade evaluation (review only).

Uses observer replay with session-scoped exit_time (not 300s virtual hold) for evaluation.
Official PF excludes virtual_hold_expired, fixed horizons, and hold_max_* policies.
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from unittest.mock import patch
from zoneinfo import ZoneInfo

from research.continuation_quality_ranking import continuation_components
from research.research_exit_criteria import _as_float
from research.structural_exit_policies import (
    POLICY_COMBINED_STRUCTURAL_EXIT_V1,
    POLICY_STRUCTURAL_OBSERVER_V1,
    STRUCTURE_EXIT_REASONS,
    combined_exit_signal_on_latest_tick,
    simulate_structural_policy,
    tick_from_candidate,
)
from research.small_paper_performance_review import (
    _build_trade_lifecycles,
    _load_events,
    _load_json,
    _parse_dt,
    _parse_ts,
    _profit_factor,
    _summarize_trades,
    quality_band,
    session_bucket_at,
)
from small_paper.discord_notifier import observer_tracker_config_from_pilot
from small_paper.observer_position_tracker import (
    OBSERVER_EXIT,
    OBSERVER_HOLD,
    OBSERVER_TAKE,
    ObserverPositionTracker,
)

JST = ZoneInfo("Asia/Tokyo")

POLICY_ID = POLICY_STRUCTURAL_OBSERVER_V1
DEFAULT_OFFICIAL_EXIT_POLICY = POLICY_STRUCTURAL_OBSERVER_V1
MIN_STRUCTURAL_PF = 1.2
MIN_STRUCTURAL_TRADES = 50
SESSION_END_EXIT_RATE_HIGH_PCT = 70.0
VERDICT_PASS = "structural_pass"
VERDICT_MORE_SESSIONS = "structural_needs_more_sessions"
VERDICT_EXIT_DESIGN = "structural_needs_exit_design"
VERDICT_FAIL = "structural_fail"

# Observer EXIT reasons counted as structural (never virtual_hold / live_virtual_hold).
STRUCTURAL_EXIT_REASONS = frozenset(
    {
        "stop_hit",
        "session_end",
        "structural_observer_review_end",
        "overlap_replaced_review",
    }
)

FORBIDDEN_OFFICIAL_EXIT_REASONS = frozenset(
    {
        "virtual_hold_expired",
        "live_virtual_hold",
    }
)


def _pnl_pct(entry: float, price: float) -> float:
    if entry <= 0:
        return 0.0
    return round((price - entry) / entry * 100.0, 4)


def _session_end_time(events: Sequence[Mapping[str, Any]]) -> str:
    best_ts = 0.0
    best_raw = ""
    for e in events:
        raw = str(e.get("entry_time") or "")
        ts = _parse_ts(raw)
        if ts >= best_ts and raw:
            best_ts = ts
            best_raw = raw
    if not best_raw:
        return datetime.now(JST).isoformat(timespec="seconds")
    return best_raw


def _trade_for_structural_eval(
    trade: Mapping[str, Any],
    session_end: str,
) -> dict[str, Any]:
    """Eval-only: extend virtual exit_time to session end so VH does not close replay."""
    out = dict(trade)
    out["exit_time"] = session_end
    out["exit_reason"] = "structural_eval_session_scope"
    return out


def _is_allowed_structural_exit(reason: str) -> bool:
    r = str(reason or "").strip()
    if not r or r in FORBIDDEN_OFFICIAL_EXIT_REASONS:
        return False
    if "virtual_hold" in r.lower():
        return False
    return r in STRUCTURAL_EXIT_REASONS


@dataclass
class StructuralTrade:
    symbol: str
    entry_time: str
    entry_price: float
    entry_quality: float
    quality_tier: str
    close_time: str = ""
    close_price: float = 0.0
    close_reason: str = ""
    realized_pnl_pct: float = 0.0
    hold_duration_sec: float = 0.0
    mfe_pct: float = 0.0
    mae_pct: float = 0.0
    tick_count: int = 0
    take_time: str = ""
    take_pnl_pct: Optional[float] = None
    take_reason: str = ""
    hold_notify_count: int = 0
    session_bucket: str = ""


@dataclass
class _ActiveStructural:
    trade: StructuralTrade
    entry_ts: float
    ticks: list[tuple[float, float]] = field(default_factory=list)
    rich_ticks: list[dict[str, Any]] = field(default_factory=list)


def _trade_key(symbol: str, entry_time: str) -> tuple[str, str]:
    return symbol, entry_time


def _last_tick_price(ticks: Sequence[tuple[float, float]], entry_px: float) -> float:
    if ticks:
        return float(ticks[-1][1])
    return entry_px


def _mfe_mae(ticks: Sequence[tuple[float, float]], entry_px: float) -> tuple[float, float]:
    if not ticks or entry_px <= 0:
        return 0.0, 0.0
    pnls = [(px - entry_px) / entry_px * 100.0 for _, px in ticks]
    return round(max(pnls), 4), round(min(pnls), 4)


def _close_structural_trade(
    active: _ActiveStructural,
    *,
    close_time: str,
    close_price: float,
    close_reason: str,
) -> StructuralTrade:
    t = active.trade
    close_ts = _parse_ts(close_time) or active.entry_ts
    t.close_time = close_time
    t.close_price = round(close_price, 4)
    t.close_reason = close_reason
    t.realized_pnl_pct = _pnl_pct(t.entry_price, close_price)
    t.hold_duration_sec = round(max(0.0, close_ts - active.entry_ts), 1)
    t.mfe_pct, t.mae_pct = _mfe_mae(active.ticks, t.entry_price)
    t.tick_count = len(active.ticks)
    return t


def replay_structural_observer_v1(
    events: Sequence[Mapping[str, Any]],
    *,
    pilot_config: Any,
    poll_interval_sec: float,
    session_end: Optional[str] = None,
) -> tuple[list[StructuralTrade], list[dict[str, Any]]]:
    import small_paper.observer_position_tracker as ot

    session_end = session_end or _session_end_time(events)
    tracker = ObserverPositionTracker(observer_tracker_config_from_pilot(pilot_config))
    ordered = sorted(events, key=lambda e: int(e.get("message_index") or 0))
    mono = [0.0]
    active: dict[tuple[str, str], _ActiveStructural] = {}
    completed: list[StructuralTrade] = []
    event_log: list[dict[str, Any]] = []

    def _mono() -> float:
        return mono[0]

    def _log(kind: str, **fields: Any) -> None:
        event_log.append({"event_kind": kind, **fields})

    for ev in ordered:
        sym = str(ev.get("symbol") or "")
        if not sym:
            continue
        ent_raw = str(ev.get("entry_time") or "")
        as_of = _parse_dt(ent_raw) if ent_raw else datetime.now(JST)
        mono[0] += max(poll_interval_sec, 0.001)
        trade = _trade_for_structural_eval(dict(ev), session_end)
        price = _as_float(ev.get("current_price"))
        bucket = session_bucket_at(as_of)

        with patch.object(ot.time, "monotonic", _mono):
            with patch.object(ot, "datetime") as mdt:
                mdt.now.return_value = as_of
                mdt.combine = datetime.combine
                mdt.fromisoformat = datetime.fromisoformat

                if ev.get("event_type") == "accepted" and price and price > 0:
                    if tracker.has_open(sym):
                        old_keys = [k for k, a in active.items() if a.trade.symbol == sym]
                        for key in old_keys:
                            act = active.pop(key)
                            cp = float(price)
                            closed = _close_structural_trade(
                                act,
                                close_time=ent_raw,
                                close_price=cp,
                                close_reason="overlap_replaced_review",
                            )
                            completed.append(closed)
                            _log(
                                "structural_exit",
                                symbol=sym,
                                entry_time=act.trade.entry_time,
                                close_time=ent_raw,
                                close_reason="overlap_replaced_review",
                                close_price=cp,
                                pnl_pct=closed.realized_pnl_pct,
                            )
                        tracker._positions.pop(sym, None)

                    ent_ts = _parse_ts(ent_raw)
                    st = StructuralTrade(
                        symbol=sym,
                        entry_time=ent_raw,
                        entry_price=float(price),
                        entry_quality=float(ev.get("continuation_quality_score") or 0),
                        quality_tier=str(ev.get("quality_tier") or ""),
                        session_bucket=bucket,
                    )
                    key = _trade_key(sym, ent_raw)
                    active[key] = _ActiveStructural(trade=st, entry_ts=ent_ts)
                    tracker.register_entry(
                        trade=trade,
                        payload=trade,
                        quality_tier=st.quality_tier,
                        entry_price=float(price),
                    )
                    _log(
                        "entry",
                        symbol=sym,
                        entry_time=ent_raw,
                        entry_price=float(price),
                        continuation_quality_score=st.entry_quality,
                        quality_tier=st.quality_tier,
                    )

                elif ev.get("event_type") == "candidate" and tracker.has_open(sym):
                    act = next((a for a in active.values() if a.trade.symbol == sym), None)
                    if act and price and price > 0:
                        act.ticks.append((_parse_ts(ent_raw), float(price)))

                    for oe in tracker.on_tick(
                        symbol=sym,
                        trade=trade,
                        payload=trade,
                        current_price=price,
                        session_bucket=bucket,
                    ):
                        ctx = oe.context
                        if oe.kind == OBSERVER_TAKE and act and not act.trade.take_time:
                            act.trade.take_time = ent_raw
                            act.trade.take_pnl_pct = _as_float(ctx.get("unrealized_pnl_pct"))
                            act.trade.take_reason = str(ctx.get("take_reason") or "")
                            _log(
                                "take",
                                symbol=sym,
                                entry_time=act.trade.entry_time,
                                take_time=ent_raw,
                                take_reason=act.trade.take_reason,
                                take_pnl_pct=act.trade.take_pnl_pct,
                                note="reference_only_not_exit",
                            )
                        elif oe.kind == OBSERVER_HOLD and act:
                            act.trade.hold_notify_count += 1
                            _log(
                                "hold",
                                symbol=sym,
                                entry_time=act.trade.entry_time,
                                hold_time=ent_raw,
                                hold_reason=ctx.get("hold_reason"),
                            )
                        elif oe.kind == OBSERVER_EXIT and act:
                            reason = str(ctx.get("exit_reason") or "")
                            if reason in FORBIDDEN_OFFICIAL_EXIT_REASONS or "virtual_hold" in reason:
                                _log(
                                    "virtual_hold_expired_ignored",
                                    symbol=sym,
                                    entry_time=act.trade.entry_time,
                                    observer_time=ent_raw,
                                    exit_reason=reason,
                                )
                                if sym in tracker._positions and tracker._positions[sym].closed:
                                    pos = tracker._positions[sym]
                                    pos.closed = False
                                    pos.exit_time = _parse_dt(session_end) + timedelta(seconds=1)
                                continue

                            if not _is_allowed_structural_exit(reason):
                                _log(
                                    "exit_skipped_non_structural",
                                    symbol=sym,
                                    entry_time=act.trade.entry_time,
                                    exit_reason=reason,
                                )
                                if sym in tracker._positions and tracker._positions[sym].closed:
                                    pos = tracker._positions[sym]
                                    pos.closed = False
                                    pos.exit_time = _parse_dt(session_end) + timedelta(seconds=1)
                                continue

                            close_px = _as_float(ctx.get("current_price")) or float(price or act.trade.entry_price)
                            closed = _close_structural_trade(
                                act,
                                close_time=ent_raw,
                                close_price=close_px,
                                close_reason=reason,
                            )
                            key = _trade_key(sym, act.trade.entry_time)
                            active.pop(key, None)
                            completed.append(closed)
                            _log(
                                "structural_exit",
                                symbol=sym,
                                entry_time=closed.entry_time,
                                close_time=ent_raw,
                                close_reason=reason,
                                close_price=close_px,
                                pnl_pct=closed.realized_pnl_pct,
                            )

    # Session end: remaining open positions at last tick price in session window.
    close_reason = "session_end"
    with patch.object(ot.time, "monotonic", lambda: mono[0]):
        with patch.object(ot, "datetime") as mdt:
            end_dt = _parse_dt(session_end) if session_end else datetime.now(JST)
            mdt.now.return_value = end_dt
            mdt.combine = datetime.combine
            mdt.fromisoformat = datetime.fromisoformat
            for oe in tracker.close_all(reason=close_reason):
                if oe.kind != OBSERVER_EXIT:
                    continue
                sym = oe.symbol
                for key, act in list(active.items()):
                    if act.trade.symbol != sym:
                        continue
                    close_px = _last_tick_price(act.ticks, act.trade.entry_price)
                    ctx_px = _as_float(oe.context.get("current_price"))
                    if ctx_px and ctx_px > 0:
                        close_px = float(ctx_px)
                    closed = _close_structural_trade(
                        act,
                        close_time=str(oe.context.get("timestamp") or session_end),
                        close_price=close_px,
                        close_reason=close_reason,
                    )
                    active.pop(key, None)
                    completed.append(closed)
                    _log(
                        "structural_exit",
                        symbol=sym,
                        entry_time=closed.entry_time,
                        close_time=closed.close_time,
                        close_reason=close_reason,
                        close_price=close_px,
                        pnl_pct=closed.realized_pnl_pct,
                    )

    for key, act in list(active.items()):
        close_px = _last_tick_price(act.ticks, act.trade.entry_price)
        closed = _close_structural_trade(
            act,
            close_time=session_end,
            close_price=close_px,
            close_reason=close_reason,
        )
        active.pop(key, None)
        completed.append(closed)
        _log(
            "structural_exit",
            symbol=closed.symbol,
            entry_time=closed.entry_time,
            close_time=session_end,
            close_reason=close_reason,
            close_price=close_px,
            pnl_pct=closed.realized_pnl_pct,
            note="flush_remaining_open",
        )

    return completed, event_log


def replay_combined_structural_exit_v1(
    events: Sequence[Mapping[str, Any]],
    *,
    pilot_config: Any,
    poll_interval_sec: float,
    session_end: Optional[str] = None,
) -> tuple[list[StructuralTrade], list[dict[str, Any]]]:
    """Structure-only EXIT replay: combined rules + overlap + session_end (no observer EXIT/VH)."""
    import small_paper.observer_position_tracker as ot

    session_end = session_end or _session_end_time(events)
    cfg = observer_tracker_config_from_pilot(pilot_config)
    tracker = ObserverPositionTracker(cfg)
    ordered = sorted(events, key=lambda e: int(e.get("message_index") or 0))
    mono = [0.0]
    active: dict[tuple[str, str], _ActiveStructural] = {}
    completed: list[StructuralTrade] = []
    event_log: list[dict[str, Any]] = []

    def _mono() -> float:
        return mono[0]

    def _log(kind: str, **fields: Any) -> None:
        event_log.append({"event_kind": kind, **fields})

    def _close_active(act: _ActiveStructural, *, close_time: str, close_px: float, reason: str) -> StructuralTrade:
        closed = _close_structural_trade(
            act,
            close_time=close_time,
            close_price=close_px,
            close_reason=reason,
        )
        completed.append(closed)
        _log(
            "structural_exit",
            symbol=closed.symbol,
            entry_time=closed.entry_time,
            close_time=close_time,
            close_reason=reason,
            close_price=close_px,
            pnl_pct=closed.realized_pnl_pct,
            exit_policy=POLICY_COMBINED_STRUCTURAL_EXIT_V1,
        )
        return closed

    for ev in ordered:
        sym = str(ev.get("symbol") or "")
        if not sym:
            continue
        ent_raw = str(ev.get("entry_time") or "")
        as_of = _parse_dt(ent_raw) if ent_raw else datetime.now(JST)
        mono[0] += max(poll_interval_sec, 0.001)
        trade = _trade_for_structural_eval(dict(ev), session_end)
        price = _as_float(ev.get("current_price"))
        bucket = session_bucket_at(as_of)

        with patch.object(ot.time, "monotonic", _mono):
            with patch.object(ot, "datetime") as mdt:
                mdt.now.return_value = as_of
                mdt.combine = datetime.combine
                mdt.fromisoformat = datetime.fromisoformat

                if ev.get("event_type") == "accepted" and price and price > 0:
                    if any(a.trade.symbol == sym for a in active.values()) or tracker.has_open(sym):
                        for key in [k for k, a in active.items() if a.trade.symbol == sym]:
                            act = active.pop(key)
                            _close_active(act, close_time=ent_raw, close_px=float(price), reason="overlap_replaced_review")
                        tracker._positions.pop(sym, None)

                    ent_ts = _parse_ts(ent_raw)
                    st = StructuralTrade(
                        symbol=sym,
                        entry_time=ent_raw,
                        entry_price=float(price),
                        entry_quality=float(ev.get("continuation_quality_score") or 0),
                        quality_tier=str(ev.get("quality_tier") or ""),
                        session_bucket=bucket,
                    )
                    key = _trade_key(sym, ent_raw)
                    active[key] = _ActiveStructural(trade=st, entry_ts=ent_ts)
                    tracker.register_entry(
                        trade=trade,
                        payload=trade,
                        quality_tier=st.quality_tier,
                        entry_price=float(price),
                    )
                    _log(
                        "entry",
                        symbol=sym,
                        entry_time=ent_raw,
                        entry_price=float(price),
                        exit_policy=POLICY_COMBINED_STRUCTURAL_EXIT_V1,
                    )

                elif ev.get("event_type") == "candidate":
                    act = next((a for a in active.values() if a.trade.symbol == sym), None)
                    if not act or not price or price <= 0:
                        if tracker.has_open(sym):
                            for oe in tracker.on_tick(
                                symbol=sym,
                                trade=trade,
                                payload=trade,
                                current_price=price,
                                session_bucket=bucket,
                            ):
                                if oe.kind == OBSERVER_EXIT and str(oe.context.get("exit_reason") or "").startswith(
                                    "virtual"
                                ):
                                    if sym in tracker._positions and tracker._positions[sym].closed:
                                        tracker._positions[sym].closed = False
                                        tracker._positions[sym].exit_time = _parse_dt(session_end) + timedelta(
                                            seconds=1
                                        )
                        continue

                    tick = tick_from_candidate(trade, act.trade.entry_price, act.trade.entry_quality)
                    tick["ts_epoch"] = _parse_ts(ent_raw)
                    act.ticks.append((tick["ts_epoch"], float(tick["price"])))
                    act.rich_ticks.append(tick)

                    sig = combined_exit_signal_on_latest_tick(act.rich_ticks, act.trade.entry_price, cfg)
                    if sig:
                        pnl, reason, close_px = sig
                        key = _trade_key(sym, act.trade.entry_time)
                        closed_act = active.pop(key, None)
                        if closed_act:
                            _close_active(closed_act, close_time=ent_raw, close_px=close_px, reason=reason)
                        if sym in tracker._positions:
                            tracker._positions.pop(sym, None)
                        continue

                    if tracker.has_open(sym):
                        for oe in tracker.on_tick(
                            symbol=sym,
                            trade=trade,
                            payload=trade,
                            current_price=price,
                            session_bucket=bucket,
                        ):
                            ctx = oe.context
                            if oe.kind == OBSERVER_TAKE and act and not act.trade.take_time:
                                act.trade.take_time = ent_raw
                                act.trade.take_pnl_pct = _as_float(ctx.get("unrealized_pnl_pct"))
                                act.trade.take_reason = str(ctx.get("take_reason") or "")
                                _log(
                                    "take",
                                    symbol=sym,
                                    entry_time=act.trade.entry_time,
                                    take_time=ent_raw,
                                    take_reason=act.trade.take_reason,
                                    note="reference_only_not_exit",
                                )
                            elif oe.kind == OBSERVER_HOLD and act:
                                act.trade.hold_notify_count += 1
                            elif oe.kind == OBSERVER_EXIT:
                                reason = str(ctx.get("exit_reason") or "")
                                if "virtual_hold" in reason:
                                    if sym in tracker._positions and tracker._positions[sym].closed:
                                        tracker._positions[sym].closed = False
                                        tracker._positions[sym].exit_time = _parse_dt(session_end) + timedelta(
                                            seconds=1
                                        )

    for key, act in list(active.items()):
        active.pop(key, None)
        close_px = _last_tick_price(act.ticks, act.trade.entry_price)
        end_reason = "session_end"
        if act.rich_ticks:
            result = simulate_structural_policy(
                act.rich_ticks,
                act.trade.entry_price,
                POLICY_COMBINED_STRUCTURAL_EXIT_V1,
                cfg,
                allow_session_end=True,
            )
            if result:
                _, end_reason = result
        _close_active(act, close_time=session_end, close_px=close_px, reason=end_reason)

    return completed, event_log


def _legacy_virtual_hold_summary(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    lifecycles = _build_trade_lifecycles(events)
    summary = _summarize_trades(lifecycles)
    if isinstance(summary.get("exit_reason"), Counter):
        summary["exit_reason"] = dict(summary["exit_reason"])
    return {
        "legacy_virtual_hold_trade_count": summary.get("trade_count", 0),
        "legacy_virtual_hold_pf": summary.get("profit_factor"),
        "legacy_virtual_hold_avg_pnl_pct": summary.get("avg_pnl_pct"),
        "legacy_virtual_hold_win_rate": summary.get("win_rate"),
        "legacy_virtual_hold_max_loss_pct": summary.get("max_loss_pct"),
        "legacy_virtual_hold_exit_reason_distribution": summary.get("exit_reason"),
    }


def _summarize_structural_trades(trades: Sequence[StructuralTrade]) -> dict[str, Any]:
    if not trades:
        return {
            "structural_trade_count": 0,
            "structural_pf": None,
            "structural_avg_pnl": None,
            "structural_win_rate": None,
            "structural_max_loss": None,
        }

    pnls = [t.realized_pnl_pct for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    reasons = Counter(t.close_reason for t in trades)
    session_end_n = sum(1 for t in trades if t.close_reason == "session_end")
    stop_n = sum(1 for t in trades if t.close_reason == "stop_hit")
    structure_n = sum(1 for t in trades if t.close_reason in STRUCTURE_EXIT_REASONS)
    stop_loss_sum = round(sum(t.realized_pnl_pct for t in trades if t.close_reason == "stop_hit"), 4)
    with_take = [t for t in trades if t.take_time]
    deltas = [
        t.realized_pnl_pct - float(t.take_pnl_pct or 0)
        for t in with_take
        if t.take_pnl_pct is not None
    ]

    holds = [t.hold_duration_sec for t in trades]
    pf = _profit_factor(pnls)

    return {
        "structural_trade_count": len(trades),
        "structural_pf": round(pf, 4) if pf not in (None, float("inf")) else pf,
        "structural_avg_pnl": round(statistics.mean(pnls), 4),
        "structural_win_rate": round(len(wins) / len(pnls), 4),
        "structural_max_loss": round(min(pnls), 4),
        "structural_max_gain": round(max(pnls), 4),
        "exit_reason_distribution": dict(reasons),
        "avg_hold_duration_structural": round(statistics.mean(holds), 1),
        "median_hold_duration_structural": round(statistics.median(holds), 1),
        "take_before_exit_rate": round(len(with_take) / len(trades), 4),
        "take_to_exit_pnl_delta": round(statistics.mean(deltas), 4) if deltas else None,
        "session_end_exit_rate": round(100.0 * session_end_n / len(trades), 2),
        "stop_hit_rate": round(100.0 * stop_n / len(trades), 2),
        "structure_exit_rate": round(100.0 * structure_n / len(trades), 2),
        "stop_hit_count": reasons.get("stop_hit", 0),
        "structure_exit_count": structure_n,
        "stop_hit_loss_sum_pct": stop_loss_sum,
        "session_end_exit_count": session_end_n,
    }


def _compute_official_verdict(
    metrics: Mapping[str, Any],
    *,
    baseline_metrics: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    n = int(metrics.get("structural_trade_count") or 0)
    pf = _as_float(metrics.get("structural_pf"))
    avg = _as_float(metrics.get("structural_avg_pnl"))
    se_rate = float(metrics.get("session_end_exit_rate") or 0)
    stop_loss = float(metrics.get("stop_hit_loss_sum_pct") or 0)

    failures: list[str] = []
    if n < MIN_STRUCTURAL_TRADES:
        failures.append("trade_count_below_50")
    if pf is None:
        failures.append("structural_pf_undefined")
    elif round(pf, 2) < MIN_STRUCTURAL_PF:
        failures.append("structural_pf_below_1_2")
    if avg is None or avg <= 0:
        failures.append("structural_avg_pnl_not_positive")
    if se_rate >= SESSION_END_EXIT_RATE_HIGH_PCT:
        failures.append("session_end_exit_rate_too_high")

    stop_improved = True
    if baseline_metrics is not None:
        base_stop = float(baseline_metrics.get("stop_hit_loss_sum_pct") or 0)
        stop_improved = stop_loss >= base_stop
        if not stop_improved:
            failures.append("stop_hit_loss_not_improved_vs_baseline")

    if n < MIN_STRUCTURAL_TRADES:
        verdict = VERDICT_MORE_SESSIONS
    elif pf is not None and pf < 0.75:
        verdict = VERDICT_FAIL
    elif se_rate >= SESSION_END_EXIT_RATE_HIGH_PCT:
        verdict = VERDICT_EXIT_DESIGN
    elif failures:
        verdict = VERDICT_EXIT_DESIGN
    elif (
        pf is not None
        and round(pf, 2) >= MIN_STRUCTURAL_PF
        and avg is not None
        and avg > 0
    ):
        verdict = VERDICT_PASS
    else:
        verdict = VERDICT_EXIT_DESIGN

    return {
        "official_verdict": verdict,
        "official_verdict_failures": failures,
        "stop_hit_loss_improved_vs_baseline": stop_improved,
        "official_verdict_criteria": {
            "min_structural_pf": MIN_STRUCTURAL_PF,
            "min_trade_count": MIN_STRUCTURAL_TRADES,
            "min_structural_avg_pnl": 0.0,
            "max_session_end_exit_rate_pct": SESSION_END_EXIT_RATE_HIGH_PCT,
            "require_stop_hit_loss_improvement": baseline_metrics is not None,
        },
    }


def _policy_comparison_row(
    policy: str,
    metrics: Mapping[str, Any],
    *,
    role: str,
) -> dict[str, Any]:
    return {
        "policy": policy,
        "role": role,
        "structural_trade_count": metrics.get("structural_trade_count"),
        "structural_pf": metrics.get("structural_pf"),
        "structural_avg_pnl": metrics.get("structural_avg_pnl"),
        "structural_win_rate": metrics.get("structural_win_rate"),
        "structural_max_loss": metrics.get("structural_max_loss"),
        "session_end_exit_rate": metrics.get("session_end_exit_rate"),
        "stop_hit_rate": metrics.get("stop_hit_rate"),
        "structure_exit_rate": metrics.get("structure_exit_rate"),
        "stop_hit_loss_sum_pct": metrics.get("stop_hit_loss_sum_pct"),
    }


def _exit_policy_summary_rows(
    official_policy: str,
    official_metrics: Mapping[str, Any],
    baseline_metrics: Mapping[str, Any],
    legacy: Mapping[str, Any],
) -> list[dict[str, Any]]:
    return [
        {
            "metric": "structural_exit_policy",
            "value": official_policy,
        },
        {
            "metric": "structural_pf",
            "value": official_metrics.get("structural_pf"),
        },
        {
            "metric": "baseline_structural_observer_v1_pf",
            "value": baseline_metrics.get("structural_pf"),
        },
        {
            "metric": "legacy_virtual_hold_pf",
            "value": legacy.get("legacy_virtual_hold_pf"),
        },
        {
            "metric": "structure_exit_rate_pct",
            "value": official_metrics.get("structure_exit_rate"),
        },
        {
            "metric": "stop_hit_rate_pct",
            "value": official_metrics.get("stop_hit_rate"),
        },
        {
            "metric": "session_end_exit_rate_pct",
            "value": official_metrics.get("session_end_exit_rate"),
        },
    ]


def _trade_to_row(t: StructuralTrade) -> dict[str, Any]:
    delta = None
    if t.take_pnl_pct is not None:
        delta = round(t.realized_pnl_pct - float(t.take_pnl_pct), 4)
    return {
        "symbol": t.symbol,
        "entry_time": t.entry_time,
        "entry_price": t.entry_price,
        "close_time": t.close_time,
        "close_price": t.close_price,
        "close_reason": t.close_reason,
        "realized_pnl_pct": t.realized_pnl_pct,
        "continuation_quality_score": t.entry_quality,
        "quality_tier": t.quality_tier,
        "quality_band": quality_band(t.entry_quality),
        "session_bucket": t.session_bucket,
        "hold_duration_sec": t.hold_duration_sec,
        "mfe_pct": t.mfe_pct,
        "mae_pct": t.mae_pct,
        "tick_count": t.tick_count,
        "take_time": t.take_time,
        "take_pnl_pct": t.take_pnl_pct,
        "take_reason": t.take_reason,
        "take_to_exit_pnl_delta": delta,
        "had_take_before_exit": bool(t.take_time),
    }


def _exit_reason_rows(metrics: Mapping[str, Any], trades: Sequence[StructuralTrade]) -> list[dict[str, Any]]:
    dist = metrics.get("exit_reason_distribution") or {}
    n = max(1, int(metrics.get("structural_trade_count") or 0))
    rows = []
    for reason, count in sorted(dist.items(), key=lambda x: (-x[1], x[0])):
        pnls = [t.realized_pnl_pct for t in trades if t.close_reason == reason]
        rows.append(
            {
                "close_reason": reason,
                "trade_count": count,
                "pct_of_trades": round(100.0 * count / n, 2),
                "avg_pnl_pct": round(statistics.mean(pnls), 4) if pnls else None,
                "profit_factor": round(_profit_factor(pnls), 4)
                if pnls and _profit_factor(pnls) not in (None, float("inf"))
                else (_profit_factor(pnls) if pnls else None),
            }
        )
    return rows


def run_structural_observer_review(
    session_dir: Path,
    *,
    pilot_config: Any,
    poll_interval_sec: Optional[float] = None,
    structural_exit_policy: str = DEFAULT_OFFICIAL_EXIT_POLICY,
) -> dict[str, Any]:
    session_dir = session_dir.resolve()
    if structural_exit_policy not in (POLICY_STRUCTURAL_OBSERVER_V1, POLICY_COMBINED_STRUCTURAL_EXIT_V1):
        raise ValueError(f"unsupported structural_exit_policy: {structural_exit_policy}")

    summary = _load_json(session_dir / "small_paper_summary.json")
    events = _load_events(session_dir)
    interval = poll_interval_sec if poll_interval_sec is not None else float(
        summary.get("poll_interval_sec") or 5.0
    )
    session_end = _session_end_time(events)

    baseline_trades, baseline_log = replay_structural_observer_v1(
        events,
        pilot_config=pilot_config,
        poll_interval_sec=interval,
        session_end=session_end,
    )
    combined_trades, combined_log = replay_combined_structural_exit_v1(
        events,
        pilot_config=pilot_config,
        poll_interval_sec=interval,
        session_end=session_end,
    )

    baseline_metrics = _summarize_structural_trades(baseline_trades)
    combined_metrics = _summarize_structural_trades(combined_trades)
    legacy = _legacy_virtual_hold_summary(events)

    if structural_exit_policy == POLICY_COMBINED_STRUCTURAL_EXIT_V1:
        official_trades = combined_trades
        official_log = combined_log
        official_metrics = combined_metrics
    else:
        official_trades = baseline_trades
        official_log = baseline_log
        official_metrics = baseline_metrics

    verdict_block = _compute_official_verdict(
        official_metrics,
        baseline_metrics=baseline_metrics,
    )

    forbidden_in_official = [
        t for t in official_trades
        if t.close_reason in FORBIDDEN_OFFICIAL_EXIT_REASONS
        or "virtual_hold" in (t.close_reason or "")
    ]

    official_role = "official"
    policy_comparison = [
        _policy_comparison_row(
            POLICY_STRUCTURAL_OBSERVER_V1,
            baseline_metrics,
            role="baseline_reference" if structural_exit_policy != POLICY_STRUCTURAL_OBSERVER_V1 else official_role,
        ),
        _policy_comparison_row(
            POLICY_COMBINED_STRUCTURAL_EXIT_V1,
            combined_metrics,
            role=official_role if structural_exit_policy == POLICY_COMBINED_STRUCTURAL_EXIT_V1 else "candidate",
        ),
    ]

    return {
        "phase": 60,
        "mode": "structural_observer_review",
        "structural_exit_policy": structural_exit_policy,
        "policy": structural_exit_policy,
        "observer_only": True,
        "what_if_only": False,
        "session_dir": str(session_dir),
        "session_end_time": session_end,
        "poll_interval_sec": interval,
        "policy_context": {
            "min_continuation_quality": summary.get("min_continuation_quality", 0.7),
            "max_concurrent_positions": summary.get("max_concurrent_positions", 3),
            "policy_label": summary.get("policy_label"),
        },
        **official_metrics,
        "structural_metrics": official_metrics,
        "baseline_structural_observer_v1_metrics": baseline_metrics,
        "combined_structural_exit_v1_metrics": combined_metrics,
        "legacy_comparison": legacy,
        **legacy,
        **verdict_block,
        "live_observer_continue_worthwhile": verdict_block.get("official_verdict") == VERDICT_PASS,
        "validation": {
            "forbidden_exit_reason_in_official_trades": len(forbidden_in_official),
            "take_used_as_exit": False,
            "note": (
                f"Official metrics use {structural_exit_policy}; "
                "legacy_* fields are reference only."
            ),
        },
        "_structural_trades": [_trade_to_row(t) for t in official_trades],
        "_structural_events": official_log,
        "_exit_reason_rows": _exit_reason_rows(official_metrics, official_trades),
        "_policy_comparison_rows": policy_comparison,
        "_exit_policy_summary_rows": _exit_policy_summary_rows(
            structural_exit_policy,
            official_metrics,
            baseline_metrics,
            legacy,
        ),
    }


def write_structural_observer_review(session_dir: Path, review: Mapping[str, Any]) -> dict[str, Path]:
    session_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    public = {k: v for k, v in review.items() if not k.startswith("_")}
    json_path = session_dir / "structural_observer_review.json"
    json_path.write_text(json.dumps(public, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    paths["json"] = json_path

    trades = review.get("_structural_trades") or []
    if trades:
        p = session_dir / "structural_trades.csv"
        with p.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(trades[0].keys()), extrasaction="ignore")
            w.writeheader()
            w.writerows(trades)
        paths["trades_csv"] = p

    events = review.get("_structural_events") or []
    if events:
        p = session_dir / "structural_events.csv"
        fields = sorted({k for row in events for k in row})
        with p.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(events)
        paths["events_csv"] = p

    exit_rows = review.get("_exit_reason_rows") or []
    if exit_rows:
        p = session_dir / "structural_exit_reasons.csv"
        with p.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(exit_rows[0].keys()), extrasaction="ignore")
            w.writeheader()
            w.writerows(exit_rows)
        paths["exit_reasons_csv"] = p

    for key, name in (
        ("_policy_comparison_rows", "structural_policy_comparison.csv"),
        ("_exit_policy_summary_rows", "structural_exit_policy_summary.csv"),
    ):
        rows = review.get(key) or []
        if not rows:
            continue
        p = session_dir / name
        with p.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        paths[name.replace(".csv", "")] = p

    return paths


def build_and_write_structural_observer_review(
    session_dir: Path,
    *,
    pilot_config: Any,
    poll_interval_sec: Optional[float] = None,
    structural_exit_policy: str = DEFAULT_OFFICIAL_EXIT_POLICY,
) -> dict[str, Any]:
    review = run_structural_observer_review(
        session_dir,
        pilot_config=pilot_config,
        poll_interval_sec=poll_interval_sec,
        structural_exit_policy=structural_exit_policy,
    )
    paths = write_structural_observer_review(session_dir, review)
    public = {k: v for k, v in review.items() if not k.startswith("_")}
    public["output_files"] = {k: str(v) for k, v in paths.items()}
    paths["json"].write_text(json.dumps(public, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return public
