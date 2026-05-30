"""
Phase 44: Small paper pilot dry-run runner (no order placement).
"""

from __future__ import annotations

import csv
import json
import signal
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.exposure_gate import ExposureGate, ExposureGateConfig, run_exposure_gate_simulation
from research.research_exit_criteria import _load_csv
from small_paper.config import SmallPaperPilotConfig
from small_paper.discord_notifier import (
    SmallPaperDiscordNotifier,
    build_session_summary_extras,
    discord_notifier_from_pilot,
    observer_tracker_config_from_pilot,
)
from small_paper.observer_position_tracker import (
    OBSERVER_EXIT,
    OBSERVER_HOLD,
    OBSERVER_TAKE,
    ObserverJudgmentEvent,
    ObserverPositionTracker,
)
from small_paper.live_writer import LiveSessionWriter
from small_paper.session_schedule import (
    SessionSchedule,
    empty_bucket_summary,
    session_bucket,
    wait_until,
)

JST = ZoneInfo("Asia/Tokyo")

EVENT_FIELDS = (
    "event_time",
    "event_type",
    "symbol",
    "profile",
    "entry_time",
    "exit_time",
    "continuation_quality_score",
    "quality_tier",
    "gate_accept",
    "gate_reject_reason",
    "pnl_pct",
    "exit_reason",
    "dry_run",
    "source",
    "message_index",
    "current_price",
    "quality_fallback_path",
    "live_feature_complete",
    "rolling_mfe_pct",
    "rolling_mae_pct",
    "momentum_continuation_score",
    "pure_price_momentum",
    "peak_pure_price_momentum",
    "price_momentum_fade_ratio",
    "favorable_continuation",
    "max_continuation_duration",
    "adverse_shrinking",
    "quality_components_json",
    "symbol_cooloff_reason",
    "prior_avg_pnl",
    "prior_trades",
    "daytrade_suitability_score",
    "daytrade_suitability_threshold",
    "atr_pct",
    "intraday_range_pct",
    "trading_value",
    "turnover_proxy",
    "low_liquidity_shadow_rejected",
    "low_liquidity_shadow_reason",
    "low_liquidity_shadow_trading_value",
    "low_liquidity_shadow_turnover_proxy",
    "tick_size",
    "hold_sec",
    "entry_price",
    "exit_price",
    "structural_exit_reason",
    "peak_mfe_pct",
    "trailing_mfe_activated",
    "stop_hit",
    "session_close",
    "overlap_replaced_review",
    "extended_entry_shadow_flag",
    "extended_entry_shadow_reasons",
    "entry_rise_5min_pct",
    "entry_rise_10min_pct",
    "entry_vwap_dev_pct",
    "entry_near_day_high_pct",
    "entry_high_break_recent",
    "entry_rolling_mfe_pct",
    "entry_momentum_continuation_score",
    "high_quality_low_momentum_shadow_flag",
    "r30_sec",
    "r60_sec",
    "r120_sec",
    "extended_plus_early_adverse_shadow_flag",
    "vwap_shadow_reject_candidate",
    "vwap_shadow_reject_reason",
    "trailing_mfe_exit",
    "shadow_quality_score",
    "shadow_quality_rank",
    "current_quality_rank",
    "trading_value_band",
    "tv_sweet_band_flag",
    "entry_order_book_imbalance",
    "entry_imbalance_percentile",
    "imbalance_shadow_candidate",
    "imbalance_shadow_tier",
)


@dataclass
class PilotRunResult:
    output_dir: Path
    summary: dict[str, Any]
    events: list[dict[str, Any]] = field(default_factory=list)
    accepted: list[dict[str, Any]] = field(default_factory=list)
    rejects: list[dict[str, Any]] = field(default_factory=list)


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def run_replay_dry_run(
    config: SmallPaperPilotConfig,
    *,
    trades_csv: Path,
    output_dir: Path,
) -> PilotRunResult:
    """Replay candidate trades through exposure gate; no orders."""
    trades = _load_csv(trades_csv)
    gate = config.make_exposure_gate()
    gate_cfg = config.exposure_gate_config()
    accepted, rejects = run_exposure_gate_simulation(
        trades, gate_cfg, allowed_windows=config.allowed_windows()
    )

    events: list[dict[str, Any]] = []
    for t in trades:
        if str(t.get("profile")) != config.profile:
            continue
        events.append(
            {
                "event_time": _now_iso(),
                "event_type": "candidate",
                "symbol": t.get("symbol"),
                "profile": t.get("profile"),
                "entry_time": t.get("entry_time"),
                "exit_time": t.get("exit_time"),
                "continuation_quality_score": t.get("continuation_quality_score"),
                "quality_tier": t.get("quality_tier"),
                "gate_accept": None,
                "gate_reject_reason": "",
                "pnl_pct": t.get("pnl_pct"),
                "exit_reason": t.get("exit_reason"),
                "dry_run": True,
                "source": "replay",
            }
        )

    for row in accepted:
        events.append(
            {
                "event_time": _now_iso(),
                "event_type": "accepted",
                "symbol": row.get("symbol"),
                "profile": row.get("profile"),
                "entry_time": row.get("entry_time"),
                "exit_time": row.get("exit_time"),
                "continuation_quality_score": row.get("continuation_quality_score"),
                "quality_tier": row.get("quality_tier"),
                "gate_accept": True,
                "gate_reject_reason": "",
                "pnl_pct": row.get("pnl_pct"),
                "exit_reason": row.get("exit_reason"),
                "dry_run": True,
                "source": "replay",
            }
        )
    for row in rejects:
        events.append(
            {
                "event_time": _now_iso(),
                "event_type": "rejected",
                "symbol": row.get("symbol"),
                "profile": row.get("profile"),
                "entry_time": row.get("entry_time"),
                "exit_time": row.get("exit_time"),
                "continuation_quality_score": row.get("continuation_quality_score"),
                "quality_tier": row.get("quality_tier"),
                "gate_accept": False,
                "gate_reject_reason": row.get("gate_reject_reason"),
                "pnl_pct": row.get("pnl_pct"),
                "exit_reason": row.get("exit_reason"),
                "dry_run": True,
                "source": "replay",
            }
        )

    positions = _build_positions_snapshot(accepted, gate)
    summary = {
        "phase": 44,
        "mode": "small_paper_pilot_dry_run",
        "generated_at": _now_iso(),
        "order_enabled": False,
        "paper_only": True,
        "profile": config.profile,
        "entry_profile": config.entry_profile,
        "source": "replay",
        "trades_csv": str(trades_csv),
        "candidate_count": len([e for e in events if e["event_type"] == "candidate"]),
        "accepted_count": len(accepted),
        "rejected_count": len(rejects),
        "reject_reason_counts": _count_reasons(rejects),
        "config": {
            "min_continuation_quality": config.min_continuation_quality,
            "max_concurrent_positions": config.max_concurrent_positions,
        },
    }

    _write_outputs(output_dir, events=events, accepted=accepted, rejects=rejects, positions=positions, summary=summary)
    return PilotRunResult(output_dir=output_dir, summary=summary, events=events, accepted=accepted, rejects=rejects)


def _symbol_from_push(payload: Mapping[str, Any], code_to_symbol: Mapping[str, str]) -> str:
    code = str(payload.get("Symbol") or "").strip()
    if code in code_to_symbol:
        return code_to_symbol[code]
    if code.endswith(".T"):
        return code.upper()
    return f"{code}.T" if code else ""


def _candidate_trade_from_push(
    payload: Mapping[str, Any],
    *,
    symbol: str,
    profile: str,
    virtual_hold_sec: float = 300.0,
    feature_snapshot: Optional[Any] = None,
) -> dict[str, Any]:
    """Map PUSH/board payload to gate input (virtual hold for concurrent cap only)."""
    from research.continuation_quality_ranking import continuation_quality_score
    from small_paper.live_feature_bridge import LiveFeatureBridge
    from storage.intraday_recorder import parse_kabu_time

    now = datetime.now(JST)
    ent = parse_kabu_time(payload.get("CurrentPriceTime"), fallback=now)
    ex_ts = ent.timestamp() + virtual_hold_sec
    ex = datetime.fromtimestamp(ex_ts, tz=JST)
    mfe = payload.get("max_favorable_excursion_pct")
    if mfe is None:
        mfe = payload.get("rolling_mfe_pct")
    mae = payload.get("max_adverse_excursion_pct")
    if mae is None:
        mae = payload.get("rolling_mae_pct")
    trade = {
        "profile": profile,
        "symbol": symbol,
        "entry_time": ent.isoformat(),
        "exit_time": ex.isoformat(),
        "trade_date": ent.date().isoformat(),
        "pnl_pct": 0.0,
        "exit_reason": "live_virtual_hold",
        "momentum_continuation_score": payload.get("momentum_continuation_score"),
        "bullish_continuation_score": payload.get("bullish_continuation_score")
        or payload.get("bullish_weighted_score"),
        "favorable_continuation": payload.get("favorable_continuation")
        or payload.get("favorable_weighted"),
        "bearish_accumulation_score": payload.get("bearish_accumulation_score")
        or payload.get("bearish_weighted_score"),
        "max_favorable_excursion_pct": mfe,
        "max_adverse_excursion_pct": mae,
        "max_continuation_duration": payload.get("max_continuation_duration"),
    }
    from small_paper.daytrade_suitability_gate import attach_entry_metrics_to_trade

    attach_entry_metrics_to_trade(trade, payload)
    q = continuation_quality_score(trade)
    trade["continuation_quality_score"] = round(q, 4)
    if feature_snapshot is not None:
        trade.update(LiveFeatureBridge.trade_quality_extras(trade, feature_snapshot))
    return trade


def _event_from_gate(
    *,
    event_type: str,
    trade: Mapping[str, Any],
    decision: Any,
    source: str,
    message_index: int,
    current_price: Any = None,
) -> dict[str, Any]:
    base = {
        "event_time": _now_iso(),
        "event_type": event_type,
        "symbol": trade.get("symbol"),
        "profile": trade.get("profile"),
        "entry_time": trade.get("entry_time"),
        "exit_time": trade.get("exit_time"),
        "continuation_quality_score": trade.get("continuation_quality_score"),
        "quality_tier": getattr(decision, "quality_tier", ""),
        "gate_accept": decision.accept if event_type != "candidate" else None,
        "gate_reject_reason": "" if decision.accept else getattr(decision, "reason", ""),
        "pnl_pct": trade.get("pnl_pct"),
        "exit_reason": trade.get("exit_reason"),
        "dry_run": True,
        "source": source,
        "message_index": message_index,
        "current_price": current_price,
    }
    for key in EVENT_FIELDS:
        if key in (
            "event_time",
            "event_type",
            "gate_accept",
            "gate_reject_reason",
            "quality_tier",
            "current_price",
        ):
            continue
        if key in trade:
            base[key] = trade.get(key)
    if decision.accept:
        for key in (
            "daytrade_suitability_score",
            "daytrade_suitability_threshold",
            "atr_pct",
            "intraday_range_pct",
            "trading_value",
            "turnover_proxy",
            "tick_size",
            "tick_ratio_pct",
            "low_liquidity_shadow_rejected",
            "low_liquidity_shadow_reason",
            "low_liquidity_shadow_trading_value",
            "low_liquidity_shadow_turnover_proxy",
        ):
            if trade.get(key) not in (None, ""):
                base[key] = trade.get(key)
    else:
        base["symbol_cooloff_reason"] = getattr(decision, "symbol_cooloff_reason", "") or ""
        base["prior_avg_pnl"] = getattr(decision, "prior_avg_pnl", None)
        base["prior_trades"] = getattr(decision, "prior_trades", 0) or 0
        base["daytrade_suitability_score"] = getattr(decision, "daytrade_suitability_score", None)
        base["daytrade_suitability_threshold"] = getattr(
            decision, "daytrade_suitability_threshold", None
        )
        base["atr_pct"] = getattr(decision, "atr_pct", None)
        base["intraday_range_pct"] = getattr(decision, "intraday_range_pct", None)
        base["trading_value"] = getattr(decision, "trading_value", None)
        base["turnover_proxy"] = getattr(decision, "turnover_proxy", None)
        base["tick_size"] = getattr(decision, "entry_price_risk_guard_tick_size", None)
        base["tick_ratio_pct"] = getattr(decision, "entry_price_risk_guard_tick_ratio_pct", None)
        trigger = getattr(decision, "entry_price_risk_guard_trigger", "") or ""
        if trigger:
            base["entry_price_risk_guard_trigger"] = trigger
    return base


