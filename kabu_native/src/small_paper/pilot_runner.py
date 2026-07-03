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

from research.exposure_gate import (
    REJECT_MAX_CONCURRENT,
    ExposureGate,
    ExposureGateConfig,
    run_exposure_gate_simulation,
)
from research.research_exit_criteria import _load_csv
from small_paper.config import SmallPaperPilotConfig
from small_paper.discord_notifier import (
    SmallPaperDiscordNotifier,
    build_session_summary_extras,
    discord_notifier_from_pilot,
    notify_discord_session_end,
    observer_tracker_config_from_pilot,
)
from small_paper.discord_ux_session import DiscordUxSessionStats
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
    "pbv2_internal_reason",
    "pbv2_internal_gate",
    "or_overlay_reason",
    "final_reject_reason",
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
    "no_progress_exit",
    "shadow_quality_score",
    "shadow_quality_rank",
    "current_quality_rank",
    "trading_value_band",
    "tv_sweet_band_flag",
    "entry_order_book_imbalance",
    "entry_board_mid_token_active",
    "entry_imbalance_percentile",
    "imbalance_shadow_candidate",
    "imbalance_shadow_tier",
    "entry_expectancy_score",
    "entry_expectancy_score_ge5_flag",
    "entry_expectancy_score_ge6_flag",
    "entry_expectancy_score_v2",
    "entry_expectancy_score_v2_ge5_flag",
    "entry_expectancy_score_v2_ge6_flag",
    "entry_score_v2_threshold",
    "entry_score_v2_gate_pass",
    "shadow_board_dynamic_tier",
    "shadow_board_dynamic_activate_pct",
    "shadow_board_dynamic_giveback_frac",
    "shadow_exit_reason",
    "shadow_exit_price",
    "shadow_exit_time",
    "shadow_pnl_pct",
    "shadow_pnl_yen_100",
    "actual_vs_shadow_delta_yen",
    "actual_vs_shadow_delta_pct",
    "board_dynamic_trailing_tier",
    "board_dynamic_trailing_activate_pct",
    "board_dynamic_trailing_giveback_frac",
    "pullback_misread_dynamic40_guard_blocked",
    "pullback_misread_dynamic40_guard_candidate",
    "day_high_distance_pct",
    "entry_momentum_score",
    "near_day_high_low_momentum_dynamic40_guard_blocked",
    "near_day_high_low_momentum_dynamic40_guard_candidate",
    "high_drift_pullback_guard_blocked",
    "high_drift_pullback_guard_candidate",
    "entry_rise_15min_pct",
    "entry_rise_30min_pct",
    "weak_shape_reject_guard_blocked",
    "weak_shape_reject_guard_candidate",
    "weak_shape_class",
    "late_chase_guard_blocked",
    "late_chase_guard_candidate",
    "rsi14",
    "rsi_over80",
    "late_chase_flag",
    "classic_late_chase_rsi_guard_pass",
    "classic_late_chase_rsi_guard_blocked",
    "classic_late_chase_rsi_guard_candidate",
    "reentry_rsi_guard_pass",
    "reentry_rsi_guard_blocked",
    "reentry_rsi_guard_candidate",
    "reentry_rsi_guard_after_stop",
    "spread_bps",
    "update_count_before_entry",
    "price_freshness_source",
    "fallback_used",
    "fallback_reject_reason",
    "price_age_sec",
    "board_age_sec",
    "entry_quality_guard_pass",
    "entry_quality_guard_blocked",
    "entry_quality_guard_candidate",
    "entry_quality_guard_reject_reason",
    "day_high_minutes_from_open",
    "minutes_since_day_high_update",
    "high_to_now_drawdown_pct",
    "pullback_misread_guard_shadow_blocked",
    "pullback_misread_shadow_pnl_yen_100",
    "pullback_misread_shadow_delta_yen",
    "universe_slot",
    "universe_bucket",
    "source_bucket",
    "reject_reason",
)


@dataclass
class PilotRunResult:
    output_dir: Path
    summary: dict[str, Any]
    events: list[dict[str, Any]] = field(default_factory=list)
    accepted: list[dict[str, Any]] = field(default_factory=list)
    rejects: list[dict[str, Any]] = field(default_factory=list)
    realtime_board_shadow: Any = None
    exit_candidate_shadow: Any = None
    stage_profiler: Any = None


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
    v2 = getattr(decision, "entry_expectancy_score_v2", None)
    if v2 is not None:
        base["entry_expectancy_score_v2"] = v2
    thr = getattr(decision, "entry_score_v2_threshold", None)
    if thr is not None:
        base["entry_score_v2_threshold"] = thr
    gp = getattr(decision, "entry_score_v2_gate_pass", None)
    if gp is not None:
        base["entry_score_v2_gate_pass"] = gp
    if decision.accept:
        for key in (
            "cluster_guard_status",
            "cluster_id",
            "new_subcluster_id",
            "liquidity_burst",
            "entry_type",
        ):
            val = trade.get(key)
            if val in (None, "") and hasattr(decision, key):
                val = getattr(decision, key, None)
            if val not in (None, ""):
                base[key] = val
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


def _default_post_entry_forward_shadow_session() -> Any:
    from small_paper.post_entry_forward_shadow import PostEntryForwardShadowSession

    return PostEntryForwardShadowSession()


def _default_classic_momentum_forward_shadow_session() -> Any:
    from small_paper.classic_momentum_forward_shadow import ClassicMomentumForwardShadowSession

    return ClassicMomentumForwardShadowSession()


def _default_extended_shadow_counters() -> Any:
    from small_paper.extended_entry_shadow import ExtendedEntryShadowCounters

    return ExtendedEntryShadowCounters()


def _default_vwap_shadow_counters() -> Any:
    from small_paper.vwap_shadow_reject import VwapShadowRejectCounters

    return VwapShadowRejectCounters()


def _default_board_imbalance_shadow_counters() -> Any:
    from small_paper.board_imbalance_shadow import BoardImbalanceShadowCounters

    return BoardImbalanceShadowCounters()


def _default_board_dynamic_trailing_shadow_counters() -> Any:
    from small_paper.board_dynamic_trailing_shadow import BoardDynamicTrailingShadowCounters

    return BoardDynamicTrailingShadowCounters()


def _default_limit_up_proximity_entry_guard_shadow_counters() -> Any:
    from small_paper.limit_up_proximity_entry_guard_shadow import (
        LimitUpProximityEntryGuardShadowCounters,
    )

    return LimitUpProximityEntryGuardShadowCounters()


def _default_pullback_misread_entry_guard_shadow_counters() -> Any:
    from small_paper.pullback_misread_entry_guard_shadow import (
        PullbackMisreadEntryGuardShadowCounters,
    )

    return PullbackMisreadEntryGuardShadowCounters()


def _default_realtime_board_exit_shadow() -> Any:
    from small_paper.realtime_board_exit_shadow import RealtimeBoardExitShadowLogger

    return RealtimeBoardExitShadowLogger()


def _make_observer_tracker(
    config: SmallPaperPilotConfig,
    state: _LiveRunState,
    *,
    am_pm_policy: Optional[Any] = None,
) -> ObserverPositionTracker:
    shadow = getattr(state, "realtime_board_exit_shadow", None)
    exit_pack = getattr(state, "exit_candidate_shadow", None)
    if am_pm_policy is not None:
        cfg = am_pm_policy.observer_tracker_config(config)
    else:
        cfg = observer_tracker_config_from_pilot(config)
    return ObserverPositionTracker(
        cfg,
        board_exit_shadow=shadow,
        exit_candidate_shadow=exit_pack,
    )


def _default_entry_expectancy_score_counters() -> Any:
    from small_paper.entry_expectancy_score_shadow import EntryExpectancyScoreCounters

    return EntryExpectancyScoreCounters()


def _default_volume_gate_shadow_state() -> Any:
    from small_paper.volume_gate_relaxation_shadow import VolumeGateRelaxationShadowState

    return VolumeGateRelaxationShadowState()


def _default_live_order_dry_run_session(config: Any) -> Any:
    from small_paper.live_order_dry_run_adapter import LiveOrderDryRunSession

    cap = int(getattr(config, "max_concurrent_positions", 5) or 5)
    timeout = float(getattr(config, "live_order_entry_timeout_sec", 4.0) or 4.0)
    return LiveOrderDryRunSession(position_cap=cap, entry_timeout_sec=timeout)


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
    pullback_misread_dynamic40_reject_count: int = 0
    pullback_misread_dynamic40_reject_symbols: set[str] = field(default_factory=set)
    near_day_high_low_momentum_dynamic40_reject_count: int = 0
    near_day_high_low_momentum_dynamic40_reject_symbols: set[str] = field(
        default_factory=set
    )
    high_drift_pullback_reject_count: int = 0
    high_drift_pullback_reject_symbols: set[str] = field(default_factory=set)
    weak_shape_reject_count: int = 0
    weak_shape_reject_symbols: set[str] = field(default_factory=set)
    late_chase_reject_count: int = 0
    late_chase_reject_symbols: set[str] = field(default_factory=set)
    classic_late_chase_rsi_reject_count: int = 0
    classic_late_chase_rsi_reject_symbols: set[str] = field(default_factory=set)
    reentry_rsi_guard_reject_count: int = 0
    reentry_rsi_guard_reject_symbols: set[str] = field(default_factory=set)
    entry_quality_guard_reject_count: int = 0
    entry_quality_guard_spread_reject_count: int = 0
    entry_quality_guard_update_reject_count: int = 0
    entry_quality_guard_reject_symbols: set[str] = field(default_factory=set)
    cluster_guard_reject_count: int = 0
    cluster_guard_reject_symbols: set[str] = field(default_factory=set)
    stop_low_mfe_guard_reject_count: int = 0
    stop_low_mfe_guard_reject_symbols: set[str] = field(default_factory=set)
    board_mid_entry_count: int = 0
    board_high_entry_count: int = 0
    pbv2_internal_reason_counts: dict[str, int] = field(default_factory=dict)
    stale_reason_counts: dict[str, int] = field(default_factory=dict)
    low_liquidity_shadow_reject_count: int = 0
    volume_gate_shadow: Any = field(default_factory=_default_volume_gate_shadow_state)
    live_order_dry_run: Any = None
    live_order_wiring: Any = None
    live_capital_manager: Any = None
    live_capital_read_client: Any = None
    live_capital_api_token: str = ""
    live_order_adapter: Any = None
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
    event_stale_reject_count: int = 0
    board_stale_reject_count: int = 0
    trade_stale_tag_count: int = 0
    session_momentum_samples: list[float] = field(default_factory=list)
    session_order_book_imbalance_samples: list[float] = field(default_factory=list)
    extended_entry_shadow: Any = field(default_factory=_default_extended_shadow_counters)
    post_entry_forward_shadow: Any = field(default_factory=_default_post_entry_forward_shadow_session)
    classic_momentum_forward_shadow: Any = field(
        default_factory=_default_classic_momentum_forward_shadow_session
    )
    vwap_shadow_reject: Any = field(default_factory=_default_vwap_shadow_counters)
    board_imbalance_shadow: Any = field(default_factory=_default_board_imbalance_shadow_counters)
    board_dynamic_trailing_shadow: Any = field(
        default_factory=_default_board_dynamic_trailing_shadow_counters
    )
    limit_up_proximity_entry_guard_shadow: Any = field(
        default_factory=_default_limit_up_proximity_entry_guard_shadow_counters
    )
    pullback_misread_entry_guard_shadow: Any = field(
        default_factory=_default_pullback_misread_entry_guard_shadow_counters
    )
    realtime_board_exit_shadow: Any = field(default_factory=_default_realtime_board_exit_shadow)
    entry_expectancy_score_shadow: Any = field(default_factory=_default_entry_expectancy_score_counters)
    discord_ux: DiscordUxSessionStats = field(default_factory=DiscordUxSessionStats)
    position_cap_stats: Any = None
    peak_observer_open: int = 0
    observer_tracker: Any = None
    or_overlay: Any = None


def _init_position_cap_tracking(config: SmallPaperPilotConfig, state: _LiveRunState) -> None:
    from small_paper.position_cap_mode import make_position_cap_stats

    state.position_cap_stats = make_position_cap_stats(config)


def _init_live_order_dry_run(config: SmallPaperPilotConfig, state: _LiveRunState) -> None:
    from small_paper.live_order_dry_run_adapter import dry_run_adapter_enabled

    if dry_run_adapter_enabled(config):
        state.live_order_dry_run = _default_live_order_dry_run_session(config)


def _init_live_order_api_wiring(config: SmallPaperPilotConfig, state: _LiveRunState) -> None:
    from small_paper.live_order_api_wiring import LiveOrderWiringSession, wiring_enabled

    if wiring_enabled(config):
        state.live_order_wiring = LiveOrderWiringSession()


def _init_live_capital_manager(config: SmallPaperPilotConfig, state: _LiveRunState, *, repo_root: Path) -> None:
    from research.structural_trade_normalize import resolve_kabu_root
    from small_paper.live_capital_manager import LiveCapitalManagerSession, capital_manager_enabled

    if capital_manager_enabled(config):
        kabu = resolve_kabu_root(repo_root)
        state.live_capital_manager = LiveCapitalManagerSession(
            position_cap=int(config.max_concurrent_positions),
            kill_switch_path=kabu / "configs" / "live_trading_kill_switch.flag",
        )


def _init_live_order_adapter(config: SmallPaperPilotConfig, state: _LiveRunState) -> None:
    from small_paper.live_order_adapter import LiveOrderAdapterSession, live_order_adapter_enabled

    if live_order_adapter_enabled(config):
        cap = int(config.max_concurrent_positions)
        timeout = float(getattr(config, "live_order_entry_timeout_sec", 4.0) or 4.0)
        state.live_order_adapter = LiveOrderAdapterSession(position_cap=cap, entry_timeout_sec=timeout)


def _init_or_overlay_tracking(config: SmallPaperPilotConfig, state: _LiveRunState) -> None:
    from small_paper.or_overlay_entry import build_or_overlay_state

    state.or_overlay = build_or_overlay_state(config)


def _evaluate_gate_entry(
    ctx: _PushPipelineContext,
    trade: Mapping[str, Any],
    *,
    entry_pool: str = "PBV2",
) -> Any:
    from research.exposure_gate import REJECT_MAX_CONCURRENT
    from small_paper.or_overlay_cap import ENTRY_TYPE_OR, cap_reject_reason_for_pool
    from small_paper.or_overlay_entry import pbv2_cap_kwargs
    from small_paper.position_cap_mode import maybe_track_legacy_vh_shadow

    cap_kw: dict[str, Any] = {}
    if ctx.config.position_cap_mode and ctx.observer is not None:
        cap_kw = pbv2_cap_kwargs(ctx.config, ctx.observer, str(trade.get("symbol") or ""))
        if str(entry_pool).strip().upper() == ENTRY_TYPE_OR:
            from small_paper.or_overlay_cap import observer_cap_kwargs_for_pool

            cap_kw = observer_cap_kwargs_for_pool(
                ctx.observer,
                str(trade.get("symbol") or ""),
                entry_pool=ENTRY_TYPE_OR,
                cap_pbv2=int(getattr(ctx.config, "cap_pbv2", 4) or 4),
                cap_or=int(getattr(ctx.config, "cap_or", 1) or 1),
            )
    max_cap = cap_kw.pop("max_concurrent_positions", None)
    decision = ctx.gate.evaluate_entry(
        trade,
        **cap_kw,
        max_concurrent_positions=max_cap,
    )
    stats = getattr(ctx.state, "position_cap_stats", None)
    maybe_track_legacy_vh_shadow(
        stats,
        trade,
        decision_accept=bool(decision.accept),
        decision_reason=str(decision.reason or ""),
    )
    if (
        ctx.config.position_cap_mode
        and stats is not None
        and not decision.accept
        and decision.reason == REJECT_MAX_CONCURRENT
    ):
        stats.record_cap_reject()
    if (
        not decision.accept
        and decision.reason == REJECT_MAX_CONCURRENT
        and getattr(ctx.config, "or_overlay_enabled", False)
    ):
        from dataclasses import replace

        mapped = cap_reject_reason_for_pool(entry_pool)
        if mapped != REJECT_MAX_CONCURRENT:
            decision = replace(decision, reason=mapped)
    return decision


# Phase627: reject reasons whose gate name differs from the reason string.
_PBV2_GATE_BY_REASON: dict[str, str] = {
    "classic_late_chase_rsi_over80": "classic_late_chase_rsi_guard",
    "reentry_rsi_guard_below60": "reentry_rsi_guard",
    "entry_quality_guard_spread": "entry_quality_guard",
    "entry_quality_guard_update_count": "entry_quality_guard",
    "momentum_low_required": "entry_score_v2_gate",
    "board_mid_required_for_v2": "entry_score_v2_gate",
    "board_high_required_for_v2": "entry_score_v2_gate",
    "entry_score_v2_below_threshold": "entry_score_v2_gate",
    "high_drift_pullback": "high_drift_pullback_guard",
    "pullback_misread_dynamic40_guard": "pullback_misread_dynamic40_guard",
    "quality": "continuation_quality_gate",
    "data_stale_price": "freshness",
    "data_stale_board": "freshness",
    "event_stale": "freshness",
    "board_stale": "freshness",
}


