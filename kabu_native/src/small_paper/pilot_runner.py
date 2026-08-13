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
    discord_notify_summary_fields,
    notify_discord_session_end,
    observer_tracker_config_from_pilot,
)
from small_paper.discord_ux_session import DiscordUxSessionStats
from small_paper.reject_reasons import (
    REJECT_MAX_ENTRIES_PER_SCAN,
    REJECT_SAME_SYMBOL_OPEN_OVERLAP,
    is_entry_blocked_discord_notify_reason,
)
from small_paper.entry_pipeline_stages import (
    ObserverCloseOnPush,
    SAME_PUSH_REENTRY_AFTER_NO_PROGRESS_EXIT,
    Stage0NormalizedPayload,
    Stage1FreshnessResult,
    Stage2PBv2Result,
    Stage3ClusterDecision,
    Stage4FinalEntryDecision,
    Stage6CandidateRecord,
    StageTraceLogger,
    classify_cluster_stage,
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
    "pbv2_rise5_shadow_block",
    "pbv2_rise5_shadow_reason",
    "pbv2_rise5_value",
    "pbv2_rise5_threshold",
    "pbv2_rise5_shadow_apply_pool",
    "shadow_blocked_pnl_yen_100",
    "shadow_blocked_mfe",
    "shadow_blocked_mae",
    "pbv2_rise5_shadow_pnl_yen_100",
    "pbv2_rise5_shadow_delta_yen",
    "pbv2_flat_band_shadow_block",
    "pbv2_flat_band_shadow_reason",
    "pbv2_flat_band_rise5",
    "pbv2_flat_band_rise10",
    "pbv2_flat_band_variant",
    "pbv2_flat_band_shadow_apply_pool",
    "flat_band_and_rise5_shadow_block",
    "pbv2_flat_band_shadow_blocked_pnl_yen_100",
    "pbv2_flat_band_shadow_blocked_mfe",
    "pbv2_flat_band_shadow_blocked_mae",
    "pbv2_flat_band_shadow_pnl_yen_100",
    "pbv2_flat_band_shadow_delta_yen",
    "flat_weak_range_shadow_candidate",
    "flat_weak_range_shadow_block",
    "flat_weak_range_shadow_reason",
    "pretrend_shape",
    "flat_subclass",
    "breakout_class",
    "actual_pnl_yen_100",
    "shadow_pnl_yen_100",
    "delta_yen",
    "blocked_winner",
    "blocked_loser",
    "blocked_big_winner",
    "position_id",
    "observer_position_id",
    "accept_stage",
    "universe_slot",
    "universe_bucket",
    "source_bucket",
    "reject_reason",
    "readiness_bounce_from_recent_low_accept",
    "microseq_bounce_from_recent_low",
    "microseq_fall_from_recent_high",
    "microseq_slope_5min",
    "readiness_precision_shadow_block",
    "readiness_economics_shadow_block",
    "microsequence_recovery_fail_shadow_block",
    "shadow_union_ihc_block",
    "shadow_overlap_type",
    "ihc_i_feature_source",
    "ihc_h_feature_source",
    "ihc_c_feature_source",
    "ihc_union_feature_sources",
    "np_logger_ok",
    "np_feature_complete",
    "np_logger_row_id",
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
        "market_entry_time": ent.isoformat(),
        "current_price_time": ent.isoformat(),
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


def verify_kabu_connection(
    repo_root: Path,
    *,
    symbol_key: Optional[str] = None,
    native_root: Optional[Path] = None,
    trading_date: Optional[str] = None,
) -> dict[str, Any]:
    """Token + board probe; no orders. Never defaults to 9984.

    If symbol_key is omitted, resolve from actual registered ∩ frozen AM50.
    """
    import os

    from api.kabu_register import resolve_native_root_for_register_state
    from api.rest_client import KabuNativeApiError, KabuNativeRestClient, default_base_url, load_kabu_env
    from small_paper.day_fixed_am_registration import SAME_DAY_AM_FROZEN_AUTHORITY
    from small_paper.kabu_registration_authority import (
        NO_REGISTERED_KABU_PROBE_SYMBOL,
        resolve_registered_probe_symbol,
    )

    probe: dict[str, Any] = {}
    key = str(symbol_key or "").strip()
    if not key:
        native = Path(native_root) if native_root else resolve_native_root_for_register_state(Path(repo_root))
        day = str(trading_date or datetime.now(JST).strftime("%Y%m%d"))
        probe = resolve_registered_probe_symbol(native, day)
        if not probe.get("ok"):
            raise KabuNativeApiError(str(probe.get("reason") or NO_REGISTERED_KABU_PROBE_SYMBOL))
        key = str(probe.get("symbol_key") or probe.get("kabu_probe_symbol") or "").strip()
        if not key:
            raise KabuNativeApiError(NO_REGISTERED_KABU_PROBE_SYMBOL)

    if not os.environ.get("KABU_API_PASSWORD", "").strip():
        raise KabuNativeApiError("KABU_API_PASSWORD is not set")
    load_kabu_env(repo_root=repo_root)
    client = KabuNativeRestClient(default_base_url())
    token = client.issue_token_from_env()
    board = client.get_board(key, token=token)
    out = {
        "ok": True,
        "symbol_key": key,
        "kabu_probe_symbol": key,
        "kabu_probe_symbol_registered": bool(probe.get("kabu_probe_symbol_registered", True)),
        "kabu_probe_symbol_frozen_member": bool(probe.get("kabu_probe_symbol_frozen_member", True)),
        "probe_source": str(probe.get("probe_source") or SAME_DAY_AM_FROZEN_AUTHORITY),
        "current_price": board.get("CurrentPrice"),
        "current_price_time": board.get("CurrentPriceTime"),
        "registration_mutation": int(probe.get("registration_mutation") or 0),
    }
    return out


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


def _default_pbv2_rise5_shadow_counters() -> Any:
    from small_paper.pbv2_rise5_shadow import PbV2Rise5ShadowCounters

    return PbV2Rise5ShadowCounters()


def _default_pbv2_flat_band_shadow_counters() -> Any:
    from small_paper.pbv2_flat_band_guard_shadow import PbV2FlatBandShadowCounters

    return PbV2FlatBandShadowCounters()


def _default_flat_weak_range_forward_shadow_counters() -> Any:
    from small_paper.flat_weak_range_forward_shadow import FlatWeakRangeForwardShadowCounters

    return FlatWeakRangeForwardShadowCounters()


def _default_readiness_forward_shadow_counters() -> Any:
    from small_paper.readiness_forward_shadow import ReadinessForwardShadowCounters

    return ReadinessForwardShadowCounters()


def _default_microsequence_recovery_fail_forward_shadow_counters() -> Any:
    from small_paper.microsequence_recovery_fail_forward_shadow import (
        MicrosequenceRecoveryFailForwardShadowCounters,
    )

    return MicrosequenceRecoveryFailForwardShadowCounters()


def _default_ihc_shadow_portfolio_counters() -> Any:
    from small_paper.shadow_ihc_portfolio import IhcShadowPortfolioCounters

    return IhcShadowPortfolioCounters()


def _default_np_pre_entry_feature_logger_counters() -> Any:
    from small_paper.np_pre_entry_feature_logger import NpPreEntryFeatureLoggerCounters

    return NpPreEntryFeatureLoggerCounters()


def _default_e1_x5_forward_shadow() -> Any:
    from small_paper.e1_x5_forward_shadow import E1X5ForwardShadowSession

    return E1X5ForwardShadowSession.maybe_create()


def _default_board_imbalance_reversal_shadow() -> Any:
    from small_paper.board_imbalance_reversal_shadow import BoardImbalanceReversalState

    return BoardImbalanceReversalState.maybe_create()


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


def _default_entry_stage_counters() -> Any:
    from small_paper.entry_execution_integrity import EntryStageCounters

    return EntryStageCounters()


@dataclass
class _LiveRunState:
    started_mono: float
    session_ready_ts: Optional[str] = None
    first_gate_eval_ts: Optional[str] = None
    pre_session_warmup_ring_push_count: int = 0
    session_force_close_done: bool = False
    # Phase723: set BEFORE close_all / CAP release / Discord / await — blocks new ENTRY.
    entry_admission_closed: bool = False
    events: list[dict[str, Any]] = field(default_factory=list)
    accepted_rows: list[dict[str, Any]] = field(default_factory=list)
    reject_rows: list[dict[str, Any]] = field(default_factory=list)
    push_messages: int = 0
    gate_evaluations: int = 0
    heartbeat_count: int = 0
    api_error_count: int = 0
    consecutive_api_errors: int = 0
    reconnect_count: int = 0
    # Phase675: WS lifecycle / freeze recovery (ops; no trading logic change)
    recv_timeout_count: int = 0
    consecutive_recv_timeouts: int = 0
    last_push_at: Optional[str] = None
    last_push_mono: Optional[float] = None
    last_message_at: Optional[str] = None
    websocket_state: str = "init"
    reconnect_attempt: int = 0
    reconnect_succeeded_mono: Optional[float] = None
    lifecycle_watcher_ticks: int = 0
    # Phase722: DEGRADED on silence — wait for scheduled force_close; do not early-stop.
    websocket_degraded: bool = False
    silence_degraded_logged: bool = False
    entry_blocked_degraded: bool = False
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
    pbv2_flat_band_mainline_reject_count: int = 0
    pbv2_flat_band_mainline_reject_symbols: set[str] = field(default_factory=set)
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
    order_latency_dryrun: Any = None
    live_capital_manager: Any = None
    live_capital_read_client: Any = None
    live_capital_api_token: str = ""
    live_order_adapter: Any = None
    live_order_safety_bridge: Any = None
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
    entry_stop_reject_logging_recovered_count: int = 0
    logging_error_count: int = 0
    event_stale_reject_count: int = 0
    board_stale_reject_count: int = 0
    trade_stale_tag_count: int = 0
    evaluation_reachability_summary: dict[str, Any] = field(default_factory=dict)
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
    pullback_volume_forward: Any = None
    forward_observers_startup_notified: bool = False
    pbv2_rise5_shadow: Any = field(default_factory=_default_pbv2_rise5_shadow_counters)
    pbv2_flat_band_shadow: Any = field(default_factory=_default_pbv2_flat_band_shadow_counters)
    flat_weak_range_forward_shadow: Any = field(
        default_factory=_default_flat_weak_range_forward_shadow_counters
    )
    readiness_forward_shadow: Any = field(default_factory=_default_readiness_forward_shadow_counters)
    microsequence_recovery_fail_forward_shadow: Any = field(
        default_factory=_default_microsequence_recovery_fail_forward_shadow_counters
    )
    ihc_shadow_portfolio: Any = field(default_factory=_default_ihc_shadow_portfolio_counters)
    np_pre_entry_feature_logger: Any = field(default_factory=_default_np_pre_entry_feature_logger_counters)
    realtime_board_exit_shadow: Any = field(default_factory=_default_realtime_board_exit_shadow)
    e1_x5_forward_shadow: Any = field(default_factory=_default_e1_x5_forward_shadow)
    board_imbalance_reversal_shadow: Any = field(
        default_factory=_default_board_imbalance_reversal_shadow
    )
    entry_expectancy_score_shadow: Any = field(default_factory=_default_entry_expectancy_score_counters)
    discord_ux: DiscordUxSessionStats = field(default_factory=DiscordUxSessionStats)
    position_cap_stats: Any = None
    peak_observer_open: int = 0
    observer_tracker: Any = None
    observer_session_id: Optional[str] = None
    or_overlay: Any = None
    entry_stage_counters: Any = field(default_factory=_default_entry_stage_counters)
    v1r_native_exception_count: int = 0
    v1r_day_fixed_universe: list[str] = field(default_factory=list)
    v1r_native_entry_blocked: bool = False
    v1r_native_block_reason: str = ""


def _v1r_native_writer_output_dir(ctx: "_PushPipelineContext") -> Optional[Path]:
    """LiveSessionWriter formal output root (never session_dir — attribute does not exist)."""
    w = getattr(ctx, "writer", None)
    od = getattr(w, "output_dir", None) if w is not None else None
    return Path(od) if od else None


def _log_v1r_native_entry_exception(
    ctx: "_PushPipelineContext",
    exc: BaseException,
    *,
    where: str,
    symbol: str = "",
    message_index: Any = None,
) -> None:
    from small_paper.v1r_native_entry_live import get_native_entry

    eng = get_native_entry()
    ctx.state.v1r_native_exception_count = int(ctx.state.v1r_native_exception_count) + 1
    rec = {
        "event_time": _now_iso(),
        "error_type": "v1r_native_entry_runtime",
        "where": where,
        "exception_type": type(exc).__name__,
        "message": str(exc),
        "push_sequence": message_index,
        "symbol": symbol,
        "native_exception_count": ctx.state.v1r_native_exception_count,
        "native_engine_state": eng.snapshot() if eng is not None else None,
    }
    try:
        ctx.writer.append_error(rec)
    except Exception:
        pass
    # Fail-closed Primary ENTRY on wiring/runtime faults (no PBv2 Primary fallback).
    ctx.state.v1r_native_entry_blocked = True
    ctx.state.v1r_native_block_reason = f"{where}:{type(exc).__name__}:{exc}"


def _init_v1r_native_entry_for_live(
    *,
    state: "_LiveRunState",
    writer: "LiveSessionWriter",
    native_root: Path,
    trading_date: str,
    session_symbols: Sequence[str],
) -> dict[str, Any]:
    """Wire day-fixed AM universe + session trace_dir. Fail-closed if unresolved."""
    from small_paper.v1r_live_dual_lane import live_primary_enabled
    from small_paper.v1r_native_entry_live import (
        ensure_native_entry,
        resolve_day_fixed_am_runtime_universe,
    )

    if not live_primary_enabled():
        return {"enabled": False}
    resolved = resolve_day_fixed_am_runtime_universe(
        native_root=native_root, trading_date=trading_date
    )
    # Cross-check session watch list when present (must equal day-fixed membership)
    sess = [str(s).replace(".T", "") for s in session_symbols if str(s).replace(".T", "")]
    if resolved.get("ok") and sess and set(sess) != set(resolved["symbols"]):
        resolved = {
            **resolved,
            "ok": False,
            "reason": "session_symbols_day_fixed_mismatch",
            "session_count": len(set(sess)),
        }
    trace_dir = Path(writer.output_dir)
    eng = ensure_native_entry(
        universe=list(resolved.get("symbols") or []) if resolved.get("ok") else [],
        trace_dir=trace_dir,
        native_root=native_root,
        trading_date=trading_date,
        force_rebuild=True,
    )
    if not resolved.get("ok"):
        eng.ready = False
        eng.fail_reason = (
            f"NO_PAPER_PRIMARY:EMPTY_UNIVERSE:{resolved.get('reason')}"
        )
    state.v1r_day_fixed_universe = list(eng.universe) if eng.ready else []
    if not eng.ready or not eng.universe:
        state.v1r_native_entry_blocked = True
        state.v1r_native_block_reason = eng.fail_reason or str(
            resolved.get("reason") or "EMPTY_UNIVERSE"
        )
        writer.append_error(
            {
                "event_time": _now_iso(),
                "error_type": "v1r_native_entry_boot",
                "message": "NO PAPER PRIMARY — day-fixed universe unresolved or engine not ready",
                "fail_reason": state.v1r_native_block_reason,
                "resolved": resolved,
                "native_engine_state": eng.snapshot(),
            }
        )
    else:
        state.v1r_native_entry_blocked = False
        state.v1r_native_block_reason = ""
    wiring = {
        "enabled": True,
        "resolved": resolved,
        "native_universe_count": len(eng.universe),
        "trace_dir": str(eng.trace_dir) if eng.trace_dir else None,
        "ready": eng.ready,
        "fail_reason": eng.fail_reason,
        "blocked": state.v1r_native_entry_blocked,
    }
    try:
        (trace_dir / "v1r_native_entry_wiring.json").write_text(
            json.dumps(wiring, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except Exception:
        pass
    return wiring


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


def _init_order_latency_dryrun(config: SmallPaperPilotConfig, state: _LiveRunState, output_dir: Path) -> None:
    from small_paper.order_latency_dryrun_trace import OrderLatencyDryRunSession, order_latency_dryrun_enabled

    if order_latency_dryrun_enabled(config):
        state.order_latency_dryrun = OrderLatencyDryRunSession(output_dir)


def _order_latency_session(ctx: _PushPipelineContext) -> Any:
    return getattr(ctx.state, "order_latency_dryrun", None)


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


def _init_live_order_safety_sm(
    config: SmallPaperPilotConfig,
    state: _LiveRunState,
    *,
    output_dir: Path,
    session_id: str = "",
) -> None:
    """Phase687W4: Wire LiveOrderSafetyEngine dry-run bridge (no real submits)."""
    try:
        from small_paper.live_order_runtime_bridge import build_runtime_bridge, safety_sm_enabled

        if not safety_sm_enabled(config):
            return
        client = getattr(state, "live_capital_read_client", None)
        token = str(getattr(state, "live_capital_api_token", "") or "")
        sid = session_id or output_dir.name
        bridge = build_runtime_bridge(
            output_dir=output_dir / "live_order_safety",
            session_id=sid,
            config=config,
            kabu_client=client,
            kabu_token=token,
            allow_mock_capital=False,
        )
        bridge.startup()
        state.live_order_safety_bridge = bridge
        # Phase687W7/W7A: session manifest at SafetySM start (create_then_update; no strategy change)
        try:
            from small_paper.operational_recovery import create_session_manifest, disk_usage_pct, config_sha256
            from small_paper.stateful_journal_recovery import resolve_git_commit

            cfg_path = getattr(config, "config_path", None) or getattr(config, "source_path", None)
            # Prefer explicit config path from load; fall back to production YAML path
            if not cfg_path:
                cfg_path = (
                    Path(__file__).resolve().parents[2]
                    / "configs"
                    / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
                )
            git_commit = resolve_git_commit(Path(__file__).resolve().parents[2])
            cfg_sha = config_sha256(cfg_path)
            np_enabled = bool(
                getattr(config, "phase687_np_feature_logger_enabled", False)
                or getattr(config, "np_feature_logger_enabled", False)
            )
            trading_day = str(getattr(state, "trading_day", "") or getattr(config, "trading_day", "") or "")
            am_pm = str(getattr(state, "session_am_pm", "") or getattr(config, "session_label", "") or "")
            jr = getattr(bridge, "journal_restore", {}) or {}
            create_session_manifest(
                session_id=sid,
                output_dir=output_dir / "live_order_safety",
                trading_day=trading_day,
                session_am_pm=am_pm,
                git_commit=git_commit,
                config_sha=cfg_sha,
                live_trading_enabled=bool(getattr(config, "live_trading_enabled", False)),
                order_enabled=bool(getattr(config, "order_enabled", False)),
                safety_sm_enabled=True,
                np_logger_enabled=np_enabled,
                disk_usage_pct=disk_usage_pct(output_dir),
                token_probe_status=str(
                    (getattr(bridge, "token_probe", {}) or {}).get("token_probe_status")
                    or (getattr(bridge, "token_probe", {}) or {}).get("status")
                    or "UNKNOWN"
                ),
                kabu_readonly_status=str(
                    ((getattr(bridge, "account_audit", {}) or {}).get("account_status"))
                    or ((getattr(bridge, "startup_recon", {}) or {}).get("account_status"))
                    or "UNKNOWN"
                ),
                journal_sequence_start=int(jr.get("journal_sequence_after") or 0),
            )
            # mark completeness on manifest
            man_path = output_dir / "live_order_safety" / "session_manifest.json"
            if man_path.is_file():
                import json as _json

                man = _json.loads(man_path.read_text(encoding="utf-8"))
                incomplete = (
                    man.get("git_commit") in ("", "UNSET", "demo", "UNAVAILABLE")
                    or man.get("config_sha256") in ("", "UNSET", "demo", "MISSING")
                )
                man["manifest_completeness"] = "INCOMPLETE" if incomplete else "COMPLETE"
                man["disk_usage_start"] = man.get("disk_usage_pct")
                man_path.write_text(_json.dumps(man, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except Exception:
            pass
    except Exception:
        state.live_order_safety_bridge = None


def _init_pbv2_rise5_shadow(config: SmallPaperPilotConfig, state: _LiveRunState) -> None:
    from small_paper.pbv2_rise5_shadow import build_pbv2_rise5_shadow_counters, rise5_shadow_enabled

    if rise5_shadow_enabled(config):
        state.pbv2_rise5_shadow = build_pbv2_rise5_shadow_counters(config)


def _init_pbv2_flat_band_shadow(config: SmallPaperPilotConfig, state: _LiveRunState) -> None:
    from small_paper.pbv2_flat_band_guard_shadow import (
        build_pbv2_flat_band_shadow_counters,
        flat_band_shadow_enabled,
    )

    if flat_band_shadow_enabled(config):
        state.pbv2_flat_band_shadow = build_pbv2_flat_band_shadow_counters(config)


def _init_flat_weak_range_forward_shadow(config: SmallPaperPilotConfig, state: _LiveRunState) -> None:
    from small_paper.flat_weak_range_forward_shadow import build_flat_weak_range_forward_shadow_counters

    built = build_flat_weak_range_forward_shadow_counters(config)
    if built is not None:
        state.flat_weak_range_forward_shadow = built


def _init_readiness_forward_shadow(config: SmallPaperPilotConfig, state: _LiveRunState) -> None:
    from small_paper.readiness_forward_shadow import build_readiness_forward_shadow_counters

    built = build_readiness_forward_shadow_counters(config)
    if built is not None:
        state.readiness_forward_shadow = built


def _init_microsequence_recovery_fail_forward_shadow(config: SmallPaperPilotConfig, state: _LiveRunState) -> None:
    from small_paper.microsequence_recovery_fail_forward_shadow import (
        build_microsequence_recovery_fail_forward_shadow_counters,
    )

    built = build_microsequence_recovery_fail_forward_shadow_counters(config)
    if built is not None:
        state.microsequence_recovery_fail_forward_shadow = built


def _init_ihc_shadow_portfolio(config: SmallPaperPilotConfig, state: _LiveRunState) -> None:
    from small_paper.shadow_ihc_portfolio import build_ihc_shadow_portfolio_counters

    built = build_ihc_shadow_portfolio_counters(config)
    if built is not None:
        state.ihc_shadow_portfolio = built


def _init_np_pre_entry_feature_logger(config: SmallPaperPilotConfig, state: _LiveRunState) -> None:
    from small_paper.np_pre_entry_feature_logger import build_np_pre_entry_feature_logger_counters

    built = build_np_pre_entry_feature_logger_counters(config)
    if built is not None:
        state.np_pre_entry_feature_logger = built


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
        if key not in ctx:
            continue
        val = ctx.get(key)
        # Persist booleans/zeros (FWR block=False, actual_pnl=0) — only skip None/"".
        if val is None or val == "":
            continue
        row[key] = val
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
                try:
                    pv = getattr(state, "pullback_volume_forward", None)
                    if pv is not None and getattr(pv, "enabled", False):
                        from small_paper.pullback_volume_forward_logger import note_runtime_exit

                        note_runtime_exit(pv, row)
                except Exception:
                    pass
                rise5_counters = getattr(state, "pbv2_rise5_shadow", None)
                if rise5_counters is not None:
                    rise5_counters.record_exit(row)
                flat_counters = getattr(state, "pbv2_flat_band_shadow", None)
                if flat_counters is not None:
                    flat_counters.record_exit(row)
                fwr_counters = getattr(state, "flat_weak_range_forward_shadow", None)
                if fwr_counters is not None:
                    fwr_counters.record_exit(row)
                readiness_counters = getattr(state, "readiness_forward_shadow", None)
                if readiness_counters is not None:
                    readiness_counters.record_exit(row)
                ms_c_counters = getattr(state, "microsequence_recovery_fail_forward_shadow", None)
                if ms_c_counters is not None:
                    ms_c_counters.record_exit(row)
                ihc_counters = getattr(state, "ihc_shadow_portfolio", None)
                if ihc_counters is not None:
                    ihc_counters.record_exit(row)
                np_counters = getattr(state, "np_pre_entry_feature_logger", None)
                if np_counters is not None:
                    outcome = np_counters.record_exit(row)
                    if outcome is not None and writer is not None:
                        try:
                            writer.append_np_pre_entry_outcomes(outcome)
                        except Exception:
                            pass
                try:
                    from small_paper.cost_aware_entry_v2_shadow import note_exit as note_v2_exit
                    from small_paper.cost_aware_entry_v2_shadow import shadow_enabled as v2_on

                    if v2_on(config) and state is not None:
                        st_v2 = getattr(state, "cost_aware_entry_v2_shadow", None)
                        if st_v2 is not None:
                            note_v2_exit(st_v2, row)
                except Exception:
                    pass
                try:
                    from small_paper.board_imbalance_reversal_shadow import note_exit as note_bir_exit

                    bir = getattr(state, "board_imbalance_reversal_shadow", None) if state else None
                    if bir is not None and getattr(bir, "enabled", False):
                        note_bir_exit(bir, row)
                except Exception:
                    pass
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
    _dispatch_observer_events(
        events,
        discord=discord,
        observer_session_id=getattr(state, "observer_session_id", None) if state else None,
    )


def _dispatch_observer_events(
    events: Sequence[ObserverJudgmentEvent],
    *,
    discord: Optional[SmallPaperDiscordNotifier],
    observer_session_id: Optional[str] = None,
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
                ev_sid = str(ev.context.get("session_id") or "")
                if observer_session_id and ev_sid and ev_sid != observer_session_id:
                    continue
                if ev.context.get("phantom_session_exit"):
                    continue
                discord.notify_exit(context=ev.context)
        except Exception:
            pass


def _extended_shadow_summary_fields(state: _LiveRunState) -> dict[str, Any]:
    counters = getattr(state, "extended_entry_shadow", None)
    if counters is None:
        return {}
    return counters.summary_fields()


def _cost_aware_entry_shadow_summary_fields(state: _LiveRunState) -> dict[str, Any]:
    """Observe-only; empty unless COST_AWARE_ENTRY_SHADOW=1."""
    try:
        from datetime import datetime
        from pathlib import Path
        from zoneinfo import ZoneInfo

        from small_paper.cost_aware_entry_shadow import (
            attach_runtime_compatible_to_closed_trades,
            finalize_never_filled,
            finalize_open_positions,
            shadow_enabled,
            summarize_state,
        )
        from small_paper.cost_aware_price_path import build_symbol_price_paths, parse_ts

        if not shadow_enabled():
            return {}
        st = getattr(state, "cost_aware_entry_shadow", None)
        if st is None:
            return {
                "cost_aware_entry_shadow": {
                    "enabled": True,
                    "selection_cycles": 0,
                    "np_in_decision": False,
                },
                "cost_aware_entry_shadow_enabled": True,
                "cost_aware_shadow_entries_proxy": 0,
                "cost_aware_delta_proxy": None,
                "cost_aware_status": "ENABLED_NO_STATE",
            }
        finalize_never_filled(st)
        JST = ZoneInfo("Asia/Tokyo")
        day = getattr(state, "trading_date", None) or datetime.now(JST).strftime("%Y%m%d")
        force_close = None
        am_pm = getattr(state, "am_pm_policy", None)
        if am_pm is not None and getattr(am_pm, "force_close", None):
            from small_paper.session_schedule import parse_hhmm

            try:
                y, m, d = int(str(day)[:4]), int(str(day)[4:6]), int(str(day)[6:8])
                hhmm = parse_hhmm(am_pm.force_close)
                force_close = datetime(y, m, d, hhmm.hour, hhmm.minute, tzinfo=JST)
            except Exception:
                force_close = datetime.now(JST)
        else:
            force_close = datetime.now(JST)

        # Load session events for price paths + official exits (session finalize only).
        price_paths: dict = {}
        official_exits: list = []
        session_dir = getattr(state, "session_dir", None) or getattr(
            getattr(state, "writer", None), "session_dir", None
        )
        events_path = Path(session_dir) / "small_paper_events.jsonl" if session_dir else None
        if events_path is not None and events_path.is_file():
            try:
                events = []
                with events_path.open(encoding="utf-8") as fh:
                    for line in fh:
                        if line.strip():
                            events.append(json.loads(line))
                price_paths = build_symbol_price_paths(events)
                for e in events:
                    if e.get("event_type") != "observer_exit":
                        continue
                    ts = parse_ts(e.get("exit_time") or e.get("event_time"))
                    try:
                        px = float(e.get("exit_price") or 0)
                    except (TypeError, ValueError):
                        px = 0.0
                    if ts:
                        official_exits.append(
                            (ts, str(e.get("symbol") or ""), px, str(e.get("exit_reason") or ""))
                        )
            except Exception:
                price_paths = {}
                official_exits = []

        # Phase678: never leave virtual opens after session summary
        if getattr(st, "open_shadow", None):
            finalize_open_positions(
                st,
                force_close_time=force_close,
                trading_date=str(getattr(state, "trading_date", "") or ""),
                price_paths=price_paths or None,
                is_freeze_recovery=bool(getattr(state, "session_force_close_done", False)),
            )
        # Always attach runtime-compatible on closed virtual trades at session summarize.
        if st.closed_trades:
            enriched, _join_stats = attach_runtime_compatible_to_closed_trades(
                st.closed_trades,
                official_exits=official_exits,
                price_paths=price_paths,
                force_close_time=force_close,
            )
            st.closed_trades = enriched
        block = summarize_state(st)
        # Phase722: flatten Discord inventory keys (single source from nested block).
        rt = block.get("runtime_compatible_pnl")
        sh = block.get("pnl_after_5bps_30m")
        delta = block.get("delta_total_5bps")
        if delta is None and isinstance(rt, (int, float)) and isinstance(sh, (int, float)):
            delta = round(float(sh) - float(rt), 2)
        pf_delta = block.get("pf_delta_5bps")
        return {
            "cost_aware_entry_shadow": block,
            "cost_aware_entry_shadow_enabled": bool(block.get("enabled", True)),
            "cost_aware_shadow_entries_proxy": int(block.get("shadow_entries") or 0),
            "cost_aware_virtual_entry_count": int(block.get("virtual_entry_count") or block.get("shadow_entries") or 0),
            "cost_aware_real_block_count": 0,
            "cost_aware_evaluable_count": int(block.get("evaluable_count") or block.get("n_closed") or 0),
            "cost_aware_delta_proxy": delta,
            "cost_aware_entry_shadow_pf_delta": pf_delta,
            "cost_aware_status": str(block.get("status") or ""),
            "cost_aware_status_reason": block.get("status_reason"),
            "cost_aware_runtime_compatible_pnl": rt,
            "cost_aware_shadow_pnl_after_5bps": sh,
            "cost_aware_join_success_count": block.get("join_success_count"),
            "cost_aware_join_failed_count": block.get("join_failed_count"),
            "cost_aware_pending_count": block.get("pending_count"),
        }
    except Exception:
        return {}


def _e1_x5_forward_shadow_summary_fields(state: _LiveRunState) -> dict[str, Any]:
    """Independent CAP5 observe-only; never touches PBv2 CAP / orders."""
    try:
        sess = getattr(state, "e1_x5_forward_shadow", None)
        if sess is None:
            return {
                "e1_x5_forward_shadow_enabled": False,
                "e1_x5_forward_shadow": {"enabled": False},
            }
        s = sess.summary() if hasattr(sess, "summary") else {}
        decision = getattr(sess, "enable_decision", None)
        return {
            "e1_x5_forward_shadow_enabled": bool(getattr(sess, "enabled", False)),
            "e1_x5_forward_shadow_enable_reason": (
                decision.reason if decision is not None else s.get("enable_reason")
            ),
            "e1_x5_forward_shadow_trades": int(s.get("trades") or 0),
            "e1_x5_forward_shadow_total_pnl_yen_100": s.get("total_pnl_yen_100"),
            "e1_x5_forward_shadow_profit_factor_yen_100": s.get("profit_factor_yen_100"),
            "e1_x5_forward_shadow_open_positions": int(s.get("open_positions") or 0),
            "e1_x5_forward_shadow_evaluated_count": int(s.get("evaluated_count") or 0),
            "e1_x5_forward_shadow_no_evaluation_count": int(s.get("no_evaluation_count") or 0),
            "e1_x5_forward_shadow_entries_n": int(s.get("entries_n") or 0),
            "e1_x5_forward_shadow_wins": int(s.get("wins") or 0),
            "e1_x5_forward_shadow_losses": int(s.get("losses") or 0),
            "e1_x5_forward_shadow_draws": int(s.get("draws") or 0),
            "e1_x5_forward_shadow_cap_blocked": int(s.get("cap_blocked") or 0),
            "e1_x5_forward_shadow_same_symbol_blocked": int(s.get("same_symbol_blocked") or 0),
            "e1_x5_forward_shadow_missing_score_count": int(s.get("missing_score_count") or 0),
            "e1_x5_forward_shadow_candidate_count": int(
                s.get("candidate_count") if s.get("candidate_count") is not None else s.get("candidates") or 0
            ),
            "e1_x5_forward_shadow_evaluation_status": s.get("evaluation_status"),
            "e1_x5_forward_shadow": s,
        }
    except Exception:
        return {"e1_x5_forward_shadow_enabled": False}


def _apply_e1_x5_forward_shadow_finalize(
    state: _LiveRunState,
    summary: dict[str, Any],
    *,
    output_dir: Optional[Path] = None,
) -> None:
    """Persist independent E1_X5 virtual ENTRY/EXIT ledger; refresh summary fields."""
    summary.update(_e1_x5_forward_shadow_summary_fields(state))
    sess = getattr(state, "e1_x5_forward_shadow", None)
    if sess is None or output_dir is None:
        return
    try:
        from small_paper.e1_x5_forward_shadow import persist_e1_x5_virtual_ledger

        meta = persist_e1_x5_virtual_ledger(sess, Path(output_dir))
        summary["e1_x5_virtual_ledger"] = meta
        summary["e1_x5_virtual_ledger_sha256"] = meta.get("ledger_sha256")
        summary["e1_x5_virtual_ledger_path"] = meta.get("ledger_path")
        # Mirror aggregates for Discord --- E1_X5 --- panel
        agg = meta.get("aggregates") if isinstance(meta.get("aggregates"), dict) else {}
        nested = summary.get("e1_x5_forward_shadow")
        if isinstance(nested, dict):
            nested = dict(nested)
            nested["virtual_ledger_sha256"] = meta.get("ledger_sha256")
            nested["virtual_ledger_aggregates"] = agg
            summary["e1_x5_forward_shadow"] = nested
    except Exception as exc:
        log.warning("e1_x5 virtual ledger persist failed: %s", exc)
        summary["e1_x5_virtual_ledger_error"] = str(exc)


def _board_imbalance_reversal_shadow_summary_fields(state: _LiveRunState) -> dict[str, Any]:
    try:
        from small_paper.board_imbalance_reversal_shadow import summary_fields

        st = getattr(state, "board_imbalance_reversal_shadow", None)
        if st is None:
            return {"board_imbalance_reversal_shadow_enabled": False}
        return summary_fields(st)
    except Exception:
        return {"board_imbalance_reversal_shadow_enabled": False}


def _cost_aware_entry_v2_shadow_summary_fields(state: _LiveRunState) -> dict[str, Any]:
    """Observe-only V2; never contributes to mainline / canonical PnL."""
    try:
        from pathlib import Path

        from small_paper.cost_aware_entry_v2_shadow import (
            finalize_pending_exits,
            shadow_enabled_with_source,
            summarize_state,
        )

        enabled, src = shadow_enabled_with_source()
        if not enabled:
            return {
                "cost_aware_entry_v2_shadow": {
                    "enabled": False,
                    "enabled_source": src,
                    "observe_only": True,
                    "mainline_pnl_included": False,
                    "canonical_pnl_mixed": False,
                    "primary_arm": "H_board_ts",
                    "secondary_arm": "I_price_board",
                    "evaluated_candidates": 0,
                    "submit": 0,
                    "cancel": 0,
                    "live_order": 0,
                },
                "cost_aware_entry_v2_shadow_enabled": False,
            }
        st = getattr(state, "cost_aware_entry_v2_shadow", None)
        if st is None:
            return {
                "cost_aware_entry_v2_shadow": {
                    "enabled": True,
                    "enabled_source": src,
                    "observe_only": True,
                    "mainline_pnl_included": False,
                    "canonical_pnl_mixed": False,
                    "primary_arm": "H_board_ts",
                    "secondary_arm": "I_price_board",
                    "evaluated_candidates": 0,
                    "board_feature_available": 0,
                    "board_feature_missing": 0,
                    "fail_open_count": 0,
                    "warmup_count": 0,
                    "submit": 0,
                    "cancel": 0,
                    "live_order": 0,
                },
                "cost_aware_entry_v2_shadow_enabled": True,
            }
        st.enabled = True
        st.enabled_source = src
        # Session finalize: join any still-pending V2 rows from official observer exits.
        session_dir = getattr(state, "session_dir", None) or getattr(
            getattr(state, "writer", None), "session_dir", None
        )
        events_path = Path(session_dir) / "small_paper_events.jsonl" if session_dir else None
        if events_path is not None and events_path.is_file():
            try:
                exit_rows = []
                with events_path.open(encoding="utf-8") as fh:
                    for line in fh:
                        if not line.strip():
                            continue
                        e = json.loads(line)
                        if e.get("event_type") != "observer_exit":
                            continue
                        exit_rows.append(e)
                if exit_rows:
                    finalize_pending_exits(st, exit_rows, session_force_close=True)
            except Exception:
                pass
        block = summarize_state(st)
        am_pm = getattr(state, "am_pm_policy", None)
        kind = str(getattr(am_pm, "kind", "") or getattr(state, "am_pm_kind", "") or "").upper()
        if kind in ("AM", "PM"):
            block["session_kind"] = kind
        # Persist by_key snapshot for Daily merge (optional)
        block["by_key"] = {k: dict(v) for k, v in st.by_key.items()}
        return {
            "cost_aware_entry_v2_shadow": block,
            "cost_aware_entry_v2_shadow_enabled": True,
        }
    except Exception:
        return {}


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


def _pbv2_flat_band_mainline_summary_fields(
    gate: ExposureGate,
    state: _LiveRunState,
) -> dict[str, Any]:
    guard = getattr(gate, "pbv2_flat_band_entry_guard", None)
    if guard is None:
        return {
            "pbv2_flat_band_mainline_enabled": False,
            "pbv2_flat_band_mainline_reject_count": 0,
            "pbv2_flat_band_mainline_reject_symbols": [],
        }
    out = guard.summary_fields()
    out["pbv2_flat_band_mainline_reject_count"] = state.pbv2_flat_band_mainline_reject_count
    out["pbv2_flat_band_mainline_reject_symbols"] = sorted(
        state.pbv2_flat_band_mainline_reject_symbols
    )
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


def _notify_forward_observers_startup_once(
    state: _LiveRunState,
    discord: Optional[SmallPaperDiscordNotifier],
    config: Any = None,
) -> None:
    """Phase687W59: one-shot [TRADEBOT PAPER START]. Fail-open; never blocks Paper."""
    if getattr(state, "forward_observers_startup_notified", False):
        return
    try:
        from small_paper.discord_current_system_summary import (
            build_runtime_status,
            render_paper_start_lines,
        )
        from small_paper.forward_observer_defaults import forward_observer_status_block

        status = build_runtime_status(config)
        lines = render_paper_start_lines(status)
        try:
            from small_paper.e1_x5_forward_shadow import (
                emit_e1_x5_forward_shadow_startup_once,
                resolve_e1_x5_forward_shadow_from_runtime,
            )
            from small_paper.shadow_registry import format_shadow_portfolio_startup_lines

            e1_decision = resolve_e1_x5_forward_shadow_from_runtime(cfg=config)
            e1_lines = emit_e1_x5_forward_shadow_startup_once(e1_decision)
            lines = (
                list(lines)
                + [""]
                + list(format_shadow_portfolio_startup_lines())
                + [""]
                + list(e1_lines)
            )
            e1x5 = getattr(state, "e1_x5_forward_shadow", None)
            if e1x5 is not None:
                e1x5.enable_decision = e1_decision
                e1x5.enabled = e1_decision.enabled
                e1x5.startup_lines = list(e1_lines)
        except Exception:
            pass
        state.forward_observers_startup_notified = True
        if discord is None or not getattr(discord, "active", False):
            return
        discord.notify_forward_observers_startup(lines=lines)
        block = forward_observer_status_block(config)
        if block.get("warning"):
            try:
                discord.notify_error(
                    operation="forward_observers",
                    message=str(block["warning"]),
                    extra={
                        "cost_aware": block.get("cost_aware_entry_shadow_enabled"),
                        "pullback_volume": block.get("pullback_volume_forward_enabled"),
                    },
                )
            except Exception:
                pass
    except Exception:
        state.forward_observers_startup_notified = True


def _ensure_pullback_volume_forward(state: _LiveRunState, config: Any = None) -> Any:
    """Phase687W57 observe-only logger; fail-open; never affects GateDecision."""
    st = getattr(state, "pullback_volume_forward", None)
    if st is not None:
        return st
    try:
        from small_paper.pullback_volume_forward_logger import (
            PullbackVolumeForwardState,
            logger_enabled,
        )

        enabled = logger_enabled(getattr(config, "__dict__", None) if config is not None else None)
        # also accept config mapping-like
        if not enabled and isinstance(config, dict):
            enabled = logger_enabled(config)
        st = PullbackVolumeForwardState(enabled=enabled)
        if enabled:
            from datetime import datetime as _dt
            from zoneinfo import ZoneInfo as _ZI

            st.trading_date = _dt.now(_ZI("Asia/Tokyo")).strftime("%Y%m%d")
        state.pullback_volume_forward = st
        return st
    except Exception:
        return None


def _pullback_volume_forward_on_push(
    ctx: "_PushPipelineContext",
    *,
    symbol: str,
    payload: Mapping[str, Any],
    px_tick: float,
) -> None:
    """Observe-only volume/imbalance ring + path labels. Never touches GateDecision."""
    try:
        pv = _ensure_pullback_volume_forward(ctx.state, ctx.config)
        if pv is None or not getattr(pv, "enabled", False):
            return
        from small_paper.board_imbalance_shadow import compute_entry_order_book_imbalance_field
        from small_paper.extended_entry_shadow import tick_ts_from_payload
        from small_paper.pullback_volume_forward_logger import note_push, update_price_path

        ts = tick_ts_from_payload(payload)
        try:
            epoch = float(ts)
        except (TypeError, ValueError):
            epoch = datetime.now(JST).timestamp()
        imb_fields = compute_entry_order_book_imbalance_field(payload=payload)
        note_push(
            pv,
            symbol=symbol,
            payload={**dict(payload), **imb_fields},
            event_epoch=epoch,
        )
        if px_tick > 0:
            update_price_path(pv, symbol=symbol, price=px_tick, event_epoch=epoch)
    except Exception:
        pass


def _pullback_volume_forward_summary_fields(state: _LiveRunState) -> dict[str, Any]:
    st = getattr(state, "pullback_volume_forward", None)
    if st is None:
        return {}
    try:
        from small_paper.pullback_volume_forward_logger import aggregate_rows

        block = st.summary_block()
        if st.rows:
            block.update(aggregate_rows(list(st.rows.values())))
        return {"pullback_volume_forward": block}
    except Exception:
        return {}


def _pullback_misread_entry_guard_shadow_summary_fields(state: _LiveRunState) -> dict[str, Any]:
    counters = getattr(state, "pullback_misread_entry_guard_shadow", None)
    if counters is None:
        return {}
    return counters.summary_fields()


def _pbv2_rise5_shadow_summary_fields(state: _LiveRunState) -> dict[str, Any]:
    counters = getattr(state, "pbv2_rise5_shadow", None)
    if counters is None:
        return {}
    return counters.summary_fields()


def _pbv2_flat_band_shadow_summary_fields(state: _LiveRunState) -> dict[str, Any]:
    counters = getattr(state, "pbv2_flat_band_shadow", None)
    if counters is None:
        return {}
    return counters.summary_fields()


def _flat_weak_range_forward_shadow_summary_fields(state: _LiveRunState) -> dict[str, Any]:
    counters = getattr(state, "flat_weak_range_forward_shadow", None)
    if counters is None:
        return {"flat_weak_range_shadow_enabled": False}
    return counters.summary_fields()


def _readiness_forward_shadow_summary_fields(state: _LiveRunState) -> dict[str, Any]:
    counters = getattr(state, "readiness_forward_shadow", None)
    if counters is None:
        return {}
    return counters.summary_fields()


def _microsequence_recovery_fail_forward_shadow_summary_fields(state: _LiveRunState) -> dict[str, Any]:
    counters = getattr(state, "microsequence_recovery_fail_forward_shadow", None)
    if counters is None:
        return {}
    return counters.summary_fields()


def _ihc_shadow_portfolio_summary_fields(state: _LiveRunState) -> dict[str, Any]:
    counters = getattr(state, "ihc_shadow_portfolio", None)
    if counters is None:
        return {}
    return counters.summary_fields()


def _np_pre_entry_feature_logger_summary_fields(state: _LiveRunState) -> dict[str, Any]:
    counters = getattr(state, "np_pre_entry_feature_logger", None)
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


def _apply_ihc_shadow_counterfactual_finalize(
    state: _LiveRunState,
    summary: dict[str, Any],
    *,
    output_dir: Path,
    config: SmallPaperPilotConfig,
) -> None:
    from small_paper.ihc_shadow_counterfactual import finalize_session_ihc_shadow_summary

    ihc = finalize_session_ihc_shadow_summary(
        state.accepted_rows,
        state.events,
        session_dir=output_dir,
        config=config,
    )
    if ihc:
        summary.update(ihc)


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
    symbol_board_ring: dict[str, list] = field(default_factory=dict)
    entry_scan: Optional[Any] = None
    symbol_universe_meta: dict[str, dict[str, str]] = field(default_factory=dict)
    latency_trace: Optional[Any] = None
    extension_bus: Optional[Any] = None
    stage_profiler: Optional[Any] = None
    # Phase687W43F: readiness / recovery evaluation tracker (no PBv2 condition changes)
    evaluation_reachability: Optional[Any] = None


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
_ENTRY_STOP_PRE_GATE_REASONS = frozenset({"am_pm_entry_stop", REJECT_OUTSIDE_REFRESH_UNIVERSE})


def _is_entry_stop_pre_gate_reason(reason: str) -> bool:
    return str(reason or "") in _ENTRY_STOP_PRE_GATE_REASONS


def _entry_stop_source_event_id(
    *,
    symbol: str,
    reason: str,
    message_index: int,
    entry_time: Any = None,
) -> str:
    et = str(entry_time or "")
    return f"entry_stop|{reason}|{symbol}|{message_index}|{et}"


def _annotate_entry_stop_reject_event(
    ctx: _PushPipelineContext,
    rej: dict[str, Any],
    *,
    reason: str,
    symbol: str,
    message_index: int,
    trade: Mapping[str, Any],
    cand: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Ensure Phase640 schema fields on entry-stop / outside-universe rejects."""
    am_pm = ""
    policy = getattr(ctx, "am_pm_policy", None)
    if policy is not None:
        am_pm = str(getattr(policy, "kind", "") or "")
    source_event_id = _entry_stop_source_event_id(
        symbol=symbol,
        reason=reason,
        message_index=message_index,
        entry_time=trade.get("entry_time") or rej.get("entry_time"),
    )
    candidate_id = ""
    if cand:
        candidate_id = str(cand.get("candidate_id") or cand.get("message_index") or "")
    if not candidate_id:
        candidate_id = f"cand|{symbol}|{message_index}"
    rej.setdefault("accepted", False)
    rej["accepted"] = False
    rej["rejected"] = True
    rej["reject_reason"] = reason
    rej["gate_reject_reason"] = reason
    rej.setdefault(
        "entry_score_v2",
        trade.get("entry_expectancy_score_v2")
        if trade.get("entry_expectancy_score_v2") is not None
        else trade.get("entry_score_v2"),
    )
    rej.setdefault("strategy_namespace", str(trade.get("profile") or getattr(ctx.config, "profile", "") or ""))
    rej.setdefault("actual_or_shadow", "actual")
    rej["candidate_id"] = candidate_id
    rej["source_event_id"] = source_event_id
    if am_pm:
        rej.setdefault("am_pm_session", am_pm)
    day = str(trade.get("trade_date") or trade.get("trading_date") or "")
    if not day and rej.get("entry_time"):
        try:
            day = str(rej.get("entry_time") or "")[:10].replace("-", "")
        except Exception:
            day = ""
    if day:
        rej.setdefault("trading_date", day)
    return rej


def _entry_stop_reject_already_logged(ctx: _PushPipelineContext, source_event_id: str) -> bool:
    if not source_event_id:
        return False
    for ev in ctx.state.events:
        if ev.get("event_type") == "rejected" and str(ev.get("source_event_id") or "") == source_event_id:
            return True
    return False


def _record_pipeline_logging_error(
    ctx: _PushPipelineContext,
    *,
    stage: str,
    exc: BaseException,
    symbol: str = "",
) -> None:
    ctx.state.logging_error_count += 1
    try:
        ctx.writer.append_error(
            {
                "event_time": _now_iso(),
                "error_type": "pipeline_logging_error",
                "stage": stage,
                "symbol": symbol,
                "message": str(exc),
            }
        )
    except Exception:
        pass


def _entry_stop_reject_logging_summary_fields(state: _LiveRunState) -> dict[str, Any]:
    return {
        "entry_stop_reject_logging_recovered_count": int(
            getattr(state, "entry_stop_reject_logging_recovered_count", 0) or 0
        ),
        "logging_error_count": int(getattr(state, "logging_error_count", 0) or 0),
    }


def _notify_entry_blocked_discord(
    ctx: _PushPipelineContext,
    *,
    sym: str,
    trade: Mapping[str, Any],
    rej: Mapping[str, Any],
    payload: Mapping[str, Any],
    enriched: Mapping[str, Any],
    block_reason: str,
    score5_ord: Optional[int] = None,
) -> None:
    """trade-cap-blocked webhook only (never trade-notify)."""
    if not is_entry_blocked_discord_notify_reason(block_reason):
        return
    discord = getattr(ctx, "discord", None)
    if discord is None or not discord.cap_blocked_notify_enabled():
        return
    if block_reason == REJECT_MAX_CONCURRENT:
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
    discord.notify_entry_cap_blocked(
        event=rej,
        payload=enriched,
        trade_data={**dict(trade), **shadow},
        open_slots=_active_cap_count(ctx),
        score5_candidate_ordinal=score5_ord,
        ux_stats=ctx.state.discord_ux,
        block_reason=block_reason,
    )


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
    _notify_entry_blocked_discord(
        ctx,
        sym=sym,
        trade=trade,
        rej=rej,
        payload=payload,
        enriched=dict(payload),
        block_reason=REJECT_SAME_SYMBOL_OPEN_OVERLAP,
    )
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

    # Phase723: re-check immediately before position registration.
    if _entry_admission_closed(ctx):
        _reject_session_closing_entry(
            ctx, sym=sym, trade=trade, payload=payload, msg_i=msg_i, decision=decision
        )
        return

    # EMERGENCY 20260812 decontamination:
    # When V1R is PAPER_PRIMARY, PBv2 gate_accept is SHADOW_ONLY.
    # Must not mutate Primary observer / pending / open / cap / dual primary.
    try:
        from small_paper.v1r_live_dual_lane import live_primary_enabled
        from small_paper.v1r_native_entry_live import get_native_entry, ensure_native_entry

        if live_primary_enabled():
            eng = get_native_entry() or ensure_native_entry(
                universe=list(getattr(ctx.state, "v1r_day_fixed_universe", None) or []),
                trace_dir=_v1r_native_writer_output_dir(ctx),
            )
            entry_px = 0.0
            try:
                entry_px = float(
                    trade.get("entry_price")
                    or payload.get("CurrentPrice")
                    or enriched.get("CurrentPrice")
                    or 0.0
                )
            except (TypeError, ValueError):
                entry_px = 0.0
            snap = eng.note_pbv2_shadow_accept(
                symbol=str(sym),
                entry_price=entry_px,
                entry_time=str(trade.get("entry_time") or _now_iso()),
            )
            # Record shadow accept row (not official Primary ENTRY)
            try:
                row = _event_from_gate(
                    event_type="pbv2_shadow_accepted",
                    trade=trade,
                    decision=decision,
                    source=ctx.source,
                    message_index=msg_i,
                    current_price=payload.get("CurrentPrice"),
                )
                row["pbv2_role"] = "SHADOW_ONLY"
                row["primary_role"] = "PAPER_PRIMARY_V1R_NATIVE"
                row["entry_mode"] = "PBv2_SHADOW_ONLY"
                row["primary_occupancy_unchanged"] = True
                row["shadow_admit"] = snap.get("shadow_admit") if isinstance(snap, dict) else None
                row["v1r_primary_snapshot"] = {
                    "open": eng.open_n,
                    "pending": eng.pending_n,
                    "exposure": eng.exposure(),
                }
                row["shadow_note"] = snap
                row["shadow_pbv2_snapshot"] = eng.shadow_pbv2.snapshot()
                ctx.writer.append_event(row)
            except Exception:
                pass
            # Discord: digest only (never per-PUSH / per-second trade-research flood).
            # Internal shadow ledger above is unchanged; Primary occupancy untouched.
            try:
                from small_paper.v1r_pbv2_shadow_discord_digest import (
                    get_pbv2_shadow_discord_digest,
                )

                digest = get_pbv2_shadow_discord_digest(
                    trace_dir=_v1r_native_writer_output_dir(ctx)
                )
                shadow_admit = (
                    snap.get("shadow_admit") if isinstance(snap, dict) else {}
                ) or {}
                digest.note_accept_attempt(
                    symbol=str(sym),
                    shadow_admit=dict(shadow_admit),
                    entry_price=float(entry_px or 0.0),
                    trading_date=str(getattr(ctx.state, "trading_date", "") or ""),
                    open_n=int(eng.shadow_pbv2.open_n),
                    cap=int(eng.shadow_pbv2.cap),
                )
            except Exception as exc:
                try:
                    ctx.writer.append_error(
                        {
                            "event": "PBV2_SHADOW_DISCORD_DIGEST_EXCEPTION",
                            "symbol": str(sym),
                            "error": f"{type(exc).__name__}:{exc}",
                            "channel_expected": "trade-research",
                        }
                    )
                except Exception:
                    pass
                print(
                    f"[PBV2_SHADOW_DISCORD_DIGEST_EXCEPTION] symbol={sym} "
                    f"err={type(exc).__name__}:{exc}",
                    flush=True,
                )
            return
    except Exception:
        # Fail-closed: if diversion fails under live primary, do NOT fall through to classic Primary
        try:
            from small_paper.v1r_live_dual_lane import live_primary_enabled

            if live_primary_enabled():
                return
        except Exception:
            pass

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
        from small_paper.vwap_shadow_reject import (
            compute_vwap_shadow_reject_fields,
            vwap_shadow_reject_enabled,
        )

        if vwap_shadow_reject_enabled(ctx.config):
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
        try:
            pv = _ensure_pullback_volume_forward(ctx.state, ctx.config)
            if pv is not None and getattr(pv, "enabled", False):
                from small_paper.pullback_volume_forward_logger import build_entry_row

                build_entry_row(
                    pv,
                    {**trade, **pb_shadow},
                    official_entry=True,
                    official_reject=False,
                    session=str(getattr(ctx.state, "session_kind", "") or ""),
                    trading_date=str(getattr(pv, "trading_date", "") or ""),
                )
        except Exception:
            pass
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
    from small_paper.pbv2_rise5_shadow import compute_pbv2_rise5_shadow_fields, rise5_shadow_enabled

    rise5_shadow_block: Optional[bool] = None
    if rise5_shadow_enabled(ctx.config):
        rise5_shadow = compute_pbv2_rise5_shadow_fields(ctx.config, trade)
        trade.update(rise5_shadow)
        rise5_shadow_block = bool(rise5_shadow.get("pbv2_rise5_shadow_block"))
        rise5_counters = getattr(ctx.state, "pbv2_rise5_shadow", None)
        if rise5_counters is not None:
            rise5_counters.record_accept(rise5_shadow)
    from small_paper.pbv2_flat_band_guard_shadow import (
        compute_pbv2_flat_band_shadow_fields,
        flat_band_shadow_enabled,
    )

    if flat_band_shadow_enabled(ctx.config):
        flat_shadow = compute_pbv2_flat_band_shadow_fields(
            ctx.config, trade, rise5_shadow_block=rise5_shadow_block
        )
        trade.update(flat_shadow)
        flat_counters = getattr(ctx.state, "pbv2_flat_band_shadow", None)
        if flat_counters is not None:
            flat_counters.record_accept(flat_shadow)
    from small_paper.flat_weak_range_forward_shadow import (
        compute_flat_weak_range_shadow_fields,
        flat_weak_range_shadow_enabled,
    )

    if flat_weak_range_shadow_enabled(ctx.config):
        fwr_shadow = compute_flat_weak_range_shadow_fields(ctx.config, trade)
        trade.update(fwr_shadow)
        fwr_counters = getattr(ctx.state, "flat_weak_range_forward_shadow", None)
        if fwr_counters is not None:
            fwr_counters.record_accept({**trade, **fwr_shadow})
    from small_paper.readiness_forward_shadow import (
        compute_readiness_shadow_fields,
        readiness_shadow_any_enabled,
    )

    if readiness_shadow_any_enabled(ctx.config):
        from small_paper.extended_entry_shadow import tick_ts_from_payload

        entry_ts = tick_ts_from_payload(payload)
        ring = ctx.symbol_price_ring.get(sym, [])
        same_sym_n = (
            sum(1 for r in ctx.state.accepted_rows if str(r.get("symbol") or "") == sym) + 1
        )
        readiness_shadow = compute_readiness_shadow_fields(
            ctx.config,
            trade,
            price_ring=ring,
            entry_ts=entry_ts,
            same_symbol_entry_count_today=same_sym_n,
        )
        trade.update(readiness_shadow)
        readiness_counters = getattr(ctx.state, "readiness_forward_shadow", None)
        if readiness_counters is not None:
            readiness_counters.record_accept(readiness_shadow)
    from small_paper.microsequence_recovery_fail_forward_shadow import (
        compute_microsequence_recovery_fail_shadow_fields,
        microsequence_recovery_fail_shadow_enabled,
    )

    ms_c_shadow: dict[str, Any] = {}
    if microsequence_recovery_fail_shadow_enabled(ctx.config):
        from small_paper.extended_entry_shadow import tick_ts_from_payload

        entry_ts_ms = tick_ts_from_payload(payload)
        ring_ms = ctx.symbol_price_ring.get(sym, [])
        ms_c_shadow = compute_microsequence_recovery_fail_shadow_fields(
            ctx.config,
            trade,
            price_ring=ring_ms,
            entry_ts=entry_ts_ms,
        )
        trade.update(ms_c_shadow)
        ms_c_counters = getattr(ctx.state, "microsequence_recovery_fail_forward_shadow", None)
        if ms_c_counters is not None:
            ms_c_counters.record_accept(ms_c_shadow)
    from small_paper.shadow_ihc_portfolio import compute_ihc_shadow_fields

    ihc_fields = compute_ihc_shadow_fields(
        i_block=bool(trade.get("readiness_precision_shadow_block")),
        h_block=bool(trade.get("readiness_economics_shadow_block")),
        c_block=bool(trade.get("microsequence_recovery_fail_shadow_block")),
    )
    trade.update(ihc_fields)
    ihc_counters = getattr(ctx.state, "ihc_shadow_portfolio", None)
    if ihc_counters is not None:
        ihc_counters.record_accept({**trade, **ihc_fields})
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
    from small_paper.np_pre_entry_feature_logger import (
        compute_np_pre_entry_predictor_row,
        np_pre_entry_feature_logger_enabled,
    )

    if np_pre_entry_feature_logger_enabled(ctx.config):
        from small_paper.extended_entry_shadow import tick_ts_from_payload

        accepted_at_ts = float(tick_ts_from_payload(payload))
        accepted_at_iso = str(acc.get("event_time") or trade.get("accepted_at") or _now_iso())
        np_row = compute_np_pre_entry_predictor_row(
            trade={**trade, "accepted_at": accepted_at_iso},
            board_ring=ctx.symbol_board_ring.get(sym, []),
            accepted_at_ts=accepted_at_ts,
            accepted_at_iso=accepted_at_iso,
        )
        trade["np_logger_ok"] = np_row.get("np_logger_ok")
        trade["np_feature_complete"] = np_row.get("np_feature_complete")
        trade["np_logger_row_id"] = np_row.get("np_logger_row_id")
        acc["np_logger_ok"] = np_row.get("np_logger_ok")
        acc["np_feature_complete"] = np_row.get("np_feature_complete")
        acc["np_logger_row_id"] = np_row.get("np_logger_row_id")
        np_counters = getattr(ctx.state, "np_pre_entry_feature_logger", None)
        if np_counters is not None:
            np_counters.record_accept(np_row)
        try:
            ctx.writer.append_np_pre_entry_features(np_row)
        except Exception:
            pass
        # Cost-Aware V2 Shadow: observe-only note at ACCEPT with causal NP features
        try:
            from small_paper.cost_aware_entry_v2_shadow import (
                CostAwareV2ShadowState,
                note_accepted_candidate,
                shadow_enabled_with_source,
            )

            enabled, src = shadow_enabled_with_source(getattr(ctx, "config", None))
            if enabled:
                st_v2 = getattr(ctx.state, "cost_aware_entry_v2_shadow", None)
                if st_v2 is None:
                    st_v2 = CostAwareV2ShadowState(enabled=True, enabled_source=src)
                    out_dir = getattr(getattr(ctx, "writer", None), "session_dir", None) or getattr(
                        ctx.state, "session_dir", None
                    )
                    if out_dir is not None:
                        st_v2.session_dir = str(out_dir)
                    ctx.state.cost_aware_entry_v2_shadow = st_v2
                note_accepted_candidate(
                    st_v2,
                    symbol=str(sym),
                    trade={**trade, **{k: np_row.get(k) for k in np_row}},
                    np_row=np_row,
                    session=str(getattr(ctx.state, "am_pm_kind", "") or ""),
                    position_id=str(acc.get("position_id") or trade.get("position_id") or ""),
                    entry_time=str(acc.get("entry_time") or trade.get("entry_time") or ""),
                    entry_price=acc.get("entry_price") or trade.get("entry_price"),
                )
        except Exception:
            pass
        # Board Imbalance Reversal (H_board_ts TEMP_FORWARD) — independent of Cost-Aware V2
        try:
            from small_paper.board_imbalance_reversal_shadow import note_accepted as note_bir

            bir = getattr(ctx.state, "board_imbalance_reversal_shadow", None)
            if bir is not None and getattr(bir, "enabled", False):
                note_bir(
                    bir,
                    symbol=str(sym),
                    trade={**trade, **{k: np_row.get(k) for k in np_row}},
                    np_row=np_row,
                    session=str(getattr(ctx.state, "am_pm_kind", "") or ""),
                    position_id=str(acc.get("position_id") or trade.get("position_id") or ""),
                    entry_time=str(acc.get("entry_time") or trade.get("entry_time") or ""),
                    entry_price=acc.get("entry_price") or trade.get("entry_price"),
                )
        except Exception:
            pass
    else:
        # NP logger off: still record V2 accept as fail-open (board feature missing)
        try:
            from small_paper.cost_aware_entry_v2_shadow import (
                CostAwareV2ShadowState,
                note_accepted_candidate,
                shadow_enabled_with_source,
            )

            enabled, src = shadow_enabled_with_source(getattr(ctx, "config", None))
            if enabled:
                st_v2 = getattr(ctx.state, "cost_aware_entry_v2_shadow", None)
                if st_v2 is None:
                    st_v2 = CostAwareV2ShadowState(enabled=True, enabled_source=src)
                    ctx.state.cost_aware_entry_v2_shadow = st_v2
                note_accepted_candidate(
                    st_v2,
                    symbol=str(sym),
                    trade=trade,
                    np_row=None,
                    session=str(getattr(ctx.state, "am_pm_kind", "") or ""),
                    position_id=str(acc.get("position_id") or trade.get("position_id") or ""),
                    entry_time=str(acc.get("entry_time") or trade.get("entry_time") or ""),
                    entry_price=acc.get("entry_price") or trade.get("entry_price"),
                )
        except Exception:
            pass
        try:
            from small_paper.board_imbalance_reversal_shadow import note_accepted as note_bir

            bir = getattr(ctx.state, "board_imbalance_reversal_shadow", None)
            if bir is not None and getattr(bir, "enabled", False):
                note_bir(
                    bir,
                    symbol=str(sym),
                    trade=trade,
                    np_row=None,
                    session=str(getattr(ctx.state, "am_pm_kind", "") or ""),
                    position_id=str(acc.get("position_id") or trade.get("position_id") or ""),
                    entry_time=str(acc.get("entry_time") or trade.get("entry_time") or ""),
                    entry_price=acc.get("entry_price") or trade.get("entry_price"),
                )
        except Exception:
            pass
    _finalize_accepted_entry_stages(
        ctx,
        sym=sym,
        trade=trade,
        decision=decision,
        payload=payload,
        enriched=enriched,
        acc=acc,
        scan_meta=scan_meta,
        bucket=bucket,
        score5_ord=score5_ord,
        msg_i=msg_i,
        slot_before=slot_before,
        slot_after=slot_after,
    )


def _record_entry_stage(
    ctx: _PushPipelineContext,
    *,
    decision_id: str,
    stage: str,
    symbol: str,
    event_time: str,
    position_id: str = "",
    current_price: Any = None,
    entry_price: Any = None,
    validation_result: str = "",
    failure_reason: str = "",
    extra: Optional[Mapping[str, Any]] = None,
) -> None:
    from small_paper.entry_execution_integrity import stage_event_row

    counters = getattr(ctx.state, "entry_stage_counters", None)
    if counters is not None and not counters.record(decision_id, stage):
        return
    session_key = ""
    if ctx.observer:
        session_key = str(getattr(ctx.observer, "session_id", "") or "")
    row = stage_event_row(
        decision_id=decision_id,
        stage=stage,
        symbol=symbol,
        event_time=event_time,
        session_key=session_key,
        position_id=position_id,
        current_price=current_price,
        entry_price=entry_price,
        validation_result=validation_result,
        failure_reason=failure_reason,
        extra=extra,
    )
    ctx.state.events.append(row)
    try:
        ctx.writer.append_event(row)
    except Exception:
        pass


def _emit_entry_aborted_audit(
    ctx: _PushPipelineContext,
    *,
    acc: Mapping[str, Any],
    decision_id: str,
    reason: str,
) -> None:
    """Record [ENTRY ABORTED] audit — never sends official Discord ENTRY."""
    audit = {
        "timestamp": _now_iso(),
        "notification_type": "ENTRY_ABORTED",
        "official_entry": False,
        "decision_id": decision_id,
        "position_id": acc.get("position_id") or "",
        "symbol": acc.get("symbol"),
        "abort_reason": reason,
        "delivery_result": "not_sent_official_entry",
        "final_result": "aborted",
        "event_time": acc.get("event_time") or acc.get("entry_time"),
    }
    try:
        ctx.writer.append_discord_entry_delivery(audit)
    except Exception:
        pass
    try:
        ctx.writer.append_live_order_event(
            {
                "timestamp": _now_iso(),
                "event_type": "ORDER_INTENT_SKIPPED_INVALID_ENTRY_PAYLOAD",
                "symbol": acc.get("symbol"),
                "decision_id": decision_id,
                "abort_reason": reason,
                "side": "ENTRY",
            }
        )
    except Exception:
        pass
    # One Discord alert per decision_id (fail-open); never official ENTRY.
    try:
        seen = getattr(ctx.state, "_entry_aborted_discord_ids", None)
        if seen is None:
            seen = set()
            ctx.state._entry_aborted_discord_ids = seen
        if decision_id and decision_id in seen:
            return
        if decision_id:
            seen.add(decision_id)
        discord = getattr(ctx, "discord", None)
        if discord is None or not getattr(discord, "active", False):
            return
        from small_paper.discord_current_system_summary import render_entry_aborted_lines

        stage = str(acc.get("accept_stage") or "execution_payload_validation")
        lines = render_entry_aborted_lines(acc, reason=reason, stage=stage)
        discord.notify_error(
            operation="ENTRY_ABORTED",
            message="\n".join(lines),
            extra={"decision_id": decision_id, "official_entry": False},
        )
    except Exception:
        pass


def _finalize_accepted_entry_stages(
    ctx: _PushPipelineContext,
    *,
    sym: str,
    trade: dict[str, Any],
    decision: Any,
    payload: Mapping[str, Any],
    enriched: Mapping[str, Any],
    acc: dict[str, Any],
    scan_meta: Optional[Mapping[str, Any]],
    bucket: str,
    score5_ord: Optional[int],
    msg_i: int,
    slot_before: int,
    slot_after: int,
) -> None:
    """gate_accepted → validate → position_registered → official_entry → Discord/order.

    Phase687W43B-FIX2: blocks Ghost accept (null price) from Discord ENTRY / order paths.
    """
    from small_paper.entry_execution_integrity import (
        STAGE_ACCEPT_ABORTED,
        STAGE_EXECUTION_PAYLOAD_VALIDATED,
        STAGE_GATE_ACCEPTED,
        STAGE_OFFICIAL_ENTRY,
        STAGE_POSITION_REGISTERED,
        STAGE_QUEUE_SELECTED,
        is_official_entry_ready,
        make_decision_id,
        validate_execution_payload,
    )

    accept_clock = str(acc.get("event_time") or _now_iso())
    trade["accepted_at"] = accept_clock
    trade["accepted_event_time"] = accept_clock
    acc["accepted_at"] = accept_clock
    acc["accepted_event_time"] = accept_clock
    if trade.get("market_entry_time") in (None, ""):
        trade["market_entry_time"] = trade.get("entry_time")
    if trade.get("current_price_time") in (None, ""):
        trade["current_price_time"] = trade.get("market_entry_time") or trade.get("entry_time")
    acc["market_entry_time"] = trade.get("market_entry_time")
    acc["current_price_time"] = trade.get("current_price_time")
    if ctx.observer and ctx.observer.session_id:
        acc["session_id"] = ctx.observer.session_id
        acc["session_kind"] = ctx.observer.session_kind
        trade["session_id"] = ctx.observer.session_id
        trade["session_kind"] = ctx.observer.session_kind

    decision_id = make_decision_id(
        symbol=sym,
        entry_time=trade.get("entry_time") or accept_clock,
        message_index=msg_i,
        scan_id=(scan_meta or {}).get("scan_id"),
    )
    acc["decision_id"] = decision_id
    trade["decision_id"] = decision_id
    # Idempotent: same decision_id must not re-emit Discord / position / order.
    counters = getattr(ctx.state, "entry_stage_counters", None)
    if counters is not None:
        seen = getattr(counters, "_seen_stage_keys", set())
        if f"{decision_id}|{STAGE_OFFICIAL_ENTRY}" in seen or (
            f"{decision_id}|{STAGE_ACCEPT_ABORTED}" in seen
        ):
            acc["accept_stage"] = "duplicate_decision_skipped"
            acc["duplicate_decision_skip"] = True
            acc["position_registered"] = f"{decision_id}|{STAGE_OFFICIAL_ENTRY}" in seen
            acc["official_entry"] = f"{decision_id}|{STAGE_OFFICIAL_ENTRY}" in seen
            return
    acc["accept_stage"] = STAGE_GATE_ACCEPTED
    acc["position_registered"] = False
    acc["official_entry"] = False
    _record_entry_stage(
        ctx,
        decision_id=decision_id,
        stage=STAGE_GATE_ACCEPTED,
        symbol=sym,
        event_time=accept_clock,
        current_price=payload.get("CurrentPrice"),
        entry_price=trade.get("entry_price"),
    )

    validation = validate_execution_payload(
        symbol=sym,
        trade=trade,
        payload=payload,
        event_time=accept_clock,
        quantity=trade.get("quantity") or 100,
        side=trade.get("side") or "2",
        session_entry_allowed=True,
    )
    acc.update(validation.to_fields())
    if not validation.ok:
        acc["accept_stage"] = STAGE_ACCEPT_ABORTED
        acc["accept_aborted"] = True
        acc["ghost_accept_reason"] = acc.get("failure_reason") or "execution_payload_invalid"
        _record_entry_stage(
            ctx,
            decision_id=decision_id,
            stage=STAGE_ACCEPT_ABORTED,
            symbol=sym,
            event_time=accept_clock,
            current_price=payload.get("CurrentPrice"),
            entry_price=trade.get("entry_price"),
            validation_result="failed",
            failure_reason=acc.get("failure_reason") or "",
        )
        ctx.state.events.append(acc)
        ctx.writer.append_event(acc)
        ctx.state.accepted_rows.append(dict(trade))
        _record_bucket(ctx.state, "accepted")
        _emit_entry_aborted_audit(
            ctx,
            acc=acc,
            decision_id=decision_id,
            reason=str(acc.get("failure_reason") or "execution_payload_invalid"),
        )
        return

    acc["accept_stage"] = STAGE_EXECUTION_PAYLOAD_VALIDATED
    acc["current_price"] = validation.current_price
    acc["entry_price"] = validation.entry_price
    trade["current_price"] = validation.current_price
    trade["entry_price"] = validation.entry_price
    entry_px = float(validation.entry_price or 0.0)
    _record_entry_stage(
        ctx,
        decision_id=decision_id,
        stage=STAGE_EXECUTION_PAYLOAD_VALIDATED,
        symbol=sym,
        event_time=accept_clock,
        current_price=validation.current_price,
        entry_price=validation.entry_price,
        validation_result="ok",
    )
    if scan_meta:
        acc["accept_stage"] = STAGE_QUEUE_SELECTED
        _record_entry_stage(
            ctx,
            decision_id=decision_id,
            stage=STAGE_QUEUE_SELECTED,
            symbol=sym,
            event_time=accept_clock,
            current_price=validation.current_price,
            entry_price=validation.entry_price,
            extra={"scan_id": (scan_meta or {}).get("scan_id")},
        )

    if ctx.observer:
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
            # EMERGENCY 20260812: PBv2 accept MUST NOT hitchhike dual Primary/Control.
            # V1R-native ENTRY alone may call dual.try_admit_fill(source="v1r_native").
            # (Previously: register_entry → dual.try_admit_fill contaminated PAPER_PRIMARY.)
            from small_paper.observer_entry_time import observer_entry_fields

            acc.update(observer_entry_fields(trade, payload=enriched))
            pid = ctx.observer.position_id_for(sym)
            if pid:
                acc["position_id"] = pid
                acc["observer_position_id"] = pid
                acc["position_registered"] = True
                acc["accept_stage"] = STAGE_POSITION_REGISTERED
                trade["position_id"] = pid
                trade["observer_position_id"] = pid
                try:
                    fwr_counters = getattr(ctx.state, "flat_weak_range_forward_shadow", None)
                    if fwr_counters is not None and hasattr(fwr_counters, "bind_position"):
                        fwr_counters.bind_position(
                            position_id=pid,
                            symbol=sym,
                            entry_time=str(trade.get("entry_time") or acc.get("entry_time") or ""),
                            decision_id=str(decision_id or ""),
                        )
                except Exception:
                    pass
                _record_entry_stage(
                    ctx,
                    decision_id=decision_id,
                    stage=STAGE_POSITION_REGISTERED,
                    symbol=sym,
                    event_time=accept_clock,
                    position_id=pid,
                    current_price=validation.current_price,
                    entry_price=validation.entry_price,
                )
            else:
                acc["accept_stage"] = STAGE_ACCEPT_ABORTED
                acc["accept_aborted"] = True
                acc["ghost_accept_reason"] = "register_entry_did_not_open_position"
        elif ctx.observer.has_open(sym):
            acc["accept_stage"] = STAGE_ACCEPT_ABORTED
            acc["accept_aborted"] = True
            acc["ghost_accept_reason"] = "observer_still_open_after_overlap_path"
        _record_observer_open_peak(ctx)
        if ctx.config.position_cap_mode:
            slot_after = _active_cap_count(ctx)
            acc["position_slot_after"] = slot_after
    else:
        # No observer tracker (non-paper-observer modes): validated payload alone
        # may proceed with synthetic position_id — Paper AM/PM always has observer.
        pid = f"no_observer:{decision_id}"
        acc["position_id"] = pid
        acc["observer_position_id"] = pid
        acc["position_registered"] = True
        acc["accept_stage"] = STAGE_POSITION_REGISTERED
        trade["position_id"] = pid
        _record_entry_stage(
            ctx,
            decision_id=decision_id,
            stage=STAGE_POSITION_REGISTERED,
            symbol=sym,
            event_time=accept_clock,
            position_id=pid,
            current_price=validation.current_price,
            entry_price=validation.entry_price,
            extra={"no_observer_mode": True},
        )

    if acc.get("accept_aborted") or not acc.get("position_registered"):
        if not acc.get("accept_aborted"):
            acc["accept_stage"] = STAGE_ACCEPT_ABORTED
            acc["accept_aborted"] = True
            acc["ghost_accept_reason"] = acc.get("ghost_accept_reason") or "position_not_registered"
        _record_entry_stage(
            ctx,
            decision_id=decision_id,
            stage=STAGE_ACCEPT_ABORTED,
            symbol=sym,
            event_time=accept_clock,
            current_price=validation.current_price,
            entry_price=validation.entry_price,
            validation_result="failed",
            failure_reason=str(acc.get("ghost_accept_reason") or ""),
        )
        ctx.state.events.append(acc)
        ctx.writer.append_event(acc)
        ctx.state.accepted_rows.append(dict(trade))
        _record_bucket(ctx.state, "accepted")
        _emit_entry_aborted_audit(
            ctx,
            acc=acc,
            decision_id=decision_id,
            reason=str(acc.get("ghost_accept_reason") or "position_not_registered"),
        )
        return

    # Official entry path
    acc["official_entry"] = True
    acc["accept_stage"] = STAGE_OFFICIAL_ENTRY
    _record_entry_stage(
        ctx,
        decision_id=decision_id,
        stage=STAGE_OFFICIAL_ENTRY,
        symbol=sym,
        event_time=accept_clock,
        position_id=str(acc.get("position_id") or ""),
        current_price=validation.current_price,
        entry_price=validation.entry_price,
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
            "open_slots_after": slot_after,
            "position_id": acc.get("position_id"),
            "decision_id": decision_id,
        },
        fields=ctx.pos_fields,
    )

    if ctx.discord and ctx.discord.active and is_official_entry_ready(acc):
        import time
        from small_paper.discord_entry_delivery import FINAL_DELIVERED

        notify_mono = time.monotonic()
        signal_mono = float((scan_meta or {}).get("entry_signal_mono") or notify_mono)
        entry_res = ctx.discord.notify_entry(
            event=acc,
            payload=enriched,
            open_slots=slot_after,
            slot_before=slot_before if ctx.config.position_cap_mode else None,
            session_bucket=bucket,
            score5_candidate_ordinal=score5_ord,
            ux_stats=ctx.state.discord_ux,
            entry_signal_mono=signal_mono,
            notify_mono=notify_mono,
        )
        acc["entry_delivery_result"] = entry_res.final_result
        acc["entry_delivery_failure_classification"] = entry_res.failure_classification
        acc["entry_notify_retry_count"] = entry_res.retry_count
        acc["notification_type"] = "ENTRY"
        acc["official_entry_notification"] = True
        if entry_res.final_result == FINAL_DELIVERED:
            acc["discord_sent_ts"] = entry_res.sent_time
            acc["entry_delivery_http_status"] = entry_res.http_status
            if entry_res.discord_message_id:
                acc["discord_message_id"] = entry_res.discord_message_id

    # Order / dry-run only after official_entry + validated payload
    _maybe_record_live_order_pipeline_entry(
        ctx, sym=sym, trade=trade, payload=payload, acc=acc, scan_meta=scan_meta
    )
    _maybe_record_live_order_wiring_entry(
        ctx, sym=sym, trade=trade, payload=payload, acc=acc, scan_meta=scan_meta
    )
    _maybe_record_live_order_safety_entry(ctx, sym=sym, trade=trade, payload=payload, acc=acc)
    if not _legacy_live_order_hooks_enabled(ctx.config):
        return
    _maybe_record_live_capital_check_entry(ctx, sym=sym, trade=trade, payload=payload, acc=acc)
    _maybe_record_live_order_entry(ctx, sym=sym, trade=trade, payload=payload, acc=acc)


def _cost_aware_shadow_on_scan_flush(ctx: _PushPipelineContext, flush: Any) -> None:
    """Observe-only selection cycle (all noted symbols). Fail-open. Default OFF."""
    try:
        from small_paper.cost_aware_entry_shadow import (
            finalize_never_filled,
            run_selection_cycle,
            shadow_enabled,
            summarize_state,
        )

        if not shadow_enabled(getattr(ctx, "config", None)):
            return
        st = getattr(ctx.state, "cost_aware_entry_shadow", None)
        if st is None:
            return
        official = [str(c.symbol) for c in (getattr(flush, "accepted", None) or [])]
        run_selection_cycle(
            st,
            scan_id=str(getattr(flush, "scan_id", "") or ""),
            trading_date=str(getattr(ctx.state, "trading_date", "") or ""),
            official_accepted_symbols=official,
        )
        ctx.state.cost_aware_entry_shadow_summary = summarize_state(st)
    except Exception:
        pass


REJECT_SESSION_CLOSING = "REJECT_SESSION_CLOSING"

# Stop / force-close reasons that close ENTRY admission (Paper ops boundary).
SESSION_CLOSING_STOP_REASONS = frozenset(
    {
        "morning_session_close",  # AM Session Close
        "afternoon_session_close",  # PM Session Close
        "session_end",
        "recovery_session_close",  # Recovery Force Close
        "signal_interrupt",  # Manual Stop (SIGINT)
        "keyboard_interrupt",  # Manual Stop
        "cancelled",
        "max_consecutive_api_errors",  # Emergency Stop
        "register_failed",  # Emergency / hard fail stop
        "duration_elapsed",
        "max_polls",
        # WebSocket / comm-fault force-close paths (may normalize later)
        "push_reconnect_silence_timeout",
        "WS_RECONNECT_EXHAUSTED",
        "push_unexpected",
        "EVENT_LOOP_STALL",
    }
)


def _entry_admission_closed(ctx: _PushPipelineContext) -> bool:
    st = getattr(ctx, "state", None)
    if st is None:
        return False
    if bool(getattr(st, "entry_admission_closed", False)):
        return True
    if bool(getattr(st, "session_force_close_done", False)):
        return True
    if bool(getattr(st, "stop_requested", False)):
        # Any requested stop closes new ENTRY (manual / emergency / WS / recovery).
        return True
    reason = str(getattr(st, "stop_reason", "") or "")
    if reason in SESSION_CLOSING_STOP_REASONS:
        return True
    if reason.endswith("session_close") or reason.startswith("push_"):
        return True
    return False


def _reject_session_closing_entry(
    ctx: _PushPipelineContext,
    *,
    sym: str,
    trade: Mapping[str, Any],
    payload: Mapping[str, Any],
    msg_i: int,
    decision: Any = None,
) -> None:
    """Operational boundary reject — not an ENTRY-quality reject."""
    from research.exposure_gate import GateDecision

    if decision is None:
        decision = GateDecision(
            accept=False,
            reason=REJECT_SESSION_CLOSING,
            continuation_quality_score=float(trade.get("continuation_quality_score") or 0),
            quality_tier="",
        )
    rej_row = dict(trade)
    rej_row["gate_reject_reason"] = REJECT_SESSION_CLOSING
    rej_row["final_reject_reason"] = REJECT_SESSION_CLOSING
    rej_row["operational_boundary_reject"] = True
    ctx.state.reject_rows.append(rej_row)
    rej = _event_from_gate(
        event_type="rejected",
        trade=trade,
        decision=decision,
        source=ctx.source,
        message_index=msg_i,
        current_price=payload.get("CurrentPrice") if isinstance(payload, Mapping) else None,
    )
    rej["reject_reason"] = REJECT_SESSION_CLOSING
    rej["operational_boundary_reject"] = True
    ctx.state.events.append(rej)
    ctx.writer.append_event(rej)
    _record_bucket(ctx.state, "rejected")
    # Cost-Aware V2: close後候補は評価対象外として別記録（本線PnLに混ぜない）
    try:
        st_v2 = getattr(ctx.state, "cost_aware_entry_v2_shadow", None)
        if st_v2 is not None:
            n = int(getattr(st_v2, "session_closing_excluded_count", 0) or 0) + 1
            setattr(st_v2, "session_closing_excluded_count", n)
            try:
                st_v2.enqueue_jsonl(
                    {
                        "event": "session_closing_excluded",
                        "symbol": str(sym),
                        "reason": REJECT_SESSION_CLOSING,
                        "message_index": int(msg_i or 0),
                    }
                )
            except Exception:
                pass
    except Exception:
        pass


def _maybe_e1_x13_execution_risk_observer(cand: Any, scan_meta: Optional[Mapping[str, Any]] = None) -> None:
    """E1_X13 OBSERVER_ONLY: opt-in telemetry; never blocks or alters ENTRY decision.

    Enable with env E1_X13_EXECUTION_RISK_OBSERVER=1. Default off. Exceptions swallowed.
    """
    try:
        from research.e1_x13_execution_risk_observer.observer import observer_enabled, observe_candidate
        if not observer_enabled():
            return
        trade = getattr(cand, "trade", None)
        if not isinstance(trade, dict):
            return
        payload = getattr(cand, "payload", None) or {}
        enriched = getattr(cand, "enriched", None) or {}
        sm = dict(scan_meta or {})
        # Fill quote fields onto trade for measure input if missing (non-destructive defaults).
        for src_key, dest_key in (
            ("best_bid", "best_bid"),
            ("best_ask", "best_ask"),
            ("best_bid_qty", "best_bid_qty"),
            ("best_ask_qty", "best_ask_qty"),
            ("reference_price", "reference_price"),
            ("tick_size", "tick_size"),
        ):
            if trade.get(dest_key) is None:
                v = payload.get(src_key) if isinstance(payload, Mapping) else None
                if v is None and isinstance(enriched, Mapping):
                    v = enriched.get(src_key)
                if v is not None:
                    trade[dest_key] = v
        if trade.get("board_age_sec") is None and sm.get("board_age_sec") is not None:
            trade["board_age_sec"] = sm.get("board_age_sec")
        if trade.get("event_time") is None:
            trade["event_time"] = sm.get("entry_signal_ts") or trade.get("entry_time")
        if trade.get("candidate_id") is None:
            trade["candidate_id"] = f"{getattr(cand, 'symbol', '')}|{getattr(cand, 'msg_i', '')}"
        if trade.get("symbol") is None:
            trade["symbol"] = str(getattr(cand, "symbol", "") or "")
        observe_candidate(
            trade,
            rolling={
                "rolling_spread_cost_p95": trade.get("rolling_spread_cost_p95"),
                "rolling_down_bid_jump_p95": trade.get("rolling_down_bid_jump_p95"),
                "rolling_executable_loss_5s_p95": trade.get("rolling_executable_loss_5s_p95"),
                "history_support_status": trade.get("history_support_status") or "RUNTIME_ROLLING_UNRESOLVED",
            },
        )
        tel = trade.get("execution_risk_observer") or {}
        for k in (
            "one_lot_notional_yen", "one_tick_risk_yen_100", "current_spread_cost_yen_100",
            "estimated_execution_risk_yen", "history_support_status", "board_age_sec",
            "measurement_status", "reason_codes", "capital_policy_status",
        ):
            if k in tel:
                trade[k] = tel[k]
    except Exception:
        return


def _process_scan_flush(ctx: _PushPipelineContext, flush: Any) -> None:
    from research.exposure_gate import GateDecision
    import time as _time

    # Phase723: session closing — drop queued accepts; do not CAP-release re-ENTRY.
    if _entry_admission_closed(ctx):
        for cand in list(getattr(flush, "accepted", []) or []):
            _reject_session_closing_entry(
                ctx,
                sym=str(cand.symbol),
                trade=cand.trade,
                payload=cand.payload,
                msg_i=int(cand.msg_i or 0),
            )
        return

    _cost_aware_shadow_on_scan_flush(ctx, flush)

    ol = getattr(ctx.state, "order_latency_dryrun", None)
    flush_start = _time.monotonic() if ol is not None else 0.0

    for cand in flush.accepted:
        if ol is not None:
            ol.mark_flush_start(
                symbol=cand.symbol,
                entry_signal_mono=float(cand.entry_signal_mono or 0.0),
                flush_start_mono=flush_start,
            )
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
        # E1_X13: observer AFTER candidate accepted into flush; does not reject or alter decision.
        _maybe_e1_x13_execution_risk_observer(cand, scan_meta)
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
        if ol is not None:
            ol.finish_max_scan_blocked(
                symbol=cand.symbol,
                entry_signal_mono=float(cand.entry_signal_mono or 0.0),
            )
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
        _notify_entry_blocked_discord(
            ctx,
            sym=cand.symbol,
            trade=cand.trade,
            rej=rej,
            payload=cand.payload,
            enriched=cand.enriched,
            block_reason=REJECT_MAX_ENTRIES_PER_SCAN,
            score5_ord=cand.score5_ord,
        )


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


def _ensure_evaluation_reachability(ctx: _PushPipelineContext) -> Any:
    from small_paper.evaluation_reachability import EvaluationReachabilityTracker

    if ctx.evaluation_reachability is None:
        ctx.evaluation_reachability = EvaluationReachabilityTracker()
    try:
        ctx.state._evaluation_reachability_tracker = ctx.evaluation_reachability  # type: ignore[attr-defined]
    except Exception:
        pass
    return ctx.evaluation_reachability


def _reachability_update_from_push(
    ctx: _PushPipelineContext,
    payload: Mapping[str, Any],
    *,
    symbol: str,
    reference_now: Optional[datetime] = None,
    feature_complete: bool = False,
) -> None:
    """Always update per-symbol price/board/history readiness (even if eval throttled)."""
    tracker = _ensure_evaluation_reachability(ctx)
    ring = ctx.symbol_price_ring.get(symbol) or []
    hist_ticks = len(ring)
    tracker.update_from_payload(
        symbol,
        payload,
        reference_now=reference_now,
        feature_complete=feature_complete,
        history_ticks=hist_ticks,
        min_history_ticks=int(getattr(ctx.feature_bridge, "min_ticks_for_complete", 3) or 3),
    )


def _throttled_state_only_push(
    ctx: _PushPipelineContext,
    payload: Mapping[str, Any],
    *,
    symbol: str,
) -> None:
    """Phase687W43F: update rings/features/readiness without candidate evaluation.

    E1_X5 FeatureEngine + EXIT must still see every push (Offline parity).
    Score/ENTRY remain gated inside the E1 provider (5s + state_change).
    PBv2 candidate eval stays behind should_evaluate — unchanged.
    """
    from small_paper.extended_entry_shadow import append_price_tick, tick_ts_from_payload

    try:
        px_tick = float(payload.get("CurrentPrice") or 0)
    except (TypeError, ValueError):
        px_tick = 0.0
    if px_tick > 0:
        ring = ctx.symbol_price_ring.setdefault(symbol, [])
        append_price_tick(ring, ts=tick_ts_from_payload(payload), px=px_tick)
    feature_complete = False
    try:
        snap = ctx.feature_bridge.update(symbol, payload)
        feature_complete = bool(getattr(snap, "live_feature_complete", False))
    except Exception:
        pass
    ref = _replay_reference_now(ctx, payload)
    _reachability_update_from_push(
        ctx,
        payload,
        symbol=symbol,
        reference_now=ref,
        feature_complete=feature_complete,
    )
    _sync_reachability_summary(ctx)
    # E1_X5 dense path: every push, independent of PBv2 5s eval gate.
    try:
        from small_paper.e1_x5_decision_core import feed_e1_x5_from_runtime_state

        feed_e1_x5_from_runtime_state(ctx.state, symbol=symbol, payload=payload)
    except Exception:
        pass


def _sync_reachability_summary(
    ctx: _PushPipelineContext, *, finalize: bool = False
) -> None:
    tracker = getattr(ctx, "evaluation_reachability", None)
    if tracker is None:
        return
    ctx.state.evaluation_reachability_summary = tracker.summary_fields(finalize=finalize)
    elig = ctx.entry_eligible_symbols
    if elig is not None:
        ctx.state.evaluation_reachability_summary["universe_active_symbol_count"] = int(len(elig))
    # Candidate / accept counts for Discord summary (from existing state)
    ctx.state.evaluation_reachability_summary["candidate_count"] = int(
        sum(1 for e in ctx.state.events if e.get("event_type") == "candidate")
    )
    ctx.state.evaluation_reachability_summary["gate_accepted_count"] = int(len(ctx.state.accepted_rows))
    ctx.state.evaluation_reachability_summary["official_entry_count"] = int(
        sum(1 for r in ctx.state.accepted_rows if r.get("position_registered") or r.get("official_entry"))
    )


def _stage0_normalize_payload(
    ctx: _PushPipelineContext,
    payload: Mapping[str, Any],
    msg_i: int,
    *,
    symbol: Optional[str] = None,
    t0_push_received_at: Optional[str] = None,
    t0_mono: Optional[float] = None,
    eval_mono: Optional[float] = None,
) -> Optional[Stage0NormalizedPayload]:
    """Phase629 Stage0: Payload Normalize.

    enrich_payload / candidate generation / price ring (+ scan begin flush,
    tick counters). Code moved verbatim from _process_push_payload.

    eval_mono: optional deterministic clock for entry-scan batching (push-replay
    passes recorded_at epoch so flush boundaries do not depend on wall-clock
    Stage overhead — Phase629A).
    """
    import time

    eval_start_mono = float(eval_mono) if eval_mono is not None else time.monotonic()
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
        from small_paper.np_pre_entry_feature_logger import (
            append_board_snap,
            extract_board_snap,
            np_pre_entry_feature_logger_enabled,
        )

        if np_pre_entry_feature_logger_enabled(ctx.config):
            snap = extract_board_snap(payload, ts=tick_ts_from_payload(payload))
            if snap is not None:
                board_ring = ctx.symbol_board_ring.setdefault(sym, [])
                append_board_snap(board_ring, snap)
        or_st = getattr(ctx.state, "or_overlay", None)
        if or_st is not None:
            or_st.record_day_tick(
                sym,
                current_price=px_tick,
                prev_close=_as_float(payload.get("PreviousClose")),
            )
        _pullback_volume_forward_on_push(ctx, symbol=sym, payload=payload, px_tick=px_tick)

    snapshot = ctx.feature_bridge.update(sym, payload)
    slm_guard = getattr(ctx.gate, "stop_low_mfe_guard", None)
    if slm_guard is not None:
        slm_guard.ingest_push(sym, payload)
    enriched = ctx.feature_bridge.enrich_payload(payload, snapshot)
    if t0_push_received_at and not enriched.get("recorded_at"):
        enriched["recorded_at"] = t0_push_received_at
    # Canonical quote normalize once at Stage0 (additive keys; raw Bid/Ask preserved).
    from small_paper.canonical_board import attach_canonical_board

    attach_canonical_board(
        enriched,
        payload,
        event_id=f"{sym}:{msg_i}:{t0_push_received_at or ''}",
        received_at=str(t0_push_received_at or enriched.get("recorded_at") or ""),
    )
    if prof is not None:
        prof.mark("enrich_done")
    if bus is not None:
        bus.mark_payload_parsed()
    elif lt is not None:
        lt.mark_payload_parsed()
    ol = _order_latency_session(ctx)
    if ol is not None:
        ol.mark_enrich_end()
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
    return Stage0NormalizedPayload(
        symbol=sym,
        msg_i=msg_i,
        payload=payload,
        enriched=enriched,
        trade=trade,
        snapshot=snapshot,
        bucket=bucket,
        scan_id=scan_id,
        eval_start_ts=eval_start_ts,
        eval_start_mono=eval_start_mono,
        t0_push_received_at=t0_push_received_at,
        t0_mono=t0_mono,
    )


def _observer_open_position_tick(
    ctx: _PushPipelineContext, norm: Stage0NormalizedPayload
) -> Optional[ObserverCloseOnPush]:
    """Held-position tick dispatch (EXIT hot path). Runs between Stage0 and Stage1.

    Not an ENTRY stage; kept as a separate step so the original execution order
    is preserved exactly (Phase629 moved this code verbatim).

    Phase687W22B: returns close metadata for the current message_index when an
    EXIT occurs on this symbol (used only to skip same-PUSH re-ENTRY after
    no_progress_exit). No durable/global cooloff state.
    """
    from small_paper.observer_position_tracker import OBSERVER_EXIT

    sym = norm.symbol
    trade = norm.trade
    payload = norm.payload
    enriched = norm.enriched
    msg_i = norm.msg_i
    bucket = norm.bucket
    close_info: Optional[ObserverCloseOnPush] = None
    # V1R dual-lane always ticks independently (even if classic observer closed)
    try:
        from small_paper.v1r_live_dual_lane import ensure_dual_lane, live_primary_enabled

        if live_primary_enabled():
            td = _v1r_native_writer_output_dir(ctx)
            dual = ensure_dual_lane(trace_dir=td)
            if dual is not None:
                if dual.error_sink is None:

                    def _dual_err(rec: Mapping[str, Any]) -> None:
                        try:
                            ctx.writer.append_error(dict(rec))
                        except Exception:
                            pass
                        ctx.state.v1r_native_exception_count = int(
                            getattr(ctx.state, "v1r_native_exception_count", 0) or 0
                        ) + 1
                        if rec.get("error_type") in (
                            "v1r_dual_lane_symbol_lookup_mismatch",
                            "v1r_dual_lane_exception",
                        ):
                            ctx.state.v1r_native_entry_blocked = True
                            ctx.state.v1r_native_block_reason = str(
                                rec.get("message") or rec.get("error_type") or "dual_fail"
                            )

                    dual.set_error_sink(_dual_err)
                seq = int(payload.get("sequence") or payload.get("Sequence") or msg_i or 0)
                dual.on_push_meta(
                    sequence=seq, push_at=str(payload.get("CurrentPriceTime") or _now_iso())
                )
                from small_paper.v1r_native_entry_live import board_event_epoch_from_payload

                pay_for_t = dict(enriched if isinstance(enriched, dict) else payload or {})
                t0_recv = getattr(norm, "t0_push_received_at", None)
                if t0_recv and not pay_for_t.get("recorded_at"):
                    pay_for_t["recorded_at"] = t0_recv
                if t0_recv and not pay_for_t.get("received_at"):
                    pay_for_t["received_at"] = t0_recv
                et = board_event_epoch_from_payload(pay_for_t)
                dual.on_tick(
                    symbol=sym,
                    payload=enriched if isinstance(enriched, dict) else dict(payload or {}),
                    event_t=et,
                    push_sequence=seq,
                )
    except Exception as exc:
        _log_v1r_native_entry_exception(
            ctx,
            exc,
            where="dual_lane_on_tick",
            symbol=str(sym or ""),
            message_index=msg_i,
        )
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
        for ev in obs_events:
            if getattr(ev, "kind", None) != OBSERVER_EXIT:
                continue
            ctx_map = getattr(ev, "context", None) or {}
            reason = str(ctx_map.get("exit_reason") or "")
            close_info = ObserverCloseOnPush(
                closed_symbol=str(getattr(ev, "symbol", "") or sym),
                close_reason=reason,
                close_message_index=int(msg_i),
                close_event_time=str(
                    ctx_map.get("exit_time") or ctx_map.get("timestamp") or _now_iso()
                ),
            )
            break
    return close_info


def _should_skip_same_push_reentry_after_no_progress(
    close_info: Optional[ObserverCloseOnPush],
    *,
    symbol: str,
    message_index: int,
) -> bool:
    """True when no_progress EXIT and ENTRY would share the same message_index."""
    if close_info is None:
        return False
    if str(close_info.close_reason) != "no_progress_exit":
        return False
    if str(close_info.closed_symbol) != str(symbol):
        return False
    return int(close_info.close_message_index) == int(message_index)


def _record_same_push_reentry_skip(
    ctx: _PushPipelineContext,
    norm: Stage0NormalizedPayload,
    close_info: ObserverCloseOnPush,
) -> None:
    """Audit-only reject row; does not run Stage1–5 / gate / observer register."""
    from research.exposure_gate import GateDecision

    trade = norm.trade
    decision = GateDecision(
        accept=False,
        reason=SAME_PUSH_REENTRY_AFTER_NO_PROGRESS_EXIT,
        continuation_quality_score=float(trade.get("continuation_quality_score") or 0),
        quality_tier="",
    )
    row = _event_from_gate(
        event_type="rejected",
        trade=trade,
        decision=decision,
        source=ctx.source,
        message_index=norm.msg_i,
        current_price=norm.payload.get("CurrentPrice"),
    )
    row["final_reject_reason"] = SAME_PUSH_REENTRY_AFTER_NO_PROGRESS_EXIT
    row["gate_reject_reason"] = SAME_PUSH_REENTRY_AFTER_NO_PROGRESS_EXIT
    row["same_push_reentry_skip"] = True
    row["closed_symbol"] = close_info.closed_symbol
    row["close_reason"] = close_info.close_reason
    row["close_message_index"] = close_info.close_message_index
    row["close_event_time"] = close_info.close_event_time
    ctx.state.events.append(row)
    ctx.writer.append_event(row)
    _record_bucket(ctx.state, "rejected")
    # Count for session diagnostics (optional attr; never required for correctness)
    try:
        n = int(getattr(ctx.state, "same_push_reentry_skip_count", 0) or 0)
        setattr(ctx.state, "same_push_reentry_skip_count", n + 1)
    except Exception:
        pass
    # Discord Summary audit only — does not affect trading
    try:
        from pathlib import Path

        from small_paper.daily_symbol_discord_state import get_daily_symbol_state

        get_daily_symbol_state(
            native_root=Path(__file__).resolve().parents[2]
        ).record_same_push_suppression(str(close_info.closed_symbol or ""))
    except Exception:
        pass


def _stage1_evaluate_freshness(
    ctx: _PushPipelineContext, norm: Stage0NormalizedPayload
) -> Stage1FreshnessResult:
    """Phase629 Stage1: Freshness (event/board/trade/tag/reject_reason).

    Includes the pre-freshness short-circuits (am_pm_entry_stop /
    outside_refresh_universe) and the expectancy-score field update, which sat
    immediately before the freshness computation in the original code.
    Code moved verbatim from _process_push_payload.
    """
    sym = norm.symbol
    trade = norm.trade
    enriched = norm.enriched
    scan_id = norm.scan_id
    prof = getattr(ctx, "stage_profiler", None)
    bus = getattr(ctx, "extension_bus", None)
    lt = _latency_trace(ctx)
    from small_paper.am_pm_session_policy import AmPmSessionPolicy

    stale_reason: Optional[str] = None
    policy: Optional[AmPmSessionPolicy] = ctx.am_pm_policy
    # Phase723: session closing / force_close — gate evaluation前に拒否
    if _entry_admission_closed(ctx):
        from research.exposure_gate import GateDecision

        decision = GateDecision(
            accept=False,
            reason=REJECT_SESSION_CLOSING,
            continuation_quality_score=float(trade.get("continuation_quality_score") or 0),
            quality_tier="",
        )
        ref_now = _replay_reference_now(ctx, enriched) or datetime.now(JST)
        return Stage1FreshnessResult(
            ref_now=ref_now,
            stale_reason=stale_reason,
            pre_gate_reason=REJECT_SESSION_CLOSING,
            short_circuit_decision=decision,
        )
    # Phase722: while WS DEGRADED, block new ENTRY only (EXIT path still active).
    if bool(getattr(ctx.state, "entry_blocked_degraded", False) or getattr(ctx.state, "websocket_degraded", False)):
        from research.exposure_gate import GateDecision

        decision = GateDecision(
            accept=False,
            reason="push_degraded_entry_block",
            continuation_quality_score=float(trade.get("continuation_quality_score") or 0),
            quality_tier="",
        )
        ref_now = _replay_reference_now(ctx, enriched) or datetime.now(JST)
        return Stage1FreshnessResult(
            ref_now=ref_now,
            stale_reason=stale_reason,
            pre_gate_reason="push_degraded_entry_block",
            short_circuit_decision=decision,
        )
    if policy is not None and not policy.entry_allowed_now():
        from research.exposure_gate import GateDecision

        decision = GateDecision(
            accept=False,
            reason="am_pm_entry_stop",
            continuation_quality_score=float(trade.get("continuation_quality_score") or 0),
            quality_tier="",
        )
        ref_now = _replay_reference_now(ctx, enriched) or datetime.now(JST)
        return Stage1FreshnessResult(
            ref_now=ref_now,
            stale_reason=stale_reason,
            pre_gate_reason="am_pm_entry_stop",
            short_circuit_decision=decision,
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
        ref_now = _replay_reference_now(ctx, enriched) or datetime.now(JST)
        return Stage1FreshnessResult(
            ref_now=ref_now,
            stale_reason=stale_reason,
            pre_gate_reason=REJECT_OUTSIDE_REFRESH_UNIVERSE,
            short_circuit_decision=decision,
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
        # Phase687W43F: use carried-forward last board/price times (thresholds unchanged)
        try:
            from small_paper.evaluation_reachability import merge_freshness_snapshot_with_state

            tracker = _ensure_evaluation_reachability(ctx)
            ov = tracker.freshness_overrides(sym)
            freshness = merge_freshness_snapshot_with_state(
                freshness,
                last_price_update_ts=ov.get("last_price_update_ts"),
                last_board_update_ts=ov.get("last_board_update_ts"),
                reference_now=ref_now,
                tracker=tracker,
            )
        except Exception:
            pass
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
            return Stage1FreshnessResult(
                ref_now=ref_now,
                freshness=freshness,
                freshness_decision=freshness_decision,
                stale_reason=stale_reason,
                short_circuit_decision=decision,
            )
        return Stage1FreshnessResult(
            ref_now=ref_now,
            freshness=freshness,
            freshness_decision=freshness_decision,
            stale_reason=stale_reason,
        )


def _stage2_evaluate_pbv2(
    ctx: _PushPipelineContext, norm: Stage0NormalizedPayload
) -> Stage2PBv2Result:
    """Phase629 Stage2: PBv2 (GateDecision: accept/reason/audit/score/internal reason).

    The full ExposureGate chain runs here, which INCLUDES the entry cluster
    guard (Phase549/627) — Stage3 classifies its outcome without re-running it.
    The returned GateDecision is immutable by convention: no later stage
    mutates it. Code moved verbatim from _process_push_payload.
    """
    sym = norm.symbol
    trade = norm.trade
    payload = norm.payload
    prof = getattr(ctx, "stage_profiler", None)
    bus = getattr(ctx, "extension_bus", None)
    lt = _latency_trace(ctx)
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
    return Stage2PBv2Result(
        decision=pbv2_decision,
        internal_reason=str(trade.get("pbv2_internal_reason", "") or ""),
        internal_gate=str(trade.get("pbv2_internal_gate", "") or ""),
    )


def _stage3_cluster_decision(
    norm: Stage0NormalizedPayload, pbv2: Stage2PBv2Result
) -> Stage3ClusterDecision:
    """Phase629 Stage3: Cluster Guard decision (FEATURE_INCOMPLETE / REJECT / PASS).

    Read-only classification of the cluster-guard outcome that the ExposureGate
    chain produced during Stage2. No side effects, no decision changes.
    """
    return classify_cluster_stage(pbv2.decision, norm.trade)


def _stage4_finalize_decision(
    ctx: _PushPipelineContext,
    norm: Stage0NormalizedPayload,
    fresh: Stage1FreshnessResult,
    pbv2: Optional[Stage2PBv2Result],
) -> Stage4FinalEntryDecision:
    """Phase629 Stage4: OR Overlay -> FinalEntryDecision (PBv2 / OR / Reject).

    The PBv2 internal reason is never modified here (Phase627 guarantee).
    Code moved verbatim from _process_push_payload.
    """
    sym = norm.symbol
    trade = norm.trade
    payload = norm.payload
    or_overlay_reason = ""
    if pbv2 is None:
        # Pre-gate or stale short-circuit: OR overlay was never attempted.
        decision = fresh.short_circuit_decision
        entry_route = "stale_reject" if fresh.stale_reason else "pre_gate_reject"
    else:
        pbv2_decision = pbv2.decision
        decision = _maybe_try_or_overlay_entry(
            ctx,
            sym=sym,
            trade=trade,
            payload=payload,
            pbv2_decision=pbv2_decision,
        )
        if decision is not pbv2_decision and not decision.accept:
            trade["or_overlay_reason"] = str(getattr(decision, "reason", "") or "")
            or_overlay_reason = trade["or_overlay_reason"]
        if decision.accept:
            entry_route = "pbv2" if decision is pbv2_decision else "or"
        else:
            entry_route = "reject"
    final_reject_reason = ""
    if not decision.accept:
        trade["final_reject_reason"] = str(getattr(decision, "reason", "") or "")
        final_reject_reason = trade["final_reject_reason"]
    ctx.state.gate_evaluations += 1
    return Stage4FinalEntryDecision(
        decision=decision,
        entry_route=entry_route,
        or_overlay_reason=or_overlay_reason,
        final_reject_reason=final_reject_reason,
        stale_reason=fresh.stale_reason,
    )


def _stage6_record_candidate(
    ctx: _PushPipelineContext,
    norm: Stage0NormalizedPayload,
    fresh: Stage1FreshnessResult,
    final: Stage4FinalEntryDecision,
) -> Stage6CandidateRecord:
    """Phase629 Stage6 (part 1): candidate event write / profiler / latency trace /
    entry_scan audit / ExtensionBus on_post_eval.

    Runs before Stage5 for accepted candidates and before the reject recording,
    exactly matching the original ordering. Code moved verbatim from
    _process_push_payload.
    """
    import time

    sym = norm.symbol
    trade = norm.trade
    payload = norm.payload
    enriched = norm.enriched
    msg_i = norm.msg_i
    scan_id = norm.scan_id
    eval_start_ts = norm.eval_start_ts
    eval_start_mono = norm.eval_start_mono
    decision = final.decision
    stale_reason = final.stale_reason
    freshness_decision = fresh.freshness_decision
    prof = getattr(ctx, "stage_profiler", None)
    bus = getattr(ctx, "extension_bus", None)
    lt = _latency_trace(ctx)

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

        ref_now = fresh.ref_now or _replay_reference_now(ctx, enriched) or datetime.now(JST)
        try:
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
            # cost_aware_entry_shadow: note every Watch50 eval (not only official accept)
            try:
                from small_paper.cost_aware_entry_shadow import (
                    CostAwareShadowState,
                    note_symbol_eval,
                    shadow_enabled,
                )

                if shadow_enabled(getattr(ctx, "config", None)):
                    st = getattr(ctx.state, "cost_aware_entry_shadow", None)
                    if st is None:
                        st = CostAwareShadowState()
                        ctx.state.cost_aware_entry_shadow = st
                    trade_for_shadow = dict(trade)
                    if payload.get("CurrentPrice") is not None:
                        trade_for_shadow.setdefault("CurrentPrice", payload.get("CurrentPrice"))
                    note_symbol_eval(
                        st,
                        scan_id=str(scan_id or ""),
                        symbol=str(sym),
                        trade=trade_for_shadow,
                        official_accept=bool(decision.accept),
                    )
            except Exception:
                pass
            # Stage6: Cost-Aware V2 notes at ACCEPT only (see accept path).
            if bus is not None:
                bus.on_post_eval(
                    ctx,
                    sym=sym,
                    trade=trade,
                    decision=decision,
                    timestamp=eval_end_ts,
                )
        except Exception as exc:
            _record_pipeline_logging_error(
                ctx, stage="stage6_record_candidate", exc=exc, symbol=sym
            )
    return Stage6CandidateRecord(
        score5_ord=score5_ord,
        eval_end_ts=eval_end_ts,
        eval_latency_ms=eval_latency_ms,
    )


def _stage5_execute_entry(
    ctx: _PushPipelineContext,
    norm: Stage0NormalizedPayload,
    final: Stage4FinalEntryDecision,
    rec: Stage6CandidateRecord,
) -> None:
    """Phase629 Stage5: Entry Execute (queue / flush / register_entry / positions /
    accepted rows). Code moved verbatim from _process_push_payload accept branch.
    """
    sym = norm.symbol
    trade = norm.trade
    payload = norm.payload
    enriched = norm.enriched
    msg_i = norm.msg_i
    bucket = norm.bucket
    scan_id = norm.scan_id
    eval_start_ts = norm.eval_start_ts
    eval_start_mono = norm.eval_start_mono
    decision = final.decision
    score5_ord = rec.score5_ord
    eval_end_ts = rec.eval_end_ts
    eval_latency_ms = rec.eval_latency_ms
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
        ol = _order_latency_session(ctx)
        if ol is not None:
            ol.mark_queue_enqueue(entry_signal_mono=eval_start_mono, scan_id=scan_id or "")
        flush_now = ctx.entry_scan.maybe_flush_after_eval()
        if flush_now is not None:
            _process_scan_flush(ctx, flush_now)
    else:
        ol = _order_latency_session(ctx)
        if ol is not None:
            ol.mark_direct_execute(entry_signal_mono=eval_start_mono)
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


def _stage6_record_reject(
    ctx: _PushPipelineContext,
    norm: Stage0NormalizedPayload,
    final: Stage4FinalEntryDecision,
    rec: Stage6CandidateRecord,
) -> None:
    """Phase629 Stage6 (part 2): reject row / rejected event / Discord notify.

    Code moved verbatim from the _process_push_payload reject branch.
    """
    sym = norm.symbol
    trade = norm.trade
    payload = norm.payload
    enriched = norm.enriched
    msg_i = norm.msg_i
    decision = final.decision
    score5_ord = rec.score5_ord
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
        try:
            pv = _ensure_pullback_volume_forward(ctx.state, ctx.config)
            if pv is not None and getattr(pv, "enabled", False):
                from small_paper.pullback_volume_forward_logger import build_entry_row

                build_entry_row(
                    pv,
                    {**trade, **pb_fields},
                    official_entry=False,
                    official_reject=True,
                    session=str(getattr(ctx.state, "session_kind", "") or ""),
                    trading_date=str(getattr(pv, "trading_date", "") or ""),
                )
        except Exception:
            pass
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
    if decision.reason == "flat_band_mainline":
        from small_paper.pbv2_flat_band_entry_guard import (
            LOG_EVENT_KIND as FB_LOG_EVENT_KIND,
            REJECT_FLAT_BAND_MAINLINE,
            compute_flat_band_mainline_fields,
        )

        ctx.state.pbv2_flat_band_mainline_reject_count += 1
        ctx.state.pbv2_flat_band_mainline_reject_symbols.add(sym)
        fb_fields = compute_flat_band_mainline_fields(ctx.config, trade)
        trade.update(fb_fields)
        rej_row.update(fb_fields)
        rej_row["reject_reason"] = REJECT_FLAT_BAND_MAINLINE
        ctx.writer.append_error(
            {
                "event_kind": FB_LOG_EVENT_KIND,
                "symbol": sym,
                "pbv2_flat_band_shadow_reason": getattr(
                    decision, "pbv2_flat_band_shadow_reason", fb_fields.get("pbv2_flat_band_shadow_reason", "")
                ),
                "pbv2_flat_band_rise5": getattr(
                    decision, "pbv2_flat_band_rise5", fb_fields.get("pbv2_flat_band_rise5")
                ),
                "pbv2_flat_band_rise10": getattr(
                    decision, "pbv2_flat_band_rise10", fb_fields.get("pbv2_flat_band_rise10")
                ),
                "pbv2_flat_band_variant": getattr(
                    decision, "pbv2_flat_band_variant", fb_fields.get("pbv2_flat_band_variant", "")
                ),
                "reject_reason": REJECT_FLAT_BAND_MAINLINE,
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
    block_reason = str(decision.reason or "")
    try:
        if _is_entry_stop_pre_gate_reason(block_reason):
            sid = _entry_stop_source_event_id(
                symbol=sym,
                reason=block_reason,
                message_index=msg_i,
                entry_time=trade.get("entry_time"),
            )
            if _entry_stop_reject_already_logged(ctx, sid):
                ol = _order_latency_session(ctx)
                if ol is not None:
                    ol.finish_reject(
                        gate_reason=block_reason or "reject",
                        entry_route=str(getattr(final, "entry_route", None) or "reject"),
                    )
                return
        if block_reason == REJECT_SESSION_CLOSING:
            rej_row["operational_boundary_reject"] = True
            rej_row["final_reject_reason"] = REJECT_SESSION_CLOSING
            rej_row["gate_reject_reason"] = REJECT_SESSION_CLOSING
        ctx.state.reject_rows.append(rej_row)
        rej = _event_from_gate(
            event_type="rejected",
            trade=trade,
            decision=decision,
            source=ctx.source,
            message_index=msg_i,
            current_price=payload.get("CurrentPrice"),
        )
        if block_reason == REJECT_SESSION_CLOSING:
            rej["reject_reason"] = REJECT_SESSION_CLOSING
            rej["operational_boundary_reject"] = True
            # Cost-Aware V2: exclude from research evaluation pool
            try:
                st_v2 = getattr(ctx.state, "cost_aware_entry_v2_shadow", None)
                if st_v2 is not None:
                    n = int(getattr(st_v2, "session_closing_excluded_count", 0) or 0) + 1
                    setattr(st_v2, "session_closing_excluded_count", n)
            except Exception:
                pass
        if _is_entry_stop_pre_gate_reason(block_reason):
            cand_ev = None
            for ev in reversed(ctx.state.events):
                if ev.get("event_type") == "candidate" and ev.get("symbol") == sym:
                    cand_ev = ev
                    break
            _annotate_entry_stop_reject_event(
                ctx,
                rej,
                reason=block_reason,
                symbol=sym,
                message_index=msg_i,
                trade=trade,
                cand=cand_ev,
            )
        ctx.state.events.append(rej)
        ctx.writer.append_event(rej)
        _record_bucket(ctx.state, "rejected")
        if _is_entry_stop_pre_gate_reason(block_reason):
            ctx.state.entry_stop_reject_logging_recovered_count += 1
        # Session-closing rejects: no ENTRY Discord and no reject Discord spam.
        if block_reason == REJECT_SESSION_CLOSING:
            pass
        elif is_entry_blocked_discord_notify_reason(block_reason):
            _notify_entry_blocked_discord(
                ctx,
                sym=sym,
                trade=trade,
                rej=rej,
                payload=payload,
                enriched=enriched,
                block_reason=block_reason,
                score5_ord=score5_ord,
            )
        elif ctx.discord and ctx.discord.active:
            ctx.discord.notify_rejected(
                event=rej,
                payload=enriched,
                open_slots=_active_cap_count(ctx),
                session_bucket=session_bucket(),
            )
    except Exception as exc:
        _record_pipeline_logging_error(
            ctx, stage="stage6_record_reject", exc=exc, symbol=sym
        )
    ol = _order_latency_session(ctx)
    if ol is not None:
        ol.finish_reject(
            gate_reason=block_reason or "reject",
            entry_route=str(getattr(final, "entry_route", None) or "reject"),
        )


def _warmup_ring_only_push(
    ctx: _PushPipelineContext,
    payload: Mapping[str, Any],
    msg_i: int,
    *,
    symbol: Optional[str] = None,
) -> None:
    """Phase645: ring/tick update only — no gate evaluation or ENTRY during warmup."""
    sym = symbol or _symbol_from_push(payload, ctx.code_to_symbol)
    if not sym:
        return
    ctx.state.push_messages = msg_i
    ctx.state.pre_session_warmup_ring_push_count += 1
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
        from small_paper.np_pre_entry_feature_logger import (
            append_board_snap,
            extract_board_snap,
            np_pre_entry_feature_logger_enabled,
        )

        if np_pre_entry_feature_logger_enabled(ctx.config):
            snap = extract_board_snap(payload, ts=tick_ts_from_payload(payload))
            if snap is not None:
                board_ring = ctx.symbol_board_ring.setdefault(sym, [])
                append_board_snap(board_ring, snap)
        or_st = getattr(ctx.state, "or_overlay", None)
        if or_st is not None:
            or_st.record_day_tick(
                sym,
                current_price=px_tick,
                prev_close=_as_float(payload.get("PreviousClose")),
            )
        _pullback_volume_forward_on_push(ctx, symbol=sym, payload=payload, px_tick=px_tick)
    # E1_X5 FeatureEngine warmup during ring-only (ENTRY still gated by provider/session).
    try:
        from small_paper.e1_x5_decision_core import feed_e1_x5_from_runtime_state

        feed_e1_x5_from_runtime_state(ctx.state, symbol=sym, payload=payload)
    except Exception:
        pass


def _process_push_payload(
    ctx: _PushPipelineContext,
    payload: Mapping[str, Any],
    msg_i: int,
    *,
    symbol: Optional[str] = None,
    t0_push_received_at: Optional[str] = None,
    t0_mono: Optional[float] = None,
    eval_mono: Optional[float] = None,
) -> None:
    """Phase629 ENTRY pipeline orchestrator (Stage0..Stage6).

    Structure-only refactoring: every stage function contains the original
    _process_push_payload code moved verbatim; execution order, side effects
    and outputs are identical to the pre-Phase629 single-function version.
    Stages exchange data exclusively through the Stage* dataclasses.

    eval_mono: optional deterministic scan clock (push-replay recorded_at epoch).
    """
    from small_paper.pre_session_warmup import ring_only_warmup_active

    warmup_now: Optional[datetime] = None
    for raw in (
        t0_push_received_at,
        payload.get("recorded_at") if isinstance(payload, Mapping) else None,
        payload.get("CurrentPriceTime") if isinstance(payload, Mapping) else None,
    ):
        if not raw:
            continue
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=JST)
            warmup_now = dt.astimezone(JST)
            break
        except Exception:
            continue
    if ring_only_warmup_active(
        config=ctx.config, am_pm_policy=ctx.am_pm_policy, now=warmup_now
    ):
        _warmup_ring_only_push(ctx, payload, msg_i, symbol=symbol)
        return

    if ctx.state.first_gate_eval_ts is None:
        ctx.state.first_gate_eval_ts = _now_iso()

    trace = StageTraceLogger(symbol=symbol or "", msg_i=msg_i)
    trace.start("stage0_payload_normalize")
    ol = _order_latency_session(ctx)
    if ol is not None:
        import time as _time

        ol.begin_push(
            symbol=symbol or "",
            payload=payload,
            message_index=msg_i,
            t1_push_received_at=t0_push_received_at,
            t2_mono=t0_mono if t0_mono is not None else _time.monotonic(),
        )
    norm = _stage0_normalize_payload(
        ctx,
        payload,
        msg_i,
        symbol=symbol,
        t0_push_received_at=t0_push_received_at,
        t0_mono=t0_mono,
        eval_mono=eval_mono,
    )
    trace.end("stage0_payload_normalize", note="no_symbol" if norm is None else "")
    if norm is None:
        return
    if trace.enabled:
        trace.symbol = norm.symbol
    # Phase687W43F: readiness after Stage0 state/history update (never before)
    try:
        feat_ok = bool((norm.enriched or {}).get("live_feature_complete"))
        _reachability_update_from_push(
            ctx,
            norm.enriched or payload,
            symbol=norm.symbol,
            reference_now=_replay_reference_now(ctx, norm.enriched or payload),
            feature_complete=feat_ok,
        )
    except Exception:
        pass
    close_info = _observer_open_position_tick(ctx, norm)
    # V1R-native Primary ENTRY: board ingest + anchor fire + pending fill (independent of PBv2)
    try:
        from small_paper.v1r_live_dual_lane import live_primary_enabled
        from small_paper.v1r_native_entry_live import ensure_native_entry, get_native_entry

        if live_primary_enabled() and not getattr(ctx.state, "v1r_native_entry_blocked", False):
            eng = get_native_entry()
            if eng is None:
                eng = ensure_native_entry(
                    universe=list(getattr(ctx.state, "v1r_day_fixed_universe", None) or []),
                    trace_dir=_v1r_native_writer_output_dir(ctx),
                )
            elif eng.trace_dir is None:
                td = _v1r_native_writer_output_dir(ctx)
                if td is not None:
                    eng.trace_dir = td
            if eng.ready and eng.universe:
                from small_paper.v1r_native_entry_live import board_event_epoch_from_payload

                # Causal Capture/ingress clock (recorded_at/received_at) — not consumer wall.
                pay_for_t = dict(norm.enriched or norm.payload or {})
                if t0_push_received_at and not pay_for_t.get("recorded_at"):
                    pay_for_t["recorded_at"] = t0_push_received_at
                if t0_push_received_at and not pay_for_t.get("received_at"):
                    pay_for_t["received_at"] = t0_push_received_at
                et = board_event_epoch_from_payload(pay_for_t)
                eng.ingest_push(
                    symbol=norm.symbol,
                    payload=pay_for_t,
                    event_t=et,
                )
                eng.maybe_fire_anchor(now_t=et)
                eng.on_tick_fill_check(event_t=et, payload=pay_for_t)
                # PBv2 shadow Discord digest: flush on 5m / fixed-anchor boundary
                # (evaluation continues; this only gates trade-research Discord).
                try:
                    from small_paper.v1r_pbv2_shadow_discord_digest import (
                        get_pbv2_shadow_discord_digest,
                    )

                    get_pbv2_shadow_discord_digest(
                        trace_dir=_v1r_native_writer_output_dir(ctx)
                    ).maybe_flush(
                        trading_date=str(getattr(ctx.state, "trading_date", "") or ""),
                        open_n=int(eng.shadow_pbv2.open_n),
                        cap=int(eng.shadow_pbv2.cap),
                    )
                except Exception:
                    pass
            else:
                # Fail-closed: native ENTRY not ready → no Primary (PBv2 already diverted)
                if not getattr(ctx.state, "v1r_native_entry_blocked", False):
                    ctx.state.v1r_native_entry_blocked = True
                    ctx.state.v1r_native_block_reason = eng.fail_reason or "NOT_READY"
                    ctx.writer.append_error(
                        {
                            "event_time": _now_iso(),
                            "error_type": "v1r_native_entry_runtime",
                            "where": "push_hook_not_ready",
                            "message": "NO PAPER PRIMARY — native ENTRY not ready",
                            "push_sequence": getattr(norm, "msg_i", None),
                            "symbol": getattr(norm, "symbol", ""),
                            "native_engine_state": eng.snapshot(),
                        }
                    )
    except Exception as exc:
        _log_v1r_native_entry_exception(
            ctx,
            exc,
            where="push_native_entry_hook",
            symbol=str(getattr(norm, "symbol", "") or ""),
            message_index=getattr(norm, "msg_i", None),
        )
    if _should_skip_same_push_reentry_after_no_progress(
        close_info, symbol=norm.symbol, message_index=norm.msg_i
    ):
        # Phase687W22B Part A: EXIT already dispatched; skip Stage1+ ENTRY on this PUSH.
        assert close_info is not None
        _record_same_push_reentry_skip(ctx, norm, close_info)
        return
    trace.start("stage1_freshness")
    fresh = _stage1_evaluate_freshness(ctx, norm)
    if ol is not None:
        ol.mark_freshness_end()
    trace.end("stage1_freshness", note=fresh.pre_gate_reason or (fresh.stale_reason or ""))
    # Phase687W43F: record evaluation attempt / recovery consumption
    try:
        import time as _time

        tracker = _ensure_evaluation_reachability(ctx)
        cycle = getattr(ctx, "_current_evaluation_cycle_id", None) or f"{norm.symbol}:{msg_i}"
        market_ts = float(eval_mono) if eval_mono is not None else None
        stale_rej = bool(fresh.stale_reason)
        st = tracker.get(norm.symbol)
        tracker.mark_evaluated(
            norm.symbol,
            now_mono=_time.monotonic(),
            market_ts=market_ts,
            cycle_id=str(cycle),
            fresh_ok=not stale_rej and fresh.short_circuit_decision is None,
            stale_reject=stale_rej,
            price_state_updated_at=getattr(st, "price_state_updated_at", None),
            board_state_updated_at=getattr(st, "board_state_updated_at", None),
            history_updated_at=getattr(st, "history_ready_at", None),
            feature_computed_at=getattr(st, "history_ready_at", None),
        )
        _sync_reachability_summary(ctx)
    except Exception:
        pass
    pbv2: Optional[Stage2PBv2Result] = None
    if fresh.short_circuit_decision is None:
        trace.start("stage2_pbv2")
        pbv2 = _stage2_evaluate_pbv2(ctx, norm)
        trace.end("stage2_pbv2", note=str(getattr(pbv2.decision, "reason", "") or ""))
        trace.start("stage3_cluster_guard")
        cluster = _stage3_cluster_decision(norm, pbv2)
        trace.end("stage3_cluster_guard", note=cluster.status)
    trace.start("stage4_or_overlay")
    final = _stage4_finalize_decision(ctx, norm, fresh, pbv2)
    if ol is not None:
        ol.mark_decision_end(
            accepted=bool(final.decision.accept),
            entry_route=str(final.entry_route or ""),
            gate_reason=str(final.decision.reason or ""),
        )
    trace.end("stage4_or_overlay", note=final.entry_route)
    trace.start("stage6_post_entry")
    rec = _stage6_record_candidate(ctx, norm, fresh, final)
    trace.end("stage6_post_entry", note="candidate_recorded")
    if final.decision.accept:
        trace.start("stage5_entry_execute")
        _stage5_execute_entry(ctx, norm, final, rec)
        trace.end("stage5_entry_execute")
    else:
        trace.start("stage6_post_entry")
        _stage6_record_reject(ctx, norm, final, rec)
        trace.end("stage6_post_entry", note="reject_recorded")


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

    peak_hint = summary.get("observer_open_max_positions")
    if peak_hint is None:
        peak_hint = summary.get("peak_open_slots")
    return enrich_summary_with_canonical(
        summary,
        events,
        peak_open_slots=int(peak_hint) if peak_hint is not None else None,
        max_concurrent_positions=config.max_concurrent_positions,
        watch_symbols_count=watch_symbols_count,
    )


def _entry_stage_summary_fields(state: _LiveRunState) -> dict[str, Any]:
    counters = getattr(state, "entry_stage_counters", None)
    if counters is None:
        return {"accepted_count_source": "gate_accepted"}
    out = counters.summary_fields()
    # Keep legacy accepted_count; clarify it is gate-level.
    out["accepted_count_note"] = (
        "accepted_count remains gate_accepted_count for compatibility; "
        "use position_registered_count / official_entry_count for official ENTRY metrics"
    )
    return out


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
    base.update(_cost_aware_entry_shadow_summary_fields(state))
    base.update(_cost_aware_entry_v2_shadow_summary_fields(state))
    base.update(_e1_x5_forward_shadow_summary_fields(state))
    base.update(_board_imbalance_reversal_shadow_summary_fields(state))
    base.update(_pullback_volume_forward_summary_fields(state))
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
    base.update(_pbv2_flat_band_mainline_summary_fields(gate, state))
    base.update(_late_chase_guard_summary_fields(gate, state))
    base.update(_classic_late_chase_rsi_guard_summary_fields(gate, state))
    base.update(_reentry_rsi_guard_summary_fields(gate, state))
    base.update(_entry_quality_guard_summary_fields(gate, state))
    base.update(_entry_cluster_guard_summary_fields(gate, state))
    base.update(_gate_dominance_alert_fields(state))
    base.update(_stop_low_mfe_guard_summary_fields(gate, state))
    base.update(_board_entry_summary_fields(state))
    base.update(_pullback_misread_entry_guard_shadow_summary_fields(state))
    base.update(_pbv2_rise5_shadow_summary_fields(state))
    base.update(_pbv2_flat_band_shadow_summary_fields(state))
    base.update(_flat_weak_range_forward_shadow_summary_fields(state))
    base.update(_readiness_forward_shadow_summary_fields(state))
    base.update(_microsequence_recovery_fail_forward_shadow_summary_fields(state))
    base.update(_ihc_shadow_portfolio_summary_fields(state))
    base.update(_np_pre_entry_feature_logger_summary_fields(state))
    base.update(_entry_expectancy_score_summary_fields(state))
    base.update(_freshness_semantics_v2_summary_fields(config, state))
    base.update(_or_overlay_summary_fields(config, state))
    base.update(_observer_exit_pnl_summary_fields(state.events))
    base.update(_entry_stop_reject_logging_summary_fields(state))
    base.update(_entry_stage_summary_fields(state))
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


def _entry_order_path_allowed(acc: Mapping[str, Any]) -> bool:
    from small_paper.entry_execution_integrity import is_official_entry_ready

    if acc.get("accept_aborted"):
        return False
    if not bool(acc.get("execution_payload_validated")):
        return False
    return is_official_entry_ready(acc)


def _maybe_record_live_order_pipeline_entry(
    ctx: _PushPipelineContext,
    *,
    sym: str,
    trade: Mapping[str, Any],
    payload: Mapping[str, Any],
    acc: Mapping[str, Any],
    scan_meta: Optional[Mapping[str, Any]] = None,
) -> None:
    if not _entry_order_path_allowed(acc):
        return
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
    if not _entry_order_path_allowed(acc):
        return
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
    if not _entry_order_path_allowed(acc):
        return
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
    if not _entry_order_path_allowed(acc):
        return
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
            latency_session=getattr(ctx.state, "order_latency_dryrun", None),
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


def _maybe_record_live_order_safety_entry(
    ctx: "_PushPipelineContext",
    *,
    sym: str,
    trade: Mapping[str, Any],
    payload: Mapping[str, Any],
    acc: dict[str, Any],
) -> None:
    """Phase687W4: actual accepted ENTRY → SafetySM dry-run intent (no real submit)."""
    if not _entry_order_path_allowed(acc):
        return
    try:
        from small_paper.live_order_runtime_bridge import ENTRY_SOURCE_ACTUAL, safety_sm_enabled
        from small_paper.entry_execution_integrity import finite_positive

        bridge = getattr(ctx.state, "live_order_safety_bridge", None)
        if bridge is None or not safety_sm_enabled(ctx.config):
            return
        # No AskPrice/CalcPrice fallback — only validated CurrentPrice/entry_price.
        entry_px = finite_positive(acc.get("validated_entry_price"))
        if entry_px is None:
            entry_px = finite_positive(trade.get("entry_price"))
        if entry_px is None:
            entry_px = finite_positive(payload.get("CurrentPrice"))
        if entry_px is None or entry_px <= 0:
            return
        position_id = str(acc.get("position_id") or trade.get("position_id") or "")
        if not position_id:
            return
        bridge.on_actual_entry(
            symbol=sym,
            price=entry_px,
            position_id=position_id,
            accepted_at=str(acc.get("accepted_at") or ""),
            signal_event_id=str(trade.get("entry_time") or position_id),
            source_kind=ENTRY_SOURCE_ACTUAL,
            timestamps={
                "push_received_mono": float(acc.get("push_received_mono") or 0.0),
                "accepted_mono": float(acc.get("accepted_mono") or 0.0),
            },
            freshness={
                "price_age_sec": acc.get("price_age_sec"),
                "board_age_sec": acc.get("board_age_sec"),
                "market_event_age_sec": acc.get("market_event_age_sec"),
            },
        )
    except Exception as exc:
        writer = getattr(ctx, "writer", None)
        if writer is not None:
            try:
                writer.append_error(
                    {
                        "event_time": _now_iso(),
                        "component": "live_order_safety_sm_entry",
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
        from small_paper.live_order_runtime_bridge import EXIT_SOURCE_ACTUAL, safety_sm_enabled

        bridge = getattr(state, "live_order_safety_bridge", None)
        if bridge is not None and safety_sm_enabled(config) and context.get("is_structural_exit"):
            qty = context.get("quantity") or context.get("shares") or context.get("lot_size")
            try:
                qty_i = int(qty) if qty is not None else None
            except (TypeError, ValueError):
                qty_i = None
            bridge.on_actual_exit(
                symbol=symbol,
                position_id=str(context.get("position_id") or context.get("entry_time") or symbol),
                exit_reason=str(context.get("exit_reason") or context.get("reason") or ""),
                holding_quantity=qty_i,
                exit_signal_at=str(context.get("exit_time") or _now_iso()),
                is_structural_exit=True,
                source_kind=EXIT_SOURCE_ACTUAL,
            )
    except Exception as exc:
        if writer is not None:
            try:
                writer.append_error(
                    {
                        "event_time": _now_iso(),
                        "component": "live_order_safety_sm_exit",
                        "symbol": symbol,
                        "error": str(exc),
                    }
                )
            except Exception:
                pass
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
    _init_pbv2_rise5_shadow(replay_config, state)
    _init_pbv2_flat_band_shadow(replay_config, state)
    _init_flat_weak_range_forward_shadow(replay_config, state)
    _init_readiness_forward_shadow(replay_config, state)
    _init_microsequence_recovery_fail_forward_shadow(replay_config, state)
    _init_ihc_shadow_portfolio(replay_config, state)
    _init_np_pre_entry_feature_logger(replay_config, state)
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
                        "error_type": str(extra.get("error_type") or "discord_error"),
                        "operation": extra.get("operation") or op,
                        "message": msg,
                        **{k: v for k, v in dict(extra).items() if k not in ("error_type", "operation")},
                    }
                )

            def _entry_delivery_audit(record: Mapping[str, Any]) -> None:
                writer.append_discord_entry_delivery(record)

            discord = discord_notifier_from_pilot(
                replay_config,
                error_logger=_discord_error_logger,
                delivery_audit=_entry_delivery_audit,
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
        push_payload = dict(payload)
        if recorded_at:
            push_payload["recorded_at"] = recorded_at
        replay_mono = _parse_recorded_at_ts(recorded_at) if recorded_at else None
        # Phase687W43F: state update when throttled; full Stage0 only when evaluating
        try:
            tracker = _ensure_evaluation_reachability(ctx)
            ref = _replay_reference_now(ctx, push_payload)
            _reachability_update_from_push(
                ctx, push_payload, symbol=sym, reference_now=ref
            )
            ts = float(replay_mono) if replay_mono else 0.0
            do_eval, _skip, cycle_id = tracker.should_evaluate(
                sym,
                now_mono=ts if ts else time.monotonic(),
                market_ts=ts if ts else None,
                poll_interval_sec=float(poll_interval_sec or 0),
                ring_only_warmup=False,
            )
            if not do_eval:
                _throttled_state_only_push(ctx, push_payload, symbol=sym)
                continue
            ctx._current_evaluation_cycle_id = cycle_id  # type: ignore[attr-defined]
            if ts:
                last_eval_ts[sym] = ts
        except Exception:
            if poll_interval_sec > 0:
                ts = _parse_recorded_at_ts(recorded_at)
                prev = last_eval_ts.get(sym)
                if prev is not None and (ts - prev) < poll_interval_sec:
                    continue
                last_eval_ts[sym] = ts
        # Phase629A: drive entry-scan batch windows from market time so Stage
        # call overhead cannot change flush boundaries / accepted counts.
        _process_push_payload(
            ctx,
            push_payload,
            msg_i,
            symbol=sym,
            eval_mono=replay_mono if replay_mono else None,
        )
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
    try:
        _sync_reachability_summary(ctx, finalize=True)
    except Exception:
        pass
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
    if discord:
        summary.update(discord_notify_summary_fields(discord))
    if discord and discord.active:
        summary.update(
            build_session_summary_extras(
                accepted_rows=state.accepted_rows,
                bucket_summary=state.bucket_summary,
                observer_stats=_observer_stats_dict(observer),
            )
        )
    _attach_canonical_summary_fields(summary, state.events, config=config)
    # Phase687W10A: Shadow finalize BEFORE session-end notify (RESEARCH_SHADOW hook).
    _apply_quality_formula_shadow_finalize(state, summary)
    _apply_trading_value_shadow_finalize(state, summary)
    _apply_board_imbalance_shadow_finalize(state, summary)
    _apply_entry_expectancy_score_shadow_finalize(state, summary)
    _apply_ihc_shadow_counterfactual_finalize(state, summary, output_dir=output_dir, config=replay_config)
    _apply_post_entry_forward_shadow_finalize(state, summary, output_dir=output_dir)
    _apply_classic_momentum_forward_shadow_finalize(state, summary, output_dir=output_dir)
    _apply_e1_x5_forward_shadow_finalize(state, summary, output_dir=output_dir)
    try:
        notify_discord_session_end(
            discord,
            events=state.events,
            summary=summary,
            reject_rows=state.reject_rows,
            ux_stats=state.discord_ux,
            native_root=Path(__file__).resolve().parents[2],
            output_dir=output_dir,
        )
    except Exception as exc:
        log.warning("discord session_end notify failed: %s", exc)
        summary["discord_session_end_error"] = str(exc)
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
    # Phase687W34: AM/PM Summary + Shadow hooks require am_pm_session on summary
    # (live_session_config already has it; previously omitted → daily_summary forever-DEDUPED).
    if session_cfg.get("am_pm_session"):
        summary["am_pm_session"] = dict(session_cfg.get("am_pm_session") or {})
    if session_cfg.get("universe_mode"):
        summary.setdefault("universe_mode", session_cfg.get("universe_mode"))
    try:
        from datetime import datetime as _dt
        from zoneinfo import ZoneInfo as _ZI

        summary.setdefault("trading_date", _dt.now(_ZI("Asia/Tokyo")).strftime("%Y%m%d"))
    except Exception:
        pass
    summary.update(_policy_summary_extras(config))
    summary.update(_symbol_cooloff_summary_fields(gate, state))
    summary.update(_daytrade_suitability_summary_fields(gate, state))
    summary.update(_entry_price_risk_guard_summary_fields(gate, state))
    summary.update(_execution_audit_fields(config, session_cfg))
    if getattr(config, "low_liquidity_shadow_enabled", False):
        summary["low_liquidity_shadow_reject_count"] = state.low_liquidity_shadow_reject_count
    summary.update(_intraday_refresh_summary_fields(state))
    summary.update(_extended_shadow_summary_fields(state))
    summary.update(_cost_aware_entry_shadow_summary_fields(state))
    summary.update(_cost_aware_entry_v2_shadow_summary_fields(state))
    summary.update(_e1_x5_forward_shadow_summary_fields(state))
    summary.update(_board_imbalance_reversal_shadow_summary_fields(state))
    summary.update(_pullback_volume_forward_summary_fields(state))
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
    summary.update(_pbv2_flat_band_mainline_summary_fields(gate, state))
    summary.update(_late_chase_guard_summary_fields(gate, state))
    summary.update(_classic_late_chase_rsi_guard_summary_fields(gate, state))
    summary.update(_reentry_rsi_guard_summary_fields(gate, state))
    summary.update(_entry_quality_guard_summary_fields(gate, state))
    summary.update(_entry_cluster_guard_summary_fields(gate, state))
    summary.update(_gate_dominance_alert_fields(state))
    summary.update(_stop_low_mfe_guard_summary_fields(gate, state))
    summary.update(_board_entry_summary_fields(state))
    summary.update(_pullback_misread_entry_guard_shadow_summary_fields(state))
    summary.update(_pbv2_rise5_shadow_summary_fields(state))
    summary.update(_pbv2_flat_band_shadow_summary_fields(state))
    summary.update(_flat_weak_range_forward_shadow_summary_fields(state))
    summary.update(_entry_expectancy_score_summary_fields(state))
    summary.update(_freshness_semantics_v2_summary_fields(config, state))
    # Phase687W43F evaluation reachability metrics
    ers = dict(getattr(state, "evaluation_reachability_summary", None) or {})
    if ers:
        summary["evaluation_reachability"] = ers
        summary["universe_active_symbol_count"] = ers.get("universe_active_symbol_count")
        summary["push_received_symbol_count"] = ers.get("push_received_symbol_count")
        summary["price_ready_symbol_count"] = ers.get("price_ready_symbol_count")
        summary["board_ready_symbol_count"] = ers.get("board_ready_symbol_count")
        summary["history_ready_symbol_count"] = ers.get("history_ready_symbol_count")
        summary["feature_ready_symbol_count"] = ers.get("feature_ready_symbol_count")
        summary["evaluation_ready_symbol_count"] = ers.get("evaluation_ready_symbol_count")
        summary["evaluation_attempted_count"] = ers.get("evaluation_attempted_count")
        summary["evaluation_skipped_not_ready_count"] = ers.get("evaluation_skipped_not_ready_count")
        summary["evaluation_skipped_stale_count"] = ers.get("evaluation_skipped_stale_count")
        summary["evaluation_recovery_triggered_count"] = ers.get("evaluation_recovery_triggered_count")
        summary["pipeline_integrity_error_count"] = ers.get("pipeline_integrity_error_count")
        summary["false_board_stale_prevented_count"] = ers.get("false_board_stale_prevented_count")
        summary["ready_transition_count"] = ers.get("ready_transition_count")
        summary["ready_transition_missing_evaluation_count"] = ers.get(
            "ready_transition_missing_evaluation_count"
        )
        summary["recovery_missing_evaluation_count"] = ers.get("recovery_missing_evaluation_count")
        summary["ready_evaluation_coverage"] = ers.get("ready_evaluation_coverage")
        summary["recovery_evaluation_coverage"] = ers.get("recovery_evaluation_coverage")
    summary.update(_or_overlay_summary_fields(config, state))
    summary.update(_observer_exit_pnl_summary_fields(state.events))
    summary.update(_entry_stop_reject_logging_summary_fields(state))
    summary.update(_entry_stage_summary_fields(state))
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
    from small_paper.order_latency_dryrun_trace import order_latency_dryrun_summary_fields

    summary.update(order_latency_dryrun_summary_fields(getattr(state, "order_latency_dryrun", None)))
    from small_paper.pre_session_warmup import warmup_summary_fields

    summary.update(
        warmup_summary_fields(
            config=config,
            state=state,
            am_pm_policy=session_cfg.get("am_pm_session"),
            trade_date=datetime.now(JST).date(),
        )
    )
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
    import os
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

    from small_paper.pre_session_warmup import apply_init_wait, resolve_warmup_init_plan

    trade_date = now.date()
    init_plan = resolve_warmup_init_plan(
        config=config,
        full_session=full_session,
        wait_until_session=wait_until_session,
        session_start=session_start,
        trade_date=trade_date,
        am_pm_policy=am_pm_policy,
        now=now,
    )
    apply_init_wait(init_plan)

    if full_session and auto_stop:
        duration_sec = sched.seconds_until_end()

    from small_paper.day_fixed_am_registration import SAME_DAY_AM_FROZEN_AUTHORITY
    from small_paper.kabu_registration_authority import (
        NO_REGISTERED_KABU_PROBE_SYMBOL,
        resolve_registered_probe_symbol,
    )

    probe_day = now.strftime("%Y%m%d")
    probe = resolve_registered_probe_symbol(Path(native_root), probe_day)
    if not probe.get("ok"):
        raise KabuNativeApiError(str(probe.get("reason") or NO_REGISTERED_KABU_PROBE_SYMBOL))
    probe_key = str(probe.get("symbol_key") or probe.get("kabu_probe_symbol") or "")
    conn = verify_kabu_connection(
        repo_root,
        symbol_key=probe_key,
        native_root=native_root,
        trading_date=probe_day,
    )
    conn["kabu_probe_symbol"] = probe_key
    conn["kabu_probe_symbol_registered"] = bool(probe.get("kabu_probe_symbol_registered"))
    conn["kabu_probe_symbol_frozen_member"] = bool(probe.get("kabu_probe_symbol_frozen_member"))
    conn["probe_source"] = str(probe.get("probe_source") or SAME_DAY_AM_FROZEN_AUTHORITY)
    conn["registration_mutation"] = int(probe.get("registration_mutation") or 0)
    from small_paper.core_runtime_mode import get_core_runtime_mode, log_core_runtime_mode
    from small_paper.market_ingress_protocol import market_ingress_v2_enabled

    log_core_runtime_mode(config)
    rest = KabuNativeRestClient(default_base_url())
    token = rest.issue_token_from_env()
    from api.order_read_client import KabuOrderReadClient

    capital_read_client = KabuOrderReadClient(default_base_url())
    # MARKET_INGRESS_V2: Paper does NOT own Kabu WebSocket (Ingress does).
    ingress_v2 = market_ingress_v2_enabled()
    push: Any = None
    bus_bridge: Any = None
    if not ingress_v2:
        push = KabuNativePushClient(rest, token)
    else:
        from small_paper.consumer_ack_state import resolve_resume_ack
        from small_paper.local_market_bus import RESUME_MODE_CONTINUE
        from small_paper.paper_market_bus_consumer import PaperMarketBusBridge

        _day_yyyymmdd = datetime.now(JST).strftime("%Y%m%d")
        _resume_ack, _resume_src = resolve_resume_ack(
            native_root=native_root,
            ingress_session_id="",
            trading_date=_day_yyyymmdd,
            ingress_hint_ack=0,
        )
        bus_bridge = PaperMarketBusBridge(
            consumer_id="paper_runtime",
            resume_mode=RESUME_MODE_CONTINUE,
            resume_from_ack=int(_resume_ack or 0),
            native_root=native_root,
            trading_date=_day_yyyymmdd,
        )
        print(
            f"[INGRESS_V2] ack_resume_seed={_resume_ack} source={_resume_src}",
            flush=True,
        )

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
    _init_order_latency_dryrun(config, state, output_dir)
    _init_position_cap_tracking(config, state)
    _init_extension_stack_for_mode(config, state, repo_root=repo_root)
    state.live_capital_read_client = capital_read_client
    state.live_capital_api_token = token
    _init_live_order_safety_sm(config, state, output_dir=output_dir, session_id=output_dir.name)
    _init_or_overlay_tracking(config, state)
    _init_pbv2_rise5_shadow(config, state)
    _init_pbv2_flat_band_shadow(config, state)
    _init_flat_weak_range_forward_shadow(config, state)
    _init_readiness_forward_shadow(config, state)
    _init_microsequence_recovery_fail_forward_shadow(config, state)
    _init_ihc_shadow_portfolio(config, state)
    _init_np_pre_entry_feature_logger(config, state)
    pos_fields = ["symbol", "entry_time", "exit_time", "open_slots_after"]
    gap_threshold_sec = max(stale_tick_sec * 2, poll_interval_sec * 3)
    pipeline_ctx: Optional[_PushPipelineContext] = None

    discord: Optional[SmallPaperDiscordNotifier] = None
    observer: Optional[ObserverPositionTracker] = None
    if config.discord_observer_only and not config.order_enabled:
        observer = _make_observer_tracker(config, state, am_pm_policy=am_pm_policy)
        state.observer_tracker = observer
        from small_paper.observer_session_scope import build_observer_session_scope

        observer_scope = build_observer_session_scope(
            output_dir=output_dir,
            trade_date=datetime.now(JST).date(),
            am_pm_policy=am_pm_policy,
        )
        observer.bind_session(observer_scope)
        state.observer_session_id = observer_scope.session_id
        if config.discord_enabled:

            def _discord_error_logger(op: str, msg: str, extra: Mapping[str, Any]) -> None:
                writer.append_error(
                    {
                        "event_time": _now_iso(),
                        "error_type": str(extra.get("error_type") or "discord_error"),
                        "operation": extra.get("operation") or op,
                        "message": msg,
                        **{k: v for k, v in dict(extra).items() if k not in ("error_type", "operation")},
                    }
                )

            def _entry_delivery_audit(record: Mapping[str, Any]) -> None:
                writer.append_discord_entry_delivery(record)

            discord = discord_notifier_from_pilot(
                config,
                error_logger=_discord_error_logger,
                delivery_audit=_entry_delivery_audit,
            )
        # Phase687W58: one-shot Forward observer status (Paper live only)
        _notify_forward_observers_startup_once(state, discord, config)
        if discord is not None and discord.active and am_pm_policy is not None:
            sk = str(getattr(am_pm_policy, "kind", "am")).lower()
            screening_label = "PM Screening" if sk == "pm" else "AM Screening"
            watch_syms = sorted({str(sym) for sym, _, _ in symbols})
            day_stamp = datetime.now(JST).strftime("%Y%m%d")
            # Phase687W31: before runtime register — prepared only, not SCREENING success
            discord.notify_universe_screening(
                session_label=f"UNIVERSE PREPARED ({screening_label})",
                watch_symbols=watch_syms,
                day_stamp=day_stamp,
                status="登録: 未実施 / Paper: 未稼働",
                generated_at=datetime.now(JST).isoformat(timespec="milliseconds"),
            )
            state._pending_screening_notify = {
                "session_label": screening_label,
                "watch_symbols": watch_syms,
                "day_stamp": day_stamp,
            }

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
    # V1R-native ENTRY wiring: day-fixed AM universe + session output_dir traces
    try:
        from small_paper.v1r_live_dual_lane import ensure_dual_lane, live_primary_enabled

        if live_primary_enabled():
            _init_v1r_native_entry_for_live(
                state=state,
                writer=writer,
                native_root=native_root,
                trading_date=day_compact,
                session_symbols=[t[0] for t in symbols],
            )
            dual = ensure_dual_lane(trace_dir=output_dir)

            def _dual_err(rec: Mapping[str, Any]) -> None:
                try:
                    writer.append_error(dict(rec))
                except Exception:
                    pass
                state.v1r_native_exception_count = int(
                    getattr(state, "v1r_native_exception_count", 0) or 0
                ) + 1

            if dual is not None:
                dual.set_error_sink(_dual_err)
                dual.emit_heartbeat_summary()
    except Exception as exc:
        state.v1r_native_entry_blocked = True
        state.v1r_native_block_reason = f"init:{type(exc).__name__}:{exc}"
        writer.append_error(
            {
                "event_time": _now_iso(),
                "error_type": "v1r_native_entry_boot",
                "exception_type": type(exc).__name__,
                "message": str(exc),
                "where": "run_live_dry_run_init",
            }
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
            if ingress_v2:
                from small_paper.ingress_control_channel import write_desired_universe

                write_desired_universe(
                    native_root,
                    symbols=[s[0] for s in specs],
                    position_symbols=list(open_syms or []),
                    trading_date=datetime.now(JST).strftime("%Y%m%d"),
                )
                reg_meta = {
                    "register_count": len(specs),
                    "owner": "MARKET_INGRESS_SERVICE",
                    "paper_register": "DISABLED",
                }
            else:
                register_symbols_cleared(
                    push,
                    specs,
                    native_root=native_root,
                    trading_date=datetime.now(JST).strftime("%Y%m%d"),
                )
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
            # Phase687W43F: keep history/readiness for continuing symbols; warmup only new
            try:
                tracker = _ensure_evaluation_reachability(pipeline_ctx)
                continuing = set(after_syms) - set(added)
                tracker.mark_subscribed(set(after_syms), continuing=continuing)
                if removed:
                    tracker.mark_unsubscribed(set(removed))
                _sync_reachability_summary(pipeline_ctx)
            except Exception:
                pass
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
            # Phase687W9: publish Paper SoT symbols to registration manifest (Sidecar follower).
            # Does not change universe selection — notify only.
            try:
                from small_paper.market_capture_registration import notify_registration_refresh
                from small_paper.market_capture_sidecar import capture_day_dir

                notify_syms = after_syms or [s[0] for s in specs]
                day = datetime.now(JST).strftime("%Y%m%d")
                notify_registration_refresh(
                    native_root,
                    trading_date=day,
                    new_symbols=notify_syms,
                    previous_symbols=before_syms,
                    universe_path=str(refresh_path),
                    # Ingress owns Kabu PUT; Paper MATCH alone must not set verified
                    verified=False if ingress_v2 else True,
                    capture_day_dir=capture_day_dir(native_root, day),
                )
            except Exception:
                pass
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
        # Phase723: atomic ENTRY lock BEFORE close_all / CAP release / Discord / await.
        state.entry_admission_closed = True
        state.session_force_close_done = True
        _request_stop(am_pm_policy.force_close_reason)
        if observer and observer.open_count() > 0:
            exit_events = observer.close_all(reason=am_pm_policy.force_close_reason)
            _log_and_dispatch_observer_events(
                exit_events,
                discord=discord,
                writer=writer,
                state=state,
                gate=gate,
                source="am_pm_force_close",
                config=config,
            )
        gate.state.open_slots = []

    def _request_stop(reason: str) -> None:
        state.stop_requested = True
        state.stop_reason = reason
        # Any stop closes ENTRY admission (AM/PM close, manual stop, degraded stop).
        state.entry_admission_closed = True

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
        close_due = bool(am_pm_policy.force_close_due()) if am_pm_policy is not None else False
        active_pos = int(observer.open_count()) if observer else len(gate.state.open_slots)
        session_state = (
            "force_close_done"
            if state.session_force_close_done
            else ("close_due" if close_due else (state.websocket_state or "running"))
        )
        try:
            from small_paper.ws_freeze_recovery import enrich_heartbeat_fields

            hb_extra = enrich_heartbeat_fields(
                runtime_pid=os.getpid(),
                event_loop_alive=True,
                last_push_at=state.last_push_at,
                last_push_mono=state.last_push_mono,
                websocket_state=state.websocket_state,
                reconnect_attempt=state.reconnect_attempt,
                session_state=session_state,
                active_positions=active_pos,
                close_due=close_due,
                consecutive_recv_timeouts=state.consecutive_recv_timeouts,
                recv_timeout_count=state.recv_timeout_count,
            )
        except Exception:
            hb_extra = {
                "emitted_at": _now_iso(),
                "runtime_pid": os.getpid(),
                "event_loop_alive": True,
                "last_push_at": state.last_push_at,
                "websocket_state": state.websocket_state,
                "active_positions": active_pos,
                "close_due": close_due,
            }
        hb = {
            "event_time": _now_iso(),
            "heartbeat_index": state.heartbeat_count,
            "runtime_sec": round(runtime, 1),
            "push_messages": state.push_messages,
            "gate_evaluations": state.gate_evaluations,
            "api_error_count": state.api_error_count,
            "open_slots": len(gate.state.open_slots),
            "note": note,
            **hb_extra,
        }
        try:
            from small_paper.v1r_live_dual_lane import get_dual_lane, live_primary_enabled
            from small_paper.v1r_native_entry_live import get_native_entry

            if live_primary_enabled():
                dual = get_dual_lane(trace_dir=output_dir)
                eng = get_native_entry()
                # Frozen V1R session-close (AM 11:30 / PM 15:00) — independent of
                # observer.close_all / PBv2 11:25/15:23. Wall clock is live event time.
                try:
                    now_t = time.time()
                    if dual is not None:
                        dual.maybe_session_close(event_t=now_t)
                    if eng is not None:
                        eng.on_tick_fill_check(event_t=now_t)
                except Exception:
                    pass
                if dual is not None:
                    hb["v1r_exit_v2"] = dual.heartbeat_fields()
                    try:
                        dual.emit_heartbeat_summary()
                    except Exception:
                        pass
                if eng is not None:
                    hb["v1r_native_entry"] = eng.heartbeat_fields()
                hb["v1r_native_exception_count"] = int(
                    getattr(state, "v1r_native_exception_count", 0) or 0
                )
                hb["v1r_native_entry_blocked"] = bool(
                    getattr(state, "v1r_native_entry_blocked", False)
                )
        except Exception:
            pass
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
        if discord and discord.active:
            discord.flush_entry_notify_retries()
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
        # Prefer Ingress/Capture received_at (propagated on bus) over consumer wall clock.
        t0_iso = str(
            payload.get("recorded_at")
            or payload.get("received_at")
            or payload.get("__ingress_received_at__")
            or _now_iso()
        )
        t0_m = time.monotonic()
        _process_push_payload(
            pipeline_ctx,
            payload,
            msg_i,
            t0_push_received_at=t0_iso,
            t0_mono=t0_m,
        )

    async def _loop() -> None:
        nonlocal push, token, bus_bridge

        push_rec_local: Any = None
        if config.live_record_push_jsonl:
            from small_paper.async_push_recorder import AsyncPushRecorder

            push_rec_local = AsyncPushRecorder(PushRecorder(native_root, trade_date))
            push_rec_local.start()
        try:
            from api.kabu_register import format_register_failure_message, register_symbols_cleared

            day = datetime.now(JST).strftime("%Y%m%d")
            if ingress_v2:
                # Ingress owns Station register; Paper publishes desired universe only.
                from small_paper.ingress_control_channel import write_desired_universe

                reg_meta = write_desired_universe(
                    native_root,
                    symbols=[s[0] for s in sym_specs],
                    trading_date=day,
                )
                reg_meta = {
                    **reg_meta,
                    "owner": "MARKET_INGRESS_SERVICE",
                    "paper_register": "DISABLED",
                    "ok": True,
                }
                if bus_bridge is not None:
                    bus_bridge.start()
                    # OPEN=0 + large backlog → REALTIME_RESYNC (ACK jump; skip stale ENTRY eval).
                    try:
                        open_n = int(len(gate.state.open_slots))
                        policy_out = bus_bridge.maybe_apply_lag_policy(open_positions=open_n)
                        print(f"[INGRESS_V2] lag_policy={policy_out}", flush=True)
                        if policy_out.get("resync"):
                            (output_dir / "consumer_lag_realtime_resync.json").write_text(
                                json.dumps(policy_out, ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8",
                            )
                    except Exception as pol_exc:
                        _log_api_error("lag_policy_resync", pol_exc)
                    # Architecture boot banner
                    try:
                        print(
                            "Market Data Architecture:\n"
                            "INGRESS_V2\n\n"
                            "WebSocket Owner:\n"
                            "MARKET_INGRESS_SERVICE\n\n"
                            "Runtime Market Source:\n"
                            "LOCAL_MARKET_BUS\n\n"
                            "Capture Source:\n"
                            "INGRESS_RAW_WRITER\n\n"
                            "Legacy Paper WebSocket:\n"
                            "DISABLED\n\n"
                            "submit/cancel/live:\n"
                            "0/0/0",
                            flush=True,
                        )
                    except Exception:
                        pass
            else:
                reg_meta = register_symbols_cleared(
                    push,
                    sym_specs,
                    native_root=native_root,
                    trading_date=day,
                )
            state.session_ready_ts = _now_iso()
            try:
                (output_dir / "register_api_trace.json").write_text(
                    json.dumps(reg_meta, ensure_ascii=False, indent=2, default=str) + "\n",
                    encoding="utf-8",
                )
            except Exception:
                pass
            try:
                from small_paper.market_capture_registration import notify_registration_refresh
                from small_paper.market_capture_sidecar import capture_day_dir

                notify_registration_refresh(
                    native_root,
                    trading_date=day,
                    new_symbols=[s[0] for s in sym_specs],
                    universe_path=str(universe_csv_path or ""),
                    # Ingress owns Kabu PUT; Paper MATCH alone must not set verified
                    verified=False if ingress_v2 else True,
                    capture_day_dir=capture_day_dir(native_root, day),
                )
            except Exception:
                pass
            if reg_meta.get("recovered_from_register_limit") and reg_meta.get("register_recovered"):
                writer.append_error(
                    {
                        "event_time": _now_iso(),
                        "error_type": "register_limit_recovered",
                        "message": "register 4002006 recovered after unregister/all + readback retry",
                        "symbol_count": len(sym_specs),
                        "steps": reg_meta.get("steps"),
                    }
                )
                if discord and discord.active:
                    from small_paper.session_validity import format_register_recovered_discord_lines

                    # PUSH confirmation happens after messages; interim wait notice only
                    discord.notify_error(
                        operation="register",
                        message="\n".join(
                            format_register_recovered_discord_lines(
                                registered=int(reg_meta.get("symbol_count") or len(sym_specs)),
                                expected=len(sym_specs),
                                push_receiving=False,
                            )
                        ),
                        extra={"symbol_count": len(sym_specs), "register_recovered": True},
                    )
            # Phase687W31: official AM/PM Screening only after runtime register success
            pending = getattr(state, "_pending_screening_notify", None)
            if pending and discord and discord.active:
                try:
                    discord.notify_universe_screening(
                        session_label=str(pending.get("session_label") or "AM Screening"),
                        watch_symbols=list(pending.get("watch_symbols") or []),
                        day_stamp=str(pending.get("day_stamp") or ""),
                        status="completed",
                        generated_at=datetime.now(JST).isoformat(timespec="milliseconds"),
                    )
                except Exception:
                    pass
                state._pending_screening_notify = None
            print(
                f"[PAPER TRADE] Runtime registration...PASS "
                f"{int(reg_meta.get('symbol_count') or len(sym_specs))}/{len(sym_specs)}"
                + (" (reused)" if reg_meta.get("reused_existing") else ""),
                flush=True,
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
                from small_paper.session_validity import format_paper_not_running_discord_lines

                discord.notify_error(
                    operation="register",
                    message="\n".join(
                        format_paper_not_running_discord_lines(
                            stop_point="register",
                            push=0,
                            gate=0,
                            capture_status="待機中",
                        )
                        + [
                            "原因: Kabu銘柄登録失敗",
                            f"登録状態: ?/{len(sym_specs)}",
                            "PUSH: 未開始",
                            "ENTRY評価: 未開始",
                            "損益: 無効",
                            "Paper session: INVALID_REGISTER_FAILED",
                            fail_msg,
                        ]
                    ),
                    extra={"symbol_count": len(sym_specs), "stop_reason": "register_failed"},
                )
            _request_stop("register_failed")
            return

        start = time.monotonic()
        last_hb = start
        msg_i = 0
        last_eval: dict[str, float] = {}
        from small_paper.data_path_stall_monitor import (
            DataPathStallMonitor,
            StallMonitorConfig,
            format_process_dead_discord_message,
            format_stall_discord_message,
            format_stall_recovered_discord_message,
        )
        from small_paper.session_schedule import session_bucket as _session_bucket

        data_path_monitor = DataPathStallMonitor(
            StallMonitorConfig(
                heartbeat_sec=float(heartbeat_sec),
                startup_grace_sec=60.0,
                observe_window_sec=60.0,
            )
        )
        data_path_monitor.reset(start_mono=start)

        def _capture_status_for_stall() -> str:
            try:
                from pathlib import Path as _Path

                root = _Path(native_root) if native_root else None
                if root is None:
                    return "不明"
                # Prefer same-day capture_status under data/market_capture/YYYYMMDD
                day = _now_iso()[:10].replace("-", "")
                candidates = [
                    root / "data" / "market_capture" / day / "capture_status.json",
                    root / "runtime" / "market_capture_status.json",
                ]
                for p in candidates:
                    if not p.is_file():
                        continue
                    import json as _json

                    o = _json.loads(p.read_text(encoding="utf-8"))
                    return str(o.get("capture_status") or o.get("status") or "不明")
            except Exception:
                return "不明"
            return "不明"

        def _maybe_notify_data_path_stalled() -> None:
            if not full_session:
                return
            try:
                in_market = bool(sched.is_in_session())
            except Exception:
                in_market = True
            try:
                in_entry = _session_bucket() in ("morning", "afternoon")
            except Exception:
                in_entry = True
            push_n = int(getattr(state, "push_messages", 0) or 0)
            gate_n = int(getattr(state, "gate_evaluations", 0) or 0)
            hb_n = int(getattr(state, "heartbeat_count", 0) or 0)
            snap = data_path_monitor.evaluate(
                mono=time.monotonic(),
                push_messages=push_n,
                gate_evaluations=gate_n,
                heartbeat_count=hb_n,
                in_market_hours=in_market,
                in_entry_hours=in_entry,
                process_alive=True,
            )
            if snap.notify_process_dead:
                writer.append_error(
                    {
                        "event_time": _now_iso(),
                        "error_type": "PAPER_DATA_PATH_STALLED",
                        "message": snap.reason,
                        "monitor_state": snap.state.value,
                        "push_messages": push_n,
                        "gate_evaluations": gate_n,
                        "heartbeat_count": hb_n,
                        "heartbeat_age_sec": snap.heartbeat_age_sec,
                        "push_delta": snap.push_delta,
                        "gate_delta": snap.gate_delta,
                    }
                )
                if discord and discord.active:
                    discord.notify_error(
                        operation="paper_data_path",
                        message=format_process_dead_discord_message(
                            capture_status=_capture_status_for_stall()
                        ),
                        extra={"stop_reason": "PAPER_PROCESS_DEAD"},
                    )
                return
            if snap.notify_stalled:
                writer.append_error(
                    {
                        "event_time": _now_iso(),
                        "error_type": "PAPER_DATA_PATH_STALLED",
                        "message": snap.reason,
                        "monitor_state": snap.state.value,
                        "push_messages": push_n,
                        "gate_evaluations": gate_n,
                        "heartbeat_count": hb_n,
                        "heartbeat_age_sec": snap.heartbeat_age_sec,
                        "push_delta": snap.push_delta,
                        "gate_delta": snap.gate_delta,
                    }
                )
                if discord and discord.active:
                    discord.notify_error(
                        operation="paper_data_path",
                        message=format_stall_discord_message(
                            heartbeat_age_sec=snap.heartbeat_age_sec,
                            push_delta=snap.push_delta,
                            gate_delta=snap.gate_delta,
                            process_alive=snap.process_alive,
                            capture_status=_capture_status_for_stall(),
                        ),
                        extra={"stop_reason": "PAPER_DATA_PATH_STALLED"},
                    )
                return
            if snap.notify_recovered:
                writer.append_error(
                    {
                        "event_time": _now_iso(),
                        "error_type": "PAPER_DATA_PATH_RECOVERED",
                        "message": "push_or_gate_increment_resumed",
                        "monitor_state": snap.state.value,
                        "push_messages": push_n,
                        "gate_evaluations": gate_n,
                        "heartbeat_count": hb_n,
                        "push_delta": snap.push_delta,
                        "gate_delta": snap.gate_delta,
                    }
                )
                if discord and discord.active:
                    discord.notify_error(
                        operation="paper_data_path_recovered",
                        message=format_stall_recovered_discord_message(
                            push_delta=snap.push_delta,
                            gate_delta=snap.gate_delta,
                        ),
                        extra={"stop_reason": "PAPER_DATA_PATH_RECOVERED"},
                    )

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

        from small_paper.ws_freeze_recovery import (
            DEFAULT_LIFECYCLE_INTERVAL_SEC,
            DEGRADED_WS_STATE,
            PUSH_RECONNECT_SILENCE_TIMEOUT,
            ReconnectBudget,
            WS_RECONNECT_EXHAUSTED,
            effective_recv_poll_sec,
            is_lifecycle_tick,
        )

        reconnect_budget = ReconnectBudget()
        recv_poll_eff = effective_recv_poll_sec(poll_interval_sec)

        def _enter_degraded(reason: str) -> None:
            """Phase722: DEGRADED — block ENTRY, keep OPEN, wait for scheduled force_close."""
            state.websocket_degraded = True
            state.entry_blocked_degraded = True
            state.websocket_state = DEGRADED_WS_STATE
            if not state.silence_degraded_logged:
                state.silence_degraded_logged = True
                writer.append_error(
                    {
                        "event_time": _now_iso(),
                        "error_type": reason,
                        "reconnect_count": state.reconnect_count,
                        "last_push_at": state.last_push_at,
                        "action": "DEGRADED_RECONNECT_WAIT",
                        "note": "entry blocked; positions held until scheduled session close",
                    }
                )

        def _clear_degraded_on_push() -> None:
            if state.websocket_degraded or state.entry_blocked_degraded:
                state.websocket_degraded = False
                state.entry_blocked_degraded = False
                state.silence_degraded_logged = False
                state.websocket_state = "receiving"
                writer.append_error(
                    {
                        "event_time": _now_iso(),
                        "error_type": "push_degraded_recovered",
                        "last_push_at": state.last_push_at,
                        "action": "RESUME_EXIT_MONITOR",
                    }
                )

        def _tick_lifecycle(source: str = "") -> None:
            """WS-independent progress: close / stop / heartbeat / reconnect silence."""
            nonlocal last_hb
            state.lifecycle_watcher_ticks += 1
            _maybe_notify_data_path_stalled()
            _maybe_intraday_refresh()
            # Scheduled force_close must run even while DEGRADED (no early Summary).
            _maybe_am_pm_force_close()
            if reconnect_budget.silence_exceeded(
                last_push_mono=state.last_push_mono,
                reconnect_succeeded_mono=state.reconnect_succeeded_mono,
            ):
                # Do NOT _request_stop / close_all / Summary here.
                _enter_degraded(PUSH_RECONNECT_SILENCE_TIMEOUT)
            if (time.monotonic() - last_hb) >= heartbeat_sec:
                _emit_heartbeat(note=f"lifecycle:{source}")
                last_hb = time.monotonic()

        async def _lifecycle_watcher() -> None:
            # Phase675: independent Task — does not wait for PUSH payload yield.
            while not _should_stop():
                try:
                    _tick_lifecycle(source="watcher")
                except Exception as exc:
                    writer.append_error(
                        {
                            "event_time": _now_iso(),
                            "error_type": "lifecycle_watcher_error",
                            "message": str(exc),
                        }
                    )
                await asyncio.sleep(DEFAULT_LIFECYCLE_INTERVAL_SEC)

        async def _reconnect_push() -> bool:
            nonlocal push, token
            ok, reason = reconnect_budget.can_attempt()
            if not ok:
                writer.append_error(
                    {
                        "event_time": _now_iso(),
                        "error_type": WS_RECONNECT_EXHAUSTED,
                        "reconnect_count": state.reconnect_count,
                        "attempts_in_window": reconnect_budget.attempts_in_window,
                        "message": reason,
                        "action": "DEGRADED_RECONNECT_WAIT",
                    }
                )
                # Phase722: do not abort pilot; wait for scheduled force_close.
                _enter_degraded(WS_RECONNECT_EXHAUSTED)
                state.websocket_state = "reconnect_exhausted"
                return False
            attempt_n = reconnect_budget.note_attempt_start()
            state.reconnect_count += 1
            state.reconnect_attempt = attempt_n
            state.websocket_state = "reconnecting"
            writer.append_error(
                {
                    "event_time": _now_iso(),
                    "error_type": "reconnect",
                    "reconnect_count": state.reconnect_count,
                    "reconnect_attempt": attempt_n,
                }
            )
            try:
                from small_paper.registration_lifetime import safe_paper_unregister

                safe_paper_unregister(
                    push,
                    native_root=native_root,
                    paper_session_id=str(getattr(state, "session_id", "") or ""),
                    am_pm=str(getattr(am_pm_policy, "kind", "") or "") if am_pm_policy else "",
                    path_label="reconnect_cleanup",
                )
            except Exception as e:
                _log_api_error("unregister_all", e)
            backoff = reconnect_budget.backoff_sec(attempt_n, float(poll_interval_sec or 5.0))
            # Bound sleep so lifecycle watcher / force_close can still progress via Task.
            slept = 0.0
            while slept < backoff and not state.stop_requested:
                step = min(1.0, backoff - slept)
                await asyncio.sleep(step)
                slept += step
                _tick_lifecycle(source="reconnect_backoff")
            if state.stop_requested:
                return False
            try:
                from api.kabu_register import register_symbols_cleared

                def _sync_register() -> None:
                    nonlocal push, token
                    token = rest.issue_token_from_env()
                    push = KabuNativePushClient(rest, token)
                    register_symbols_cleared(
                        push,
                        sym_specs,
                        clear_first=False,
                        native_root=native_root,
                    )

                await asyncio.wait_for(
                    asyncio.to_thread(_sync_register),
                    timeout=reconnect_budget.attempt_timeout_sec,
                )
            except Exception as e:
                _log_api_error("reconnect_register", e)
                state.websocket_state = "reconnect_failed"
                ok2, _reason2 = reconnect_budget.can_attempt()
                if not ok2:
                    _enter_degraded(WS_RECONNECT_EXHAUSTED)
                return False
            state.consecutive_api_errors = 0
            state.reconnect_succeeded_mono = time.monotonic()
            reconnect_budget.note_success(state.reconnect_succeeded_mono)
            state.websocket_state = "receiving"
            return True

        watcher_task = asyncio.create_task(_lifecycle_watcher())
        try:
            while not _should_stop():
                _tick_lifecycle(source="loop_top")
                if _should_stop():
                    break
                if state.websocket_degraded and state.websocket_state in (
                    "reconnect_exhausted",
                    DEGRADED_WS_STATE,
                    "reconnect_failed",
                ):
                    # Stay alive until scheduled force_close without busy-spinning reconnect.
                    await asyncio.sleep(DEFAULT_LIFECYCLE_INTERVAL_SEC)
                    continue
                state.websocket_state = state.websocket_state if state.websocket_degraded else "receiving"
                try:
                    if ingress_v2:
                        assert bus_bridge is not None
                        _msg_iter = bus_bridge.iter_messages(recv_poll_sec=recv_poll_eff)
                    else:
                        _msg_iter = push.iter_messages(recv_poll_sec=recv_poll_eff)
                    async for payload in _msg_iter:
                        if _should_stop():
                            break
                        if is_lifecycle_tick(payload):
                            state.recv_timeout_count += 1
                            state.consecutive_recv_timeouts = int(
                                payload.get("consecutive_timeouts") or 0
                            )
                            state.last_message_at = _now_iso()
                            _tick_lifecycle(source="recv_timeout")
                            # Ingress ENTRY_BLOCK propagates via bus control envelopes
                            if ingress_v2 and bus_bridge is not None and bus_bridge.entry_blocked:
                                _enter_degraded(bus_bridge.entry_block_reason or "ingress_entry_block")
                            continue
                        if not isinstance(payload, dict):
                            continue
                        # Legacy fanout only when Paper owns WS (V1). V2: Ingress Raw-first.
                        if not ingress_v2:
                            try:
                                from small_paper.paper_capture_fanout import fanout_push_payload

                                fanout_push_payload(payload)
                            except Exception:
                                pass
                        sym = _symbol_from_push(payload, code_to_symbol)
                        if not sym:
                            continue
                        msg_i += 1
                        state.push_messages = max(state.push_messages, msg_i)
                        state.last_push_at = _now_iso()
                        state.last_push_mono = time.monotonic()
                        state.last_message_at = state.last_push_at
                        if state.reconnect_succeeded_mono is not None:
                            reconnect_budget.note_push_resumed()
                            state.reconnect_succeeded_mono = None
                        if state.websocket_degraded or state.entry_blocked_degraded:
                            _clear_degraded_on_push()
                        if push_rec_local:
                            try:
                                from datetime import datetime as _dt

                                _ra_raw = (
                                    payload.get("recorded_at")
                                    or payload.get("received_at")
                                    or payload.get("__ingress_received_at__")
                                )
                                _ra_dt = None
                                if _ra_raw:
                                    try:
                                        _ra_dt = _dt.fromisoformat(
                                            str(_ra_raw).replace("Z", "+00:00")
                                        )
                                        if _ra_dt.tzinfo is None:
                                            _ra_dt = _ra_dt.replace(tzinfo=JST)
                                    except Exception:
                                        _ra_dt = None
                                push_rec_local.append(
                                    sym,
                                    payload,
                                    recorded_at=_ra_dt,
                                    source="live_push",
                                )
                            except Exception as e:
                                _log_api_error("push_recorder", e)
                        # REALTIME_RESYNC warmup: ring update + ACK only (no ENTRY/EXIT eval).
                        if ingress_v2 and bus_bridge is not None and bool(getattr(bus_bridge, "warmup_only", False)):
                            try:
                                _warmup_ring_only_push(
                                    pipeline_ctx, payload, msg_i, symbol=sym
                                )
                            except Exception as warm_exc:
                                _log_api_error("resync_warmup", warm_exc)
                            bus_bridge.ack_processed(payload)
                            warm_n = int(getattr(state, "_resync_warmup_count", 0) or 0) + 1
                            state._resync_warmup_count = warm_n  # type: ignore[attr-defined]
                            # Bounded warmup then release ENTRY gate for fresh PUSH.
                            if warm_n >= 200:
                                try:
                                    bus_bridge.finish_warmup()
                                    print(
                                        f"[INGRESS_V2] warmup_finished after {warm_n} events "
                                        f"ack={bus_bridge.last_ack_sequence}",
                                        flush=True,
                                    )
                                except Exception:
                                    pass
                            if (time.monotonic() - last_hb) >= heartbeat_sec:
                                _emit_heartbeat(note="resync_warmup")
                                last_hb = time.monotonic()
                            continue
                        # lightweight lifecycle on real push (HB / force_close)
                        if (time.monotonic() - last_hb) >= heartbeat_sec:
                            _emit_heartbeat(note="push")
                            last_hb = time.monotonic()
                        _maybe_am_pm_force_close()
                        if _should_stop():
                            break
                        ev_now = time.monotonic()
                        # Phase687W43F: always update state; throttle only evaluation
                        try:
                            from small_paper.pre_session_warmup import ring_only_warmup_active

                            ring_only = ring_only_warmup_active(
                                config=config, am_pm_policy=am_pm_policy, now=datetime.now(JST)
                            )
                            if ring_only:
                                _warmup_ring_only_push(
                                    pipeline_ctx, payload, msg_i, symbol=sym
                                )
                                if ingress_v2 and bus_bridge is not None:
                                    bus_bridge.ack_processed(payload)
                                continue
                            tracker = _ensure_evaluation_reachability(pipeline_ctx)
                            # Timestamp/readiness peek only — avoid double ring/feature update
                            _reachability_update_from_push(
                                pipeline_ctx, payload, symbol=sym, reference_now=datetime.now(JST)
                            )
                            do_eval, _skip_reason, cycle_id = tracker.should_evaluate(
                                sym,
                                now_mono=ev_now,
                                market_ts=None,
                                poll_interval_sec=float(poll_interval_sec or 0),
                                ring_only_warmup=False,
                            )
                            if not do_eval:
                                _throttled_state_only_push(
                                    pipeline_ctx, payload, symbol=sym
                                )
                                if ingress_v2 and bus_bridge is not None:
                                    bus_bridge.ack_processed(payload)
                                continue
                            pipeline_ctx._current_evaluation_cycle_id = cycle_id  # type: ignore[attr-defined]
                        except Exception:
                            if sym in last_eval and (ev_now - last_eval[sym]) < poll_interval_sec:
                                if ingress_v2 and bus_bridge is not None:
                                    bus_bridge.ack_processed(payload)
                                continue
                        last_eval[sym] = ev_now
                        if ingress_v2 and bus_bridge is not None:
                            try:
                                _process_payload(payload, msg_i)
                                bus_bridge.ack_processed(payload)
                            except Exception as proc_exc:
                                bus_bridge.mark_process_error(type(proc_exc).__name__)
                                _log_api_error("ingress_consumer_process", proc_exc)
                        else:
                            _process_payload(payload, msg_i)
                except asyncio.CancelledError:
                    _request_stop("cancelled")
                    break
                except KabuNativeApiError as e:
                    _log_api_error("push_iter", e)
                    state.websocket_state = "error"
                except Exception as e:
                    _log_api_error("push_unexpected", e)
                    state.websocket_state = "unexpected_close"
                if _should_stop():
                    break
                if state.consecutive_api_errors >= max_consecutive_api_errors:
                    break
                if ingress_v2:
                    # Ingress owns reconnect; Paper waits DEGRADED until bus recovers or session close.
                    if bus_bridge is not None and bus_bridge.entry_blocked:
                        _enter_degraded(bus_bridge.entry_block_reason or "ingress_entry_block")
                    await asyncio.sleep(DEFAULT_LIFECYCLE_INTERVAL_SEC)
                    continue
                if not await _reconnect_push():
                    if state.websocket_degraded:
                        # Stay in outer while; top-of-loop DEGRADED wait until force_close.
                        continue
                    break
        finally:
            if push_rec_local is not None and hasattr(push_rec_local, "stop"):
                try:
                    push_rec_local.stop(drain=True, timeout=2.0)
                except Exception:
                    pass
            if ingress_v2 and bus_bridge is not None:
                try:
                    bus_bridge.stop()
                except Exception:
                    pass
            watcher_task.cancel()
            try:
                await watcher_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
            # One last close check before leaving the event loop
            try:
                _maybe_am_pm_force_close()
            except Exception:
                pass
            # Phase723: never flush queued ENTRY after session closing / CAP release.
            if pipeline_ctx.entry_scan is not None and not (
                state.entry_admission_closed or state.session_force_close_done or state.stop_requested
            ):
                final_flush = pipeline_ctx.entry_scan.flush_pending()
                if final_flush is not None:
                    _process_scan_flush(pipeline_ctx, final_flush)
            elif pipeline_ctx.entry_scan is not None:
                # Drop pending without executing accepts.
                try:
                    dropped = pipeline_ctx.entry_scan.flush_pending()
                    if dropped is not None:
                        _process_scan_flush(pipeline_ctx, dropped)  # rejects via admission guard
                except Exception:
                    pass
            try:
                from small_paper.registration_lifetime import safe_paper_unregister

                safe_paper_unregister(
                    push,
                    native_root=native_root,
                    paper_session_id=str(getattr(state, "session_id", "") or ""),
                    am_pm=str(getattr(am_pm_policy, "kind", "") or "") if am_pm_policy else "",
                    path_label="session_finally",
                )
            except Exception:
                # socket close failure is warning-only; finalize must continue
                writer.append_error(
                    {
                        "event_time": _now_iso(),
                        "error_type": "unregister_warning",
                        "message": "safe_paper_unregister failed; continuing finalize",
                    }
                )

    try:
        asyncio.run(_loop())
    except KeyboardInterrupt:
        _request_stop("keyboard_interrupt")

    runtime_sec = time.monotonic() - state.started_mono
    positions = _build_positions_snapshot(state.accepted_rows, gate)
    # Finalize W43F reachability counters (pending ready/recovery → missing if unevaluated)
    try:
        from small_paper.evaluation_reachability import EvaluationReachabilityTracker

        # Find tracker via last pipeline context stored on state if present
        ers_tracker = getattr(state, "_evaluation_reachability_tracker", None)
        if isinstance(ers_tracker, EvaluationReachabilityTracker):
            state.evaluation_reachability_summary = ers_tracker.summary_fields(finalize=True)
    except Exception:
        pass
    summary = _build_live_summary(
        config=config,
        state=state,
        session_cfg=session_cfg,
        gate=gate,
        full_session=full_session,
        runtime_sec=runtime_sec,
    )
    if observer:
        from small_paper.ws_freeze_recovery import normalize_session_close_reason

        final_reason = state.stop_reason or "session_end"
        force_due = bool(am_pm_policy.force_close_due()) if am_pm_policy is not None else False
        am_pm_reason = str(getattr(am_pm_policy, "force_close_reason", "") or "") if am_pm_policy else ""
        if am_pm_policy and not state.session_force_close_done and force_due:
            final_reason = am_pm_reason or final_reason
        # Phase722: never Discord-notify with raw push_* / silence reasons.
        final_reason = normalize_session_close_reason(
            final_reason,
            am_pm_force_close_reason=am_pm_reason,
            force_close_due=force_due or bool(state.session_force_close_done),
        )
        state.stop_reason = final_reason
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
    # Phase687W4S: persist SafetySM soak snapshot (no strategy change)
    # Phase687W32: do NOT seal here — summary/events/journals must be final first.
    try:
        bridge = getattr(state, "live_order_safety_bridge", None)
        if bridge is not None:
            from small_paper.live_order_runtime_bridge import write_soak_session_snapshot

            write_soak_session_snapshot(
                bridge,
                output_dir=output_dir / "live_order_safety",
                canonical_entry_count=len(state.accepted_rows),
                canonical_exit_count=sum(
                    1
                    for e in state.events
                    if str(e.get("event") or e.get("kind") or "") in ("OBSERVER_EXIT", "exit")
                    or e.get("is_structural_exit")
                ),
            )
            try:
                import json as _json

                from small_paper.operational_recovery import (
                    disk_usage_pct,
                    finalize_session_manifest,
                )

                safety_dir = output_dir / "live_order_safety"
                man_path = safety_dir / "session_manifest.json"
                already = False
                if man_path.is_file():
                    try:
                        prev = _json.loads(man_path.read_text(encoding="utf-8"))
                        already = bool(prev.get("sealed")) and prev.get("session_seal_status") in (
                            "SEALED",
                            "SEALED_VALID",
                        )
                    except Exception:
                        already = False
                if not already:
                    eng = getattr(bridge, "engine", None)
                    submit_n = 0
                    cancel_n = 0
                    leak_n = 0
                    intent_n = 0
                    seq_end = 0
                    if eng is not None:
                        submit_attr = getattr(eng, "actual_broker_submit_count", 0)
                        submit_n = int(submit_attr() if callable(submit_attr) else (submit_attr or 0))
                        br = getattr(eng, "broker", None)
                        cancel_n = int(getattr(br, "actual_broker_cancel_count", 0) or 0)
                        ledger = getattr(eng, "ledger", None)
                        if ledger is not None and hasattr(ledger, "leak_count"):
                            leak_n = int(ledger.leak_count())
                        intent_n = len(getattr(eng, "orders", {}) or {})
                        store = getattr(eng, "store", None)
                        if store is not None:
                            seq_end = int(getattr(store, "_seq", 0) or 0)
                    exit_n = sum(
                        1
                        for e in state.events
                        if str(e.get("event") or e.get("kind") or "") in ("OBSERVER_EXIT", "exit")
                        or e.get("is_structural_exit")
                    )
                    finalize_session_manifest(
                        safety_dir,
                        canonical_entry_count=len(state.accepted_rows),
                        canonical_exit_count=exit_n,
                        safety_sm_signal_count=int(getattr(bridge, "actual_entry_signal_count", 0) or 0)
                        + int(getattr(bridge, "actual_exit_signal_count", 0) or 0),
                        intent_count=intent_n,
                        submit_count=submit_n,
                        cancel_count=cancel_n,
                        reservation_leak=leak_n,
                        reconciliation_mismatch=int(
                            (getattr(bridge, "startup_recon", {}) or {}).get("diff_count") or 0
                        ),
                        kill_switch_events=1 if getattr(eng, "kill_switch", False) else 0,
                        journal_sequence_end=seq_end,
                        snapshot_completeness="COMPLETE",
                        session_seal_status="PENDING_SEAL",
                    )
                    if man_path.is_file():
                        man = _json.loads(man_path.read_text(encoding="utf-8"))
                        man["disk_usage_end"] = disk_usage_pct(output_dir)
                        try:
                            start = float(man.get("disk_usage_start") or man.get("disk_usage_pct") or 0)
                            end = float(man.get("disk_usage_end") or 0)
                            man["session_growth_mb"] = None
                            man["disk_usage_delta_pct"] = round(end - start, 3) if start and end else None
                        except Exception:
                            pass
                        man_path.write_text(_json.dumps(man, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            except Exception:
                pass
    except Exception:
        pass
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
    from small_paper.shadow_registry import is_shadow_runtime_enabled, shadow_portfolio_status

    try:
        summary.update(shadow_portfolio_status())
    except Exception:
        pass
    # RETIRED research autos skipped (artifacts retained on disk; no runtime work)
    if is_shadow_runtime_enabled("sector_heat_forward_shadow"):
        _run_sector_heat_forward_shadow_auto(
            repo_root=repo_root,
            output_dir=output_dir,
            summary=summary,
            config=config,
            poll_interval_sec=float(session_cfg.get("poll_interval_sec") or poll_interval_sec),
        )
    if is_shadow_runtime_enabled("risk_sizing_forward_shadow"):
        _run_risk_sizing_forward_shadow_auto(
            repo_root=repo_root,
            output_dir=output_dir,
            summary=summary,
            config=config,
            poll_interval_sec=float(session_cfg.get("poll_interval_sec") or poll_interval_sec),
        )
    if is_shadow_runtime_enabled("equity_dynamic_stop_shadow"):
        _run_equity_dynamic_stop_shadow_auto(
            repo_root=repo_root,
            output_dir=output_dir,
            summary=summary,
            config=config,
            poll_interval_sec=float(session_cfg.get("poll_interval_sec") or poll_interval_sec),
        )
    if is_shadow_runtime_enabled("live_config_forward_shadow"):
        _run_live_config_forward_shadow_auto(
            repo_root=repo_root,
            output_dir=output_dir,
            summary=summary,
            config=config,
            poll_interval_sec=float(session_cfg.get("poll_interval_sec") or poll_interval_sec),
        )
    if is_shadow_runtime_enabled("live_config_transition_shadow"):
        _run_live_config_transition_shadow_auto(
            repo_root=repo_root,
            output_dir=output_dir,
            summary=summary,
            config=config,
            poll_interval_sec=float(session_cfg.get("poll_interval_sec") or poll_interval_sec),
        )
    # boundary research auto kept for optional offline packs only when explicitly re-enabled later
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
        _apply_ihc_shadow_counterfactual_finalize(state, summary, output_dir=output_dir, config=config)
    _apply_e1_x5_forward_shadow_finalize(state, summary, output_dir=output_dir)
    if discord:
        summary.update(discord_notify_summary_fields(discord))
    try:
        from small_paper.session_validity import classify_session_validity

        summary.update(classify_session_validity(summary))
    except Exception:
        pass
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
    # Phase723: persist canonical Summary BEFORE Discord so hang/timeout cannot skip local artifacts.
    writer.finalize_batch(
        events=state.events,
        positions=positions,
        summary=summary,
        pos_fields=pos_fields,
    )
    try:
        # Phase675 / stop-risk: Discord must not block archive/seal (Summary already on disk).
        # Killable subprocess worker (terminate→kill); never ThreadPoolExecutor fake timeout.
        from small_paper.bounded_side_task import run_subprocess_bounded, telemetry as side_task_telemetry

        _stop = str(getattr(state, "stop_reason", "") or summary.get("stop_reason") or "")
        _day = str(summary.get("trading_date") or datetime.now(JST).strftime("%Y%m%d")).replace("-", "")[:8]
        if _stop == "morning_session_close":
            _dedupe = f"am_summary|{_day}"
        elif _stop == "afternoon_session_close":
            _dedupe = f"pm_summary|{_day}"
        else:
            _dedupe = f"session_end|{getattr(state, 'session_id', '')}|{_stop}"
        _dres = run_subprocess_bounded(
            task="discord_session_end",
            session_dir=output_dir,
            timeout_sec=60.0,
            name="discord_session_end",
            extra={
                "native_root": str(native_root),
                "summary_path": str(output_dir / "small_paper_summary.json"),
                "events_path": str(output_dir / "small_paper_events.jsonl"),
                "dedupe_key": _dedupe,
                "flush_sec": 25.0,
            },
        )
        summary["side_task_telemetry"] = side_task_telemetry()
        summary["discord_session_end_dedupe_key"] = _dedupe
        if _dres.timed_out:
            log.warning("discord session_end notify timed out; continuing finalize")
            summary["discord_session_end_error"] = "timeout"
            summary["discord_session_end_timeout"] = True
            summary["discord_session_end_pending"] = True
        elif not _dres.ok and _dres.error:
            log.warning("discord session_end notify failed: %s", _dres.error)
            summary["discord_session_end_error"] = str(_dres.error)
            summary["discord_session_end_pending"] = True
        else:
            summary["discord_session_end_ok"] = True
        try:
            writer.write_summary(summary)
        except Exception:
            pass
    except Exception as exc:
        log.warning("discord session_end notify failed: %s", exc)
        summary["discord_session_end_error"] = str(exc)
        try:
            writer.write_summary(summary)
        except Exception:
            pass
    # Phase687W70: session-end archive copy (no source delete / no overwrite).
    # Killable subprocess; timeout → pending retry marker under _side_task_tmp only.
    try:
        from small_paper.bounded_side_task import run_subprocess_bounded, telemetry as side_task_telemetry

        _ares = run_subprocess_bounded(
            task="archive_session_copy",
            session_dir=output_dir,
            timeout_sec=300.0,
            name="session_archive",
            extra={"native_root": str(native_root)},
        )
        summary["side_task_telemetry"] = side_task_telemetry()
        if _ares.timed_out or not _ares.ok:
            bak = {
                "ok": False,
                "errors": [_ares.error or "archive_timeout"],
                "pending": True,
                "timed_out": bool(_ares.timed_out),
                "killed": bool(_ares.killed),
                "code": "ARCHIVE_PENDING",
                "task_id": _ares.task_id,
            }
        else:
            bak = _ares.value if isinstance(_ares.value, dict) else {"ok": True, "value": _ares.value}
        summary["session_archive_backup"] = bak
        if not bak.get("ok"):
            log.warning("session archive backup failed: %s", bak.get("errors"))
        writer.write_summary(summary)
    except Exception as exc:
        log.warning("session archive backup error: %s", exc)
        summary["session_archive_backup_error"] = str(exc)
        try:
            writer.write_summary(summary)
        except Exception:
            pass
    # Phase687W71: external D:\kabudata sync after C archive (warn/pending if D missing).
    try:
        from small_paper.bounded_side_task import run_subprocess_bounded, telemetry as side_task_telemetry

        _eres = run_subprocess_bounded(
            task="external_backup",
            session_dir=output_dir,
            timeout_sec=300.0,
            name="external_backup",
            extra={"native_root": str(native_root)},
        )
        summary["side_task_telemetry"] = side_task_telemetry()
        if _eres.timed_out:
            ext = {
                "ok": False,
                "pending": True,
                "code": "EXTERNAL_BACKUP_PENDING",
                "error": "external_backup_timeout",
                "session": str(Path(output_dir).name),
                "timed_out": True,
                "killed": bool(_eres.killed),
                "task_id": _eres.task_id,
            }
        elif not _eres.ok:
            ext = {
                "ok": False,
                "pending": True,
                "code": "EXTERNAL_BACKUP_PENDING",
                "error": str(_eres.error or "external_backup_error"),
                "session": str(Path(output_dir).name),
                "task_id": _eres.task_id,
            }
        else:
            ext = _eres.value if isinstance(_eres.value, dict) else {"ok": True, "value": _eres.value}
        summary["session_external_backup"] = ext
        if ext.get("pending"):
            log.warning("external backup pending (D not connected): %s", ext.get("session"))
        elif not ext.get("ok") and not ext.get("skipped"):
            log.warning("external backup failed: %s", ext)
        writer.write_summary(summary)
    except Exception as exc:
        log.warning("external backup error: %s", exc)
        summary["session_external_backup_error"] = str(exc)
        summary["session_external_backup"] = {
            "ok": False,
            "pending": True,
            "code": "EXTERNAL_BACKUP_PENDING",
            "error": str(exc),
        }
        try:
            writer.write_summary(summary)
        except Exception:
            pass
    # Phase687W32: single seal point AFTER all required artifacts are final and closed.
    # Never rewrite seal-target files after this (summary included).
    # Side-task workers must not mutate sealed paths after mark_session_sealed.
    try:
        from small_paper.operational_recovery import finalize_session_manifest
        from small_paper.stateful_journal_recovery import ensure_required_seal_artifacts
        from small_paper.w4s_seal_propagation import finalize_session_seal_propagation

        bridge = getattr(state, "live_order_safety_bridge", None)
        safety_dir = output_dir / "live_order_safety"
        safety_dir.mkdir(parents=True, exist_ok=True)
        # Refresh soak with final accepted/exit counts after summarize
        if bridge is not None:
            try:
                from small_paper.live_order_runtime_bridge import write_soak_session_snapshot

                write_soak_session_snapshot(
                    bridge,
                    output_dir=safety_dir,
                    canonical_entry_count=len(state.accepted_rows),
                    canonical_exit_count=sum(
                        1
                        for e in state.events
                        if str(e.get("event") or e.get("kind") or "") in ("OBSERVER_EXIT", "exit")
                        or e.get("is_structural_exit")
                    ),
                )
            except Exception:
                pass
        ensure_required_seal_artifacts(output_dir, safety_dir=safety_dir)
        # Ensure manifest exists even on early abort without prior finalize
        man_path = safety_dir / "session_manifest.json"
        if not man_path.is_file():
            try:
                finalize_session_manifest(
                    safety_dir,
                    canonical_entry_count=len(state.accepted_rows),
                    canonical_exit_count=0,
                    safety_sm_signal_count=0,
                    intent_count=0,
                    submit_count=0,
                    cancel_count=0,
                    reservation_leak=0,
                    reconciliation_mismatch=0,
                    kill_switch_events=0,
                    journal_sequence_end=0,
                    snapshot_completeness="COMPLETE",
                    session_seal_status="PENDING_SEAL",
                )
            except Exception:
                pass
        seal_path = output_dir / "session_seal.json"
        need_seal = True
        if seal_path.is_file():
            try:
                prev_seal = json.loads(seal_path.read_text(encoding="utf-8"))
                if (
                    prev_seal.get("session_seal_status") == "SEALED_VALID"
                    and prev_seal.get("finalize_locked")
                ):
                    need_seal = False
            except Exception:
                need_seal = True
        if need_seal:
            # Delete stale INCOMPLETE seal so reseal is clean
            if seal_path.is_file():
                try:
                    prev_seal = json.loads(seal_path.read_text(encoding="utf-8"))
                    if prev_seal.get("session_seal_status") != "SEALED_VALID":
                        seal_path.unlink(missing_ok=True)
                        safety_seal = safety_dir / "session_seal.json"
                        if safety_seal.is_file():
                            safety_seal.unlink(missing_ok=True)
                except Exception:
                    pass
            prop = finalize_session_seal_propagation(
                output_dir,
                safety_dir=safety_dir,
                session_id=str(getattr(bridge, "session_id", "") or output_dir.name),
                skip_if_locked=True,
            )
            # In-memory only — never rewrite sealed small_paper_summary.json / journals
            summary["session_seal_propagation"] = {
                "pass": bool(prop.get("pass")),
                "seal_propagation_status": prop.get("seal_propagation_status"),
            }
            if seal_path.is_file():
                try:
                    seal_obj = json.loads(seal_path.read_text(encoding="utf-8"))
                    summary["session_seal_status"] = seal_obj.get("session_seal_status")
                    if str(seal_obj.get("session_seal_status") or "") in ("SEALED_VALID", "SEALED"):
                        from small_paper.bounded_side_task import mark_session_sealed, telemetry as side_task_telemetry

                        mark_session_sealed(output_dir)
                        summary["side_task_telemetry"] = side_task_telemetry()
                        summary["session_sealed_for_side_tasks"] = True
                except Exception:
                    pass
    except Exception as exc:
        log.warning("post-finalize seal failed: %s", exc)
    try:
        from small_paper.am_pm_summary_preservation import preserve_session_summary_at_end

        # Copies to small_paper_summary_{am|pm}.json only — not a seal required artifact
        preserved = preserve_session_summary_at_end(
            output_dir,
            session_cfg=session_cfg,
            summary=summary,
        )
        if preserved is not None:
            summary["am_pm_summary_preserved_path"] = str(preserved)
    except Exception as exc:
        log.warning("am_pm summary preservation failed: %s", exc)
    # Phase687W38: research multi-day board dataset append (fail-open; no strategy impact)
    try:
        from research.board_entry_dataset_append import maybe_append_session_board_dataset

        summary["board_entry_dataset_append"] = maybe_append_session_board_dataset(
            native_root=native_root,
            session_dir=output_dir,
            summary=summary,
        )
    except Exception as exc:
        log.warning("board_entry_dataset append failed: %s", exc)
        summary["board_entry_dataset_append"] = {"status": "ERROR", "error": str(exc)}
    # Phase687W43: research pre-entry market state append (fail-open; no strategy impact)
    try:
        from research.pre_entry_market_state import maybe_append_session_market_state

        summary["pre_entry_market_state_append"] = maybe_append_session_market_state(
            native_root=native_root,
            session_dir=output_dir,
            summary=summary,
        )
    except Exception as exc:
        log.warning("pre_entry_market_state append failed: %s", exc)
        summary["pre_entry_market_state_append"] = {"status": "ERROR", "error": str(exc)}
    # Phase687W57: Pullback Volume Forward logger finalize (observe-only)
    try:
        pv = getattr(state, "pullback_volume_forward", None)
        if pv is not None and getattr(pv, "enabled", False):
            from small_paper.pullback_volume_forward_logger import finalize_session

            summary["pullback_volume_forward_finalize"] = finalize_session(pv)
            summary.update(_pullback_volume_forward_summary_fields(state))
    except Exception as exc:
        log.warning("pullback_volume_forward finalize failed: %s", exc)
        summary["pullback_volume_forward_finalize"] = {"status": "ERROR", "error": str(exc)}
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
    try:
        summary["pilot_exit_code"] = pilot_process_exit_code(summary)
    except Exception:
        summary["pilot_exit_code"] = 0 if str(summary.get("stop_reason") or "") != "register_failed" else 2
    return PilotRunResult(
        output_dir=output_dir,
        summary=summary,
        events=state.events,
        accepted=state.accepted_rows,
        rejects=state.reject_rows,
    )


def pilot_process_exit_code(summary: Mapping[str, Any] | None) -> int:
    """Phase687W31: register_failed / invalid sessions must not exit 0."""
    s = summary or {}
    reason = str(s.get("stop_reason") or "")
    validity = str(s.get("session_validity") or "")
    if reason == "register_failed" or validity == "INVALID_REGISTER_FAILED":
        return 2
    if validity.startswith("INVALID_"):
        return 2
    if s.get("include_in_strategy_metrics") is False and reason:
        return 2
    return 0


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