def verify_kabu_connection(repo_root: Path, *, symbol_key: str = "9984@1") -> dict[str, Any]:
    """Token + board probe; no orders."""
    import os

    from api.rest_client import KabuNativeApiError, KabuNativeRestClient, default_base_url, load_kabu_env

    if not os.environ.get("KABU_API_PASSWORD", "").strip():
        raise KabuNativeApiError("KABU_API_PASSWORD is not set")
    load_kabu_env(repo_root=repo_root)
    client = KabuNativeRestClient(default_base_url())
    token = client.issue_token_from_env()
    board = client.get_board(symbol_key, token=token)
    return {
        "ok": True,
        "symbol_key": symbol_key,
        "current_price": board.get("CurrentPrice"),
        "current_price_time": board.get("CurrentPriceTime"),
    }


def _default_extended_shadow_counters() -> Any:
    from small_paper.extended_entry_shadow import ExtendedEntryShadowCounters

    return ExtendedEntryShadowCounters()


def _default_vwap_shadow_counters() -> Any:
    from small_paper.vwap_shadow_reject import VwapShadowRejectCounters

    return VwapShadowRejectCounters()


def _default_board_imbalance_shadow_counters() -> Any:
    from small_paper.board_imbalance_shadow import BoardImbalanceShadowCounters

    return BoardImbalanceShadowCounters()


@dataclass
class _LiveRunState:
    started_mono: float
    session_force_close_done: bool = False
    events: list[dict[str, Any]] = field(default_factory=list)
    accepted_rows: list[dict[str, Any]] = field(default_factory=list)
    reject_rows: list[dict[str, Any]] = field(default_factory=list)
    push_messages: int = 0
    gate_evaluations: int = 0
    heartbeat_count: int = 0
    api_error_count: int = 0
    consecutive_api_errors: int = 0
    reconnect_count: int = 0
    stale_tick_count: int = 0
    data_gap_count: int = 0
    peak_open_slots: int = 0
    quality_scores: list[float] = field(default_factory=list)
    quality_fallback_count: int = 0
    live_feature_complete_count: int = 0
    bucket_summary: dict[str, dict[str, int]] = field(default_factory=empty_bucket_summary)
    stop_reason: str = ""
    stop_requested: bool = False
    symbol_cooloff_reject_count: int = 0
    daytrade_suitability_reject_count: int = 0
    entry_price_risk_guard_reject_count: int = 0
    low_liquidity_shadow_reject_count: int = 0
    intraday_refresh_done: bool = False
    intraday_refresh_count: int = 0
    intraday_refresh_triggered_count: int = 0
    intraday_refresh_completed_count: int = 0
    intraday_refresh_failed_count: int = 0
    intraday_refresh_last_time: str = ""
    intraday_refresh_last_register_count: int = 0
    intraday_refresh_enabled: bool = False
    intraday_refresh_csv: str = ""
    intraday_refresh_scheduled_time: str = ""
    outside_refresh_universe_reject_count: int = 0
    session_momentum_samples: list[float] = field(default_factory=list)
    session_order_book_imbalance_samples: list[float] = field(default_factory=list)
    extended_entry_shadow: Any = field(default_factory=_default_extended_shadow_counters)
    vwap_shadow_reject: Any = field(default_factory=_default_vwap_shadow_counters)
    board_imbalance_shadow: Any = field(default_factory=_default_board_imbalance_shadow_counters)


def _quality_distribution(scores: Sequence[float]) -> dict[str, int]:
    dist = {"lt_0.55": 0, "0.55_0.65": 0, "0.65_0.75": 0, "ge_0.75": 0}
    for q in scores:
        if q < 0.55:
            dist["lt_0.55"] += 1
        elif q < 0.65:
            dist["0.55_0.65"] += 1
        elif q < 0.75:
            dist["0.65_0.75"] += 1
        else:
            dist["ge_0.75"] += 1
    return dist


_SESSION_CLOSE_EXIT_REASONS = frozenset(
    {"morning_session_close", "afternoon_session_close", "session_end"}
)


def _as_float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _infer_exit_policy_shadow(config: SmallPaperPilotConfig) -> str:
    pol = str(getattr(config, "structural_exit_policy", "") or "")
    if "trailing_mfe" in pol:
        return "trailing-mfe"
    if "fade_hybrid" in pol or "fade-hybrid" in pol:
        return "fade-hybrid"
    if "fade_breakdown" in pol or "fade-breakdown" in pol:
        return "fade-breakdown"
    return ""


def _execution_audit_fields(
    config: SmallPaperPilotConfig,
    session_cfg: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "order_enabled": False,
        "paper_only": True,
        "shadow_only": bool(getattr(config, "shadow_only", False)),
        "structural_exit_policy": getattr(config, "structural_exit_policy", "") or "",
        "low_liquidity_shadow": bool(getattr(config, "low_liquidity_shadow_enabled", False)),
        "exit_policy_shadow": _infer_exit_policy_shadow(config),
        "intraday_refresh_enabled": False,
    }
    if session_cfg:
        out["config_path"] = session_cfg.get("config_path", "")
        out["config_sha256"] = session_cfg.get("config_sha256", "")
        out["intraday_refresh_enabled"] = bool(session_cfg.get("intraday_refresh_enabled"))
        if session_cfg.get("universe_mode"):
            out["universe_mode"] = session_cfg.get("universe_mode")
        if session_cfg.get("exit_policy_shadow"):
            out["exit_policy_shadow"] = session_cfg.get("exit_policy_shadow")
    return out


def _enrich_accept_audit_fields(
    trade: dict[str, Any],
    *,
    gate: ExposureGate,
    current_price: Any = None,
) -> None:
    """Phase180: suitability + tick fields on every accepted event."""
    suit = getattr(gate, "daytrade_suitability", None)
    if suit is not None:
        chk = suit.check(trade)
        if chk.score is not None:
            trade["daytrade_suitability_score"] = chk.score
        if chk.threshold is not None:
            trade["daytrade_suitability_threshold"] = chk.threshold
        for key in ("atr_pct", "intraday_range_pct", "trading_value", "turnover_proxy"):
            val = getattr(chk, key, None)
            if val is not None and trade.get(key) in (None, ""):
                trade[key] = val
    if trade.get("daytrade_suitability_score") is None:
        vls = trade.get("volatility_liquidity_score")
        if vls is not None:
            trade["daytrade_suitability_score"] = vls
    if current_price is not None and trade.get("current_price") in (None, ""):
        trade["current_price"] = current_price
    guard = getattr(gate, "entry_price_risk_guard", None)
    if guard is not None:
        gr = guard.check(trade)
        if gr.tick_size_yen:
            trade["tick_size"] = gr.tick_size_yen
        if gr.tick_ratio_pct is not None:
            trade["tick_ratio_pct"] = gr.tick_ratio_pct
        if gr.current_price and trade.get("current_price") in (None, ""):
            trade["current_price"] = gr.current_price


def _observer_exit_event_row(
    ev: ObserverJudgmentEvent,
    *,
    source: str,
    message_index: int,
    profile: str,
) -> dict[str, Any]:
    ctx = ev.context
    reason = str(ctx.get("exit_reason") or "")
    pnl = ctx.get("realized_pnl_pct", ctx.get("unrealized_pnl_pct"))
    peak_mfe = ctx.get("peak_mfe_pct", ctx.get("peak_pnl_pct", ctx.get("mfe_pct")))
    row: dict[str, Any] = {
        "event_time": _now_iso(),
        "event_type": "observer_exit",
        "symbol": ev.symbol,
        "profile": ctx.get("profile") or profile,
        "entry_time": ctx.get("entry_time", ""),
        "exit_time": ctx.get("exit_time", ctx.get("timestamp", "")),
        "hold_sec": ctx.get("hold_sec", ctx.get("hold_duration_sec")),
        "entry_price": ctx.get("entry_price"),
        "exit_price": ctx.get("current_price"),
        "pnl_pct": pnl,
        "exit_reason": reason,
        "structural_exit_reason": ctx.get("structural_exit_reason") or (
            reason if ctx.get("is_structural_exit") else ""
        ),
        "rolling_mfe_pct": ctx.get("rolling_mfe_pct", peak_mfe),
        "rolling_mae_pct": ctx.get("rolling_mae_pct", ctx.get("mae_pct")),
        "peak_mfe_pct": peak_mfe,
        "trailing_mfe_activated": bool(ctx.get("trailing_mfe_activated"))
        or bool(
            ctx.get("trailing_mfe_active")
            or ctx.get("trailing_mfe_threshold_reached")
            or ctx.get("trailing_mfe_exit_triggered")
        ),
        "stop_hit": bool(ctx.get("stop_hit")) or reason == "stop_hit",
        "session_close": bool(ctx.get("session_close")) or reason in _SESSION_CLOSE_EXIT_REASONS,
        "overlap_replaced_review": bool(ctx.get("overlap_replaced_review"))
        or reason == "overlap_replaced_review",
        "dry_run": True,
        "source": source,
        "message_index": message_index,
        "structural_exit_policy": ctx.get("structural_exit_policy", ""),
    }
    for key in EVENT_FIELDS:
        if key in row:
            continue
        if key in ctx and ctx.get(key) not in (None, ""):
            row[key] = ctx.get(key)
    return row


def _log_and_dispatch_observer_events(
    events: Sequence[ObserverJudgmentEvent],
    *,
    discord: Optional[SmallPaperDiscordNotifier],
    writer: Optional[LiveSessionWriter] = None,
    state: Optional["_LiveRunState"] = None,
    source: str = "",
    message_index: int = 0,
    profile: str = "",
) -> None:
    for ev in events:
        if ev.kind == OBSERVER_EXIT:
            row = _observer_exit_event_row(
                ev,
                source=source,
                message_index=message_index,
                profile=profile,
            )
            if state is not None:
                state.events.append(row)
                counters = getattr(state, "extended_entry_shadow", None)
                if counters is not None:
                    counters.record_exit(row)
                vwap_counters = getattr(state, "vwap_shadow_reject", None)
                if vwap_counters is not None:
                    vwap_counters.record_exit(row)
                imb_counters = getattr(state, "board_imbalance_shadow", None)
                if imb_counters is not None:
                    imb_counters.record_exit(row)
            if writer is not None:
                writer.append_event(row)
    _dispatch_observer_events(events, discord=discord)