def _pbv2_gate_from_reason(reason: str) -> str:
    return _PBV2_GATE_BY_REASON.get(reason, reason)


def _record_pbv2_internal_reject(state: _LiveRunState, trade: dict[str, Any], pbv2_decision: Any) -> None:
    """Phase627: persist the PBv2 reject reason BEFORE the OR overlay can mask it."""
    reason = str(getattr(pbv2_decision, "reason", "") or "")
    trade["pbv2_internal_reason"] = reason
    trade["pbv2_internal_gate"] = _pbv2_gate_from_reason(reason)
    if reason:
        state.pbv2_internal_reason_counts[reason] = (
            state.pbv2_internal_reason_counts.get(reason, 0) + 1
        )


GATE_DOMINANCE_WARNING_PCT = 80.0
GATE_DOMINANCE_CRITICAL_PCT = 95.0
GATE_DOMINANCE_MIN_SAMPLES = 50


def _gate_dominance_alert_fields(state: _LiveRunState) -> dict[str, Any]:
    """Phase627: alert (no trading stop) when a single blocker dominates all rejects."""
    combined: dict[str, int] = dict(state.stale_reason_counts)
    for k, v in state.pbv2_internal_reason_counts.items():
        combined[k] = combined.get(k, 0) + v
    total = sum(combined.values())
    fields: dict[str, Any] = {
        "pbv2_internal_reason_counts": dict(
            sorted(state.pbv2_internal_reason_counts.items(), key=lambda kv: kv[1], reverse=True)
        ),
        "gate_dominance_total_rejects": total,
        "gate_dominance_top_reason": "",
        "gate_dominance_top_share_pct": 0.0,
        "gate_dominance_alert_level": "none",
        "gate_dominance_warning_pct": GATE_DOMINANCE_WARNING_PCT,
        "gate_dominance_critical_pct": GATE_DOMINANCE_CRITICAL_PCT,
        "gate_dominance_min_samples": GATE_DOMINANCE_MIN_SAMPLES,
    }
    if combined and total >= GATE_DOMINANCE_MIN_SAMPLES:
        top_reason, top_n = max(combined.items(), key=lambda kv: kv[1])
        share = 100.0 * top_n / total
        fields["gate_dominance_top_reason"] = top_reason
        fields["gate_dominance_top_share_pct"] = round(share, 2)
        if share >= GATE_DOMINANCE_CRITICAL_PCT:
            fields["gate_dominance_alert_level"] = "critical"
        elif share >= GATE_DOMINANCE_WARNING_PCT:
            fields["gate_dominance_alert_level"] = "warning"
    return fields


def _maybe_try_or_overlay_entry(
    ctx: _PushPipelineContext,
    *,
    sym: str,
    trade: dict[str, Any],
    payload: Mapping[str, Any],
    pbv2_decision: Any,
) -> Any:
    from small_paper.extended_entry_shadow import tick_ts_from_payload
    from small_paper.or_overlay_entry import evaluate_or_overlay_entry

    or_st = getattr(ctx.state, "or_overlay", None)
    if or_st is None or pbv2_decision.accept:
        return pbv2_decision
    if ctx.observer and ctx.observer.has_open(sym):
        return pbv2_decision
    universe = sorted(ctx.entry_eligible_symbols) if ctx.entry_eligible_symbols else None
    return evaluate_or_overlay_entry(
        gate=ctx.gate,
        trade=trade,
        payload=payload,
        price_ring=ctx.symbol_price_ring.get(sym, []),
        entry_ts=tick_ts_from_payload(payload),
        observer=ctx.observer,
        or_state=or_st,
        universe_symbols=universe,
    )


def _active_cap_count(ctx: _PushPipelineContext) -> int:
    from small_paper.position_cap_mode import active_cap_positions

    return active_cap_positions(
        ctx.config,
        observer=ctx.observer,
        gate_open_slots=len(ctx.gate.state.open_slots),
    )


def _record_observer_open_peak(ctx: _PushPipelineContext) -> None:
    if ctx.observer is None:
        return
    c = int(ctx.observer.open_count())
    ctx.state.peak_observer_open = max(getattr(ctx.state, "peak_observer_open", 0), c)
    stats = getattr(ctx.state, "position_cap_stats", None)
    if stats is not None:
        stats.record_observer_open(c)


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
        "live_trading_enabled": bool(getattr(config, "live_trading_enabled", False)),
        "live_order_dry_run_enabled": bool(getattr(config, "live_order_dry_run_enabled", False)),
        "dry_run_required": bool(getattr(config, "dry_run_required", True)),
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
    gate: Optional[ExposureGate] = None,
    source: str = "",
    message_index: int = 0,
    profile: str = "",
    config: Optional[SmallPaperPilotConfig] = None,
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
                bd_counters = getattr(state, "board_dynamic_trailing_shadow", None)
                if bd_counters is not None:
                    bd_counters.record_exit(row)
                lu_counters = getattr(state, "limit_up_proximity_entry_guard_shadow", None)
                if lu_counters is not None:
                    lu_counters.record_exit(row)
                pb_counters = getattr(state, "pullback_misread_entry_guard_shadow", None)
                if pb_counters is not None:
                    pb_counters.record_exit(row)
                score_counters = getattr(state, "entry_expectancy_score_shadow", None)
                if score_counters is not None:
                    score_counters.record_exit(row)
                post_entry = getattr(state, "post_entry_forward_shadow", None)
                if post_entry is not None:
                    post_entry.record_exit(row)
                reentry_guard = getattr(gate, "reentry_rsi_guard", None) if gate else None
                if reentry_guard is not None:
                    reentry_guard.record_exit(row)
                cluster_guard = getattr(gate, "entry_cluster_guard", None) if gate else None
                if cluster_guard is not None:
                    cluster_guard.record_exit(row)
                or_st = getattr(state, "or_overlay", None)
                if or_st is not None:
                    or_st.record_exit(row)
                if config is not None and ev.context.get("is_structural_exit"):
                    _maybe_record_live_order_exit(
                        config=config,
                        state=state,
                        writer=writer,
                        symbol=ev.symbol,
                        context=ev.context,
                    )
            if writer is not None:
                writer.append_event(row)
            if state is not None and ev.kind == OBSERVER_EXIT:
                obs = getattr(state, "observer_tracker", None)
                if obs is not None:
                    c = int(obs.open_count())
                    state.peak_observer_open = max(getattr(state, "peak_observer_open", 0), c)
                    stats = getattr(state, "position_cap_stats", None)
                    if stats is not None:
                        stats.record_observer_open(c)
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


def _post_entry_forward_shadow_summary_fields(state: _LiveRunState) -> dict[str, Any]:
    session = getattr(state, "post_entry_forward_shadow", None)
    if session is None:
        return {}
    return session.summary_fields()


def _classic_momentum_forward_shadow_summary_fields(state: _LiveRunState) -> dict[str, Any]:
    session = getattr(state, "classic_momentum_forward_shadow", None)
    if session is None:
        return {}
    return session.summary_fields()


def _apply_classic_momentum_forward_shadow_finalize(
    state: _LiveRunState,
    summary: dict[str, Any],
    *,
    output_dir: Optional[Path] = None,
) -> None:
    session = getattr(state, "classic_momentum_forward_shadow", None)
    if session is None:
        return
    day = datetime.now(JST).strftime("%Y%m%d")
    session.finalize_session_end(ts=datetime.now(JST).timestamp(), day=day)
    summary.update(session.summary_fields())
    if output_dir is not None:
        session.write_session_csv(output_dir)


def _apply_post_entry_forward_shadow_finalize(
    state: _LiveRunState,
    summary: dict[str, Any],
    *,
    output_dir: Optional[Path] = None,
) -> None:
    session = getattr(state, "post_entry_forward_shadow", None)
    if session is None:
        return
    summary.update(session.summary_fields())
    if output_dir is not None:
        session.write_session_csv(output_dir)


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


def _board_dynamic_trailing_shadow_summary_fields(state: _LiveRunState) -> dict[str, Any]:
    counters = getattr(state, "board_dynamic_trailing_shadow", None)
    if counters is None:
        return {}
    return counters.summary_fields()


def _limit_up_proximity_entry_guard_shadow_summary_fields(state: _LiveRunState) -> dict[str, Any]:
    counters = getattr(state, "limit_up_proximity_entry_guard_shadow", None)
    if counters is None:
        return {}
    return counters.summary_fields()


def _pullback_misread_dynamic40_guard_summary_fields(
    gate: ExposureGate,
    state: _LiveRunState,
) -> dict[str, Any]:
    guard = getattr(gate, "pullback_misread_dynamic40_guard", None)
    if guard is None:
        return {
            "pullback_misread_dynamic40_guard_enabled": False,
            "pullback_misread_dynamic40_reject_count": 0,
            "pullback_misread_dynamic40_reject_symbols": [],
        }
    out = guard.summary_fields()
    out["pullback_misread_dynamic40_reject_count"] = state.pullback_misread_dynamic40_reject_count
    out["pullback_misread_dynamic40_reject_symbols"] = sorted(
        state.pullback_misread_dynamic40_reject_symbols
    )
    return out


def _near_day_high_low_momentum_dynamic40_guard_summary_fields(
    gate: ExposureGate,
    state: _LiveRunState,
) -> dict[str, Any]:
    guard = getattr(gate, "near_day_high_low_momentum_dynamic40_guard", None)
    if guard is None:
        return {
            "near_day_high_low_momentum_dynamic40_guard_enabled": False,
            "near_day_high_low_momentum_dynamic40_reject_count": 0,
            "near_day_high_low_momentum_dynamic40_reject_symbols": [],
        }
    out = guard.summary_fields()
    out["near_day_high_low_momentum_dynamic40_reject_count"] = (
        state.near_day_high_low_momentum_dynamic40_reject_count
    )
    out["near_day_high_low_momentum_dynamic40_reject_symbols"] = sorted(
        state.near_day_high_low_momentum_dynamic40_reject_symbols
    )
    return out


def _weak_shape_reject_guard_summary_fields(
    gate: ExposureGate,
    state: _LiveRunState,
) -> dict[str, Any]:
    guard = getattr(gate, "weak_shape_reject_guard", None)
    if guard is None:
        return {
            "weak_shape_reject_enabled": False,
            "weak_shape_reject_count": 0,
            "weak_shape_reject_symbols": [],
        }
    out = guard.summary_fields()
    out["weak_shape_reject_count"] = state.weak_shape_reject_count
    out["weak_shape_reject_symbols"] = sorted(state.weak_shape_reject_symbols)
    return out


def _board_entry_summary_fields(state: _LiveRunState) -> dict[str, Any]:
    return {
        "board_mid_entry_count": state.board_mid_entry_count,
        "board_high_entry_count": state.board_high_entry_count,
    }


def _late_chase_guard_summary_fields(
    gate: ExposureGate,
    state: _LiveRunState,
) -> dict[str, Any]:
    guard = getattr(gate, "late_chase_guard", None)
    if guard is None:
        return {
            "late_chase_guard_enabled": False,
            "late_chase_reject_count": 0,
            "late_chase_reject_symbols": [],
        }
    out = guard.summary_fields()
    out["late_chase_reject_count"] = state.late_chase_reject_count
    out["late_chase_reject_symbols"] = sorted(state.late_chase_reject_symbols)
    return out


def _classic_late_chase_rsi_guard_summary_fields(
    gate: ExposureGate,
    state: _LiveRunState,
) -> dict[str, Any]:
    guard = getattr(gate, "classic_late_chase_rsi_guard", None)
    if guard is None:
        return {
            "classic_late_chase_rsi_guard_enabled": False,
            "classic_late_chase_rsi_over80": 0,
            "classic_late_chase_rsi_reject_count": 0,
            "classic_late_chase_rsi_reject_symbols": [],
        }
    out = guard.summary_fields()
    out["classic_late_chase_rsi_over80"] = state.classic_late_chase_rsi_reject_count
    out["classic_late_chase_rsi_reject_count"] = state.classic_late_chase_rsi_reject_count
    out["classic_late_chase_rsi_reject_symbols"] = sorted(
        state.classic_late_chase_rsi_reject_symbols
    )
    return out


def _reentry_rsi_guard_summary_fields(
    gate: ExposureGate,
    state: _LiveRunState,
) -> dict[str, Any]:
    guard = getattr(gate, "reentry_rsi_guard", None)
    if guard is None:
        return {
            "reentry_rsi_guard_enabled": False,
            "reentry_rsi_guard_reject_count": 0,
            "reentry_rsi_guard_reject_symbols": [],
        }
    out = guard.summary_fields()
    out["reentry_rsi_guard_reject_count"] = state.reentry_rsi_guard_reject_count
    out["reentry_rsi_guard_reject_symbols"] = sorted(state.reentry_rsi_guard_reject_symbols)
    return out


def _entry_quality_guard_summary_fields(
    gate: ExposureGate,
    state: _LiveRunState,
) -> dict[str, Any]:
    guard = getattr(gate, "entry_quality_guard", None)
    if guard is None:
        return {
            "entry_quality_guard_enabled": False,
            "entry_quality_guard_reject_count": 0,
            "entry_quality_guard_spread_reject_count": 0,
            "entry_quality_guard_update_reject_count": 0,
            "entry_quality_guard_reject_symbols": [],
        }
    out = guard.summary_fields()
    out["entry_quality_guard_reject_count"] = state.entry_quality_guard_reject_count
    out["entry_quality_guard_spread_reject_count"] = state.entry_quality_guard_spread_reject_count
    out["entry_quality_guard_update_reject_count"] = state.entry_quality_guard_update_reject_count
    out["entry_quality_guard_reject_symbols"] = sorted(state.entry_quality_guard_reject_symbols)
    return out


def _entry_cluster_guard_summary_fields(
    gate: ExposureGate,
    state: _LiveRunState,
) -> dict[str, Any]:
    guard = getattr(gate, "entry_cluster_guard", None)
    if guard is None:
        return {
            "entry_cluster_guard_enabled": False,
            "cluster_guard_reject_count": 0,
            "cluster_guard_exception_count": 0,
            "cluster_guard_rejected_pnl": 0.0,
            "cluster_guard_exception_pnl": 0.0,
            "cluster_guard_exception_win_rate": 0.0,
            "cluster_guard_exception_pf": 0.0,
            "cluster_guard_exception_big_winner": 0,
            "cluster_guard_exception_mfe0": 0,
            "cluster_guard_blocked_cluster_counts": {},
        }
    out = guard.summary_fields()
    out["cluster_guard_reject_count"] = max(state.cluster_guard_reject_count, guard.reject_count)
    out["cluster_guard_reject_symbols"] = sorted(
        state.cluster_guard_reject_symbols or guard.rejected_symbols
    )
    return out


def _stop_low_mfe_guard_summary_fields(
    gate: ExposureGate,
    state: _LiveRunState,
) -> dict[str, Any]:
    guard = getattr(gate, "stop_low_mfe_guard", None)
    if guard is None:
        return {
            "stop_low_mfe_guard_enabled": False,
            "stop_low_mfe_guard_reject_count": 0,
            "stop_low_mfe_guard_missing_count": 0,
            "stop_low_mfe_guard_blocked_loss": 0.0,
            "stop_low_mfe_guard_blocked_winner": 0.0,
            "stop_low_mfe_guard_blocked_big_winner": 0,
            "stop_low_mfe_guard_net_shadow": 0.0,
            "stop_low_mfe_guard_volume_accel_threshold": 0.009,
        }
    out = guard.summary_fields()
    out["stop_low_mfe_guard_reject_count"] = max(
        state.stop_low_mfe_guard_reject_count, guard.reject_count
    )
    out["stop_low_mfe_guard_reject_symbols"] = sorted(
        state.stop_low_mfe_guard_reject_symbols or guard.rejected_symbols
    )
    return out


def _high_drift_pullback_guard_summary_fields(
    gate: ExposureGate,
    state: _LiveRunState,
) -> dict[str, Any]:
    guard = getattr(gate, "high_drift_pullback_guard", None)
    if guard is None:
        return {
            "high_drift_pullback_guard_enabled": False,
            "high_drift_pullback_reject_count": 0,
            "high_drift_pullback_reject_symbols": [],
        }
    out = guard.summary_fields()
    out["high_drift_pullback_reject_count"] = state.high_drift_pullback_reject_count
    out["high_drift_pullback_reject_symbols"] = sorted(state.high_drift_pullback_reject_symbols)
    return out


def _pullback_misread_entry_guard_shadow_summary_fields(state: _LiveRunState) -> dict[str, Any]:
    counters = getattr(state, "pullback_misread_entry_guard_shadow", None)
    if counters is None:
        return {}
    return counters.summary_fields()