def _dispatch_observer_events(
    events: Sequence[ObserverJudgmentEvent],
    *,
    discord: Optional[SmallPaperDiscordNotifier],
) -> None:
    if not discord or not discord.active:
        return
    for ev in events:
        try:
            if ev.kind == OBSERVER_HOLD:
                discord.notify_hold(context=ev.context)
            elif ev.kind == OBSERVER_TAKE:
                discord.notify_take(context=ev.context)
            elif ev.kind == OBSERVER_EXIT:
                if not ev.context.get("is_structural_exit"):
                    continue
                discord.notify_exit(context=ev.context)
        except Exception:
            pass


def _extended_shadow_summary_fields(state: _LiveRunState) -> dict[str, Any]:
    counters = getattr(state, "extended_entry_shadow", None)
    if counters is None:
        return {}
    return counters.summary_fields()


def _vwap_shadow_summary_fields(state: _LiveRunState) -> dict[str, Any]:
    counters = getattr(state, "vwap_shadow_reject", None)
    if counters is None:
        return {}
    return counters.summary_fields()


def _board_imbalance_shadow_summary_fields(state: _LiveRunState) -> dict[str, Any]:
    counters = getattr(state, "board_imbalance_shadow", None)
    if counters is None:
        return {}
    return counters.summary_fields()


def _apply_quality_formula_shadow_finalize(
    state: _LiveRunState,
    summary: dict[str, Any],
) -> None:
    from small_paper.quality_formula_shadow import finalize_session_quality_shadow

    summary.update(finalize_session_quality_shadow(state.accepted_rows, state.events))


def _apply_trading_value_shadow_finalize(
    state: _LiveRunState,
    summary: dict[str, Any],
) -> None:
    from small_paper.trading_value_shadow_gate import finalize_session_trading_value_shadow

    summary.update(finalize_session_trading_value_shadow(state.accepted_rows, state.events))


def _apply_board_imbalance_shadow_finalize(
    state: _LiveRunState,
    summary: dict[str, Any],
) -> None:
    from small_paper.board_imbalance_shadow import finalize_session_board_imbalance_shadow

    summary.update(finalize_session_board_imbalance_shadow(state.accepted_rows, state.events))


def _record_bucket(state: _LiveRunState, event_type: str) -> None:
    bucket = session_bucket()
    if bucket in state.bucket_summary and event_type in state.bucket_summary[bucket]:
        state.bucket_summary[bucket][event_type] += 1


def _tick_age_sec(payload: Mapping[str, Any]) -> Optional[float]:
    from storage.intraday_recorder import parse_kabu_time

    raw = payload.get("CurrentPriceTime")
    if raw is None or raw == "":
        return None
    tick = parse_kabu_time(raw, fallback=datetime.now(JST))
    return max(0.0, (datetime.now(JST) - tick).total_seconds())


@dataclass
class _PushPipelineContext:
    config: SmallPaperPilotConfig
    gate: ExposureGate
    feature_bridge: Any
    state: _LiveRunState
    writer: LiveSessionWriter
    code_to_symbol: dict[str, str]
    source: str
    pos_fields: Sequence[str]
    observer: Optional[ObserverPositionTracker] = None
    discord: Optional[SmallPaperDiscordNotifier] = None
    stale_tick_sec: float = 120.0
    gap_threshold_sec: float = 15.0
    last_symbol_tick: dict[str, float] = field(default_factory=dict)
    am_pm_policy: Optional[Any] = None
    entry_eligible_symbols: Optional[set[str]] = None
    symbol_price_ring: dict[str, list[tuple[float, float]]] = field(default_factory=dict)


REJECT_OUTSIDE_REFRESH_UNIVERSE = "outside_refresh_universe"


def _process_push_payload(
    ctx: _PushPipelineContext,
    payload: Mapping[str, Any],
    msg_i: int,
    *,
    symbol: Optional[str] = None,
) -> None:
    sym = symbol or _symbol_from_push(payload, ctx.code_to_symbol)
    if not sym:
        return
    ctx.state.push_messages = msg_i
    age = _tick_age_sec(payload)
    if age is not None and age > ctx.stale_tick_sec:
        ctx.state.stale_tick_count += 1
    import time

    now_m = time.monotonic()
    prev = ctx.last_symbol_tick.get(sym)
    if prev is not None and (now_m - prev) > ctx.gap_threshold_sec:
        ctx.state.data_gap_count += 1
    ctx.last_symbol_tick[sym] = now_m

    from small_paper.extended_entry_shadow import append_price_tick, tick_ts_from_payload

    try:
        px_tick = float(payload.get("CurrentPrice") or 0)
    except (TypeError, ValueError):
        px_tick = 0.0
    if px_tick > 0:
        ring = ctx.symbol_price_ring.setdefault(sym, [])
        append_price_tick(ring, ts=tick_ts_from_payload(payload), px=px_tick)

    snapshot = ctx.feature_bridge.update(sym, payload)
    enriched = ctx.feature_bridge.enrich_payload(payload, snapshot)
    trade = _candidate_trade_from_push(
        enriched,
        symbol=sym,
        profile=ctx.config.profile,
        feature_snapshot=snapshot,
    )
    # Phase168: ensure gate sees live price fields.
    # Guard expects one of ("current_price", "CurrentPrice", "entry_price", "close_price").
    # In live, price is carried in the push payload and used by event logging; but the gate
    # evaluates the `trade` mapping, so we must inject it here.
    try:
        live_px = payload.get("CurrentPrice")
    except Exception:
        live_px = None
    if live_px is not None:
        # keep both keys for compatibility with downstream guard readers
        trade.setdefault("CurrentPrice", live_px)
        trade.setdefault("current_price", live_px)
    if snapshot.quality_fallback_path:
        ctx.state.quality_fallback_count += 1
    if snapshot.live_feature_complete:
        ctx.state.live_feature_complete_count += 1
    q = trade.get("continuation_quality_score")
    if isinstance(q, (int, float)):
        ctx.state.quality_scores.append(float(q))
    bucket = session_bucket()
    if ctx.observer and ctx.observer.has_open(sym):
        price = payload.get("CurrentPrice")
        obs_events = ctx.observer.on_tick(
            symbol=sym,
            trade=trade,
            payload=enriched,
            current_price=float(price) if price is not None else None,
            session_bucket=bucket,
        )
        _log_and_dispatch_observer_events(
            obs_events,
            discord=ctx.discord,
            writer=ctx.writer,
            state=ctx.state,
            source=ctx.source,
            message_index=msg_i,
            profile=ctx.config.profile,
        )
    from small_paper.am_pm_session_policy import AmPmSessionPolicy

    policy: Optional[AmPmSessionPolicy] = ctx.am_pm_policy
    if policy is not None and not policy.entry_allowed_now():
        from research.exposure_gate import GateDecision

        decision = GateDecision(
            accept=False,
            reason="am_pm_entry_stop",
            continuation_quality_score=float(trade.get("continuation_quality_score") or 0),
            quality_tier="",
        )
    elif (
        ctx.entry_eligible_symbols is not None
        and sym not in ctx.entry_eligible_symbols
        and not (ctx.observer and ctx.observer.has_open(sym))
    ):
        from research.exposure_gate import GateDecision

        ctx.state.outside_refresh_universe_reject_count += 1
        decision = GateDecision(
            accept=False,
            reason=REJECT_OUTSIDE_REFRESH_UNIVERSE,
            continuation_quality_score=float(trade.get("continuation_quality_score") or 0),
            quality_tier="",
        )
    else:
        decision = ctx.gate.evaluate_entry(trade)
    ctx.state.gate_evaluations += 1

    cand = _event_from_gate(
        event_type="candidate",
        trade=trade,
        decision=decision,
        source=ctx.source,
        message_index=msg_i,
        current_price=payload.get("CurrentPrice"),
    )
    ctx.state.events.append(cand)
    ctx.writer.append_event(cand)
    _record_bucket(ctx.state, "candidate")

    if decision.accept:
        # Phase179: low-liquidity shadow-only reject (logging only; do not block accept).
        ll_shadow = bool(getattr(ctx.config, "low_liquidity_shadow_enabled", False))
        tv_min = float(getattr(ctx.config, "low_liquidity_shadow_trading_value_min", 1e8) or 1e8)
        to_min = float(getattr(ctx.config, "low_liquidity_shadow_turnover_proxy_min", 0.002) or 0.002)
        tv = _as_float(trade.get("trading_value"))
        to = _as_float(trade.get("turnover_proxy"))
        ll_rejected = False
        ll_reason = ""
        if ll_shadow:
            if tv is not None and float(tv) < tv_min:
                ll_rejected = True
                ll_reason = "trading_value_below_min"
            if not ll_rejected and to is not None and float(to) < to_min:
                ll_rejected = True
                ll_reason = "turnover_proxy_below_min"
            if ll_rejected:
                ctx.state.low_liquidity_shadow_reject_count += 1
        trade["low_liquidity_shadow_rejected"] = bool(ll_rejected)
        trade["low_liquidity_shadow_reason"] = ll_reason
        trade["low_liquidity_shadow_trading_value"] = tv
        trade["low_liquidity_shadow_turnover_proxy"] = to
        _enrich_accept_audit_fields(
            trade,
            gate=ctx.gate,
            current_price=payload.get("CurrentPrice"),
        )
        from small_paper.extended_entry_shadow import compute_entry_shadow_fields

        mom_sample = _as_float(trade.get("momentum_continuation_score"))
        if mom_sample is not None:
            ctx.state.session_momentum_samples.append(float(mom_sample))
        shadow = compute_entry_shadow_fields(
            trade=trade,
            payload=payload,
            price_ring=ctx.symbol_price_ring.get(sym, []),
            entry_ts=tick_ts_from_payload(payload),
            session_momentum_samples=ctx.state.session_momentum_samples,
        )
        trade.update(shadow)
        ctx.state.extended_entry_shadow.record_accept(shadow)
        from small_paper.vwap_shadow_reject import compute_vwap_shadow_reject_fields

        try:
            entry_px_vwap = float(payload.get("CurrentPrice") or 0)
        except (TypeError, ValueError):
            entry_px_vwap = 0.0
        vwap_shadow = compute_vwap_shadow_reject_fields(
            payload=payload,
            entry_px=entry_px_vwap,
            entry_vwap_dev_pct=shadow.get("entry_vwap_dev_pct"),
        )
        trade.update(vwap_shadow)
        ctx.state.vwap_shadow_reject.record_accept(vwap_shadow)
        from small_paper.board_imbalance_shadow import compute_board_imbalance_shadow_fields

        imb_shadow = compute_board_imbalance_shadow_fields(
            trade=trade,
            payload=payload,
            session_imbalance_samples=ctx.state.session_order_book_imbalance_samples,
        )
        trade.update(imb_shadow)
        ctx.state.board_imbalance_shadow.record_accept(imb_shadow)
        from small_paper.quality_formula_shadow import compute_shadow_quality_fields
        from small_paper.trading_value_shadow_gate import compute_trading_value_shadow_fields

        trade.update(compute_shadow_quality_fields(trade))
        trade.update(compute_trading_value_shadow_fields(trade))

        ctx.gate.record_accepted(trade)
        ctx.state.peak_open_slots = max(ctx.state.peak_open_slots, len(ctx.gate.state.open_slots))
        acc = _event_from_gate(
            event_type="accepted",
            trade=trade,
            decision=decision,
            source=ctx.source,
            message_index=msg_i,
            current_price=payload.get("CurrentPrice"),
        )
        ctx.state.events.append(acc)
        ctx.writer.append_event(acc)
        ctx.state.accepted_rows.append(dict(trade))
        _record_bucket(ctx.state, "accepted")
        ctx.writer.append_position_row(
            {
                "symbol": trade.get("symbol"),
                "entry_time": trade.get("entry_time"),
                "exit_time": trade.get("exit_time"),
                "open_slots_after": len(ctx.gate.state.open_slots),
            },
            fields=ctx.pos_fields,
        )
        if ctx.observer:
            try:
                entry_px = float(payload.get("CurrentPrice") or 0)
            except (TypeError, ValueError):
                entry_px = 0.0
            if entry_px > 0 and ctx.observer.has_open(sym):
                overlap_events = ctx.observer.close_for_overlap(
                    symbol=sym,
                    trade=trade,
                    payload=enriched,
                    current_price=entry_px,
                    session_bucket=bucket,
                )
                _log_and_dispatch_observer_events(
                    overlap_events,
                    discord=ctx.discord,
                    writer=ctx.writer,
                    state=ctx.state,
                    source=ctx.source,
                    message_index=msg_i,
                    profile=ctx.config.profile,
                )
            if entry_px > 0 and not ctx.observer.has_open(sym):
                ctx.observer.register_entry(
                    trade=trade,
                    payload=enriched,
                    quality_tier=str(decision.quality_tier or ""),
                    entry_price=entry_px,
                )
        if ctx.discord and ctx.discord.active:
            ctx.discord.notify_entry(
                event=acc,
                payload=enriched,
                open_slots=len(ctx.gate.state.open_slots),
                session_bucket=bucket,
            )
    else:
        rej_row = dict(trade)
        rej_row["gate_reject_reason"] = decision.reason
        if decision.reason == "symbol_cooloff":
            ctx.state.symbol_cooloff_reject_count += 1
            rej_row["symbol_cooloff_reason"] = getattr(decision, "symbol_cooloff_reason", "") or ""
            rej_row["prior_avg_pnl"] = getattr(decision, "prior_avg_pnl", None)
            rej_row["prior_trades"] = getattr(decision, "prior_trades", 0)
        if decision.reason == "daytrade_suitability":
            ctx.state.daytrade_suitability_reject_count += 1
            rej_row["daytrade_suitability_score"] = getattr(
                decision, "daytrade_suitability_score", None
            )
            rej_row["daytrade_suitability_threshold"] = getattr(
                decision, "daytrade_suitability_threshold", None
            )
            rej_row["atr_pct"] = getattr(decision, "atr_pct", None)
            rej_row["intraday_range_pct"] = getattr(decision, "intraday_range_pct", None)
            rej_row["trading_value"] = getattr(decision, "trading_value", None)
            rej_row["turnover_proxy"] = getattr(decision, "turnover_proxy", None)
        if decision.reason == "entry_price_risk_guard":
            from small_paper.entry_price_risk_guard import LOG_EVENT_KIND, REJECT_ENTRY_PRICE_RISK_GUARD

            ctx.state.entry_price_risk_guard_reject_count += 1
            guard_st = getattr(ctx.gate, "entry_price_risk_guard", None)
            min_px = float(getattr(guard_st.config, "min_entry_price", 50.0)) if guard_st else 50.0
            max_tr = float(getattr(guard_st.config, "max_tick_ratio_pct", 5.0)) if guard_st else 5.0
            tick_sz = getattr(decision, "entry_price_risk_guard_tick_size", None)
            tick_tr = getattr(decision, "entry_price_risk_guard_tick_ratio_pct", None)
            trigger = getattr(decision, "entry_price_risk_guard_trigger", "") or ""
            price_source = getattr(decision, "entry_price_risk_guard_price_source", "") or ""
            guard_price = getattr(decision, "entry_price_risk_guard_price", None)
            bypassed = bool(
                getattr(decision, "entry_price_risk_guard_shadow_missing_price_bypassed", False)
            )
            close_used = bool(
                getattr(decision, "entry_price_risk_guard_universe_close_price_used", False)
            )
            rej_row["reject_reason"] = REJECT_ENTRY_PRICE_RISK_GUARD
            rej_row["tick_size"] = tick_sz
            rej_row["tick_ratio_pct"] = tick_tr
            rej_row["min_entry_price"] = min_px
            rej_row["max_tick_ratio_pct"] = max_tr
            rej_row["entry_price_risk_guard_trigger"] = trigger
            rej_row["entry_price_risk_guard_price_source"] = price_source
            rej_row["entry_price_risk_guard_price"] = guard_price
            rej_row["entry_price_risk_guard_shadow_missing_price_bypassed"] = bypassed
            rej_row["entry_price_risk_guard_universe_close_price_used"] = close_used
            log_rec = {
                "event_kind": LOG_EVENT_KIND,
                "symbol": sym,
                "current_price": payload.get("CurrentPrice"),
                "tick_size": tick_sz,
                "tick_ratio_pct": tick_tr,
                "min_entry_price": min_px,
                "max_tick_ratio_pct": max_tr,
                "reject_reason": REJECT_ENTRY_PRICE_RISK_GUARD,
                "trigger": trigger,
                "price_source": price_source,
                "guard_price": guard_price,
                "shadow_missing_price_bypassed": bypassed,
                "universe_close_price_used": close_used,
            }
            ctx.writer.append_error(log_rec)
        ctx.state.reject_rows.append(rej_row)
        rej = _event_from_gate(
            event_type="rejected",
            trade=trade,
            decision=decision,
            source=ctx.source,
            message_index=msg_i,
            current_price=payload.get("CurrentPrice"),
        )
        ctx.state.events.append(rej)
        ctx.writer.append_event(rej)
        _record_bucket(ctx.state, "rejected")
        if ctx.discord and ctx.discord.active:
            ctx.discord.notify_rejected(
                event=rej,
                payload=enriched,
                open_slots=len(ctx.gate.state.open_slots),
                session_bucket=session_bucket(),
            )


def _quality_ge_0_55_count(scores: Sequence[float]) -> int:
    return sum(1 for q in scores if q >= 0.55)


def _policy_summary_extras(config: SmallPaperPilotConfig) -> dict[str, Any]:
    out = dict(config.policy_summary_fields())
    if config.policy_trial and config.policy_label in (
        "q070_cap3_trial",
        "q070_cap3_mfe_fav_trial",
        "q070_cap3_mfe_fav_symbol_cooloff_trial",
        "q070_cap3_mfe_fav_vol_liq_trial",
    ):
        from small_paper.live_observer_readiness import (
            DEFAULT_PHASE60_STRUCTURAL_SESSION_REL,
            DEFAULT_PHASE54_SESSION_REL,
            live_observer_retrial_summary_fields,
        )

        repo_root = Path(__file__).resolve().parents[3]
        ref = repo_root / DEFAULT_PHASE54_SESSION_REL
        struct_ref = repo_root / DEFAULT_PHASE60_STRUCTURAL_SESSION_REL
        out.update(
            live_observer_retrial_summary_fields(
                config,
                reference_session_dir=ref,
                structural_session_dir=struct_ref,
            )
        )
    return out


def _write_quality_top_debug(output_dir: Path, events: Sequence[Mapping[str, Any]], *, top_n: int = 100) -> None:
    candidates = [e for e in events if e.get("event_type") == "candidate"]
    top = sorted(
        candidates,
        key=lambda e: float(e.get("continuation_quality_score") or 0),
        reverse=True,
    )[:top_n]
    if not top:
        return
    out_json = output_dir / "quality_top_debug.json"
    out_json.write_text(json.dumps(top, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(output_dir / "quality_top_debug.csv", list(top[0].keys()), top)


def _parse_recorded_at_ts(recorded_at: str) -> float:
    try:
        return datetime.fromisoformat(str(recorded_at).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _load_push_replay_records(
    push_dir: Path,
    *,
    max_rows: Optional[int] = None,
) -> list[tuple[str, str, dict[str, Any]]]:
    rows: list[tuple[str, str, dict[str, Any]]] = []
    for fp in sorted(push_dir.glob("*.jsonl")):
        file_sym = fp.stem
        with fp.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                src = str(rec.get("source") or "")
                if src and src not in ("live_push", "push", "dry_run"):
                    continue
                payload = rec.get("payload")
                if not isinstance(payload, dict):
                    continue
                sym = str(rec.get("symbol") or file_sym).strip().upper()
                if not sym.endswith(".T"):
                    sym = f"{sym}.T"
                recorded_at = str(rec.get("recorded_at") or "")
                rows.append((recorded_at, sym, payload))
                if max_rows is not None and len(rows) >= max_rows:
                    return sorted(rows, key=lambda r: r[0])
    return sorted(rows, key=lambda r: r[0])


_C_GROUP_FAVORABLE_FADE_KEYS = frozenset(
    {
        ("7013.T", "2026-05-19T10:11:57+09:00"),
        ("9412.T", "2026-05-19T10:37:03+09:00"),
        ("9434.T", "2026-05-19T10:59:28+09:00"),
        ("8306.T", "2026-05-19T12:54:03+09:00"),
        ("7974.T", "2026-05-19T13:15:04+09:00"),
        ("8058.T", "2026-05-19T14:55:45+09:00"),
    }
)


def _structural_metrics_for_push_replay(
    events: Sequence[Mapping[str, Any]],
    *,
    config: SmallPaperPilotConfig,
    poll_interval_sec: float,
) -> dict[str, Any]:
    from research.structural_observer_review import (
        _session_end_time,
        _summarize_structural_trades,
        replay_combined_structural_exit_v1,
    )

    if not config.structural_exit_policy:
        return {}
    session_end = _session_end_time(events)
    trades, _ = replay_combined_structural_exit_v1(
        events,
        pilot_config=config,
        poll_interval_sec=poll_interval_sec,
        session_end=session_end,
    )
    met = _summarize_structural_trades(trades)
    accepted_keys = {
        (str(e.get("symbol") or ""), str(e.get("entry_time") or ""))
        for e in events
        if e.get("event_type") == "accepted"
    }
    exit_dist = met.get("exit_reason_distribution") or {}
    return {
        "structural_exit_policy": config.structural_exit_policy,
        "structural_pf": met.get("structural_pf"),
        "structural_avg_pnl_pct": met.get("structural_avg_pnl"),
        "structural_trade_count": met.get("structural_trade_count"),
        "favorable_fade_exit_count": int(exit_dist.get("favorable_fade_exit", 0)),
        "c_group_pass_count": sum(1 for k in _C_GROUP_FAVORABLE_FADE_KEYS if k in accepted_keys),
        "favorable_mode": config.favorable_mode,
        "favorable_mfe_scale": config.favorable_mfe_scale,
        "use_market_time_window": config.use_market_time_window,
    }


def _build_push_replay_summary(
    *,
    config: SmallPaperPilotConfig,
    state: _LiveRunState,
    gate: ExposureGate,
    push_dir: Path,
    push_rows: int,
    runtime_sec: float,
    poll_interval_sec: float,
    replay_speed_sec: float,
) -> dict[str, Any]:
    reject_events = [e for e in state.events if e.get("event_type") == "rejected"]
    ge55 = _quality_ge_0_55_count(state.quality_scores)
    base = {
        "phase": 51 if config.policy_trial else 48,
        "mode": "small_paper_pilot_push_replay_dry_run",
        "source": "push-replay",
        "live_feature_bridge": True,
        "push_dir": str(push_dir),
        "push_rows": push_rows,
        "push_messages": state.push_messages,
        "gate_evaluations": state.gate_evaluations,
        "generated_at": _now_iso(),
        "ended_at": _now_iso(),
        "runtime_sec": round(runtime_sec, 1),
        "poll_interval_sec": poll_interval_sec,
        "replay_speed_sec": replay_speed_sec,
        "order_enabled": False,
        "paper_only": True,
        "profile": config.profile,
        "candidate_count": len([e for e in state.events if e.get("event_type") == "candidate"]),
        "accepted_count": len(state.accepted_rows),
        "rejected_count": len(reject_events),
        "reject_reason_counts": _count_reasons(state.reject_rows),
        "max_concurrent_positions": config.max_concurrent_positions,
        "peak_open_slots": state.peak_open_slots,
        "quality_ge_0_55_count": ge55,
        "quality_ge_0_55_pct": round(100.0 * ge55 / max(1, state.gate_evaluations), 2),
        "quality_fallback_count": state.quality_fallback_count,
        "quality_fallback_rate_pct": round(
            100.0 * state.quality_fallback_count / max(1, state.gate_evaluations),
            2,
        ),
        "live_feature_complete_count": state.live_feature_complete_count,
        "live_feature_complete_rate_pct": round(
            100.0 * state.live_feature_complete_count / max(1, state.gate_evaluations),
            2,
        ),
        "quality_distribution": _quality_distribution(state.quality_scores),
        "open_slots_end": len(gate.state.open_slots),
        "discord_enabled": config.discord_enabled,
        "note": "Offline push_jsonl replay; same bridge+gate as live; no orders placed.",
    }
    base.update(_policy_summary_extras(config))
    base.update(_symbol_cooloff_summary_fields(gate, state))
    base.update(_daytrade_suitability_summary_fields(gate, state))
    base.update(_entry_price_risk_guard_summary_fields(gate, state))
    base.update(_execution_audit_fields(config))
    if getattr(config, "low_liquidity_shadow_enabled", False):
        base["low_liquidity_shadow_reject_count"] = state.low_liquidity_shadow_reject_count
    base.update(_extended_shadow_summary_fields(state))
    base.update(_vwap_shadow_summary_fields(state))
    base.update(_board_imbalance_shadow_summary_fields(state))
    return base


def _symbol_cooloff_summary_fields(gate: ExposureGate, state: _LiveRunState) -> dict[str, Any]:
    cooloff = getattr(gate, "symbol_cooloff", None)
    if cooloff is None:
        return {}
    out = cooloff.summary_fields()
    out["rejected_by_symbol_cooloff"] = state.symbol_cooloff_reject_count
    return out


def _daytrade_suitability_summary_fields(gate: ExposureGate, state: _LiveRunState) -> dict[str, Any]:
    suit = getattr(gate, "daytrade_suitability", None)
    if suit is None:
        return {}
    out = suit.summary_fields()
    out["rejected_by_daytrade_suitability"] = state.daytrade_suitability_reject_count
    return out


def _entry_price_risk_guard_summary_fields(gate: ExposureGate, state: _LiveRunState) -> dict[str, Any]:
    guard = getattr(gate, "entry_price_risk_guard", None)
    if guard is None:
        return {}
    out = guard.summary_fields()
    out["rejected_by_entry_price_risk_guard"] = state.entry_price_risk_guard_reject_count
    return out


def _intraday_refresh_summary_fields(
    state: _LiveRunState,
) -> dict[str, Any]:
    if not state.intraday_refresh_enabled:
        return {"intraday_refresh_enabled": False}
    return {
        "intraday_refresh_enabled": True,
        "intraday_refresh_csv": state.intraday_refresh_csv,
        "intraday_refresh_time": state.intraday_refresh_scheduled_time,
        "intraday_refresh_triggered_count": state.intraday_refresh_triggered_count,
        "intraday_refresh_completed_count": state.intraday_refresh_completed_count,
        "intraday_refresh_failed_count": state.intraday_refresh_failed_count,
        "intraday_refresh_last_time": state.intraday_refresh_last_time or "",
        "intraday_refresh_last_register_count": state.intraday_refresh_last_register_count,
    }


def run_push_replay_dry_run(
    config: SmallPaperPilotConfig,
    *,
    push_dir: Path,
    output_dir: Path,
    repo_root: Optional[Path] = None,
    poll_interval_sec: float = 0.0,
    replay_speed_sec: float = 0.0,
    max_push_rows: Optional[int] = None,
    enable_discord: bool = False,
) -> PilotRunResult:
    """Replay saved push_jsonl through live feature bridge + exposure gate (no kabu connection)."""
    import time

    from small_paper.live_feature_bridge import LiveFeatureBridge

    if not push_dir.is_dir():
        raise FileNotFoundError(f"push_dir not found: {push_dir}")

    replay_config = config
    if not enable_discord:
        from dataclasses import replace

        replay_config = replace(config, discord_enabled=False)
    # else: respect config.discord_enabled (trial q070 keeps observer on for push-replay)

    records = _load_push_replay_records(push_dir, max_rows=max_push_rows)
    push_rows = len(records)

    code_to_symbol: dict[str, str] = {}
    for _, sym, _ in records:
        code = sym.replace(".T", "")
        code_to_symbol[code] = sym

    root = repo_root or Path(__file__).resolve().parents[3]
    from small_paper.symbol_cooloff import session_key_from_output_dir

    run_key = session_key_from_output_dir(output_dir, root)
    gate = replay_config.make_exposure_gate(repo_root=root, run_session_key=run_key)
    gate_cfg = replay_config.exposure_gate_config()
    feature_bridge = LiveFeatureBridge(replay_config.feature_bridge_config())
    output_dir.mkdir(parents=True, exist_ok=True)
    writer = LiveSessionWriter(output_dir, incremental=True, event_fields=EVENT_FIELDS)
    state = _LiveRunState(started_mono=time.monotonic())
    pos_fields = ["symbol", "entry_time", "exit_time", "open_slots_after"]

    observer: Optional[ObserverPositionTracker] = None
    discord: Optional[SmallPaperDiscordNotifier] = None
    if replay_config.discord_enabled and replay_config.discord_observer_only:

        def _discord_error_logger(op: str, msg: str, extra: Mapping[str, Any]) -> None:
            writer.append_error(
                {
                    "event_time": _now_iso(),
                    "error_type": "discord_error",
                    "operation": op,
                    "message": msg,
                    **dict(extra),
                }
            )

        discord = discord_notifier_from_pilot(
            replay_config,
            error_logger=_discord_error_logger,
        )
        observer = ObserverPositionTracker(observer_tracker_config_from_pilot(replay_config))

    gap_threshold_sec = max(replay_config.live_stale_tick_sec * 2, max(poll_interval_sec, 0.001) * 3)
    ctx = _PushPipelineContext(
        config=replay_config,
        gate=gate,
        feature_bridge=feature_bridge,
        state=state,
        writer=writer,
        code_to_symbol=code_to_symbol,
        source="push-replay",
        pos_fields=pos_fields,
        observer=observer,
        discord=discord,
        stale_tick_sec=replay_config.live_stale_tick_sec,
        gap_threshold_sec=gap_threshold_sec,
    )

    last_eval_ts: dict[str, float] = {}
    msg_i = 0
    for recorded_at, sym, payload in records:
        msg_i += 1
        if poll_interval_sec > 0:
            ts = _parse_recorded_at_ts(recorded_at)
            prev = last_eval_ts.get(sym)
            if prev is not None and (ts - prev) < poll_interval_sec:
                continue
            last_eval_ts[sym] = ts
        _process_push_payload(ctx, payload, msg_i, symbol=sym)
        if replay_speed_sec > 0:
            time.sleep(replay_speed_sec)

    if observer:
        exit_events = observer.close_all(reason="push_replay_end")
        _log_and_dispatch_observer_events(
            exit_events,
            discord=discord,
            writer=writer,
            state=state,
            source="push-replay",
            message_index=msg_i,
            profile=replay_config.profile,
        )

    runtime_sec = time.monotonic() - state.started_mono
    positions = _build_positions_snapshot(state.accepted_rows, gate)
    summary = _build_push_replay_summary(
        config=replay_config,
        state=state,
        gate=gate,
        push_dir=push_dir,
        push_rows=push_rows,
        runtime_sec=runtime_sec,
        poll_interval_sec=poll_interval_sec,
        replay_speed_sec=replay_speed_sec,
    )
    summary.update(
        _structural_metrics_for_push_replay(
            state.events,
            config=replay_config,
            poll_interval_sec=poll_interval_sec,
        )
    )
    if discord and discord.active:
        summary.update(
            build_session_summary_extras(
                accepted_rows=state.accepted_rows,
                bucket_summary=state.bucket_summary,
                observer_stats=_observer_stats_dict(observer),
            )
        )
        discord.notify_session_summary(summary=summary)

    _apply_quality_formula_shadow_finalize(state, summary)
    _apply_trading_value_shadow_finalize(state, summary)
    _apply_board_imbalance_shadow_finalize(state, summary)
    writer.finalize_batch(
        events=state.events,
        positions=positions,
        summary=summary,
        pos_fields=pos_fields,
    )
    _write_quality_top_debug(output_dir, state.events)
    return PilotRunResult(
        output_dir=output_dir,
        summary=summary,
        events=state.events,
        accepted=state.accepted_rows,
        rejects=state.reject_rows,
    )


def _build_live_summary(
    *,
    config: SmallPaperPilotConfig,
    state: _LiveRunState,
    session_cfg: Mapping[str, Any],
    gate: ExposureGate,
    full_session: bool,
    runtime_sec: float,
) -> dict[str, Any]:
    reject_events = [e for e in state.events if e.get("event_type") == "rejected"]
    is_retrial = config.policy_trial and config.policy_label == "q070_cap3_trial"
    summary = {
        "phase": 55 if is_retrial else (51 if config.policy_trial else 47),
        "mode": "small_paper_pilot_live_observer_retrial"
        if is_retrial and full_session
        else (
            "small_paper_pilot_live_full_dry_run"
            if full_session
            else "small_paper_pilot_live_dry_run"
        ),
        "live_feature_bridge": True,
        "quality_fallback_count": state.quality_fallback_count,
        "live_feature_complete_count": state.live_feature_complete_count,
        "quality_fallback_rate_pct": round(
            100.0 * state.quality_fallback_count / max(1, state.gate_evaluations),
            2,
        ),
        "live_feature_complete_rate_pct": round(
            100.0 * state.live_feature_complete_count / max(1, state.gate_evaluations),
            2,
        ),
        "generated_at": session_cfg.get("generated_at"),
        "ended_at": _now_iso(),
        "order_enabled": False,
        "paper_only": True,
        "profile": config.profile,
        "source": "live",
        "full_session": full_session,
        "runtime_sec": round(runtime_sec, 1),
        "heartbeat_count": state.heartbeat_count,
        "api_error_count": state.api_error_count,
        "reconnect_count": state.reconnect_count,
        "push_messages": state.push_messages,
        "gate_evaluations": state.gate_evaluations,
        "candidate_count": len([e for e in state.events if e.get("event_type") == "candidate"]),
        "accepted_count": len(state.accepted_rows),
        "rejected_count": len(reject_events),
        "reject_reason_counts": _count_reasons(state.reject_rows),
        "max_concurrent_positions": config.max_concurrent_positions,
        "peak_open_slots": state.peak_open_slots,
        "quality_distribution": _quality_distribution(state.quality_scores),
        "session_bucket_summary": state.bucket_summary,
        "data_gap_count": state.data_gap_count,
        "stale_tick_count": state.stale_tick_count,
        "open_slots_end": len(gate.state.open_slots),
        "config_sha256": session_cfg.get("config_sha256"),
        "stop_reason": state.stop_reason or "completed",
        "note": "Virtual hold on PUSH for concurrent cap; no orders placed.",
        "pilot_continue_review": {
            "data_received_ok": state.push_messages > 0,
            "gate_active_ok": state.gate_evaluations > 0,
            "suggest_continue_next_session": (
                state.push_messages > 0
                and state.gate_evaluations > 0
                and state.api_error_count < 50
            ),
            "human_decision_required": True,
        },
        **{k: session_cfg[k] for k in ("duration_sec", "poll_interval_sec", "session_start", "session_end") if k in session_cfg},
    }
    summary.update(_policy_summary_extras(config))
    summary.update(_symbol_cooloff_summary_fields(gate, state))
    summary.update(_daytrade_suitability_summary_fields(gate, state))
    summary.update(_entry_price_risk_guard_summary_fields(gate, state))
    summary.update(_execution_audit_fields(config, session_cfg))
    if getattr(config, "low_liquidity_shadow_enabled", False):
        summary["low_liquidity_shadow_reject_count"] = state.low_liquidity_shadow_reject_count
    summary.update(_intraday_refresh_summary_fields(state))
    summary.update(_extended_shadow_summary_fields(state))
    summary.update(_vwap_shadow_summary_fields(state))
    summary.update(_board_imbalance_shadow_summary_fields(state))
    return summary


def _early_session_summary(
    *,
    config: SmallPaperPilotConfig,
    session_cfg: Mapping[str, Any],
    reason: str,
) -> dict[str, Any]:
    return {
        "phase": 45,
        "mode": "small_paper_pilot_live_full_dry_run",
        "generated_at": _now_iso(),
        "ended_at": _now_iso(),
        "order_enabled": False,
        "paper_only": True,
        "full_session": True,
        "stop_reason": reason,
        "runtime_sec": 0,
        "heartbeat_count": 0,
        "api_error_count": 0,
        "reconnect_count": 0,
        "candidate_count": 0,
        "accepted_count": 0,
        "rejected_count": 0,
        "reject_reason_counts": {},
        "max_concurrent_positions": config.max_concurrent_positions,
        "peak_open_slots": 0,
        "quality_distribution": _quality_distribution([]),
        "session_bucket_summary": empty_bucket_summary(),
        "data_gap_count": 0,
        "stale_tick_count": 0,
        "config_sha256": session_cfg.get("config_sha256"),
        "config_path": session_cfg.get("config_path", ""),
        "note": reason,
        **_execution_audit_fields(config, session_cfg),
    }


def run_live_dry_run(
    config: SmallPaperPilotConfig,
    *,
    symbols: Sequence[tuple[str, str, int]],
    output_dir: Path,
    repo_root: Path,
    native_root: Path,
    config_path: Path,
    duration_sec: float,
    poll_interval_sec: float,
    max_polls: Optional[int] = None,
    safety_report: Optional[Mapping[str, Any]] = None,
    full_session: bool = False,
    session_start: str = "09:00",
    session_end: str = "15:30",
    auto_stop: bool = True,
    heartbeat_sec: float = 300.0,
    wait_until_session: bool = False,
    stale_tick_sec: float = 120.0,
    max_consecutive_api_errors: int = 10,
    universe_csv_path: Optional[str] = None,
    am_pm_policy: Optional[Any] = None,
    enable_intraday_refresh: bool = False,
    intraday_refresh_csv_path: Optional[str] = None,
    universe_mode: str = "",
    exit_policy_shadow: str = "",
) -> PilotRunResult:
    """Live PUSH observation with exposure gate (no orders)."""
    import asyncio
    import time

    from api.push_client import KabuNativePushClient
    from api.rest_client import KabuNativeApiError, KabuNativeRestClient, default_base_url, load_kabu_env
    from research.exposure_gate import ExposureGate
    from small_paper.config import config_file_sha256
    from small_paper.live_feature_bridge import LiveFeatureBridge
    from storage.push_recorder import PushRecorder

    load_kabu_env(repo_root=repo_root)
    now = datetime.now(JST)
    sched = SessionSchedule(session_start, session_end, now.date())

    if full_session and sched.is_after_session(now):
        conn: dict[str, Any] = {"ok": False, "skipped": "after_session_end"}
        session_cfg = _live_session_cfg(
            config_path=config_path,
            config=config,
            conn=conn,
            full_session=True,
            duration_sec=0,
            poll_interval_sec=poll_interval_sec,
            max_polls=max_polls,
            symbol_count=len(symbols),
            session_start=session_start,
            session_end=session_end,
            auto_stop=auto_stop,
            heartbeat_sec=heartbeat_sec,
            universe_csv_path=universe_csv_path,
            am_pm_policy=am_pm_policy,
            universe_mode=universe_mode,
            exit_policy_shadow=exit_policy_shadow,
            intraday_refresh_enabled=enable_intraday_refresh,
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        _write_live_session_meta(output_dir, session_cfg=session_cfg, safety_report=safety_report)
        summary = _early_session_summary(
            config=config, session_cfg=session_cfg, reason="after_session_end"
        )
        LiveSessionWriter(output_dir, incremental=True, event_fields=EVENT_FIELDS).write_summary(
            summary
        )
        return PilotRunResult(output_dir=output_dir, summary=summary)

    if full_session and sched.is_before_session(now) and not wait_until_session:
        conn = {"ok": False, "skipped": "before_session_start"}
        session_cfg = _live_session_cfg(
            config_path=config_path,
            config=config,
            conn=conn,
            full_session=True,
            duration_sec=0,
            poll_interval_sec=poll_interval_sec,
            max_polls=max_polls,
            symbol_count=len(symbols),
            session_start=session_start,
            session_end=session_end,
            auto_stop=auto_stop,
            heartbeat_sec=heartbeat_sec,
            universe_csv_path=universe_csv_path,
            am_pm_policy=am_pm_policy,
            universe_mode=universe_mode,
            exit_policy_shadow=exit_policy_shadow,
            intraday_refresh_enabled=enable_intraday_refresh,
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        _write_live_session_meta(output_dir, session_cfg=session_cfg, safety_report=safety_report)
        summary = _early_session_summary(
            config=config, session_cfg=session_cfg, reason="before_session_start"
        )
        LiveSessionWriter(output_dir, incremental=True, event_fields=EVENT_FIELDS).write_summary(
            summary
        )
        return PilotRunResult(output_dir=output_dir, summary=summary)

    if full_session and wait_until_session and sched.is_before_session(now):
        wait_until(sched.start_dt)

    if full_session and auto_stop:
        duration_sec = sched.seconds_until_end()

    conn = verify_kabu_connection(repo_root)
    rest = KabuNativeRestClient(default_base_url())
    token = rest.issue_token_from_env()
    push = KabuNativePushClient(rest, token)

    from small_paper.symbol_cooloff import session_key_from_output_dir

    run_key = session_key_from_output_dir(output_dir, repo_root)
    gate = config.make_exposure_gate(repo_root=repo_root, run_session_key=run_key)
    gate_cfg = config.exposure_gate_config()
    feature_bridge = LiveFeatureBridge(config.feature_bridge_config())
    code_to_symbol: dict[str, str] = {}
    sym_specs: list[tuple[str, int]] = []
    for sym, sym_key, ex in symbols:
        code = sym_key.split("@")[0]
        code_to_symbol[code] = sym
        sym_specs.append((code, ex))

    session_cfg = _live_session_cfg(
        config_path=config_path,
        config=config,
        conn=conn,
        full_session=full_session,
        duration_sec=duration_sec,
        poll_interval_sec=poll_interval_sec,
        max_polls=max_polls,
        symbol_count=len(symbols),
        session_start=session_start,
        session_end=session_end,
        auto_stop=auto_stop,
        heartbeat_sec=heartbeat_sec,
        universe_csv_path=universe_csv_path,
        am_pm_policy=am_pm_policy,
        universe_mode=universe_mode,
        exit_policy_shadow=exit_policy_shadow,
        intraday_refresh_enabled=enable_intraday_refresh,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_live_session_meta(output_dir, session_cfg=session_cfg, safety_report=safety_report)

    trade_date = datetime.now(JST).date().isoformat()
    incremental = full_session
    writer = LiveSessionWriter(output_dir, incremental=incremental, event_fields=EVENT_FIELDS)
    state = _LiveRunState(started_mono=time.monotonic())
    pos_fields = ["symbol", "entry_time", "exit_time", "open_slots_after"]
    gap_threshold_sec = max(stale_tick_sec * 2, poll_interval_sec * 3)
    pipeline_ctx: Optional[_PushPipelineContext] = None

    discord: Optional[SmallPaperDiscordNotifier] = None
    observer: Optional[ObserverPositionTracker] = None
    if config.discord_enabled and not config.order_enabled and config.discord_observer_only:

        def _discord_error_logger(op: str, msg: str, extra: Mapping[str, Any]) -> None:
            writer.append_error(
                {
                    "event_time": _now_iso(),
                    "error_type": "discord_error",
                    "operation": op,
                    "message": msg,
                    **dict(extra),
                }
            )

        discord = discord_notifier_from_pilot(config, error_logger=_discord_error_logger)
        if am_pm_policy is not None:
            observer = ObserverPositionTracker(am_pm_policy.observer_tracker_config(config))
        else:
            observer = ObserverPositionTracker(observer_tracker_config_from_pilot(config))

    entry_eligible: Optional[set[str]] = {t[0] for t in symbols} if enable_intraday_refresh else None
    pipeline_ctx = _PushPipelineContext(
        config=config,
        gate=gate,
        feature_bridge=feature_bridge,
        state=state,
        writer=writer,
        code_to_symbol=code_to_symbol,
        source="live",
        pos_fields=pos_fields,
        observer=observer,
        discord=discord,
        stale_tick_sec=stale_tick_sec,
        gap_threshold_sec=gap_threshold_sec,
        am_pm_policy=am_pm_policy,
        entry_eligible_symbols=entry_eligible,
    )
    refresh_hhmm = "10:00"
    if am_pm_policy is not None and getattr(am_pm_policy, "kind", "") == "pm":
        refresh_hhmm = "14:30"
    state.intraday_refresh_enabled = bool(enable_intraday_refresh and intraday_refresh_csv_path)
    state.intraday_refresh_csv = str(intraday_refresh_csv_path or "")
    state.intraday_refresh_scheduled_time = refresh_hhmm

    def _emit_intraday_refresh_event(event: str, *, extra: Mapping[str, Any]) -> None:
        # Use errors.jsonl as the structured event channel (historical behavior).
        writer.append_error(
            {
                "event_time": _now_iso(),
                "error_type": "intraday_refresh",
                "event": event,  # started/completed/failed
                "session_kind": getattr(am_pm_policy, "kind", "am") if am_pm_policy else "am",
                "refresh_time": refresh_hhmm,
                "refresh_csv": str(intraday_refresh_csv_path or ""),
                **dict(extra),
            }
        )

    def _maybe_intraday_refresh() -> None:
        nonlocal sym_specs, code_to_symbol, push, token
        if not enable_intraday_refresh or not intraday_refresh_csv_path:
            return
        if state.intraday_refresh_done:
            return
        from small_paper.session_schedule import parse_hhmm

        if datetime.now(JST).time() < parse_hhmm(refresh_hhmm):
            return
        # Mark as triggered early to avoid repeated attempts.
        state.intraday_refresh_triggered_count += 1
        state.intraday_refresh_last_time = refresh_hhmm
        before_syms = (
            sorted(list(pipeline_ctx.entry_eligible_symbols))
            if pipeline_ctx.entry_eligible_symbols is not None
            else []
        )
        _emit_intraday_refresh_event(
            "started",
            extra={
                "before_symbol_count": len(before_syms) or len(sym_specs),
                "open_symbols_count": len(observer.open_symbols()) if observer else 0,
            },
        )
        refresh_path = Path(intraday_refresh_csv_path)
        if not refresh_path.is_file():
            _log_api_error("intraday_refresh", FileNotFoundError(str(refresh_path)))
            state.intraday_refresh_failed_count += 1
            _emit_intraday_refresh_event(
                "failed",
                extra={"reason": "refresh_csv_missing", "path": str(refresh_path)},
            )
            return
        import csv

        from universe.intraday_refresh import (
            merge_register_specs,
            merge_universe_with_open_symbols,
        )
        from universe.am_pm_universe import _norm

        base_rows = [dict(r) for r in csv.DictReader(refresh_path.open(encoding="utf-8"))]
        open_syms = observer.open_symbols() if observer else []
        session_kind = getattr(am_pm_policy, "kind", "am") if am_pm_policy else "am"
        merged, merge_meta = merge_universe_with_open_symbols(
            base_rows,
            open_symbols=open_syms,
            feature_rows=[],
            symbol_meta={},
            session=session_kind,
            refresh_time=refresh_hhmm,
        )
        if merge_meta.get("error") == "open_symbols_exceed_cap":
            state.intraday_refresh_failed_count += 1
            # Degraded mode: keep existing subscription and continue session.
            # Do not unregister/all or change symbols when open positions exceed cap.
            state.intraday_refresh_done = True
            _emit_intraday_refresh_event(
                "failed",
                extra={
                    "reason": "open_symbols_exceed_cap",
                    "open_symbols": open_syms,
                    "merge": merge_meta,
                    "action": "continue_keep_previous_subscription",
                    "will_stop": False,
                },
            )
            return
        specs, reg_meta = merge_register_specs(merged, symbol_meta={})
        if reg_meta.get("error"):
            state.intraday_refresh_failed_count += 1
            state.intraday_refresh_done = True
            _emit_intraday_refresh_event(
                "failed",
                extra={
                    "reason": str(reg_meta.get("error")),
                    "register": reg_meta,
                    "merge": merge_meta,
                    "action": "continue_keep_previous_subscription",
                    "will_stop": False,
                },
            )
            return
        if not specs:
            # Safety: never unregister/all or register an empty symbol set.
            state.intraday_refresh_failed_count += 1
            state.intraday_refresh_done = True
            state.intraday_refresh_last_register_count = 0
            _emit_intraday_refresh_event(
                "failed",
                extra={
                    "reason": "register_count_zero",
                    "register": reg_meta,
                    "merge": merge_meta,
                    "action": "continue_keep_previous_subscription",
                    "will_stop": False,
                },
            )
            return
        try:
            from api.kabu_register import register_symbols_cleared

            register_symbols_cleared(push, specs)
            code_to_symbol.clear()
            for row in merged:
                sym = _norm(str(row.get("symbol") or ""))
                sk = str(row.get("symbol_key") or "")
                code = sk.split("@")[0] if sk else sym.replace(".T", "")
                if code:
                    code_to_symbol[code] = sym
            if pipeline_ctx.entry_eligible_symbols is not None:
                pipeline_ctx.entry_eligible_symbols = {
                    _norm(str(r.get("symbol") or "")) for r in merged if r.get("symbol")
                }
            after_syms = (
                sorted(list(pipeline_ctx.entry_eligible_symbols))
                if pipeline_ctx.entry_eligible_symbols is not None
                else []
            )
            removed = sorted(set(before_syms) - set(after_syms)) if before_syms and after_syms else []
            added = sorted(set(after_syms) - set(before_syms)) if before_syms and after_syms else []
            state.intraday_refresh_done = True
            state.intraday_refresh_count += 1
            state.intraday_refresh_completed_count += 1
            try:
                state.intraday_refresh_last_register_count = int(reg_meta.get("register_count") or 0)
            except (TypeError, ValueError):
                state.intraday_refresh_last_register_count = 0
            _emit_intraday_refresh_event(
                "completed",
                extra={
                    "before_symbol_count": len(before_syms) or len(sym_specs),
                    "after_symbol_count": len(after_syms) or int(reg_meta.get("register_count") or 0),
                    "register_count": reg_meta.get("register_count"),
                    "open_symbols": open_syms,
                    "open_symbols_count": len(open_syms),
                    "merge": merge_meta,
                    "added_symbols": added[:200],
                    "removed_symbols": removed[:200],
                },
            )
        except Exception as e:
            _log_api_error("intraday_refresh_register", e)
            state.intraday_refresh_failed_count += 1
            state.intraday_refresh_done = True
            _emit_intraday_refresh_event(
                "failed",
                extra={
                    "reason": "register_exception",
                    "message": str(e),
                    "action": "continue_keep_previous_subscription",
                    "will_stop": False,
                },
            )

    def _maybe_am_pm_force_close() -> None:
        if am_pm_policy is None or state.session_force_close_done:
            return
        if not am_pm_policy.force_close_due():
            return
        state.session_force_close_done = True
        if observer and observer.open_count() > 0:
            exit_events = observer.close_all(reason=am_pm_policy.force_close_reason)
            _dispatch_observer_events(exit_events, discord=discord)
        gate.state.open_slots = []
        _request_stop(am_pm_policy.force_close_reason)

    def _request_stop(reason: str) -> None:
        state.stop_requested = True
        state.stop_reason = reason

    def _on_signal(_sig: int, _frame: Any) -> None:
        _request_stop("signal_interrupt")

    try:
        signal.signal(signal.SIGINT, _on_signal)
    except (ValueError, OSError):
        pass

    def _log_api_error(op: str, exc: Exception) -> None:
        state.api_error_count += 1
        state.consecutive_api_errors += 1
        writer.append_error(
            {
                "event_time": _now_iso(),
                "error_type": "api_error",
                "operation": op,
                "message": str(exc),
                "consecutive": state.consecutive_api_errors,
            }
        )
        if state.consecutive_api_errors >= max_consecutive_api_errors:
            _request_stop("max_consecutive_api_errors")
        if discord and discord.active and state.consecutive_api_errors in (1, 5, 10):
            discord.notify_error(operation=op, message=str(exc), extra={"consecutive": state.consecutive_api_errors})

    def _emit_heartbeat(note: str = "") -> None:
        state.heartbeat_count += 1
        runtime = time.monotonic() - state.started_mono
        hb = {
            "event_time": _now_iso(),
            "heartbeat_index": state.heartbeat_count,
            "runtime_sec": round(runtime, 1),
            "push_messages": state.push_messages,
            "gate_evaluations": state.gate_evaluations,
            "api_error_count": state.api_error_count,
            "open_slots": len(gate.state.open_slots),
            "note": note,
        }
        writer.append_heartbeat(hb)
        summary_partial = _build_live_summary(
            config=config,
            state=state,
            session_cfg=session_cfg,
            gate=gate,
            full_session=full_session,
            runtime_sec=runtime,
        )
        writer.write_summary(summary_partial)
        if discord and discord.should_send_heartbeat():
            obs_stats = _observer_stats_dict(observer)
            extras = build_session_summary_extras(
                accepted_rows=state.accepted_rows,
                bucket_summary=state.bucket_summary,
                observer_stats=obs_stats,
            )
            discord.notify_heartbeat(summary={**summary_partial, **extras})

    def _process_payload(payload: Mapping[str, Any], msg_i: int) -> None:
        assert pipeline_ctx is not None
        state.consecutive_api_errors = 0
        _process_push_payload(pipeline_ctx, payload, msg_i)

    async def _loop() -> None:
        nonlocal push, token

        push_rec_local = (
            PushRecorder(native_root, trade_date) if config.live_record_push_jsonl else None
        )
        try:
            from api.kabu_register import format_register_failure_message, register_symbols_cleared

            reg_meta = register_symbols_cleared(push, sym_specs)
            if reg_meta.get("recovered_from_register_limit"):
                writer.append_error(
                    {
                        "event_time": _now_iso(),
                        "error_type": "register_limit_recovered",
                        "message": "register 4002006 recovered after unregister/all retry",
                        "symbol_count": len(sym_specs),
                        "steps": reg_meta.get("steps"),
                    }
                )
                if discord and discord.active:
                    discord.notify_error(
                        operation="register",
                        message=(
                            "register limit 4002006 — cleared via unregister/all and succeeded on retry"
                        ),
                        extra={"symbol_count": len(sym_specs)},
                    )
        except Exception as e:
            _log_api_error("register", e)
            fail_msg = format_register_failure_message(e, symbol_count=len(sym_specs))
            writer.append_error(
                {
                    "event_time": _now_iso(),
                    "error_type": "register_fatal",
                    "message": fail_msg,
                    "symbol_count": len(sym_specs),
                }
            )
            if discord and discord.active:
                discord.notify_error(
                    operation="register",
                    message=fail_msg,
                    extra={"symbol_count": len(sym_specs), "stop_reason": "register_failed"},
                )
            _request_stop("register_failed")
            return

        start = time.monotonic()
        last_hb = start
        msg_i = 0
        last_eval: dict[str, float] = {}

        def _should_stop() -> bool:
            if state.stop_requested:
                return True
            if full_session and auto_stop and sched.is_after_session():
                _request_stop("session_end")
                return True
            if duration_sec > 0 and (time.monotonic() - start) >= duration_sec:
                _request_stop("duration_elapsed")
                return True
            if max_polls is not None and msg_i >= max_polls:
                _request_stop("max_polls")
                return True
            return False

        async def _reconnect_push() -> bool:
            nonlocal push, token
            state.reconnect_count += 1
            writer.append_error(
                {
                    "event_time": _now_iso(),
                    "error_type": "reconnect",
                    "reconnect_count": state.reconnect_count,
                }
            )
            try:
                push.unregister_all()
            except Exception as e:
                _log_api_error("unregister_all", e)
            await asyncio.sleep(min(30.0, poll_interval_sec * 2))
            if state.stop_requested:
                return False
            try:
                from api.kabu_register import register_symbols_cleared

                token = rest.issue_token_from_env()
                push = KabuNativePushClient(rest, token)
                register_symbols_cleared(push, sym_specs)
                state.consecutive_api_errors = 0
                return True
            except Exception as e:
                _log_api_error("reconnect_register", e)
                return False

        try:
            while not _should_stop():
                _maybe_intraday_refresh()
                _maybe_am_pm_force_close()
                if _should_stop():
                    break
                if (time.monotonic() - last_hb) >= heartbeat_sec:
                    _emit_heartbeat()
                    last_hb = time.monotonic()
                try:
                    async for payload in push.iter_messages(recv_poll_sec=poll_interval_sec):
                        if _should_stop():
                            break
                        # Phase170: refresh check must run during streaming,
                        # not only between reconnect cycles.
                        _maybe_intraday_refresh()
                        if (time.monotonic() - last_hb) >= heartbeat_sec:
                            _emit_heartbeat()
                            last_hb = time.monotonic()
                        if not isinstance(payload, dict):
                            continue
                        sym = _symbol_from_push(payload, code_to_symbol)
                        if not sym:
                            continue
                        msg_i += 1
                        if push_rec_local:
                            try:
                                push_rec_local.append(sym, payload, source="live_push")
                            except Exception as e:
                                _log_api_error("push_recorder", e)
                        ev_now = time.monotonic()
                        if sym in last_eval and (ev_now - last_eval[sym]) < poll_interval_sec:
                            continue
                        last_eval[sym] = ev_now
                        _process_payload(payload, msg_i)
                except asyncio.CancelledError:
                    _request_stop("cancelled")
                    break
                except KabuNativeApiError as e:
                    _log_api_error("push_iter", e)
                except Exception as e:
                    _log_api_error("push_unexpected", e)
                if _should_stop():
                    break
                if state.consecutive_api_errors >= max_consecutive_api_errors:
                    break
                if not await _reconnect_push():
                    break
        finally:
            try:
                push.unregister_all()
            except Exception:
                pass

    try:
        asyncio.run(_loop())
    except KeyboardInterrupt:
        _request_stop("keyboard_interrupt")

    runtime_sec = time.monotonic() - state.started_mono
    positions = _build_positions_snapshot(state.accepted_rows, gate)
    summary = _build_live_summary(
        config=config,
        state=state,
        session_cfg=session_cfg,
        gate=gate,
        full_session=full_session,
        runtime_sec=runtime_sec,
    )
    if observer:
        final_reason = state.stop_reason or "session_end"
        if am_pm_policy and not state.session_force_close_done and am_pm_policy.force_close_due():
            final_reason = am_pm_policy.force_close_reason
        exit_events = observer.close_all(reason=final_reason)
        _log_and_dispatch_observer_events(
            exit_events,
            discord=discord,
            writer=writer,
            state=state,
            source="live",
            message_index=state.push_messages,
            profile=config.profile,
        )
        gate.state.open_slots = []
    summary.update(
        build_session_summary_extras(
            accepted_rows=state.accepted_rows,
            bucket_summary=state.bucket_summary,
            observer_stats=_observer_stats_dict(observer),
        )
    )
    if discord and discord.active:
        discord.notify_session_summary(summary=summary)
    _apply_quality_formula_shadow_finalize(state, summary)
    _apply_trading_value_shadow_finalize(state, summary)
    _apply_board_imbalance_shadow_finalize(state, summary)
    writer.finalize_batch(
        events=state.events,
        positions=positions,
        summary=summary,
        pos_fields=pos_fields,
    )
    _write_quality_top_debug(output_dir, state.events)
    return PilotRunResult(
        output_dir=output_dir,
        summary=summary,
        events=state.events,
        accepted=state.accepted_rows,
        rejects=state.reject_rows,
    )


def _observer_stats_dict(observer: Optional[ObserverPositionTracker]) -> Optional[dict[str, Any]]:
    if observer is None:
        return None
    s = observer.stats
    cfg = observer.cfg
    return {
        "entry_count": s.entry_count,
        "exit_count": s.exit_count,
        "hold_notify_count": s.hold_notify_count,
        "take_count": s.take_count,
        "holding_count": observer.open_count(),
        "hold_durations_sec": list(s.hold_durations_sec),
        "structural_exit_policy": cfg.structural_exit_policy,
        "structural_exit_count": s.structural_exit_count,
        "structural_exit_reason_counts": dict(s.structural_exit_reason_counts),
        "price_momentum_fade_exit_count": s.structural_exit_reason_counts.get(
            "price_momentum_fade_exit", 0
        ),
        "momentum_fade_exit_count": s.structural_exit_reason_counts.get("momentum_fade_exit", 0),
        "price_momentum_fade_ratio": cfg.price_momentum_fade_ratio,
        "virtual_hold_expired_ignored_count": s.virtual_hold_expired_ignored_count,
        "official_exit_count": s.official_exit_count,
        "session_end_exit_count": s.session_end_exit_count,
        "morning_session_close_count": s.morning_session_close_count,
        "afternoon_session_close_count": s.afternoon_session_close_count,
    }


def _live_session_cfg(
    *,
    config_path: Path,
    config: SmallPaperPilotConfig,
    conn: Mapping[str, Any],
    full_session: bool,
    duration_sec: float,
    poll_interval_sec: float,
    max_polls: Optional[int],
    symbol_count: int,
    session_start: str,
    session_end: str,
    auto_stop: bool,
    heartbeat_sec: float,
    universe_csv_path: Optional[str] = None,
    am_pm_policy: Optional[Any] = None,
    universe_mode: str = "",
    exit_policy_shadow: str = "",
    intraday_refresh_enabled: bool = False,
) -> dict[str, Any]:
    from small_paper.config import config_file_sha256

    out = {
        "phase": 45,
        "generated_at": _now_iso(),
        "config_path": str(config_path),
        "config_sha256": config_file_sha256(config_path),
        "order_enabled": False,
        "paper_only": True,
        "dry_run": True,
        "source": "live",
        "full_session": full_session,
        "duration_sec": duration_sec,
        "poll_interval_sec": poll_interval_sec,
        "max_polls": max_polls,
        "symbol_count": symbol_count,
        "universe_csv_path": universe_csv_path,
        "session_start": session_start,
        "session_end": session_end,
        "auto_stop": auto_stop,
        "heartbeat_sec": heartbeat_sec,
        "kabu_connection": dict(conn),
    }
    if am_pm_policy is not None:
        out["am_pm_session"] = am_pm_policy.to_dict()
    if universe_mode:
        out["universe_mode"] = universe_mode
    if exit_policy_shadow:
        out["exit_policy_shadow"] = exit_policy_shadow
    if intraday_refresh_enabled:
        out["intraday_refresh_enabled"] = True
    out.update(_execution_audit_fields(config, out))
    return out


def _write_live_session_meta(
    output_dir: Path,
    *,
    session_cfg: Mapping[str, Any],
    safety_report: Optional[Mapping[str, Any]],
) -> None:
    (output_dir / "live_session_config.json").write_text(
        json.dumps(dict(session_cfg), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if safety_report:
        (output_dir / "live_session_safety_report.json").write_text(
            json.dumps(dict(safety_report), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def run_poll_dry_run(
    config: SmallPaperPilotConfig,
    *,
    symbols: Sequence[tuple[str, str, int]],
    output_dir: Path,
    repo_root: Path,
    max_polls: int,
) -> PilotRunResult:
    """
  Poll kabu board snapshots; log observation events only (no ENTRY engine — no new logic).
    Does not place orders.
    """
    from api.rest_client import KabuNativeRestClient, default_base_url, load_kabu_env

    load_kabu_env(repo_root=repo_root)
    client = KabuNativeRestClient(default_base_url())
    token = client.issue_token_from_env()

    events: list[dict[str, Any]] = []
    for poll in range(max(1, max_polls)):
        poll_ts = _now_iso()
        for sym, sym_key, _ex in symbols:
            try:
                board = client.get_board(sym_key, token=token)
            except Exception as e:
                events.append(
                    {
                        "event_time": poll_ts,
                        "event_type": "poll_error",
                        "symbol": sym,
                        "profile": config.profile,
                        "gate_reject_reason": str(e),
                        "dry_run": True,
                        "source": "poll",
                        "poll_index": poll,
                    }
                )
                continue
            events.append(
                {
                    "event_time": poll_ts,
                    "event_type": "poll_observation",
                    "symbol": sym,
                    "profile": config.profile,
                    "entry_time": board.get("CurrentPriceTime"),
                    "continuation_quality_score": None,
                    "gate_accept": None,
                    "gate_reject_reason": "poll_mode_no_entry_engine",
                    "dry_run": True,
                    "source": "poll",
                    "poll_index": poll,
                    "current_price": board.get("CurrentPrice"),
                }
            )

    summary = {
        "phase": 44,
        "mode": "small_paper_pilot_poll_dry_run",
        "generated_at": _now_iso(),
        "order_enabled": False,
        "source": "poll",
        "max_polls": max_polls,
        "symbol_count": len(symbols),
        "note": "Poll mode logs observations only; use replay source for gate accepted/rejected counts.",
    }
    _write_outputs(output_dir, events=events, accepted=[], rejects=[], positions=[], summary=summary)
    return PilotRunResult(output_dir=output_dir, summary=summary, events=events)


def _count_reasons(rejects: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rejects:
        reason = str(r.get("gate_reject_reason") or "unknown")
        out[reason] = out.get(reason, 0) + 1
    return out


def _build_positions_snapshot(
    accepted: Sequence[Mapping[str, Any]],
    gate: ExposureGate,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    ordered = sorted(accepted, key=lambda t: str(t.get("entry_time") or ""))
    for t in ordered:
        decision = gate.evaluate_entry(t)
        if decision.accept:
            gate.record_accepted(t)
            rows.append(
                {
                    "symbol": t.get("symbol"),
                    "entry_time": t.get("entry_time"),
                    "exit_time": t.get("exit_time"),
                    "open_slots_after": len(gate.state.open_slots),
                }
            )
    return rows


def _write_outputs(
    output_dir: Path,
    *,
    events: list[dict[str, Any]],
    accepted: list[dict[str, Any]],
    rejects: list[dict[str, Any]],
    positions: list[dict[str, Any]],
    summary: dict[str, Any],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "small_paper_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_csv(output_dir / "small_paper_events.csv", EVENT_FIELDS, events)
    with (output_dir / "small_paper_events.jsonl").open("w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    reject_fields = list(EVENT_FIELDS)
    _write_csv(output_dir / "small_paper_rejects.csv", reject_fields, [e for e in events if e.get("event_type") == "rejected"])
    pos_fields = ["symbol", "entry_time", "exit_time", "open_slots_after"]
    _write_csv(output_dir / "small_paper_positions.csv", pos_fields, positions)


def _write_csv(path: Path, fields: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(fields), extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})