def _entry_expectancy_score_summary_fields(state: _LiveRunState) -> dict[str, Any]:
    counters = getattr(state, "entry_expectancy_score_shadow", None)
    if counters is None:
        return {}
    return counters.summary_fields()


def _apply_exit_shadow_monitor_finalize(
    state: _LiveRunState,
    summary: dict[str, Any],
    *,
    config: SmallPaperPilotConfig,
) -> None:
    from small_paper.exit_shadow_monitor import (
        config_from_pilot,
        finalize_session_exit_shadow_monitor_safe,
    )

    summary.update(
        finalize_session_exit_shadow_monitor_safe(
            state.events,
            monitor=config_from_pilot(config),
        )
    )


def _apply_entry_expectancy_score_shadow_finalize(
    state: _LiveRunState,
    summary: dict[str, Any],
) -> None:
    from small_paper.entry_expectancy_score_shadow import finalize_session_entry_expectancy_score

    summary.update(finalize_session_entry_expectancy_score(state.accepted_rows, state.events))


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
    entry_scan: Optional[Any] = None
    symbol_universe_meta: dict[str, dict[str, str]] = field(default_factory=dict)
    latency_trace: Optional[Any] = None
    extension_bus: Optional[Any] = None
    stage_profiler: Optional[Any] = None


def _latency_trace(ctx: _PushPipelineContext) -> Optional[Any]:
    bus = getattr(ctx, "extension_bus", None)
    if bus is not None and getattr(bus, "latency_trace", None) is not None:
        return bus.latency_trace
    return getattr(ctx, "latency_trace", None)


def _should_record_entry_shadows(ctx: _PushPipelineContext) -> bool:
    bus = getattr(ctx, "extension_bus", None)
    return bus is not None and bus.should_record_entry_shadows()


def _should_enrich_accept_audit(ctx: _PushPipelineContext) -> bool:
    bus = getattr(ctx, "extension_bus", None)
    return bus is not None and bus.should_enrich_accept_audit()


def _init_extension_stack_for_mode(
    config: SmallPaperPilotConfig,
    state: _LiveRunState,
    *,
    repo_root: Path,
) -> None:
    from small_paper.core_runtime_mode import full_extension_active, get_core_runtime_mode

    if not full_extension_active(get_core_runtime_mode(config)):
        return
    _init_live_order_dry_run(config, state)
    _init_live_order_api_wiring(config, state)
    _init_live_capital_manager(config, state, repo_root=repo_root)
    _init_live_order_adapter(config, state)


REJECT_OUTSIDE_REFRESH_UNIVERSE = "outside_refresh_universe"
REJECT_SAME_SYMBOL_OPEN_OVERLAP = "REJECT_SAME_SYMBOL_OPEN_OVERLAP"


def _same_symbol_open_policy(config: Any) -> str:
    return str(getattr(config, "same_symbol_open_policy", "replace") or "replace").strip().lower()


def _maybe_reject_same_symbol_open_overlap(
    ctx: _PushPipelineContext,
    *,
    sym: str,
    trade: Mapping[str, Any],
    decision: Any,
    payload: Mapping[str, Any],
    msg_i: int,
) -> bool:
    """
    Apply same_symbol_open_policy=no_overlap_replace.

    If observer has an open position for sym, reject this ENTRY and do not call close_for_overlap().
    Returns True if rejected (caller must stop processing).
    """
    if ctx.observer is None:
        return False
    if _same_symbol_open_policy(ctx.config) != "no_overlap_replace":
        return False
    try:
        entry_px = float(payload.get("CurrentPrice") or 0)
    except (TypeError, ValueError):
        entry_px = 0.0
    if entry_px <= 0:
        return False
    if not ctx.observer.has_open(sym):
        return False

    from research.exposure_gate import GateDecision

    rej_row = dict(trade)
    rej_row["reject_reason"] = REJECT_SAME_SYMBOL_OPEN_OVERLAP
    rej_row["gate_reject_reason"] = REJECT_SAME_SYMBOL_OPEN_OVERLAP
    rej_row["final_reject_reason"] = REJECT_SAME_SYMBOL_OPEN_OVERLAP
    ctx.state.reject_rows.append(rej_row)

    rej_decision = GateDecision(
        accept=False,
        reason=REJECT_SAME_SYMBOL_OPEN_OVERLAP,
        continuation_quality_score=float(trade.get("continuation_quality_score") or 0),
        quality_tier=getattr(decision, "quality_tier", "") or "",
    )
    rej = _event_from_gate(
        event_type="rejected",
        trade=trade,
        decision=rej_decision,
        source=ctx.source,
        message_index=msg_i,
        current_price=payload.get("CurrentPrice"),
    )
    rej["reject_reason"] = REJECT_SAME_SYMBOL_OPEN_OVERLAP
    rej["same_symbol_open_policy"] = _same_symbol_open_policy(ctx.config)
    ctx.state.events.append(rej)
    ctx.writer.append_event(rej)
    _record_bucket(ctx.state, "rejected")
    return True


def _load_symbol_universe_meta_for_day(
    *,
    repo_root: Path,
    day_compact: str,
    session_kind: str = "am",
    universe_csv_path: Optional[str] = None,
) -> dict[str, dict[str, str]]:
    from small_paper.pullback_misread_dynamic40_entry_guard import (
        load_symbol_universe_meta,
        resolve_universe_meta_path,
    )

    reports_dir = repo_root / "kabu_native" / "results" / "reports"
    path = resolve_universe_meta_path(
        day_compact=day_compact,
        session_kind=session_kind,
        reports_dir=reports_dir,
        universe_csv_path=universe_csv_path,
    )
    if path is None:
        return {}
    return load_symbol_universe_meta(path)


def _enrich_trade_for_entry_guards(
    ctx: _PushPipelineContext,
    *,
    sym: str,
    trade: dict[str, Any],
    payload: Mapping[str, Any],
) -> None:
    meta = (ctx.symbol_universe_meta or {}).get(sym, {})
    if meta:
        from small_paper.pullback_misread_dynamic40_entry_guard import attach_universe_fields

        attach_universe_fields(trade, meta)
    pullback_guard = getattr(ctx.gate, "pullback_misread_dynamic40_guard", None)
    near_day_guard = getattr(ctx.gate, "near_day_high_low_momentum_dynamic40_guard", None)
    high_drift_guard = getattr(ctx.gate, "high_drift_pullback_guard", None)
    weak_shape_guard = getattr(ctx.gate, "weak_shape_reject_guard", None)
    late_chase_guard = getattr(ctx.gate, "late_chase_guard", None)
    classic_late_chase_rsi_guard = getattr(ctx.gate, "classic_late_chase_rsi_guard", None)
    reentry_rsi_guard = getattr(ctx.gate, "reentry_rsi_guard", None)
    entry_quality_guard = getattr(ctx.gate, "entry_quality_guard", None)
    entry_cluster_guard = getattr(ctx.gate, "entry_cluster_guard", None)
    stop_low_mfe_guard = getattr(ctx.gate, "stop_low_mfe_guard", None)
    if (
        pullback_guard is None
        and near_day_guard is None
        and high_drift_guard is None
        and weak_shape_guard is None
        and late_chase_guard is None
        and classic_late_chase_rsi_guard is None
        and reentry_rsi_guard is None
        and entry_quality_guard is None
        and entry_cluster_guard is None
        and stop_low_mfe_guard is None
    ):
        return
    from small_paper.extended_entry_shadow import compute_entry_shadow_fields, tick_ts_from_payload

    entry_ts = tick_ts_from_payload(payload)
    shadow = compute_entry_shadow_fields(
        trade=trade,
        payload=payload,
        price_ring=ctx.symbol_price_ring.get(sym, []),
        entry_ts=entry_ts,
        session_momentum_samples=ctx.state.session_momentum_samples,
    )
    for key in (
        "entry_rise_5min_pct",
        "entry_rise_10min_pct",
        "entry_rise_15min_pct",
        "entry_rise_30min_pct",
        "entry_vwap_dev_pct",
    ):
        if shadow.get(key) is not None:
            trade[key] = shadow[key]
    if shadow.get("entry_near_day_high_pct") is not None:
        trade["entry_near_day_high_pct"] = shadow["entry_near_day_high_pct"]
        trade["day_high_distance_pct"] = shadow["entry_near_day_high_pct"]
    if shadow.get("entry_momentum_continuation_score") is not None:
        trade["entry_momentum_continuation_score"] = shadow[
            "entry_momentum_continuation_score"
        ]
        trade["entry_momentum_score"] = shadow["entry_momentum_continuation_score"]
    elif trade.get("momentum_continuation_score") is not None:
        trade["entry_momentum_score"] = trade.get("momentum_continuation_score")
    if weak_shape_guard is not None:
        from small_paper.weak_shape_reject_entry_guard import compute_day_high_timing_fields

        try:
            entry_px = float(payload.get("CurrentPrice") or trade.get("current_price") or 0)
        except (TypeError, ValueError):
            entry_px = 0.0
        board_high = _as_float(payload.get("HighPrice"))
        timing = compute_day_high_timing_fields(
            price_ring=ctx.symbol_price_ring.get(sym, []),
            entry_ts=entry_ts,
            entry_px=entry_px,
            board_high=board_high,
        )
        trade.update(timing)
    if classic_late_chase_rsi_guard is not None:
        from small_paper.classic_late_chase_rsi_guard import (
            compute_classic_late_chase_rsi_guard_fields,
        )

        cr_fields = compute_classic_late_chase_rsi_guard_fields(
            trade,
            price_ring=ctx.symbol_price_ring.get(sym, []),
            entry_ts=entry_ts,
            threshold=classic_late_chase_rsi_guard.config.rsi_threshold,
            enabled=classic_late_chase_rsi_guard.config.enabled,
        )
        trade.update(cr_fields)
    if reentry_rsi_guard is not None:
        from small_paper.reentry_rsi_guard import compute_reentry_rsi_guard_fields

        rr_fields = compute_reentry_rsi_guard_fields(
            trade,
            price_ring=ctx.symbol_price_ring.get(sym, []),
            entry_ts=entry_ts,
            threshold=reentry_rsi_guard.config.rsi_threshold,
            enabled=reentry_rsi_guard.config.enabled,
            reentry_after_stop=reentry_rsi_guard.is_reentry_after_stop(sym),
        )
        trade.update(rr_fields)
    if entry_quality_guard is not None:
        from small_paper.entry_quality_guard import compute_entry_quality_guard_fields

        eq_fields = compute_entry_quality_guard_fields(
            trade,
            payload=payload,
            price_ring=ctx.symbol_price_ring.get(sym, []),
            entry_ts=entry_ts,
            max_spread_bps=entry_quality_guard.config.max_spread_bps,
            max_update_count=entry_quality_guard.config.max_update_count,
            enabled=entry_quality_guard.config.enabled,
        )
        trade.update(eq_fields)
    if entry_cluster_guard is not None:
        from small_paper.entry_cluster_guard import compute_entry_cluster_guard_fields

        trade.update(
            compute_entry_cluster_guard_fields(trade, model=entry_cluster_guard.model)
        )
    if stop_low_mfe_guard is not None:
        from small_paper.stop_low_mfe_guard import compute_stop_low_mfe_guard_fields

        trade.update(
            compute_stop_low_mfe_guard_fields(trade, guard=stop_low_mfe_guard)
        )


def _enrich_trade_for_pullback_guard(
    ctx: _PushPipelineContext,
    *,
    sym: str,
    trade: dict[str, Any],
    payload: Mapping[str, Any],
) -> None:
    _enrich_trade_for_entry_guards(ctx, sym=sym, trade=trade, payload=payload)


def _make_entry_scan_controller(
    config: SmallPaperPilotConfig,
    *,
    source: str,
    writer: LiveSessionWriter,
) -> Any:
    from small_paper.core_runtime_mode import audit_enabled_for_mode, get_core_runtime_mode
    from small_paper.entry_scan_controller import entry_scan_controller_from_config

    mode = get_core_runtime_mode(config)
    audit_writer = writer.append_entry_scan_audit if audit_enabled_for_mode(mode) else None
    return entry_scan_controller_from_config(
        config,
        pipeline_source=source,
        audit_writer=audit_writer,
    )


def _execute_accepted_entry(
    ctx: _PushPipelineContext,
    *,
    sym: str,
    trade: dict[str, Any],
    decision: Any,
    payload: Mapping[str, Any],
    enriched: Mapping[str, Any],
    msg_i: int,
    bucket: str,
    score5_ord: Optional[int],
    scan_meta: Optional[Mapping[str, Any]] = None,
) -> None:
    from small_paper.extended_entry_shadow import compute_entry_shadow_fields, tick_ts_from_payload

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
    if scan_meta:
        trade.update({k: v for k, v in dict(scan_meta).items() if v is not None})
    if _should_enrich_accept_audit(ctx):
        _enrich_accept_audit_fields(
            trade,
            gate=ctx.gate,
            current_price=payload.get("CurrentPrice"),
        )
    if _maybe_reject_same_symbol_open_overlap(
        ctx,
        sym=sym,
        trade=trade,
        decision=decision,
        payload=payload,
        msg_i=msg_i,
    ):
        return
    mom_sample = _as_float(trade.get("momentum_continuation_score"))
    if mom_sample is not None:
        ctx.state.session_momentum_samples.append(float(mom_sample))
    if _should_record_entry_shadows(ctx):
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
        from small_paper.limit_up_proximity_entry_guard_shadow import (
            compute_limit_up_proximity_guard_fields,
        )

        try:
            entry_px_lu = float(payload.get("CurrentPrice") or 0)
        except (TypeError, ValueError):
            entry_px_lu = 0.0
        prev_close_lu = _as_float(payload.get("PreviousClose")) or _as_float(trade.get("close_price"))
        limit_up_shadow = compute_limit_up_proximity_guard_fields(
            entry_px=entry_px_lu,
            prev_close=prev_close_lu,
            entry_near_day_high_pct=shadow.get("entry_near_day_high_pct"),
            board_high=_as_float(payload.get("HighPrice")),
        )
        trade.update(limit_up_shadow)
        ctx.state.limit_up_proximity_entry_guard_shadow.record_accept(limit_up_shadow)
        from small_paper.pullback_misread_dynamic40_entry_guard import compute_pullback_misread_guard_fields

        pb_shadow = compute_pullback_misread_guard_fields(trade)
        trade.update(pb_shadow)
        ctx.state.pullback_misread_entry_guard_shadow.record_accept(pb_shadow)
        from small_paper.near_day_high_low_momentum_dynamic40_entry_guard import (
            compute_near_day_high_low_momentum_guard_fields,
        )

        nd_shadow = compute_near_day_high_low_momentum_guard_fields(trade)
        trade.update(nd_shadow)
        from small_paper.high_drift_pullback_entry_guard import compute_high_drift_pullback_guard_fields

        hd_shadow = compute_high_drift_pullback_guard_fields(trade)
        trade.update(hd_shadow)
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
        from small_paper.entry_expectancy_score_shadow import compute_entry_expectancy_score_fields

        score_fields = compute_entry_expectancy_score_fields(trade=trade)
        trade.update(score_fields)
        ctx.state.entry_expectancy_score_shadow.record_accept(score_fields)
        from small_paper.entry_expectancy_score_shadow import _feature_token

        board_tok = _feature_token("Board", trade)
        if board_tok == "Board:mid":
            ctx.state.board_mid_entry_count += 1
        elif board_tok == "Board:high":
            ctx.state.board_high_entry_count += 1
        from small_paper.weak_shape_reject_entry_guard import compute_weak_shape_reject_guard_fields

        trade.update(compute_weak_shape_reject_guard_fields(trade))
        from small_paper.late_chase_entry_guard import compute_late_chase_guard_fields

        trade.update(compute_late_chase_guard_fields(trade))

    for key in (
        "cluster_guard_status",
        "cluster_id",
        "new_subcluster_id",
        "liquidity_burst",
        "entry_cluster_guard_via_exception",
    ):
        val = getattr(decision, key, None)
        if val not in (None, ""):
            trade[key] = val

    if "entry_type" not in trade:
        trade["entry_type"] = "PBV2"
    or_st = getattr(ctx.state, "or_overlay", None)
    if or_st is not None:
        or_st.record_entry(trade)

    slot_before = _active_cap_count(ctx)
    ctx.gate.record_accepted(trade)
    if not ctx.config.position_cap_mode:
        slot_after = len(ctx.gate.state.open_slots)
        ctx.state.peak_open_slots = max(ctx.state.peak_open_slots, slot_after)
    else:
        slot_after = slot_before
    acc = _event_from_gate(
        event_type="accepted",
        trade=trade,
        decision=decision,
        source=ctx.source,
        message_index=msg_i,
        current_price=payload.get("CurrentPrice"),
    )
    if scan_meta:
        acc.update({k: v for k, v in dict(scan_meta).items() if v is not None})
    acc["position_slot_before"] = slot_before
    acc["position_slot_after"] = slot_after
    if ctx.config.position_cap_mode:
        acc["position_cap_mode"] = True
        acc["max_concurrent_positions"] = ctx.config.max_concurrent_positions
    ctx.state.events.append(acc)
    ctx.writer.append_event(acc)
    ctx.state.accepted_rows.append(dict(trade))
    _record_bucket(ctx.state, "accepted")
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
                gate=ctx.gate,
                source=ctx.source,
                message_index=msg_i,
                profile=ctx.config.profile,
                config=ctx.config,
            )
        if entry_px > 0 and not ctx.observer.has_open(sym):
            ctx.observer.register_entry(
                trade=trade,
                payload=enriched,
                quality_tier=str(decision.quality_tier or ""),
                entry_price=entry_px,
            )
        _record_observer_open_peak(ctx)
        if ctx.config.position_cap_mode:
            slot_after = _active_cap_count(ctx)
            acc["position_slot_after"] = slot_after
    ctx.writer.append_position_row(
        {
            "symbol": trade.get("symbol"),
            "entry_time": trade.get("entry_time"),
            "exit_time": trade.get("exit_time"),
            "open_slots_after": slot_after,
        },
        fields=ctx.pos_fields,
    )
    if ctx.discord and ctx.discord.active:
        import time

        notify_mono = time.monotonic()
        signal_mono = float((scan_meta or {}).get("entry_signal_mono") or notify_mono)
        ctx.discord.notify_entry(
            event=acc,
            payload=enriched,
            open_slots=slot_after,
            session_bucket=bucket,
            score5_candidate_ordinal=score5_ord,
            ux_stats=ctx.state.discord_ux,
            entry_signal_mono=signal_mono,
            notify_mono=notify_mono,
        )
    _maybe_record_live_order_pipeline_entry(ctx, sym=sym, trade=trade, payload=payload, acc=acc, scan_meta=scan_meta)
    if not _legacy_live_order_hooks_enabled(ctx.config):
        return
    _maybe_record_live_capital_check_entry(ctx, sym=sym, trade=trade, payload=payload, acc=acc)
    _maybe_record_live_order_entry(ctx, sym=sym, trade=trade, payload=payload, acc=acc)
    _maybe_record_live_order_wiring_entry(
        ctx, sym=sym, trade=trade, payload=payload, acc=acc, scan_meta=scan_meta
    )


def _process_scan_flush(ctx: _PushPipelineContext, flush: Any) -> None:
    from research.exposure_gate import GateDecision
    from small_paper.entry_scan_controller import REJECT_MAX_ENTRIES_PER_SCAN

    for cand in flush.accepted:
        n_cand = flush.entry_candidates_count
        rank_i = flush.accepted.index(cand) + 1
        scan_meta = {
            "scan_id": flush.scan_id,
            "data_source": cand.freshness.data_source,
            "price_age_sec": cand.freshness.price_age_sec,
            "board_age_sec": cand.freshness.board_age_sec,
            "same_scan_rank": f"{rank_i}/{n_cand}" if n_cand else "1/1",
            "same_scan_candidates": n_cand,
            "is_same_scan_batch_entry": n_cand > 1,
            "entry_signal_ts": cand.entry_signal_ts,
            "entry_signal_mono": cand.entry_signal_mono,
        }
        _execute_accepted_entry(
            ctx,
            sym=cand.symbol,
            trade=cand.trade,
            decision=cand.decision,
            payload=cand.payload,
            enriched=cand.enriched,
            msg_i=cand.msg_i,
            bucket=cand.bucket,
            score5_ord=cand.score5_ord,
            scan_meta=scan_meta,
        )
    for cand in flush.rejected_max_scan:
        rej_row = dict(cand.trade)
        rej_row["gate_reject_reason"] = REJECT_MAX_ENTRIES_PER_SCAN
        rej_row["final_reject_reason"] = REJECT_MAX_ENTRIES_PER_SCAN
        rej_row["scan_id"] = flush.scan_id
        ctx.state.reject_rows.append(rej_row)
        decision = GateDecision(
            accept=False,
            reason=REJECT_MAX_ENTRIES_PER_SCAN,
            continuation_quality_score=float(cand.trade.get("continuation_quality_score") or 0),
            quality_tier="",
        )
        rej = _event_from_gate(
            event_type="rejected",
            trade=cand.trade,
            decision=decision,
            source=ctx.source,
            message_index=cand.msg_i,
            current_price=cand.payload.get("CurrentPrice"),
        )
        rej["scan_id"] = flush.scan_id
        rej["reject_reason"] = REJECT_MAX_ENTRIES_PER_SCAN
        ctx.state.events.append(rej)
        ctx.writer.append_event(rej)
        _record_bucket(ctx.state, "rejected")


def _record_score5_ordinal(ctx: _PushPipelineContext, trade: Mapping[str, Any]) -> Optional[int]:
    raw = trade.get("entry_expectancy_score_v2")
    try:
        if raw is not None and int(raw) >= 5:
            return ctx.state.discord_ux.record_score5_candidate()
    except (TypeError, ValueError):
        pass
    return None


def _replay_reference_now(
    ctx: _PushPipelineContext, payload: Mapping[str, Any]
) -> Optional[datetime]:
    if ctx.source not in ("push-replay", "push_replay"):
        return None
    from storage.intraday_recorder import parse_kabu_time

    raw = payload.get("recorded_at")
    if raw is None or str(raw).strip() == "":
        return None
    return parse_kabu_time(raw, fallback=datetime.now(JST))


def _process_push_payload(
    ctx: _PushPipelineContext,
    payload: Mapping[str, Any],
    msg_i: int,
    *,
    symbol: Optional[str] = None,
    t0_push_received_at: Optional[str] = None,
    t0_mono: Optional[float] = None,
) -> None:
    import time

    eval_start_mono = time.monotonic()
    eval_start_ts = _now_iso()
    scan_id = ""
    if ctx.entry_scan is not None:
        scan_id, flush_on_begin = ctx.entry_scan.begin_symbol_eval(now_mono=eval_start_mono)
        if flush_on_begin is not None:
            _process_scan_flush(ctx, flush_on_begin)

    sym = symbol or _symbol_from_push(payload, ctx.code_to_symbol)
    if not sym:
        return
    prof = getattr(ctx, "stage_profiler", None)
    if prof is not None:
        prof.begin_tick()
    bus = getattr(ctx, "extension_bus", None)
    if bus is not None:
        bus.on_push_tick(
            symbol=sym,
            payload=payload,
            price_ring=ctx.symbol_price_ring.setdefault(sym, []),
            t0_push_received_at=t0_push_received_at,
            t0_mono=t0_mono,
        )
    if prof is not None:
        prof.mark("extension_done")
    lt = _latency_trace(ctx)
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
        or_st = getattr(ctx.state, "or_overlay", None)
        if or_st is not None:
            or_st.record_day_tick(
                sym,
                current_price=px_tick,
                prev_close=_as_float(payload.get("PreviousClose")),
            )

    snapshot = ctx.feature_bridge.update(sym, payload)
    slm_guard = getattr(ctx.gate, "stop_low_mfe_guard", None)
    if slm_guard is not None:
        slm_guard.ingest_push(sym, payload)
    enriched = ctx.feature_bridge.enrich_payload(payload, snapshot)
    if t0_push_received_at and not enriched.get("recorded_at"):
        enriched["recorded_at"] = t0_push_received_at
    if prof is not None:
        prof.mark("enrich_done")
    if bus is not None:
        bus.mark_payload_parsed()
    elif lt is not None:
        lt.mark_payload_parsed()
    trade = _candidate_trade_from_push(
        enriched,
        symbol=sym,
        profile=ctx.config.profile,
        feature_snapshot=snapshot,
        virtual_hold_sec=float(ctx.config.entry_cooldown_sec),
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
    # Phase295: HBRecent must be set before gate score (reject path included).
    from small_paper.extended_entry_shadow import (
        compute_entry_high_break_recent_field,
        tick_ts_from_payload,
    )

    trade.update(
        compute_entry_high_break_recent_field(
            trade=trade,
            payload=payload,
            price_ring=ctx.symbol_price_ring.get(sym, []),
            entry_ts=tick_ts_from_payload(payload),
        )
    )
    # Phase299: board imbalance must be set before gate score (reject path included).
    from small_paper.board_imbalance_shadow import compute_entry_order_book_imbalance_field

    trade.update(compute_entry_order_book_imbalance_field(payload=enriched))
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
            gate=ctx.gate,
            source=ctx.source,
            message_index=msg_i,
            profile=ctx.config.profile,
            config=ctx.config,
        )
    from small_paper.am_pm_session_policy import AmPmSessionPolicy

    stale_reason: Optional[str] = None
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
        from small_paper.entry_expectancy_score_shadow import compute_entry_expectancy_score_fields

        trade.update(compute_entry_expectancy_score_fields(trade=trade))
        from small_paper.entry_scan_controller import (
            evaluate_entry_data_freshness,
            compute_entry_freshness,
            REJECT_DATA_STALE_BOARD,
            REJECT_EVENT_STALE_PRICE,
            PRICE_FRESHNESS_LIQUIDITY_STALE_TRADE,
        )

        ref_now = _replay_reference_now(ctx, enriched) or datetime.now(JST)
        freshness = compute_entry_freshness(
            enriched, pipeline_source=ctx.source, reference_now=ref_now
        )
        trade["entry_data_source"] = freshness.data_source
        trade["price_age_sec"] = freshness.price_age_sec
        trade["board_age_sec"] = freshness.board_age_sec
        trade["last_price_update_ts"] = freshness.last_price_update_ts
        trade["last_board_update_ts"] = freshness.last_board_update_ts
        if scan_id:
            trade["scan_id"] = scan_id
        stale_reason = None
        freshness_decision = None
        if ctx.entry_scan is not None:
            if bus is not None:
                bus.mark_freshness_check()
            elif lt is not None:
                lt.mark_freshness_check()
            freshness_decision = evaluate_entry_data_freshness(
                freshness,
                enriched,
                max_price_age_sec=ctx.entry_scan.max_price_age_sec,
                max_board_age_sec=ctx.entry_scan.max_board_age_sec,
                guard_enabled=ctx.entry_scan.freshness_guard_enabled,
                board_fallback_enabled=ctx.entry_scan.board_fallback_enabled,
                max_fallback_spread_bps=ctx.entry_scan.board_fallback_max_spread_bps,
                reference_now=ref_now,
                freshness_semantics_v2_enabled=ctx.entry_scan.freshness_semantics_v2_enabled,
                event_stale_threshold_sec=ctx.entry_scan.event_stale_threshold_sec,
                board_stale_threshold_sec=ctx.entry_scan.board_stale_threshold_sec,
                trade_stale_threshold_sec=ctx.entry_scan.trade_stale_threshold_sec,
                trade_stale_mode=ctx.entry_scan.trade_stale_mode,
            )
            stale_reason = freshness_decision.reject_reason
            if ctx.entry_scan.freshness_semantics_v2_enabled:
                if stale_reason == REJECT_EVENT_STALE_PRICE:
                    ctx.state.event_stale_reject_count += 1
                elif stale_reason == REJECT_DATA_STALE_BOARD:
                    ctx.state.board_stale_reject_count += 1
                if freshness_decision.price_freshness_source == PRICE_FRESHNESS_LIQUIDITY_STALE_TRADE:
                    ctx.state.trade_stale_tag_count += 1
            trade["price_freshness_source"] = freshness_decision.price_freshness_source
            trade["fallback_used"] = freshness_decision.fallback_used
            trade["fallback_reject_reason"] = freshness_decision.fallback_reject_reason or ""
            if freshness_decision.spread_bps is not None:
                trade.setdefault("spread_bps", freshness_decision.spread_bps)
        if prof is not None:
            prof.mark("freshness_done")
        if stale_reason:
            from research.exposure_gate import GateDecision

            ctx.state.stale_reason_counts[stale_reason] = (
                ctx.state.stale_reason_counts.get(stale_reason, 0) + 1
            )
            decision = GateDecision(
                accept=False,
                reason=stale_reason,
                continuation_quality_score=float(trade.get("continuation_quality_score") or 0),
                quality_tier="",
            )
        else:
            _enrich_trade_for_pullback_guard(ctx, sym=sym, trade=trade, payload=payload)
            if prof is not None:
                prof.mark("pbv2_start")
            if bus is not None:
                bus.mark_pbv2_start()
            elif lt is not None:
                lt.mark_pbv2_start()
            pbv2_decision = _evaluate_gate_entry(ctx, trade, entry_pool="PBV2")
            if prof is not None:
                prof.mark("pbv2_end")
            if bus is not None:
                bus.mark_pbv2_end()
            elif lt is not None:
                lt.mark_pbv2_end()
            if not pbv2_decision.accept:
                _record_pbv2_internal_reject(ctx.state, trade, pbv2_decision)
            decision = _maybe_try_or_overlay_entry(
                ctx,
                sym=sym,
                trade=trade,
                payload=payload,
                pbv2_decision=pbv2_decision,
            )
            if decision is not pbv2_decision and not decision.accept:
                trade["or_overlay_reason"] = str(getattr(decision, "reason", "") or "")
    if not decision.accept:
        trade["final_reject_reason"] = str(getattr(decision, "reason", "") or "")
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
    if prof is not None:
        prof.mark("decision_done")
        import hashlib
        import json

        try:
            payload_hash = hashlib.sha256(
                json.dumps(dict(payload), sort_keys=True, default=str).encode()
            ).hexdigest()[:16]
        except Exception:
            payload_hash = ""
        prof.finish_tick(
            symbol=sym,
            gate_reason=str(getattr(decision, "reason", "") or ""),
            accepted=bool(decision.accept),
            payload_hash=payload_hash,
        )
    if bus is not None:
        bus.finish_latency_trace(
            stale_reason=stale_reason,
            gate_reason=str(getattr(decision, "reason", "") or ""),
            entry_score_v2=trade.get("entry_expectancy_score_v2"),
        )
    elif lt is not None:
        lt.finish(
            stale_reason=stale_reason,
            gate_reason=str(getattr(decision, "reason", "") or ""),
            entry_score_v2=trade.get("entry_expectancy_score_v2"),
        )
    score5_ord = _record_score5_ordinal(ctx, trade)

    eval_end_ts = _now_iso()
    eval_latency_ms = (time.monotonic() - eval_start_mono) * 1000.0
    if ctx.entry_scan is not None:
        from small_paper.entry_scan_controller import (
            PendingEntryCandidate,
            compute_entry_freshness,
        )

        fresh_log = compute_entry_freshness(
            enriched, pipeline_source=ctx.source, reference_now=ref_now
        )
        ctx.entry_scan.record_symbol_eval(
            scan_id=scan_id or "",
            symbol=sym,
            freshness=fresh_log,
            trade=trade,
            entry_decision=bool(decision.accept),
            reject_reason="" if decision.accept else str(decision.reason or ""),
            eval_start_ts=eval_start_ts,
            eval_end_ts=eval_end_ts,
            eval_latency_ms=eval_latency_ms,
            price_freshness_source=str(trade.get("price_freshness_source") or ""),
            spread_bps=trade.get("spread_bps") if isinstance(trade.get("spread_bps"), (int, float)) else None,
            fallback_used=bool(trade.get("fallback_used")),
            fallback_reject_reason=str(trade.get("fallback_reject_reason") or ""),
            event_stale=bool(getattr(freshness_decision, "event_stale", False)),
            board_stale=bool(getattr(freshness_decision, "board_stale", False)),
            trade_stale=bool(getattr(freshness_decision, "trade_stale", False)),
        )
        if bus is not None:
            bus.on_post_eval(
                ctx,
                sym=sym,
                trade=trade,
                decision=decision,
                timestamp=eval_end_ts,
            )

    if decision.accept:
        if ctx.entry_scan is not None and ctx.entry_scan.batch_enabled:
            from small_paper.entry_scan_controller import (
                PendingEntryCandidate,
                compute_entry_freshness,
            )

            freshness = compute_entry_freshness(
                enriched,
                pipeline_source=ctx.source,
                reference_now=_replay_reference_now(ctx, enriched),
            )
            cand = PendingEntryCandidate(
                symbol=sym,
                trade=dict(trade),
                decision=decision,
                payload=dict(payload),
                enriched=dict(enriched),
                msg_i=msg_i,
                freshness=freshness,
                eval_start_ts=eval_start_ts,
                eval_end_ts=eval_end_ts,
                eval_latency_ms=eval_latency_ms,
                entry_signal_ts=eval_end_ts,
                entry_signal_mono=eval_start_mono,
                bucket=bucket,
                score5_ord=score5_ord,
            )
            ctx.entry_scan.queue_accepted_candidate(cand)
            flush_now = ctx.entry_scan.maybe_flush_after_eval()
            if flush_now is not None:
                _process_scan_flush(ctx, flush_now)
        else:
            _execute_accepted_entry(
                ctx,
                sym=sym,
                trade=trade,
                decision=decision,
                payload=payload,
                enriched=enriched,
                msg_i=msg_i,
                bucket=bucket,
                score5_ord=score5_ord,
                scan_meta={
                    "scan_id": scan_id,
                    "entry_signal_mono": eval_start_mono,
                }
                if scan_id
                else {"entry_signal_mono": eval_start_mono},
            )
    else:
        rej_row = dict(trade)
        rej_row["gate_reject_reason"] = decision.reason
        rej_row["pbv2_internal_reason"] = trade.get("pbv2_internal_reason", "")
        rej_row["pbv2_internal_gate"] = trade.get("pbv2_internal_gate", "")
        rej_row["or_overlay_reason"] = trade.get("or_overlay_reason", "")
        rej_row["final_reject_reason"] = trade.get(
            "final_reject_reason", str(decision.reason or "")
        )
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
        if decision.reason == "pullback_misread_dynamic40_guard":
            from small_paper.pullback_misread_dynamic40_entry_guard import (
                LOG_EVENT_KIND as PB_LOG_EVENT_KIND,
                REJECT_PULLBACK_MISREAD_DYNAMIC40_GUARD,
                compute_pullback_misread_guard_fields,
            )

            ctx.state.pullback_misread_dynamic40_reject_count += 1
            ctx.state.pullback_misread_dynamic40_reject_symbols.add(sym)
            pb_fields = compute_pullback_misread_guard_fields(trade)
            trade.update(pb_fields)
            rej_row.update(pb_fields)
            rej_row["reject_reason"] = REJECT_PULLBACK_MISREAD_DYNAMIC40_GUARD
            pb_counters = getattr(ctx.state, "pullback_misread_entry_guard_shadow", None)
            if pb_counters is not None:
                pb_counters.record_reject_candidate(pb_fields)
            ctx.writer.append_error(
                {
                    "event_kind": PB_LOG_EVENT_KIND,
                    "symbol": sym,
                    "entry_rise_5min_pct": getattr(
                        decision, "pullback_misread_dynamic40_entry_rise_5min_pct", None
                    ),
                    "entry_vwap_dev_pct": getattr(
                        decision, "pullback_misread_dynamic40_entry_vwap_dev_pct", None
                    ),
                    "universe_slot": getattr(
                        decision, "pullback_misread_dynamic40_universe_slot", ""
                    ),
                    "universe_bucket": getattr(
                        decision, "pullback_misread_dynamic40_universe_bucket", ""
                    ),
                    "reject_reason": REJECT_PULLBACK_MISREAD_DYNAMIC40_GUARD,
                }
            )
        if decision.reason == "near_day_high_low_momentum_dynamic40_guard":
            from small_paper.near_day_high_low_momentum_dynamic40_entry_guard import (
                LOG_EVENT_KIND as ND_LOG_EVENT_KIND,
                REJECT_NEAR_DAY_HIGH_LOW_MOMENTUM_DYNAMIC40_GUARD,
                compute_near_day_high_low_momentum_guard_fields,
            )

            ctx.state.near_day_high_low_momentum_dynamic40_reject_count += 1
            ctx.state.near_day_high_low_momentum_dynamic40_reject_symbols.add(sym)
            nd_fields = compute_near_day_high_low_momentum_guard_fields(trade)
            trade.update(nd_fields)
            rej_row.update(nd_fields)
            rej_row["reject_reason"] = REJECT_NEAR_DAY_HIGH_LOW_MOMENTUM_DYNAMIC40_GUARD
            ctx.writer.append_error(
                {
                    "event_kind": ND_LOG_EVENT_KIND,
                    "symbol": sym,
                    "day_high_distance_pct": getattr(
                        decision,
                        "near_day_high_low_momentum_dynamic40_day_high_distance_pct",
                        None,
                    ),
                    "entry_momentum_score": getattr(
                        decision,
                        "near_day_high_low_momentum_dynamic40_entry_momentum_score",
                        None,
                    ),
                    "universe_slot": getattr(
                        decision,
                        "near_day_high_low_momentum_dynamic40_universe_slot",
                        "",
                    ),
                    "universe_bucket": getattr(
                        decision,
                        "near_day_high_low_momentum_dynamic40_universe_bucket",
                        "",
                    ),
                    "reject_reason": REJECT_NEAR_DAY_HIGH_LOW_MOMENTUM_DYNAMIC40_GUARD,
                }
            )
        if decision.reason == "high_drift_pullback":
            from small_paper.high_drift_pullback_entry_guard import (
                LOG_EVENT_KIND as HD_LOG_EVENT_KIND,
                REJECT_HIGH_DRIFT_PULLBACK,
                compute_high_drift_pullback_guard_fields,
            )

            ctx.state.high_drift_pullback_reject_count += 1
            ctx.state.high_drift_pullback_reject_symbols.add(sym)
            hd_fields = compute_high_drift_pullback_guard_fields(trade)
            trade.update(hd_fields)
            rej_row.update(hd_fields)
            rej_row["reject_reason"] = REJECT_HIGH_DRIFT_PULLBACK
            ctx.writer.append_error(
                {
                    "event_kind": HD_LOG_EVENT_KIND,
                    "symbol": sym,
                    "entry_rise_5min_pct": getattr(
                        decision, "high_drift_pullback_entry_rise_5min_pct", None
                    ),
                    "entry_rise_10min_pct": getattr(
                        decision, "high_drift_pullback_entry_rise_10min_pct", None
                    ),
                    "entry_rise_15min_pct": getattr(
                        decision, "high_drift_pullback_entry_rise_15min_pct", None
                    ),
                    "day_high_distance_pct": getattr(
                        decision, "high_drift_pullback_day_high_distance_pct", None
                    ),
                    "universe_slot": getattr(
                        decision, "high_drift_pullback_universe_slot", ""
                    ),
                    "universe_bucket": getattr(
                        decision, "high_drift_pullback_universe_bucket", ""
                    ),
                    "reject_reason": REJECT_HIGH_DRIFT_PULLBACK,
                }
            )
        if decision.reason == "weak_shape_reject":
            from small_paper.weak_shape_reject_entry_guard import (
                LOG_EVENT_KIND as WS_LOG_EVENT_KIND,
                REJECT_WEAK_SHAPE,
                compute_weak_shape_reject_guard_fields,
            )

            ctx.state.weak_shape_reject_count += 1
            ctx.state.weak_shape_reject_symbols.add(sym)
            ws_fields = compute_weak_shape_reject_guard_fields(trade)
            trade.update(ws_fields)
            rej_row.update(ws_fields)
            rej_row["reject_reason"] = REJECT_WEAK_SHAPE
            ctx.writer.append_error(
                {
                    "event_kind": WS_LOG_EVENT_KIND,
                    "symbol": sym,
                    "weak_shape_class": getattr(decision, "weak_shape_class", ""),
                    "day_high_minutes_from_open": getattr(
                        decision, "weak_shape_day_high_minutes_from_open", None
                    ),
                    "minutes_since_day_high_update": getattr(
                        decision, "weak_shape_minutes_since_day_high_update", None
                    ),
                    "day_high_distance_pct": getattr(
                        decision, "weak_shape_day_high_distance_pct", None
                    ),
                    "reject_reason": REJECT_WEAK_SHAPE,
                }
            )
        if decision.reason == "late_chase_guard":
            from small_paper.late_chase_entry_guard import (
                LOG_EVENT_KIND as LC_LOG_EVENT_KIND,
                REJECT_LATE_CHASE_GUARD,
                compute_late_chase_guard_fields,
            )

            ctx.state.late_chase_reject_count += 1
            ctx.state.late_chase_reject_symbols.add(sym)
            lc_fields = compute_late_chase_guard_fields(trade)
            trade.update(lc_fields)
            rej_row.update(lc_fields)
            rej_row["reject_reason"] = REJECT_LATE_CHASE_GUARD
            ctx.writer.append_error(
                {
                    "event_kind": LC_LOG_EVENT_KIND,
                    "symbol": sym,
                    "entry_rise_10min_pct": getattr(
                        decision, "late_chase_entry_rise_10min_pct", None
                    ),
                    "day_high_distance_pct": getattr(
                        decision, "late_chase_day_high_distance_pct", None
                    ),
                    "reject_reason": REJECT_LATE_CHASE_GUARD,
                }
            )
        if decision.reason == "classic_late_chase_rsi_over80":
            from small_paper.classic_late_chase_rsi_guard import (
                LOG_EVENT_KIND as CR_LOG_EVENT_KIND,
                REJECT_CLASSIC_LATE_CHASE_RSI_OVER80,
            )

            ctx.state.classic_late_chase_rsi_reject_count += 1
            ctx.state.classic_late_chase_rsi_reject_symbols.add(sym)
            rej_row["time"] = trade.get("entry_time")
            rej_row["rsi14"] = getattr(decision, "classic_late_chase_rsi_rsi14", trade.get("rsi14"))
            rej_row["late_chase_flag"] = getattr(
                decision, "classic_late_chase_rsi_late_chase_flag", trade.get("late_chase_flag")
            )
            rej_row["reject_reason"] = REJECT_CLASSIC_LATE_CHASE_RSI_OVER80
            ctx.writer.append_error(
                {
                    "event_kind": CR_LOG_EVENT_KIND,
                    "symbol": sym,
                    "time": trade.get("entry_time"),
                    "rsi14": rej_row["rsi14"],
                    "late_chase_flag": rej_row["late_chase_flag"],
                    "reject_reason": REJECT_CLASSIC_LATE_CHASE_RSI_OVER80,
                }
            )
        if decision.reason == "reentry_rsi_guard_below60":
            from small_paper.reentry_rsi_guard import (
                LOG_EVENT_KIND as RR_LOG_EVENT_KIND,
                REJECT_REENTRY_RSI_GUARD_BELOW60,
            )

            ctx.state.reentry_rsi_guard_reject_count += 1
            ctx.state.reentry_rsi_guard_reject_symbols.add(sym)
            rej_row["time"] = trade.get("entry_time")
            rej_row["rsi14"] = getattr(decision, "reentry_rsi_rsi14", trade.get("rsi14"))
            rej_row["reentry_rsi_guard_after_stop"] = getattr(
                decision, "reentry_rsi_after_stop", trade.get("reentry_rsi_guard_after_stop")
            )
            rej_row["reject_reason"] = REJECT_REENTRY_RSI_GUARD_BELOW60
            ctx.writer.append_error(
                {
                    "event_kind": RR_LOG_EVENT_KIND,
                    "symbol": sym,
                    "time": trade.get("entry_time"),
                    "rsi14": rej_row["rsi14"],
                    "reentry_after_stop": rej_row["reentry_rsi_guard_after_stop"],
                    "reject_reason": REJECT_REENTRY_RSI_GUARD_BELOW60,
                }
            )
        if decision.reason == "entry_quality_guard_spread":
            from small_paper.entry_quality_guard import (
                LOG_EVENT_KIND_SPREAD,
                REJECT_ENTRY_QUALITY_GUARD_SPREAD,
            )

            ctx.state.entry_quality_guard_reject_count += 1
            ctx.state.entry_quality_guard_spread_reject_count += 1
            ctx.state.entry_quality_guard_reject_symbols.add(sym)
            rej_row["time"] = trade.get("entry_time")
            rej_row["spread_bps"] = getattr(
                decision, "entry_quality_spread_bps", trade.get("spread_bps")
            )
            rej_row["update_count_before_entry"] = getattr(
                decision, "entry_quality_update_count", trade.get("update_count_before_entry")
            )
            rej_row["reject_reason"] = REJECT_ENTRY_QUALITY_GUARD_SPREAD
            ctx.writer.append_error(
                {
                    "event_kind": LOG_EVENT_KIND_SPREAD,
                    "symbol": sym,
                    "time": trade.get("entry_time"),
                    "spread_bps": rej_row["spread_bps"],
                    "update_count_before_entry": rej_row["update_count_before_entry"],
                    "reject_reason": REJECT_ENTRY_QUALITY_GUARD_SPREAD,
                }
            )
        if decision.reason == "entry_quality_guard_update_count":
            from small_paper.entry_quality_guard import (
                LOG_EVENT_KIND_UPDATE,
                REJECT_ENTRY_QUALITY_GUARD_UPDATE_COUNT,
            )

            ctx.state.entry_quality_guard_reject_count += 1
            ctx.state.entry_quality_guard_update_reject_count += 1
            ctx.state.entry_quality_guard_reject_symbols.add(sym)
            rej_row["time"] = trade.get("entry_time")
            rej_row["spread_bps"] = getattr(
                decision, "entry_quality_spread_bps", trade.get("spread_bps")
            )
            rej_row["update_count_before_entry"] = getattr(
                decision, "entry_quality_update_count", trade.get("update_count_before_entry")
            )
            rej_row["reject_reason"] = REJECT_ENTRY_QUALITY_GUARD_UPDATE_COUNT
            ctx.writer.append_error(
                {
                    "event_kind": LOG_EVENT_KIND_UPDATE,
                    "symbol": sym,
                    "time": trade.get("entry_time"),
                    "spread_bps": rej_row["spread_bps"],
                    "update_count_before_entry": rej_row["update_count_before_entry"],
                    "reject_reason": REJECT_ENTRY_QUALITY_GUARD_UPDATE_COUNT,
                }
            )
        if decision.reason == "entry_cluster_guard":
            from small_paper.entry_cluster_guard import (
                LOG_EVENT_KIND as CG_LOG_EVENT_KIND,
                REJECT_ENTRY_CLUSTER_GUARD,
            )

            ctx.state.cluster_guard_reject_count += 1
            ctx.state.cluster_guard_reject_symbols.add(sym)
            rej_row["cluster_id"] = getattr(decision, "cluster_id", trade.get("cluster_id"))
            rej_row["new_subcluster_id"] = getattr(
                decision, "new_subcluster_id", trade.get("new_subcluster_id")
            )
            rej_row["liquidity_burst"] = getattr(
                decision, "liquidity_burst", trade.get("liquidity_burst")
            )
            rej_row["cluster_guard_status"] = getattr(
                decision, "cluster_guard_status", "REJECTED"
            )
            rej_row["reject_reason"] = REJECT_ENTRY_CLUSTER_GUARD
            ctx.writer.append_error(
                {
                    "event_kind": CG_LOG_EVENT_KIND,
                    "symbol": sym,
                    "time": trade.get("entry_time"),
                    "cluster_id": rej_row["cluster_id"],
                    "new_subcluster_id": rej_row["new_subcluster_id"],
                    "liquidity_burst": rej_row["liquidity_burst"],
                    "cluster_guard_status": rej_row["cluster_guard_status"],
                    "reject_reason": REJECT_ENTRY_CLUSTER_GUARD,
                }
            )
        if decision.reason == "stop_low_mfe_guard":
            from small_paper.stop_low_mfe_guard import (
                LOG_EVENT_KIND as SLM_LOG_EVENT_KIND,
                REJECT_STOP_LOW_MFE_GUARD,
            )

            ctx.state.stop_low_mfe_guard_reject_count += 1
            ctx.state.stop_low_mfe_guard_reject_symbols.add(sym)
            rej_row["volume_acceleration_5m"] = getattr(
                decision, "volume_acceleration_5m", trade.get("volume_acceleration_5m")
            )
            rej_row["stop_low_mfe_guard_volume_accel_threshold"] = getattr(
                decision,
                "stop_low_mfe_guard_volume_accel_threshold",
                trade.get("stop_low_mfe_guard_volume_accel_threshold"),
            )
            rej_row["reject_reason"] = REJECT_STOP_LOW_MFE_GUARD
            ctx.writer.append_error(
                {
                    "event_kind": SLM_LOG_EVENT_KIND,
                    "symbol": sym,
                    "time": trade.get("entry_time"),
                    "volume_acceleration_5m": rej_row["volume_acceleration_5m"],
                    "stop_low_mfe_guard_volume_accel_threshold": rej_row[
                        "stop_low_mfe_guard_volume_accel_threshold"
                    ],
                    "reject_reason": REJECT_STOP_LOW_MFE_GUARD,
                }
            )
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
            if decision.reason == REJECT_MAX_CONCURRENT:
                try:
                    v2 = int(trade.get("entry_expectancy_score_v2") or 0)
                    if v2 >= 5:
                        ctx.state.discord_ux.record_score5_deferred_reject(
                            symbol=sym,
                            entry_score_v2=v2,
                        )
                except (TypeError, ValueError):
                    pass
                from small_paper.extended_entry_shadow import (
                    compute_entry_shadow_fields,
                    tick_ts_from_payload,
                )

                shadow = compute_entry_shadow_fields(
                    trade=trade,
                    payload=payload,
                    price_ring=ctx.symbol_price_ring.get(sym, []),
                    entry_ts=tick_ts_from_payload(payload),
                    session_momentum_samples=ctx.state.session_momentum_samples,
                )
                ctx.discord.notify_entry_cap_blocked(
                    event=rej,
                    payload=enriched,
                    trade_data={**trade, **shadow},
                    open_slots=_active_cap_count(ctx),
                    score5_candidate_ordinal=score5_ord,
                    ux_stats=ctx.state.discord_ux,
                )
            else:
                ctx.discord.notify_rejected(
                    event=rej,
                    payload=enriched,
                    open_slots=_active_cap_count(ctx),
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


def _parse_push_replay_line(
    line: str,
    *,
    file_sym: str,
) -> Optional[tuple[str, str, dict[str, Any]]]:
    line = line.strip()
    if not line:
        return None
    try:
        rec = json.loads(line)
    except json.JSONDecodeError:
        return None
    src = str(rec.get("source") or "")
    if src and src not in ("live_push", "push", "dry_run"):
        return None
    payload = rec.get("payload")
    if not isinstance(payload, dict):
        return None
    sym = str(rec.get("symbol") or file_sym).strip().upper()
    if not sym.endswith(".T"):
        sym = f"{sym}.T"
    recorded_at = str(rec.get("recorded_at") or "")
    return recorded_at, sym, payload


def _iter_push_replay_records(
    push_dir: Path,
    *,
    max_rows: Optional[int] = None,
):
    """Stream push_jsonl rows in recorded_at order without loading full day into RAM."""
    import heapq

    def _file_iter(fp: Path):
        file_sym = fp.stem
        with fp.open(encoding="utf-8") as f:
            for line in f:
                row = _parse_push_replay_line(line, file_sym=file_sym)
                if row is not None:
                    yield row

    files = sorted(push_dir.glob("*.jsonl"))
    if not files:
        return
    merged = heapq.merge(*[_file_iter(fp) for fp in files], key=lambda r: r[0])
    count = 0
    for row in merged:
        yield row
        count += 1
        if max_rows is not None and count >= max_rows:
            break


def _load_push_replay_records(
    push_dir: Path,
    *,
    max_rows: Optional[int] = None,
) -> list[tuple[str, str, dict[str, Any]]]:
    return list(_iter_push_replay_records(push_dir, max_rows=max_rows))


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


def _observer_exit_pnl_summary_fields(events: Sequence[dict[str, Any]]) -> dict[str, Any]:
    from small_paper.discord_message_builder import summarize_observer_exit_metrics

    return summarize_observer_exit_metrics(events)


def _attach_canonical_summary_fields(
    summary: dict[str, Any],
    events: Sequence[dict[str, Any]],
    *,
    config: SmallPaperPilotConfig,
    watch_symbols_count: Optional[int] = None,
) -> dict[str, Any]:
    from small_paper.canonical_summary import enrich_summary_with_canonical

    return enrich_summary_with_canonical(
        summary,
        events,
        max_concurrent_positions=config.max_concurrent_positions,
        watch_symbols_count=watch_symbols_count,
    )


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
    base.update(_post_entry_forward_shadow_summary_fields(state))
    base.update(_classic_momentum_forward_shadow_summary_fields(state))
    base.update(_vwap_shadow_summary_fields(state))
    base.update(_board_imbalance_shadow_summary_fields(state))
    base.update(_board_dynamic_trailing_shadow_summary_fields(state))
    base.update(_limit_up_proximity_entry_guard_shadow_summary_fields(state))
    base.update(_pullback_misread_dynamic40_guard_summary_fields(gate, state))
    base.update(_near_day_high_low_momentum_dynamic40_guard_summary_fields(gate, state))
    base.update(_high_drift_pullback_guard_summary_fields(gate, state))
    base.update(_weak_shape_reject_guard_summary_fields(gate, state))
    base.update(_late_chase_guard_summary_fields(gate, state))
    base.update(_classic_late_chase_rsi_guard_summary_fields(gate, state))
    base.update(_reentry_rsi_guard_summary_fields(gate, state))
    base.update(_entry_quality_guard_summary_fields(gate, state))
    base.update(_entry_cluster_guard_summary_fields(gate, state))
    base.update(_gate_dominance_alert_fields(state))
    base.update(_stop_low_mfe_guard_summary_fields(gate, state))
    base.update(_board_entry_summary_fields(state))
    base.update(_pullback_misread_entry_guard_shadow_summary_fields(state))
    base.update(_entry_expectancy_score_summary_fields(state))
    base.update(_freshness_semantics_v2_summary_fields(config, state))
    base.update(_or_overlay_summary_fields(config, state))
    base.update(_observer_exit_pnl_summary_fields(state.events))
    base.update(
        _position_cap_summary_for_session(
            config=config,
            state=state,
            gate=gate,
            events=state.events,
        )
    )
    _attach_canonical_summary_fields(base, state.events, config=config)
    _apply_exit_shadow_monitor_finalize(state, base, config=config)
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
    from small_paper.vol_liq_startup_cache import vol_liq_cache_summary_fields

    run_key = str(getattr(suit, "run_session_key", "") or "")
    out.update(vol_liq_cache_summary_fields(run_key or None))
    from small_paper.volume_gate_relaxation_shadow import volume_shadow_summary_fields

    out.update(volume_shadow_summary_fields(getattr(state, "volume_gate_shadow", None)))
    return out


def _legacy_live_order_hooks_enabled(config: SmallPaperPilotConfig) -> bool:
    from small_paper.live_order_adapter import live_order_adapter_enabled

    return not live_order_adapter_enabled(config)


def _maybe_record_live_order_pipeline_entry(
    ctx: _PushPipelineContext,
    *,
    sym: str,
    trade: Mapping[str, Any],
    payload: Mapping[str, Any],
    acc: Mapping[str, Any],
    scan_meta: Optional[Mapping[str, Any]] = None,
) -> None:
    try:
        from small_paper.live_order_adapter import live_order_adapter_enabled, process_paper_entry

        adapter = getattr(ctx.state, "live_order_adapter", None)
        if adapter is None or not live_order_adapter_enabled(ctx.config):
            return
        day = str(trade.get("day") or "")[:8]
        day_pnl = None
        if day and hasattr(ctx.gate, "state") and hasattr(ctx.gate.state, "day_pnl"):
            day_pnl = float(ctx.gate.state.day_pnl.get(day, 0.0))
        process_paper_entry(
            adapter,
            symbol=sym,
            trade=trade,
            payload=payload,
            timestamp=str(acc.get("entry_time") or _now_iso()),
            writer=ctx.writer,
            config=ctx.config,
            capital_session=getattr(ctx.state, "live_capital_manager", None),
            capital_client=getattr(ctx.state, "live_capital_read_client", None),
            capital_token=str(getattr(ctx.state, "live_capital_api_token", "") or ""),
            day_pnl_pct=day_pnl,
        )
    except Exception as exc:
        try:
            ctx.writer.append_live_order_error(
                {
                    "timestamp": _now_iso(),
                    "component": "live_order_adapter",
                    "symbol": sym,
                    "error": str(exc),
                }
            )
        except Exception:
            pass


def _maybe_record_live_capital_check_entry(
    ctx: _PushPipelineContext,
    *,
    sym: str,
    trade: Mapping[str, Any],
    payload: Mapping[str, Any],
    acc: Mapping[str, Any],
) -> None:
    try:
        from small_paper.live_capital_manager import capital_manager_enabled, check_entry_capital_on_paper_accept
        from small_paper.live_order_dry_run_adapter import _paper_trade_id

        session = getattr(ctx.state, "live_capital_manager", None)
        if session is None or not capital_manager_enabled(ctx.config):
            return
        client = getattr(ctx.state, "live_capital_read_client", None)
        token = getattr(ctx.state, "live_capital_api_token", None)
        if client is None or not token:
            return
        day = str(trade.get("day") or "")[:8]
        day_pnl = None
        if day and hasattr(ctx.gate, "state") and hasattr(ctx.gate.state, "day_pnl"):
            day_pnl = float(ctx.gate.state.day_pnl.get(day, 0.0))
        check_entry_capital_on_paper_accept(
            session,
            symbol=sym,
            trade=trade,
            payload=payload,
            writer=ctx.writer,
            config=ctx.config,
            client=client,
            token=str(token),
            repo_root=None,
            day_pnl_pct=day_pnl,
            linked_paper_trade_id=_paper_trade_id(trade, sym),
        )
    except Exception as exc:
        try:
            ctx.writer.append_error(
                {
                    "event_time": _now_iso(),
                    "component": "live_capital_manager",
                    "symbol": sym,
                    "error": str(exc),
                }
            )
        except Exception:
            pass


def _maybe_record_live_order_entry(
    ctx: _PushPipelineContext,
    *,
    sym: str,
    trade: Mapping[str, Any],
    payload: Mapping[str, Any],
    acc: Mapping[str, Any],
) -> None:
    try:
        from small_paper.live_order_dry_run_adapter import dry_run_adapter_enabled, on_paper_entry_accepted

        session = getattr(ctx.state, "live_order_dry_run", None)
        if session is None or not dry_run_adapter_enabled(ctx.config):
            return
        on_paper_entry_accepted(
            session,
            symbol=sym,
            trade=trade,
            payload=payload,
            timestamp=str(acc.get("entry_time") or _now_iso()),
            writer=ctx.writer,
            config=ctx.config,
        )
    except Exception as exc:
        try:
            ctx.writer.append_error(
                {
                    "event_time": _now_iso(),
                    "component": "live_order_dry_run_adapter",
                    "symbol": sym,
                    "error": str(exc),
                }
            )
        except Exception:
            pass


def _maybe_record_live_order_wiring_entry(
    ctx: _PushPipelineContext,
    *,
    sym: str,
    trade: Mapping[str, Any],
    payload: Mapping[str, Any],
    acc: Mapping[str, Any],
    scan_meta: Optional[Mapping[str, Any]] = None,
) -> None:
    try:
        from small_paper.live_order_api_wiring import process_entry_wiring, wiring_enabled

        wiring = getattr(ctx.state, "live_order_wiring", None)
        if wiring is None or not wiring_enabled(ctx.config):
            return
        signal_ts = str((scan_meta or {}).get("entry_signal_ts") or acc.get("entry_time") or "")
        process_entry_wiring(
            wiring,
            symbol=sym,
            trade=trade,
            payload=payload,
            writer=ctx.writer,
            config=ctx.config,
            entry_signal_ts=signal_ts or None,
        )
    except Exception as exc:
        try:
            ctx.writer.append_error(
                {
                    "event_time": _now_iso(),
                    "component": "live_order_api_wiring",
                    "symbol": sym,
                    "error": str(exc),
                }
            )
        except Exception:
            pass


def _maybe_record_live_order_exit(
    *,
    config: SmallPaperPilotConfig,
    state: _LiveRunState,
    writer: Optional[LiveSessionWriter],
    symbol: str,
    context: Mapping[str, Any],
) -> None:
    try:
        from small_paper.live_order_adapter import live_order_adapter_enabled, process_paper_exit

        adapter = getattr(state, "live_order_adapter", None)
        if adapter is not None and live_order_adapter_enabled(config) and context.get("is_structural_exit"):
            process_paper_exit(
                adapter,
                symbol=symbol,
                context=context,
                timestamp=str(context.get("exit_time") or _now_iso()),
                writer=writer,
                config=config,
            )
            if not _legacy_live_order_hooks_enabled(config):
                return
    except Exception as exc:
        if writer is not None:
            try:
                writer.append_live_order_error(
                    {
                        "timestamp": _now_iso(),
                        "component": "live_order_adapter_exit",
                        "symbol": symbol,
                        "error": str(exc),
                    }
                )
            except Exception:
                pass
    try:
        from small_paper.live_order_dry_run_adapter import dry_run_adapter_enabled, on_paper_exit_signal

        session = getattr(state, "live_order_dry_run", None)
        if session is None or writer is None or not dry_run_adapter_enabled(config):
            pass
        elif context.get("is_structural_exit"):
            on_paper_exit_signal(
                session,
                symbol=symbol,
                context=context,
                timestamp=str(context.get("exit_time") or _now_iso()),
                writer=writer,
                config=config,
            )
        _maybe_record_live_order_wiring_exit(
            config=config,
            state=state,
            writer=writer,
            symbol=symbol,
            context=context,
        )
    except Exception as exc:
        if writer is not None:
            try:
                writer.append_error(
                    {
                        "event_time": _now_iso(),
                        "component": "live_order_exit_hooks",
                        "symbol": symbol,
                        "error": str(exc),
                    }
                )
            except Exception:
                pass


def _maybe_record_live_order_wiring_exit(
    *,
    config: SmallPaperPilotConfig,
    state: _LiveRunState,
    writer: Optional[LiveSessionWriter],
    symbol: str,
    context: Mapping[str, Any],
) -> None:
    try:
        from small_paper.live_order_api_wiring import process_exit_wiring, wiring_enabled

        wiring = getattr(state, "live_order_wiring", None)
        if wiring is None or writer is None or not wiring_enabled(config):
            return
        if not context.get("is_structural_exit"):
            return
        process_exit_wiring(
            wiring,
            symbol=symbol,
            context=context,
            writer=writer,
            config=config,
        )
    except Exception as exc:
        if writer is not None:
            try:
                writer.append_error(
                    {
                        "event_time": _now_iso(),
                        "component": "live_order_api_wiring_exit",
                        "symbol": symbol,
                        "error": str(exc),
                    }
                )
            except Exception:
                pass


def _maybe_record_volume_gate_shadow(
    ctx: _PushPipelineContext,
    *,
    sym: str,
    trade: Mapping[str, Any],
    decision: Any,
    timestamp: str,
) -> None:
    from small_paper.volume_gate_relaxation_shadow import (
        record_volume_gate_shadow_eval,
        shadow_enabled,
    )

    if not shadow_enabled(ctx.config):
        return
    suit = getattr(ctx.gate, "daytrade_suitability", None)
    if suit is None:
        return
    chk = suit.check(trade)
    threshold = chk.threshold
    row = record_volume_gate_shadow_eval(
        ctx.state.volume_gate_shadow,
        trade=trade,
        threshold=threshold,
        symbol=sym,
        timestamp=timestamp,
        reject_reason="" if decision.accept else str(decision.reason or ""),
    )
    if row is not None:
        ctx.writer.append_volume_shadow_eval(row)


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
    write_board_shadow_reports: bool = True,
    enable_exit_candidate_shadow: bool = False,
    exit_candidate_ids: Optional[Sequence[str]] = None,
    enable_vwap_tuning_shadow: bool = False,
    vwap_tuning_variants: Optional[Sequence[Any]] = None,
    enable_board_failure_shadow: bool = False,
    enable_board_failure_tuning_shadow: bool = False,
    board_failure_tuning_variants: Optional[Sequence[Any]] = None,
    enable_board_failure_forensic_shadow: bool = False,
    enable_board_failure_guard_shadow: bool = False,
    board_failure_guard_variants: Optional[Sequence[Any]] = None,
    enable_phase356_exit_rebaseline_shadow: bool = False,
    streaming_push_replay: bool = False,
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

    if streaming_push_replay:
        record_iter = _iter_push_replay_records(push_dir, max_rows=max_push_rows)
        push_rows = 0
    else:
        records = _load_push_replay_records(push_dir, max_rows=max_push_rows)
        push_rows = len(records)
        record_iter = iter(records)

    code_to_symbol: dict[str, str] = {}
    for fp in sorted(push_dir.glob("*.jsonl")):
        sym = fp.stem.strip().upper()
        if not sym.endswith(".T"):
            sym = f"{sym}.T"
        code_to_symbol[sym.replace(".T", "")] = sym

    root = repo_root or Path(__file__).resolve().parents[3]
    from small_paper.symbol_cooloff import session_key_from_output_dir

    run_key = session_key_from_output_dir(output_dir, root)
    gate = replay_config.make_exposure_gate(repo_root=root, run_session_key=run_key)
    gate_cfg = replay_config.exposure_gate_config()
    feature_bridge = LiveFeatureBridge(replay_config.feature_bridge_config())
    output_dir.mkdir(parents=True, exist_ok=True)
    writer = LiveSessionWriter(output_dir, incremental=True, event_fields=EVENT_FIELDS)
    state = _LiveRunState(started_mono=time.monotonic())
    _init_position_cap_tracking(replay_config, state)
    _init_extension_stack_for_mode(replay_config, state, repo_root=root)
    _init_or_overlay_tracking(replay_config, state)
    if enable_board_failure_forensic_shadow:
        from small_paper.board_failure_forensic_pack import BoardFailureForensicPack

        state.realtime_board_exit_shadow = None
        state.exit_candidate_shadow = BoardFailureForensicPack()
    elif enable_board_failure_guard_shadow:
        from small_paper.board_failure_false_positive_guard import (
            BoardFailureGuardTuningPack,
            default_phase346_variants,
        )

        guard_variants = (
            tuple(board_failure_guard_variants)
            if board_failure_guard_variants
            else default_phase346_variants()
        )
        state.realtime_board_exit_shadow = None
        state.exit_candidate_shadow = BoardFailureGuardTuningPack(variants=guard_variants)
    elif enable_board_failure_tuning_shadow:
        from small_paper.board_failure_exit_tuning import (
            BoardFailureTuningPack,
            default_phase343_variants,
        )

        variants = (
            tuple(board_failure_tuning_variants)
            if board_failure_tuning_variants
            else default_phase343_variants()
        )
        state.realtime_board_exit_shadow = None
        state.exit_candidate_shadow = BoardFailureTuningPack(variants=variants)
    elif enable_board_failure_shadow:
        from small_paper.board_failure_exit_shadow import BoardFailureExitShadowPack

        state.realtime_board_exit_shadow = None
        state.exit_candidate_shadow = BoardFailureExitShadowPack()
    elif enable_vwap_tuning_shadow:
        from small_paper.vwap_assisted_loss_tuning import (
            VwapAssistedLossTuningPack,
            default_phase339_variants,
        )

        variants = (
            tuple(vwap_tuning_variants)
            if vwap_tuning_variants
            else default_phase339_variants()
        )
        state.realtime_board_exit_shadow = None
        state.exit_candidate_shadow = VwapAssistedLossTuningPack(variants=variants)
    elif enable_exit_candidate_shadow:
        from small_paper.exit_candidate_shadow import EXIT_CANDIDATE_IDS, ExitCandidateShadowPack

        active = tuple(exit_candidate_ids) if exit_candidate_ids else EXIT_CANDIDATE_IDS
        state.realtime_board_exit_shadow = None
        state.exit_candidate_shadow = ExitCandidateShadowPack(
            active_candidates=active,
            enable_extend=exit_candidate_ids is None,
        )
    elif enable_phase356_exit_rebaseline_shadow:
        from small_paper.phase356_exit_rebaseline_pack import Phase356ExitRebaselinePack

        state.realtime_board_exit_shadow = None
        state.exit_candidate_shadow = Phase356ExitRebaselinePack()
    pos_fields = ["symbol", "entry_time", "exit_time", "open_slots_after"]

    observer: Optional[ObserverPositionTracker] = None
    discord: Optional[SmallPaperDiscordNotifier] = None
    if replay_config.discord_observer_only:
        observer = _make_observer_tracker(replay_config, state)
        state.observer_tracker = observer
        if replay_config.discord_enabled:

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

    gap_threshold_sec = max(replay_config.live_stale_tick_sec * 2, max(poll_interval_sec, 0.001) * 3)
    day_compact = push_dir.name.replace("-", "")
    universe_meta = _load_symbol_universe_meta_for_day(
        repo_root=root,
        day_compact=day_compact,
        session_kind="am",
    )
    from small_paper.core_runtime_mode import get_core_runtime_mode, log_core_runtime_mode
    from small_paper.extension_bus import ExtensionBus
    from small_paper.pipeline_stage_profiler import PipelineStageProfiler, pipeline_stage_profile_enabled

    runtime_mode = get_core_runtime_mode(replay_config)
    log_core_runtime_mode(replay_config)
    stage_profiler = (
        PipelineStageProfiler(max_samples=3000, max_per_bucket=1000)
        if pipeline_stage_profile_enabled()
        else None
    )
    extension_bus = ExtensionBus.maybe_create(
        mode=runtime_mode,
        config=replay_config,
        state=state,
        writer=writer,
        output_dir=output_dir,
        stage_profiler=stage_profiler,
    )
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
        entry_scan=_make_entry_scan_controller(replay_config, source="push-replay", writer=writer),
        symbol_universe_meta=universe_meta,
        extension_bus=extension_bus,
        stage_profiler=stage_profiler,
    )

    last_eval_ts: dict[str, float] = {}
    msg_i = 0
    for recorded_at, sym, payload in record_iter:
        msg_i += 1
        if streaming_push_replay:
            push_rows = msg_i
        if poll_interval_sec > 0:
            ts = _parse_recorded_at_ts(recorded_at)
            prev = last_eval_ts.get(sym)
            if prev is not None and (ts - prev) < poll_interval_sec:
                continue
            last_eval_ts[sym] = ts
        push_payload = dict(payload)
        if recorded_at:
            push_payload["recorded_at"] = recorded_at
        _process_push_payload(ctx, push_payload, msg_i, symbol=sym)
        if replay_speed_sec > 0:
            time.sleep(replay_speed_sec)

    if ctx.entry_scan is not None:
        final_flush = ctx.entry_scan.flush_pending()
        if final_flush is not None:
            _process_scan_flush(ctx, final_flush)

    if observer:
        exit_events = observer.close_all(reason="push_replay_end")
        _log_and_dispatch_observer_events(
            exit_events,
            discord=discord,
            writer=writer,
            state=state,
            gate=gate,
            source="push-replay",
            message_index=msg_i,
            profile=replay_config.profile,
            config=replay_config,
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
        _attach_canonical_summary_fields(summary, state.events, config=config)
        try:
            notify_discord_session_end(
                discord,
                events=state.events,
                summary=summary,
                reject_rows=state.reject_rows,
                ux_stats=state.discord_ux,
            )
        except Exception as exc:
            log.warning("discord session_end notify failed: %s", exc)
            summary["discord_session_end_error"] = str(exc)

    _apply_quality_formula_shadow_finalize(state, summary)
    _apply_trading_value_shadow_finalize(state, summary)
    _apply_board_imbalance_shadow_finalize(state, summary)
    _apply_entry_expectancy_score_shadow_finalize(state, summary)
    _apply_post_entry_forward_shadow_finalize(state, summary, output_dir=output_dir)
    _apply_classic_momentum_forward_shadow_finalize(state, summary, output_dir=output_dir)
    writer.finalize_batch(
        events=state.events,
        positions=positions,
        summary=summary,
        pos_fields=pos_fields,
    )
    _write_quality_top_debug(output_dir, state.events)
    _write_phase396_artifacts_safe(
        root,
        config=replay_config,
        state=state,
        summary=summary,
    )
    board_shadow = getattr(state, "realtime_board_exit_shadow", None)
    if write_board_shadow_reports:
        _write_phase335_lite_board_shadow_reports(state, repo_root=root)
    exit_pack = getattr(state, "exit_candidate_shadow", None)
    return PilotRunResult(
        output_dir=output_dir,
        summary=summary,
        events=state.events,
        accepted=state.accepted_rows,
        rejects=state.reject_rows,
        realtime_board_shadow=board_shadow,
        exit_candidate_shadow=exit_pack,
        stage_profiler=stage_profiler,
    )


def _write_phase335_lite_board_shadow_reports(
    state: _LiveRunState,
    *,
    repo_root: Path,
    day_stamp: Optional[str] = None,
) -> None:
    from small_paper.realtime_board_exit_shadow import write_phase335_lite_outputs

    logger = getattr(state, "realtime_board_exit_shadow", None)
    write_phase335_lite_outputs(logger, repo_root=repo_root, day_stamp=day_stamp)


def _run_equity_dynamic_stop_shadow_auto(
    *,
    repo_root: Path,
    output_dir: Path,
    summary: dict[str, Any],
    config: SmallPaperPilotConfig,
    poll_interval_sec: Optional[float],
) -> None:
    """Phase266: research-only equity dynamic stop shadow after canonical summary."""
    from small_paper.equity_dynamic_stop_shadow_auto import run_equity_dynamic_stop_shadow_auto

    try:
        summary["equity_dynamic_stop_shadow"] = run_equity_dynamic_stop_shadow_auto(
            repo_root=repo_root,
            output_dir=output_dir,
            config=config,
            poll_interval_sec=poll_interval_sec,
        )
    except Exception as exc:
        import logging

        logging.getLogger(__name__).warning(
            "[equity_dynamic_stop_shadow] unexpected error: %s", exc
        )
        summary["equity_dynamic_stop_shadow"] = {
            "day": output_dir.parent.name if output_dir.parent else "",
            "status": "warning",
            "warning": str(exc),
        }


def _run_risk_sizing_forward_shadow_auto(
    *,
    repo_root: Path,
    output_dir: Path,
    summary: dict[str, Any],
    config: SmallPaperPilotConfig,
    poll_interval_sec: Optional[float],
) -> None:
    """Phase262: research-only risk-aware sizing forward shadow after canonical summary."""
    from small_paper.risk_sizing_forward_shadow_auto import run_risk_sizing_forward_shadow_auto

    try:
        summary["risk_sizing_forward_shadow"] = run_risk_sizing_forward_shadow_auto(
            repo_root=repo_root,
            output_dir=output_dir,
            config=config,
            poll_interval_sec=poll_interval_sec,
        )
    except Exception as exc:
        import logging

        logging.getLogger(__name__).warning(
            "[risk_sizing_forward_shadow] unexpected error: %s", exc
        )
        summary["risk_sizing_forward_shadow"] = {
            "day": output_dir.parent.name if output_dir.parent else "",
            "status": "warning",
            "warning": str(exc),
        }


def _run_live_config_transition_shadow_auto(
    *,
    repo_root: Path,
    output_dir: Path,
    summary: dict[str, Any],
    config: SmallPaperPilotConfig,
    poll_interval_sec: Optional[float],
) -> None:
    """Phase274: research-only auto-transition shadow after Phase273."""
    from small_paper.live_config_transition_shadow_auto import run_live_config_transition_shadow_auto

    try:
        summary["live_config_transition_shadow"] = run_live_config_transition_shadow_auto(
            repo_root=repo_root,
            output_dir=output_dir,
            config=config,
            poll_interval_sec=poll_interval_sec,
        )
    except Exception as exc:
        import logging

        logging.getLogger(__name__).warning(
            "[live_config_transition_shadow] unexpected error: %s", exc
        )
        summary["live_config_transition_shadow"] = {
            "day": output_dir.parent.name if output_dir.parent else "",
            "status": "warning",
            "warning": str(exc),
        }


def _run_live_config_forward_shadow_auto(
    *,
    repo_root: Path,
    output_dir: Path,
    summary: dict[str, Any],
    config: SmallPaperPilotConfig,
    poll_interval_sec: Optional[float],
) -> None:
    """Phase273: research-only live config forward shadow after equity dynamic stop."""
    from small_paper.live_config_forward_shadow_auto import run_live_config_forward_shadow_auto

    try:
        summary["live_config_forward_shadow"] = run_live_config_forward_shadow_auto(
            repo_root=repo_root,
            output_dir=output_dir,
            config=config,
            poll_interval_sec=poll_interval_sec,
        )
    except Exception as exc:
        import logging

        logging.getLogger(__name__).warning(
            "[live_config_forward_shadow] unexpected error: %s", exc
        )
        summary["live_config_forward_shadow"] = {
            "day": output_dir.parent.name if output_dir.parent else "",
            "status": "warning",
            "warning": str(exc),
        }


def _organize_daily_artifacts_safe(repo_root: Path, day: str) -> None:
    """Phase393: copy same-day report artifacts; never fails paper session."""
    try:
        from storage.daily_artifact_organizer import organize_daily_artifacts

        organize_daily_artifacts(repo_root, day)
    except Exception as exc:
        import logging

        logging.getLogger(__name__).warning(
            "[daily_artifact_organizer] unexpected error day=%s: %s", day, exc
        )


def _run_sector_heat_forward_shadow_auto(
    *,
    repo_root: Path,
    output_dir: Path,
    summary: dict[str, Any],
    config: SmallPaperPilotConfig,
    poll_interval_sec: Optional[float],
) -> None:
    """Phase256: research-only forward shadow logging after canonical summary."""
    from small_paper.sector_heat_forward_shadow_auto import run_sector_heat_forward_shadow_auto

    try:
        summary["sector_heat_forward_shadow"] = run_sector_heat_forward_shadow_auto(
            repo_root=repo_root,
            output_dir=output_dir,
            config=config,
            poll_interval_sec=poll_interval_sec,
        )
    except Exception as exc:
        import logging

        logging.getLogger(__name__).warning(
            "[sector_heat_forward_shadow] unexpected error: %s", exc
        )
        summary["sector_heat_forward_shadow"] = {
            "day": output_dir.parent.name if output_dir.parent else "",
            "status": "warning",
            "warning": str(exc),
        }


def _run_boundary_forward_shadow_auto(
    *,
    repo_root: Path,
    output_dir: Path,
    summary: dict[str, Any],
    config: SmallPaperPilotConfig,
    poll_interval_sec: Optional[float],
) -> None:
    """Phase409: research-only Phase405 corrected boundary forward shadow."""
    from small_paper.boundary_forward_shadow_auto import run_boundary_forward_shadow_auto

    try:
        summary["boundary_forward_shadow"] = run_boundary_forward_shadow_auto(
            repo_root=repo_root,
            output_dir=output_dir,
            config=config,
            poll_interval_sec=poll_interval_sec,
        )
    except Exception as exc:
        import logging

        logging.getLogger(__name__).warning(
            "[boundary_forward_shadow] unexpected error: %s", exc
        )
        summary["boundary_forward_shadow"] = {
            "day": output_dir.parent.name if output_dir.parent else "",
            "status": "warning",
            "warning": str(exc),
        }


def _run_classic_momentum_forward_shadow_auto(
    *,
    repo_root: Path,
    output_dir: Path,
    summary: dict[str, Any],
) -> None:
    from small_paper.classic_momentum_forward_shadow_auto import run_classic_momentum_forward_shadow_auto

    try:
        summary["classic_momentum_forward_shadow"] = run_classic_momentum_forward_shadow_auto(
            repo_root=repo_root,
            output_dir=output_dir,
        )
    except Exception as exc:
        import logging

        logging.getLogger(__name__).warning(
            "[classic_momentum_forward_shadow] unexpected error: %s", exc
        )
        summary["classic_momentum_forward_shadow"] = {
            "day": output_dir.parent.name if output_dir.parent else "",
            "status": "warning",
            "warning": str(exc),
        }


def _run_post_entry_forward_shadow_auto(
    *,
    repo_root: Path,
    output_dir: Path,
    summary: dict[str, Any],
) -> None:
    """Phase500: research-only post-entry forward shadow review."""
    from small_paper.post_entry_forward_shadow_auto import run_post_entry_forward_shadow_auto

    try:
        summary["post_entry_forward_shadow"] = run_post_entry_forward_shadow_auto(
            repo_root=repo_root,
            output_dir=output_dir,
        )
    except Exception as exc:
        import logging

        logging.getLogger(__name__).warning(
            "[post_entry_forward_shadow] unexpected error: %s", exc
        )
        summary["post_entry_forward_shadow"] = {
            "day": output_dir.parent.name if output_dir.parent else "",
            "status": "warning",
            "warning": str(exc),
        }


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
    same_symbol_open_policy = _same_symbol_open_policy(config)
    same_symbol_overlap_reject_count = sum(
        1
        for r in state.reject_rows
        if str(r.get("gate_reject_reason") or r.get("reject_reason") or "") == REJECT_SAME_SYMBOL_OPEN_OVERLAP
    )
    overlap_replaced_review_count = sum(
        1 for t in state.accepted_rows if str(t.get("exit_reason") or "") == "overlap_replaced_review"
    )
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
        "same_symbol_open_policy": same_symbol_open_policy,
        "rejected_by_same_symbol_open_overlap": same_symbol_overlap_reject_count > 0,
        "same_symbol_overlap_reject_count": same_symbol_overlap_reject_count,
        "overlap_replaced_review_count": overlap_replaced_review_count,
        "max_concurrent_positions": config.max_concurrent_positions,
        "peak_open_slots": state.peak_open_slots,
        "quality_distribution": _quality_distribution(state.quality_scores),
        "session_bucket_summary": state.bucket_summary,
        "data_gap_count": state.data_gap_count,
        "stale_tick_count": state.stale_tick_count,
        "open_slots_end": len(gate.state.open_slots),
        "config_sha256": session_cfg.get("config_sha256"),
        "stop_reason": state.stop_reason or "completed",
        "note": (
            "Position-CAP mode: observer open count until structural EXIT; no orders placed."
            if config.position_cap_mode
            else "Virtual hold on PUSH for concurrent cap; no orders placed."
        ),
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
    summary.update(_post_entry_forward_shadow_summary_fields(state))
    summary.update(_classic_momentum_forward_shadow_summary_fields(state))
    summary.update(_vwap_shadow_summary_fields(state))
    summary.update(_board_imbalance_shadow_summary_fields(state))
    summary.update(_board_dynamic_trailing_shadow_summary_fields(state))
    summary.update(_limit_up_proximity_entry_guard_shadow_summary_fields(state))
    summary.update(_pullback_misread_dynamic40_guard_summary_fields(gate, state))
    summary.update(_near_day_high_low_momentum_dynamic40_guard_summary_fields(gate, state))
    summary.update(_high_drift_pullback_guard_summary_fields(gate, state))
    summary.update(_weak_shape_reject_guard_summary_fields(gate, state))
    summary.update(_late_chase_guard_summary_fields(gate, state))
    summary.update(_classic_late_chase_rsi_guard_summary_fields(gate, state))
    summary.update(_reentry_rsi_guard_summary_fields(gate, state))
    summary.update(_entry_quality_guard_summary_fields(gate, state))
    summary.update(_entry_cluster_guard_summary_fields(gate, state))
    summary.update(_gate_dominance_alert_fields(state))
    summary.update(_stop_low_mfe_guard_summary_fields(gate, state))
    summary.update(_board_entry_summary_fields(state))
    summary.update(_pullback_misread_entry_guard_shadow_summary_fields(state))
    summary.update(_entry_expectancy_score_summary_fields(state))
    summary.update(_freshness_semantics_v2_summary_fields(config, state))
    summary.update(_or_overlay_summary_fields(config, state))
    summary.update(_observer_exit_pnl_summary_fields(state.events))
    summary.update(
        _position_cap_summary_for_session(
            config=config,
            state=state,
            gate=gate,
            events=state.events,
        )
    )
    _attach_canonical_summary_fields(summary, state.events, config=config)
    _apply_exit_shadow_monitor_finalize(state, summary, config=config)
    from small_paper.live_order_dry_run_adapter import dry_run_summary_fields

    summary.update(dry_run_summary_fields(getattr(state, "live_order_dry_run", None)))
    from small_paper.live_order_api_wiring import wiring_summary_fields

    summary.update(wiring_summary_fields(getattr(state, "live_order_wiring", None)))
    from small_paper.live_capital_manager import capital_summary_fields

    summary.update(capital_summary_fields(getattr(state, "live_capital_manager", None)))
    from small_paper.live_order_adapter import adapter_summary_fields

    summary.update(adapter_summary_fields(getattr(state, "live_order_adapter", None)))
    return summary


def _freshness_semantics_v2_summary_fields(
    config: SmallPaperPilotConfig, state: _LiveRunState
) -> dict[str, Any]:
    if not getattr(config, "freshness_semantics_v2_enabled", False):
        return {"freshness_semantics_v2_enabled": False}
    return {
        "freshness_semantics_v2_enabled": True,
        "event_stale_threshold_sec": float(getattr(config, "event_stale_threshold_sec", 3.0) or 3.0),
        "board_stale_threshold_sec": float(getattr(config, "board_stale_threshold_sec", 3.0) or 3.0),
        "trade_stale_threshold_sec": float(getattr(config, "trade_stale_threshold_sec", 10.0) or 10.0),
        "trade_stale_mode": str(getattr(config, "trade_stale_mode", "tag_only") or "tag_only"),
        "event_stale_reject_count": int(state.event_stale_reject_count),
        "board_stale_reject_count": int(state.board_stale_reject_count),
        "trade_stale_tag_count": int(state.trade_stale_tag_count),
    }


def _or_overlay_summary_fields(config: SmallPaperPilotConfig, state: _LiveRunState) -> dict[str, Any]:
    or_st = getattr(state, "or_overlay", None)
    if or_st is None:
        return {"or_overlay_enabled": False}
    observer = getattr(state, "observer_tracker", None)
    return or_st.summary_fields(events=state.events, observer=observer)


def _position_cap_summary_for_session(
    *,
    config: SmallPaperPilotConfig,
    state: _LiveRunState,
    gate: ExposureGate,
    events: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    from small_paper.position_cap_mode import position_cap_summary_fields

    return position_cap_summary_fields(config, state, gate, events=events)


def _write_phase396_artifacts_safe(
    repo_root: Path,
    *,
    config: SmallPaperPilotConfig,
    state: _LiveRunState,
    summary: Mapping[str, Any],
) -> None:
    if not config.position_cap_mode:
        return
    try:
        from small_paper.position_cap_mode import write_phase396_artifacts

        reports = repo_root / "results" / "reports"
        write_phase396_artifacts(
            reports,
            stats=getattr(state, "position_cap_stats", None),
            summary=dict(summary),
        )
    except Exception as exc:
        import logging

        logging.getLogger(__name__).warning("phase396 artifact write failed: %s", exc)


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
    from small_paper.core_runtime_mode import get_core_runtime_mode, log_core_runtime_mode

    log_core_runtime_mode(config)
    rest = KabuNativeRestClient(default_base_url())
    token = rest.issue_token_from_env()
    from api.order_read_client import KabuOrderReadClient

    capital_read_client = KabuOrderReadClient(default_base_url())
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
    _init_position_cap_tracking(config, state)
    _init_extension_stack_for_mode(config, state, repo_root=repo_root)
    state.live_capital_read_client = capital_read_client
    state.live_capital_api_token = token
    _init_or_overlay_tracking(config, state)
    pos_fields = ["symbol", "entry_time", "exit_time", "open_slots_after"]
    gap_threshold_sec = max(stale_tick_sec * 2, poll_interval_sec * 3)
    pipeline_ctx: Optional[_PushPipelineContext] = None

    discord: Optional[SmallPaperDiscordNotifier] = None
    observer: Optional[ObserverPositionTracker] = None
    if config.discord_observer_only and not config.order_enabled:
        observer = _make_observer_tracker(config, state, am_pm_policy=am_pm_policy)
        state.observer_tracker = observer
        if config.discord_enabled:

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
        if discord is not None and discord.active and am_pm_policy is not None:
            sk = str(getattr(am_pm_policy, "kind", "am")).lower()
            screening_label = "PM Screening" if sk == "pm" else "AM Screening"
            watch_syms = sorted({str(sym) for sym, _, _ in symbols})
            day_stamp = datetime.now(JST).strftime("%Y%m%d")
            discord.notify_universe_screening(
                session_label=screening_label,
                watch_symbols=watch_syms,
                day_stamp=day_stamp,
            )

    entry_eligible: Optional[set[str]] = {t[0] for t in symbols} if enable_intraday_refresh else None
    day_compact = datetime.now(JST).strftime("%Y%m%d")
    session_kind = str(getattr(am_pm_policy, "kind", "am") or "am").lower()
    universe_meta = _load_symbol_universe_meta_for_day(
        repo_root=repo_root,
        day_compact=day_compact,
        session_kind=session_kind,
        universe_csv_path=universe_csv_path,
    )
    from small_paper.core_runtime_mode import get_core_runtime_mode
    from small_paper.extension_bus import ExtensionBus

    runtime_mode = get_core_runtime_mode(config)
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
        entry_scan=_make_entry_scan_controller(config, source="live", writer=writer),
        symbol_universe_meta=universe_meta,
        extension_bus=ExtensionBus.maybe_create(
            mode=runtime_mode,
            config=config,
            state=state,
            writer=writer,
            output_dir=output_dir,
        ),
    )
    if pipeline_ctx.extension_bus is not None and pipeline_ctx.extension_bus.latency_trace is not None:
        print("[PAPER TRADE] entry_latency_trace_enabled=true", flush=True)
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
        if discord and discord.active and event == "completed":
            kind = getattr(am_pm_policy, "kind", "am") if am_pm_policy else "am"
            session_label = "PM" if str(kind).lower() == "pm" else "AM"
            added = extra.get("added_symbols") or []
            removed = extra.get("removed_symbols") or []
            watch_syms = [str(s) for s in (extra.get("after_symbols") or [])]
            if not watch_syms and pipeline_ctx and pipeline_ctx.entry_eligible_symbols:
                watch_syms = sorted(pipeline_ctx.entry_eligible_symbols)
            discord.notify_universe_refresh(
                session_label=session_label,
                refresh_time=refresh_hhmm,
                added_symbols=added,
                removed_symbols=removed,
                watch_symbols=watch_syms,
                status="completed",
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
        open_syms = observer.open_symbols() if observer else []
        _emit_intraday_refresh_event(
            "started",
            extra={
                "before_symbol_count": len(before_syms) or len(sym_specs),
                "open_symbols_count": len(open_syms),
                "open_symbols_count_log": len(open_syms),
            },
        )
        refresh_path = Path(intraday_refresh_csv_path)
        if not refresh_path.is_file():
            _log_api_error("intraday_refresh", FileNotFoundError(str(refresh_path)))
            state.intraday_refresh_failed_count += 1
            _emit_intraday_refresh_event(
                "failed",
                extra={
                    "reason": "refresh_csv_missing",
                    "path": str(refresh_path),
                    "open_symbols_count": len(open_syms),
                    "refresh_csv_rows": 0,
                    "carried_open_symbols_count": 0,
                    "refresh_symbols_added_count": 0,
                    "final_register_count": 0,
                    "register_called": False,
                    "register_success": False,
                    "fallback_reason": "refresh_csv_missing",
                },
            )
            return
        import csv

        from universe.intraday_refresh import (
            merge_register_specs,
            merge_universe_with_open_symbols,
        )
        from universe.am_pm_universe import _norm

        base_rows = [dict(r) for r in csv.DictReader(refresh_path.open(encoding="utf-8"))]
        refresh_csv_rows = len(base_rows)
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
                    "open_symbols_count": int(merge_meta.get("open_symbols_count") or len(open_syms)),
                    "refresh_csv_rows": refresh_csv_rows,
                    "carried_open_symbols_count": int(merge_meta.get("carried_open_symbols_count") or 0),
                    "refresh_symbols_added_count": int(merge_meta.get("refresh_symbols_added_count") or 0),
                    "final_register_count": int(merge_meta.get("final_register_count") or 0),
                    "register_called": False,
                    "register_success": False,
                    "fallback_reason": "open_symbols_exceed_cap",
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
                    "open_symbols_count": int(merge_meta.get("open_symbols_count") or len(open_syms)),
                    "refresh_csv_rows": refresh_csv_rows,
                    "carried_open_symbols_count": int(merge_meta.get("carried_open_symbols_count") or 0),
                    "refresh_symbols_added_count": int(merge_meta.get("refresh_symbols_added_count") or 0),
                    "final_register_count": int(merge_meta.get("final_register_count") or 0),
                    "register_called": False,
                    "register_success": False,
                    "fallback_reason": str(reg_meta.get("error")),
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
                    "open_symbols_count": int(merge_meta.get("open_symbols_count") or len(open_syms)),
                    "refresh_csv_rows": refresh_csv_rows,
                    "carried_open_symbols_count": int(merge_meta.get("carried_open_symbols_count") or 0),
                    "refresh_symbols_added_count": int(merge_meta.get("refresh_symbols_added_count") or 0),
                    "final_register_count": int(merge_meta.get("final_register_count") or 0),
                    "register_called": False,
                    "register_success": False,
                    "fallback_reason": "register_count_zero",
                    "action": "continue_keep_previous_subscription",
                    "will_stop": False,
                },
            )
            return
        try:
            from api.kabu_register import register_symbols_cleared
            # Phase242b logs
            register_called = True
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
            slm_guard = getattr(pipeline_ctx.gate, "stop_low_mfe_guard", None)
            if slm_guard is not None:
                slm_guard.reset_session()
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
                    "refresh_csv_rows": refresh_csv_rows,
                    "carried_open_symbols_count": int(merge_meta.get("carried_open_symbols_count") or 0),
                    "refresh_symbols_added_count": int(merge_meta.get("refresh_symbols_added_count") or 0),
                    "final_register_count": int(merge_meta.get("final_register_count") or 0),
                    "register_called": True,
                    "register_success": True,
                    "fallback_reason": merge_meta.get("fallback_reason") or "",
                    "merge": merge_meta,
                    "added_symbols": added[:200],
                    "removed_symbols": removed[:200],
                    "after_symbols": after_syms[:200],
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
                    "open_symbols_count": int(merge_meta.get("open_symbols_count") or len(open_syms)),
                    "refresh_csv_rows": refresh_csv_rows,
                    "carried_open_symbols_count": int(merge_meta.get("carried_open_symbols_count") or 0),
                    "refresh_symbols_added_count": int(merge_meta.get("refresh_symbols_added_count") or 0),
                    "final_register_count": int(merge_meta.get("final_register_count") or 0),
                    "register_called": True,
                    "register_success": False,
                    "fallback_reason": "register_exception",
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
        t0_iso = _now_iso()
        t0_m = time.monotonic()
        _process_push_payload(
            pipeline_ctx,
            payload,
            msg_i,
            t0_push_received_at=t0_iso,
            t0_mono=t0_m,
        )

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
            if pipeline_ctx.entry_scan is not None:
                final_flush = pipeline_ctx.entry_scan.flush_pending()
                if final_flush is not None:
                    _process_scan_flush(pipeline_ctx, final_flush)
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
        dry_session = getattr(state, "live_order_dry_run", None)
        if dry_session is not None:
            from small_paper.live_order_dry_run_adapter import reconcile_session_positions

            open_syms = {str(p.get("symbol") or "") for p in observer.open_positions()} if observer else set()
            open_syms.discard("")
            reconcile_session_positions(
                dry_session,
                timestamp=_now_iso(),
                writer=writer,
                open_symbols=open_syms,
            )
        exit_events = observer.close_all(reason=final_reason)
        _log_and_dispatch_observer_events(
            exit_events,
            discord=discord,
            writer=writer,
            state=state,
            gate=gate,
            source="live",
            message_index=state.push_messages,
            profile=config.profile,
            config=config,
        )
        gate.state.open_slots = []
    summary.update(
        build_session_summary_extras(
            accepted_rows=state.accepted_rows,
            bucket_summary=state.bucket_summary,
            observer_stats=_observer_stats_dict(observer),
        )
    )
    monitored_n: Optional[int] = None
    if state.intraday_refresh_last_register_count:
        monitored_n = int(state.intraday_refresh_last_register_count)
    elif pipeline_ctx and pipeline_ctx.entry_eligible_symbols is not None:
        monitored_n = len(pipeline_ctx.entry_eligible_symbols)
    else:
        monitored_n = len(symbols)
    _attach_canonical_summary_fields(
        summary,
        state.events,
        config=config,
        watch_symbols_count=monitored_n,
    )
    _run_sector_heat_forward_shadow_auto(
        repo_root=repo_root,
        output_dir=output_dir,
        summary=summary,
        config=config,
        poll_interval_sec=float(session_cfg.get("poll_interval_sec") or poll_interval_sec),
    )
    _run_risk_sizing_forward_shadow_auto(
        repo_root=repo_root,
        output_dir=output_dir,
        summary=summary,
        config=config,
        poll_interval_sec=float(session_cfg.get("poll_interval_sec") or poll_interval_sec),
    )
    _run_equity_dynamic_stop_shadow_auto(
        repo_root=repo_root,
        output_dir=output_dir,
        summary=summary,
        config=config,
        poll_interval_sec=float(session_cfg.get("poll_interval_sec") or poll_interval_sec),
    )
    _run_live_config_forward_shadow_auto(
        repo_root=repo_root,
        output_dir=output_dir,
        summary=summary,
        config=config,
        poll_interval_sec=float(session_cfg.get("poll_interval_sec") or poll_interval_sec),
    )
    _run_live_config_transition_shadow_auto(
        repo_root=repo_root,
        output_dir=output_dir,
        summary=summary,
        config=config,
        poll_interval_sec=float(session_cfg.get("poll_interval_sec") or poll_interval_sec),
    )
    _run_boundary_forward_shadow_auto(
        repo_root=repo_root,
        output_dir=output_dir,
        summary=summary,
        config=config,
        poll_interval_sec=float(session_cfg.get("poll_interval_sec") or poll_interval_sec),
    )
    bus = pipeline_ctx.extension_bus if pipeline_ctx is not None else None
    if bus is not None:
        summary = bus.on_session_end(state, summary, config=config, output_dir=output_dir)
    else:
        _apply_post_entry_forward_shadow_finalize(state, summary, output_dir=output_dir)
        _apply_classic_momentum_forward_shadow_finalize(state, summary, output_dir=output_dir)
        _apply_quality_formula_shadow_finalize(state, summary)
        _apply_trading_value_shadow_finalize(state, summary)
        _apply_board_imbalance_shadow_finalize(state, summary)
        _apply_entry_expectancy_score_shadow_finalize(state, summary)
    try:
        notify_discord_session_end(
            discord,
            events=state.events,
            summary=summary,
            monitored_symbol_count=monitored_n,
            reject_rows=state.reject_rows,
            ux_stats=state.discord_ux,
        )
    except Exception as exc:
        log.warning("discord session_end notify failed: %s", exc)
        summary["discord_session_end_error"] = str(exc)
    from small_paper.core_runtime_mode import full_extension_active, get_core_runtime_mode

    if full_extension_active(get_core_runtime_mode(config)):
        _run_post_entry_forward_shadow_auto(
            repo_root=repo_root,
            output_dir=output_dir,
            summary=summary,
        )
        _run_classic_momentum_forward_shadow_auto(
            repo_root=repo_root,
            output_dir=output_dir,
            summary=summary,
        )
    writer.finalize_batch(
        events=state.events,
        positions=positions,
        summary=summary,
        pos_fields=pos_fields,
    )
    _write_quality_top_debug(output_dir, state.events)
    _write_phase396_artifacts_safe(
        repo_root,
        config=config,
        state=state,
        summary=summary,
    )
    day_stamp = datetime.now(JST).strftime("%Y%m%d")
    _write_phase335_lite_board_shadow_reports(state, repo_root=repo_root, day_stamp=day_stamp)
    _organize_daily_artifacts_safe(repo_root, day_stamp)
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
        "no_progress_exit_count": int(s.structural_exit_reason_counts.get("no_progress_exit", 0)),
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
        "same_symbol_open_policy": str(getattr(config, "same_symbol_open_policy", "replace") or "replace"),
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
    from small_paper.core_runtime_mode import core_runtime_session_fields

    out.update(core_runtime_session_fields(config))
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
