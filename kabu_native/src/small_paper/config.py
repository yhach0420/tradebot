"""
Phase 44: Small paper pilot configuration loader.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

import yaml

from research.entry_v2 import MOMENTUM_V13_COMBINED_REFERENCE, MOMENTUM_V2_REFERENCE
from research.exposure_gate import ExposureGateConfig
from small_paper.allowed_trading_windows import (
    TradingWindow,
    parse_allowed_trading_windows,
    windows_summary,
)

FROZEN_EXIT_PROFILE = MOMENTUM_V13_COMBINED_REFERENCE
FROZEN_ENTRY_PROFILE = MOMENTUM_V2_REFERENCE


@dataclass
class SmallPaperPilotConfig:
    profile: str = FROZEN_EXIT_PROFILE
    entry_profile: str = FROZEN_ENTRY_PROFILE
    min_continuation_quality: float = 0.55
    max_concurrent_positions: int = 3
    position_cap_mode: bool = False
    # Same-symbol open behavior:
    # - replace (default, legacy): close_for_overlap() then register_entry()
    # - no_overlap_replace: reject ENTRY while same symbol is open (keep existing position)
    same_symbol_open_policy: str = "replace"
    position_cap_release: str = "structural_exit"
    virtual_hold_sec: float = 300.0
    entry_cooldown_sec: float = 300.0
    reject_below_quality: bool = True
    entry_score_v2_min: int = 0
    order_enabled: bool = False
    paper_only: bool = True
    discord_enabled: bool = False
    discord_observer_only: bool = True
    discord_send_rejects: bool = False
    discord_send_entry_deferred_max_concurrent: bool = True
    discord_entry_deferred_cooldown_sec: float = 1800.0
    discord_entry_deferred_min_score_v2: int = 5
    discord_entry_deferred_daily_max: int = 50
    discord_send_universe_refresh: bool = True
    discord_send_daily_summary: bool = True
    discord_heartbeat_min: float = 30.0
    discord_webhook_env: str = "KABU_SMALL_PAPER_DISCORD_WEBHOOK_URL"
    discord_trade_notify_webhook_env: str = "KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL"
    discord_trade_cap_blocked_webhook_env: str = "KABU_SMALL_PAPER_CAP_BLOCKED_WEBHOOK_URL"
    discord_send_entry_cap_blocked: bool = True
    discord_cooldown_sec: float = 60.0
    discord_hard_stop_pct: float = 1.20
    discord_hold_min: float = 15.0
    discord_hold_quality_delta: float = 0.03
    discord_take_quality_drop: float = 0.08
    require_phase43_pass: bool = True
    daily_loss_guard_pct: float = -2.5
    risk_cluster_consecutive_losses: int = 5
    risk_cluster_block_enabled: bool = True
    daily_loss_guard_enabled: bool = True
    dry_run_required: bool = True
    default_source: str = "replay"
    max_polls: int = 1
    poll_interval_sec: float = 30.0
    output_base_dir: str = "kabu_native/results/small_paper"
    phase43_diagnosis_glob: str = "kabu_native/results/reports/small_paper_gate_diagnosis_*.json"
    reference_trades_csv: str = ""
    live_duration_sec: float = 1800.0
    live_poll_interval_sec: float = 5.0
    live_record_push_jsonl: bool = True
    live_session_start: str = "09:00"
    live_session_end: str = "15:30"
    live_heartbeat_sec: float = 300.0
    live_stale_tick_sec: float = 120.0
    live_max_consecutive_api_errors: int = 10
    policy_label: str = "q055_cap3"
    policy_trial: bool = False
    baseline_policy: str = ""
    comparison_note: str = ""
    allowed_trading_windows: list[TradingWindow] = field(default_factory=list)
    structural_exit_policy: str = ""
    price_momentum_fade_ratio: float = 0.85
    favorable_mode: str = ""
    favorable_mfe_scale: float = 0.003
    use_market_time_window: bool = False
    symbol_cooloff_enabled: bool = False
    symbol_cooloff_rule: str = "prior_avg_pnl_negative_trades_ge_5"
    symbol_cooloff_min_trades: int = 5
    symbol_cooloff_metric: str = "avg_pnl"
    symbol_cooloff_threshold: float = 0.0
    symbol_cooloff_lookback_sessions: str = "all_available"
    symbol_cooloff_apply_mode: str = "reject_entry"
    daytrade_suitability_enabled: bool = False
    daytrade_suitability_rule: str = "volatility_liquidity_top50"
    daytrade_suitability_lookback_sessions: str = "prior_only"
    daytrade_suitability_apply_mode: str = "reject_entry"
    volume_gate_relaxation_shadow_enabled: bool = True
    live_order_dry_run_enabled: bool = True
    live_order_api_wiring_enabled: bool = True
    live_capital_check_enabled: bool = True
    live_order_adapter_enabled: bool = True
    live_order_notifier_enabled: bool = True
    live_order_discord_enabled: bool = False
    live_order_jsonl_enabled: bool = True
    dry_run: bool = True
    live_trading_enabled: bool = False
    live_order_entry_timeout_sec: float = 4.0
    vol_liq_startup_cache_enabled: bool = False
    vol_liq_startup_cache_dir: str = "kabu_native/results/cache/vol_liq_startup"
    vol_liq_startup_cache_fallback_on_error: bool = True
    vol_liq_startup_cache_write_after_fallback: bool = True
    pre625_runtime_structure_mode: bool = False
    core_runtime_mode: str = ""
    entry_latency_trace_enabled: bool = False
    entry_price_risk_guard_enabled: bool = False
    entry_price_risk_guard_shadow: bool = False
    entry_price_risk_guard_min_entry_price: float = 50.0
    entry_price_risk_guard_max_tick_ratio_pct: float = 5.0
    entry_price_risk_guard_apply_mode: str = "reject_entry"
    enable_pullback_misread_dynamic40_guard: bool = True
    high_drift_guard_enabled: bool = False
    weak_shape_reject_enabled: bool = False
    no_progress_exit_enabled: bool = False
    enable_near_day_high_low_momentum_dynamic40_guard: bool = True
    late_chase_guard_enabled: bool = False
    classic_late_chase_rsi_guard_enabled: bool = False
    classic_late_chase_rsi_threshold: float = 80.0
    reentry_rsi_guard_enabled: bool = False
    reentry_rsi_guard_threshold: float = 60.0
    entry_quality_guard_enabled: bool = False
    entry_quality_max_spread_bps: float = 50.0
    entry_quality_max_update_count: int = 5
    entry_cluster_guard_enabled: bool = False
    entry_cluster_guard_exception_enabled: bool = True
    entry_cluster_guard_liquidity_burst_threshold: float = 0.052267
    stop_low_mfe_guard_enabled: bool = False
    stop_low_mfe_guard_threshold: float = 0.009
    stop_low_mfe_guard_missing_policy: str = "pass"
    stop_low_mfe_guard_pbv2_only: bool = True
    exit_shadow_monitor_enabled: bool = False
    exit_shadow_monitor_t2_enabled: bool = True
    exit_shadow_monitor_t3_enabled: bool = True
    momentum_score_cutoff_max: float = 0.2546
    low_liquidity_shadow_enabled: bool = False
    low_liquidity_shadow_trading_value_min: float = 1e8
    low_liquidity_shadow_turnover_proxy_min: float = 0.002
    shadow_only: bool = False
    entry_max_price_age_sec: float = 3.0
    entry_max_board_age_sec: float = 3.0
    max_entries_per_scan: int = 1
    entry_scan_window_sec: float = 2.0
    entry_freshness_guard_enabled: bool = True
    entry_freshness_board_fallback_enabled: bool = False
    entry_freshness_board_fallback_max_spread_bps: float = 50.0
    entry_scan_batch_enabled: bool = True
    freshness_semantics_v2_enabled: bool = False
    event_stale_threshold_sec: float = 3.0
    board_stale_threshold_sec: float = 3.0
    trade_stale_threshold_sec: float = 10.0
    trade_stale_mode: str = "tag_only"
    or_overlay_enabled: bool = False
    cap_pbv2: int = 4
    cap_or: int = 1
    or_max_update_count: int = 8
    or_open_strength_rank_max: int = 10
    or_open_strength_mins_max: float = 90.0
    raw: dict[str, Any] = field(default_factory=dict)

    def feature_bridge_config(self) -> Any:
        from small_paper.live_feature_bridge import feature_bridge_config_from_pilot

        return feature_bridge_config_from_pilot(self)

    def policy_summary_fields(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "policy_label": self.policy_label,
            "min_continuation_quality": self.min_continuation_quality,
            "max_concurrent_positions": self.max_concurrent_positions,
            "policy_trial": self.policy_trial,
        }
        if self.baseline_policy:
            out["baseline_policy"] = self.baseline_policy
        if self.comparison_note:
            out["comparison_note"] = self.comparison_note.strip()
        if self.allowed_trading_windows:
            out["allowed_trading_windows"] = windows_summary(self.allowed_trading_windows)
        if self.structural_exit_policy:
            out["structural_exit_policy"] = self.structural_exit_policy
            out["observer_exit_mode"] = "combined_structural_exit_notification_only"
        if self.structural_exit_policy == "combined_structural_exit_v2_price_mom":
            out["price_momentum_fade_ratio"] = self.price_momentum_fade_ratio
        if self.favorable_mode:
            out["favorable_mode"] = self.favorable_mode
            out["favorable_mfe_scale"] = self.favorable_mfe_scale
            out["use_market_time_window"] = self.use_market_time_window
        if self.symbol_cooloff_enabled:
            out["symbol_cooloff_enabled"] = True
            out["symbol_cooloff_rule"] = self.symbol_cooloff_rule
            out["symbol_cooloff_apply_mode"] = self.symbol_cooloff_apply_mode
        if self.daytrade_suitability_enabled:
            out["daytrade_suitability_enabled"] = True
            out["daytrade_suitability_rule"] = self.daytrade_suitability_rule
            out["daytrade_suitability_apply_mode"] = self.daytrade_suitability_apply_mode
            if self.volume_gate_relaxation_shadow_enabled:
                out["volume_gate_relaxation_shadow_enabled"] = True
        if self.live_order_dry_run_enabled:
            out["live_order_dry_run_enabled"] = True
            out["live_trading_enabled"] = self.live_trading_enabled
        if self.live_order_api_wiring_enabled:
            out["live_order_api_wiring_enabled"] = True
        if self.live_capital_check_enabled:
            out["live_capital_check_enabled"] = True
        if self.live_order_adapter_enabled:
            out["live_order_adapter_enabled"] = True
        if self.live_order_notifier_enabled:
            out["live_order_notifier_enabled"] = True
        if self.live_order_discord_enabled:
            out["live_order_discord_enabled"] = True
        if self.live_order_jsonl_enabled:
            out["live_order_jsonl_enabled"] = True
        out["dry_run"] = self.dry_run
        if self.vol_liq_startup_cache_enabled:
            out["vol_liq_startup_cache_enabled"] = True
            out["vol_liq_startup_cache_dir"] = self.vol_liq_startup_cache_dir
        if self.pre625_runtime_structure_mode:
            out["pre625_runtime_structure_mode"] = True
        if str(self.core_runtime_mode or "").strip():
            out["core_runtime_mode"] = str(self.core_runtime_mode).strip()
        if self.entry_price_risk_guard_enabled:
            out["entry_price_risk_guard_enabled"] = True
            out["entry_price_risk_guard_shadow"] = self.entry_price_risk_guard_shadow
            out["entry_price_risk_guard_min_entry_price"] = self.entry_price_risk_guard_min_entry_price
            out["entry_price_risk_guard_max_tick_ratio_pct"] = (
                self.entry_price_risk_guard_max_tick_ratio_pct
            )
        if self.enable_pullback_misread_dynamic40_guard:
            out["enable_pullback_misread_dynamic40_guard"] = True
        if self.high_drift_guard_enabled:
            out["high_drift_guard_enabled"] = True
        if self.weak_shape_reject_enabled:
            out["weak_shape_reject_enabled"] = True
        if self.no_progress_exit_enabled:
            out["no_progress_exit_enabled"] = True
        if not self.enable_pullback_misread_dynamic40_guard:
            out["legacy_vwap_pullback_guard_enabled"] = False
        if self.enable_near_day_high_low_momentum_dynamic40_guard:
            out["enable_near_day_high_low_momentum_dynamic40_guard"] = True
        if self.late_chase_guard_enabled:
            out["late_chase_guard_enabled"] = True
            out["momentum_score_cutoff_max"] = self.momentum_score_cutoff_max
        if self.classic_late_chase_rsi_guard_enabled:
            out["classic_late_chase_rsi_guard_enabled"] = True
            out["classic_late_chase_rsi_threshold"] = self.classic_late_chase_rsi_threshold
        if self.reentry_rsi_guard_enabled:
            out["reentry_rsi_guard_enabled"] = True
            out["reentry_rsi_guard_threshold"] = self.reentry_rsi_guard_threshold
        if self.entry_quality_guard_enabled:
            out["entry_quality_guard_enabled"] = True
            out["entry_quality_max_spread_bps"] = self.entry_quality_max_spread_bps
            out["entry_quality_max_update_count"] = self.entry_quality_max_update_count
        if self.entry_cluster_guard_enabled:
            out["entry_cluster_guard_enabled"] = True
            out["entry_cluster_guard_exception_enabled"] = self.entry_cluster_guard_exception_enabled
            out["entry_cluster_guard_liquidity_burst_threshold"] = (
                self.entry_cluster_guard_liquidity_burst_threshold
            )
            reject_clusters = self.raw.get("entry_cluster_guard_reject_clusters", [5])
            reject_csubs = self.raw.get("entry_cluster_guard_reject_csubs", [0, 2, 3, 5])
            out["entry_cluster_guard_reject_clusters"] = list(reject_clusters)
            out["entry_cluster_guard_reject_csubs"] = list(reject_csubs)
        if self.stop_low_mfe_guard_enabled:
            out["stop_low_mfe_guard_enabled"] = True
            out["stop_low_mfe_guard_threshold"] = self.stop_low_mfe_guard_threshold
            out["stop_low_mfe_guard_missing_policy"] = self.stop_low_mfe_guard_missing_policy
            out["stop_low_mfe_guard_pbv2_only"] = self.stop_low_mfe_guard_pbv2_only
        if self.exit_shadow_monitor_enabled:
            out["exit_shadow_monitor_enabled"] = True
            out["exit_shadow_monitor_t2_enabled"] = self.exit_shadow_monitor_t2_enabled
            out["exit_shadow_monitor_t3_enabled"] = self.exit_shadow_monitor_t3_enabled
        if self.low_liquidity_shadow_enabled:
            out["low_liquidity_shadow_enabled"] = True
            out["low_liquidity_shadow_trading_value_min"] = self.low_liquidity_shadow_trading_value_min
            out["low_liquidity_shadow_turnover_proxy_min"] = (
                self.low_liquidity_shadow_turnover_proxy_min
            )
        if self.shadow_only:
            out["shadow_only"] = True
        if self.entry_score_v2_min > 0:
            out["entry_score_v2_min"] = self.entry_score_v2_min
            out["reject_below_quality"] = self.reject_below_quality
        if self.or_overlay_enabled:
            out["or_overlay_enabled"] = True
            out["cap_pbv2"] = self.cap_pbv2
            out["cap_or"] = self.cap_or
            out["or_max_update_count"] = self.or_max_update_count
        if self.freshness_semantics_v2_enabled:
            out["freshness_semantics_v2_enabled"] = True
            out["event_stale_threshold_sec"] = self.event_stale_threshold_sec
            out["board_stale_threshold_sec"] = self.board_stale_threshold_sec
            out["trade_stale_threshold_sec"] = self.trade_stale_threshold_sec
            out["trade_stale_mode"] = self.trade_stale_mode
        if self.position_cap_mode:
            out["position_cap_mode"] = True
            out["position_cap_release"] = self.position_cap_release
            out["virtual_hold_sec"] = self.virtual_hold_sec
            out["entry_cooldown_sec"] = self.entry_cooldown_sec
        return out

    def exposure_gate_config(self) -> ExposureGateConfig:
        return ExposureGateConfig(
            profile=self.profile,
            min_continuation_quality=self.min_continuation_quality,
            max_concurrent_positions=self.max_concurrent_positions,
            position_cap_mode=self.position_cap_mode,
            reject_below_quality=self.reject_below_quality,
            entry_score_v2_min=self.entry_score_v2_min,
            momentum_score_cutoff_max=self.momentum_score_cutoff_max,
            order_enabled=False,
            discord_enabled=self.discord_enabled,
            daily_loss_guard_pct=self.daily_loss_guard_pct,
            risk_cluster_consecutive_losses=self.risk_cluster_consecutive_losses,
        )

    def allowed_windows(self) -> list[TradingWindow]:
        return list(self.allowed_trading_windows)

    def make_exposure_gate(
        self,
        *,
        repo_root: Optional[Path] = None,
        run_session_key: Optional[str] = None,
    ) -> ExposureGate:
        from research.exposure_gate import ExposureGate

        cooloff = None
        suitability = None
        price_guard = None
        pullback_guard = None
        high_drift_guard = None
        weak_shape_guard = None
        near_day_momentum_guard = None
        late_chase_guard = None
        classic_late_chase_rsi_guard = None
        reentry_rsi_guard = None
        entry_quality_guard = None
        entry_cluster_guard = None
        stop_low_mfe_guard = None
        if self.entry_price_risk_guard_enabled:
            from small_paper.entry_price_risk_guard import build_entry_price_risk_guard_state

            price_guard = build_entry_price_risk_guard_state(self)
        if self.enable_pullback_misread_dynamic40_guard:
            from small_paper.pullback_misread_dynamic40_entry_guard import (
                build_pullback_misread_dynamic40_guard_state,
            )

            pullback_guard = build_pullback_misread_dynamic40_guard_state(self)
        if self.high_drift_guard_enabled:
            from small_paper.high_drift_pullback_entry_guard import (
                build_high_drift_pullback_guard_state,
            )

            high_drift_guard = build_high_drift_pullback_guard_state(self)
        if self.weak_shape_reject_enabled:
            from small_paper.weak_shape_reject_entry_guard import build_weak_shape_reject_guard_state

            weak_shape_guard = build_weak_shape_reject_guard_state(self)
        if self.enable_near_day_high_low_momentum_dynamic40_guard:
            from small_paper.near_day_high_low_momentum_dynamic40_entry_guard import (
                build_near_day_high_low_momentum_dynamic40_guard_state,
            )

            near_day_momentum_guard = build_near_day_high_low_momentum_dynamic40_guard_state(
                self
            )
        if self.late_chase_guard_enabled:
            from small_paper.late_chase_entry_guard import build_late_chase_guard_state

            late_chase_guard = build_late_chase_guard_state(self)
        if self.classic_late_chase_rsi_guard_enabled:
            from small_paper.classic_late_chase_rsi_guard import (
                build_classic_late_chase_rsi_guard_state,
            )

            classic_late_chase_rsi_guard = build_classic_late_chase_rsi_guard_state(self)
        if self.reentry_rsi_guard_enabled:
            from small_paper.reentry_rsi_guard import build_reentry_rsi_guard_state

            reentry_rsi_guard = build_reentry_rsi_guard_state(self)
        if self.entry_quality_guard_enabled:
            from small_paper.entry_quality_guard import build_entry_quality_guard_state

            entry_quality_guard = build_entry_quality_guard_state(self)
        if self.entry_cluster_guard_enabled and repo_root is not None:
            from small_paper.entry_cluster_guard import build_entry_cluster_guard_state

            entry_cluster_guard = build_entry_cluster_guard_state(self, repo_root=repo_root)
        if self.stop_low_mfe_guard_enabled:
            from small_paper.stop_low_mfe_guard import build_stop_low_mfe_guard_state

            stop_low_mfe_guard = build_stop_low_mfe_guard_state(self)
        if repo_root is not None and run_session_key:
            if self.symbol_cooloff_enabled:
                from small_paper.symbol_cooloff import build_symbol_cooloff_state

                cooloff = build_symbol_cooloff_state(
                    self,
                    repo_root=repo_root,
                    run_session_key=run_session_key,
                )
            if self.daytrade_suitability_enabled:
                from small_paper.daytrade_suitability_gate import build_vol_liq_threshold

                suitability = build_vol_liq_threshold(
                    self,
                    repo_root=repo_root,
                    run_session_key=run_session_key,
                )
        return ExposureGate(
            self.exposure_gate_config(),
            allowed_windows=self.allowed_windows(),
            symbol_cooloff=cooloff,
            daytrade_suitability=suitability,
            entry_price_risk_guard=price_guard,
            pullback_misread_dynamic40_guard=pullback_guard,
            high_drift_pullback_guard=high_drift_guard,
            weak_shape_reject_guard=weak_shape_guard,
            near_day_high_low_momentum_dynamic40_guard=near_day_momentum_guard,
            late_chase_guard=late_chase_guard,
            classic_late_chase_rsi_guard=classic_late_chase_rsi_guard,
            reentry_rsi_guard=reentry_rsi_guard,
            entry_quality_guard=entry_quality_guard,
            entry_cluster_guard=entry_cluster_guard,
            stop_low_mfe_guard=stop_low_mfe_guard,
        )


def load_pilot_config(path: Path) -> SmallPaperPilotConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out = raw.get("output") or {}
    ref = raw.get("reference") or {}
    return SmallPaperPilotConfig(
        profile=str(raw.get("profile", FROZEN_EXIT_PROFILE)),
        entry_profile=str(raw.get("entry_profile", FROZEN_ENTRY_PROFILE)),
        min_continuation_quality=float(raw.get("min_continuation_quality", 0.55)),
        max_concurrent_positions=int(raw.get("max_concurrent_positions", 3)),
        position_cap_mode=bool(raw.get("position_cap_mode", False)),
        same_symbol_open_policy=str(raw.get("same_symbol_open_policy", "replace") or "replace"),
        position_cap_release=str(raw.get("position_cap_release", "structural_exit")),
        virtual_hold_sec=float(raw.get("virtual_hold_sec", raw.get("entry_cooldown_sec", 300.0))),
        entry_cooldown_sec=float(raw.get("entry_cooldown_sec", raw.get("virtual_hold_sec", 300.0))),
        reject_below_quality=bool(raw.get("reject_below_quality", True)),
        entry_score_v2_min=int(raw.get("entry_score_v2_min", 0) or 0),
        order_enabled=bool(raw.get("order_enabled", False)),
        paper_only=bool(raw.get("paper_only", True)),
        discord_enabled=bool(raw.get("discord_enabled", False)),
        discord_observer_only=bool(raw.get("discord_observer_only", True)),
        discord_send_rejects=bool(raw.get("discord_send_rejects", False)),
        discord_send_entry_deferred_max_concurrent=bool(
            raw.get("discord_send_entry_deferred_max_concurrent", True)
        ),
        discord_entry_deferred_cooldown_sec=float(
            raw.get("discord_entry_deferred_cooldown_sec", 1800.0)
        ),
        discord_entry_deferred_min_score_v2=int(raw.get("discord_entry_deferred_min_score_v2", 5)),
        discord_entry_deferred_daily_max=int(raw.get("discord_entry_deferred_daily_max", 50)),
        discord_send_universe_refresh=bool(raw.get("discord_send_universe_refresh", True)),
        discord_send_daily_summary=bool(raw.get("discord_send_daily_summary", True)),
        discord_heartbeat_min=float(raw.get("discord_heartbeat_min", 30.0)),
        discord_webhook_env=str(raw.get("discord_webhook_env", "KABU_SMALL_PAPER_DISCORD_WEBHOOK_URL")),
        discord_trade_notify_webhook_env=str(
            raw.get("discord_trade_notify_webhook_env", "KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL")
        ),
        discord_trade_cap_blocked_webhook_env=str(
            raw.get(
                "discord_trade_cap_blocked_webhook_env",
                "KABU_SMALL_PAPER_CAP_BLOCKED_WEBHOOK_URL",
            )
        ),
        discord_send_entry_cap_blocked=bool(
            raw.get(
                "discord_send_entry_cap_blocked",
                raw.get("discord_send_entry_deferred_max_concurrent", True),
            )
        ),
        discord_cooldown_sec=float(raw.get("discord_cooldown_sec", 60.0)),
        discord_hard_stop_pct=float(raw.get("discord_hard_stop_pct", 1.20)),
        discord_hold_min=float(raw.get("discord_hold_min", 15.0)),
        discord_hold_quality_delta=float(raw.get("discord_hold_quality_delta", 0.03)),
        discord_take_quality_drop=float(raw.get("discord_take_quality_drop", 0.08)),
        require_phase43_pass=bool(raw.get("require_phase43_pass", True)),
        daily_loss_guard_pct=float(raw.get("daily_loss_guard_pct", -2.5)),
        risk_cluster_consecutive_losses=int(raw.get("risk_cluster_consecutive_losses", 5)),
        risk_cluster_block_enabled=bool(raw.get("risk_cluster_block_enabled", True)),
        daily_loss_guard_enabled=bool(raw.get("daily_loss_guard_enabled", True)),
        dry_run_required=bool(raw.get("dry_run_required", True)),
        default_source=str(raw.get("default_source", "replay")),
        max_polls=int(raw.get("max_polls", 1)),
        poll_interval_sec=float(raw.get("poll_interval_sec", 30.0)),
        output_base_dir=str(out.get("base_dir", "kabu_native/results/small_paper")),
        phase43_diagnosis_glob=str(
            raw.get("phase43_diagnosis_glob", "kabu_native/results/reports/small_paper_gate_diagnosis_*.json")
        ),
        reference_trades_csv=str(ref.get("trades_csv", "")),
        live_duration_sec=float((raw.get("live") or {}).get("duration_sec", 1800)),
        live_poll_interval_sec=float((raw.get("live") or {}).get("poll_interval_sec", 5.0)),
        live_record_push_jsonl=bool((raw.get("live") or {}).get("record_push_jsonl", True)),
        live_session_start=str((raw.get("live") or {}).get("session_start", "09:00")),
        live_session_end=str((raw.get("live") or {}).get("session_end", "15:30")),
        live_heartbeat_sec=float((raw.get("live") or {}).get("heartbeat_sec", 300)),
        live_stale_tick_sec=float((raw.get("live") or {}).get("stale_tick_sec", 120)),
        live_max_consecutive_api_errors=int(
            (raw.get("live") or {}).get("max_consecutive_api_errors", 10)
        ),
        policy_label=str(raw.get("policy_label", "q055_cap3")),
        policy_trial=bool(raw.get("policy_trial", False)),
        baseline_policy=str(raw.get("baseline_policy", "")),
        comparison_note=str(raw.get("comparison_note", "")),
        allowed_trading_windows=parse_allowed_trading_windows(
            raw.get("allowed_trading_windows")
        ),
        structural_exit_policy=str(raw.get("structural_exit_policy", "")),
        price_momentum_fade_ratio=float(raw.get("price_momentum_fade_ratio", 0.85)),
        favorable_mode=str(raw.get("favorable_mode", "") or ""),
        favorable_mfe_scale=float(raw.get("favorable_mfe_scale", 0.003)),
        use_market_time_window=bool(raw.get("use_market_time_window", False)),
        symbol_cooloff_enabled=bool(raw.get("symbol_cooloff_enabled", False)),
        symbol_cooloff_rule=str(
            raw.get("symbol_cooloff_rule", "prior_avg_pnl_negative_trades_ge_5")
        ),
        symbol_cooloff_min_trades=int(raw.get("symbol_cooloff_min_trades", 5)),
        symbol_cooloff_metric=str(raw.get("symbol_cooloff_metric", "avg_pnl")),
        symbol_cooloff_threshold=float(raw.get("symbol_cooloff_threshold", 0.0)),
        symbol_cooloff_lookback_sessions=str(
            raw.get("symbol_cooloff_lookback_sessions", "all_available")
        ),
        symbol_cooloff_apply_mode=str(raw.get("symbol_cooloff_apply_mode", "reject_entry")),
        daytrade_suitability_enabled=bool(raw.get("daytrade_suitability_enabled", False)),
        daytrade_suitability_rule=str(
            raw.get("daytrade_suitability_rule", "volatility_liquidity_top50")
        ),
        daytrade_suitability_lookback_sessions=str(
            raw.get("daytrade_suitability_lookback_sessions", "prior_only")
        ),
        daytrade_suitability_apply_mode=str(
            raw.get("daytrade_suitability_apply_mode", "reject_entry")
        ),
        volume_gate_relaxation_shadow_enabled=bool(
            raw.get("volume_gate_relaxation_shadow_enabled", True)
        ),
        live_order_dry_run_enabled=bool(raw.get("live_order_dry_run_enabled", True)),
        live_order_api_wiring_enabled=bool(raw.get("live_order_api_wiring_enabled", True)),
        live_capital_check_enabled=bool(raw.get("live_capital_check_enabled", True)),
        live_order_adapter_enabled=bool(raw.get("live_order_adapter_enabled", True)),
        live_order_notifier_enabled=bool(raw.get("live_order_notifier_enabled", True)),
        live_order_discord_enabled=bool(raw.get("live_order_discord_enabled", False)),
        live_order_jsonl_enabled=bool(raw.get("live_order_jsonl_enabled", True)),
        dry_run=bool(raw.get("dry_run", True)),
        live_trading_enabled=bool(raw.get("live_trading_enabled", False)),
        live_order_entry_timeout_sec=float(raw.get("live_order_entry_timeout_sec", 4.0)),
        vol_liq_startup_cache_enabled=bool(raw.get("vol_liq_startup_cache_enabled", False)),
        vol_liq_startup_cache_dir=str(
            raw.get(
                "vol_liq_startup_cache_dir",
                "kabu_native/results/cache/vol_liq_startup",
            )
        ),
        vol_liq_startup_cache_fallback_on_error=bool(
            raw.get("vol_liq_startup_cache_fallback_on_error", True)
        ),
        vol_liq_startup_cache_write_after_fallback=bool(
            raw.get("vol_liq_startup_cache_write_after_fallback", True)
        ),
        pre625_runtime_structure_mode=bool(raw.get("pre625_runtime_structure_mode", False)),
        core_runtime_mode=str(raw.get("core_runtime_mode", "") or ""),
        entry_latency_trace_enabled=bool(raw.get("entry_latency_trace_enabled", False)),
        entry_price_risk_guard_enabled=bool(raw.get("entry_price_risk_guard_enabled", False)),
        entry_price_risk_guard_shadow=bool(raw.get("entry_price_risk_guard_shadow", False)),
        entry_price_risk_guard_min_entry_price=float(
            raw.get("entry_price_risk_guard_min_entry_price", raw.get("min_entry_price", 50.0))
        ),
        entry_price_risk_guard_max_tick_ratio_pct=float(
            raw.get("entry_price_risk_guard_max_tick_ratio_pct", raw.get("max_tick_ratio_pct", 5.0))
        ),
        entry_price_risk_guard_apply_mode=str(
            raw.get("entry_price_risk_guard_apply_mode", "reject_entry")
        ),
        enable_pullback_misread_dynamic40_guard=bool(
            raw.get(
                "legacy_vwap_pullback_guard_enabled",
                raw.get("enable_pullback_misread_dynamic40_guard", True),
            )
        ),
        high_drift_guard_enabled=bool(raw.get("high_drift_guard_enabled", False)),
        weak_shape_reject_enabled=bool(raw.get("weak_shape_reject_enabled", False)),
        no_progress_exit_enabled=bool(raw.get("no_progress_exit_enabled", False)),
        enable_near_day_high_low_momentum_dynamic40_guard=bool(
            raw.get("enable_near_day_high_low_momentum_dynamic40_guard", True)
        ),
        late_chase_guard_enabled=bool(raw.get("late_chase_guard_enabled", False)),
        classic_late_chase_rsi_guard_enabled=bool(
            raw.get("classic_late_chase_rsi_guard_enabled", False)
        ),
        classic_late_chase_rsi_threshold=float(
            raw.get("classic_late_chase_rsi_threshold", 80.0)
        ),
        reentry_rsi_guard_enabled=bool(raw.get("reentry_rsi_guard_enabled", False)),
        reentry_rsi_guard_threshold=float(raw.get("reentry_rsi_guard_threshold", 60.0)),
        entry_quality_guard_enabled=bool(raw.get("entry_quality_guard_enabled", False)),
        entry_quality_max_spread_bps=float(raw.get("entry_quality_max_spread_bps", 50.0)),
        entry_quality_max_update_count=int(raw.get("entry_quality_max_update_count", 5)),
        entry_cluster_guard_enabled=bool(raw.get("entry_cluster_guard_enabled", False)),
        entry_cluster_guard_exception_enabled=bool(
            raw.get("entry_cluster_guard_exception_enabled", True)
        ),
        entry_cluster_guard_liquidity_burst_threshold=float(
            raw.get("entry_cluster_guard_liquidity_burst_threshold", 0.052267)
        ),
        stop_low_mfe_guard_enabled=bool(raw.get("stop_low_mfe_guard_enabled", False)),
        stop_low_mfe_guard_threshold=float(raw.get("stop_low_mfe_guard_threshold", 0.009)),
        stop_low_mfe_guard_missing_policy=str(raw.get("stop_low_mfe_guard_missing_policy", "pass") or "pass"),
        stop_low_mfe_guard_pbv2_only=bool(raw.get("stop_low_mfe_guard_pbv2_only", True)),
        exit_shadow_monitor_enabled=bool(raw.get("exit_shadow_monitor_enabled", False)),
        exit_shadow_monitor_t2_enabled=bool(raw.get("exit_shadow_monitor_t2_enabled", True)),
        exit_shadow_monitor_t3_enabled=bool(raw.get("exit_shadow_monitor_t3_enabled", True)),
        momentum_score_cutoff_max=float(raw.get("momentum_score_cutoff_max", 0.2546)),
        low_liquidity_shadow_enabled=bool(raw.get("low_liquidity_shadow_enabled", False)),
        low_liquidity_shadow_trading_value_min=float(
            raw.get("low_liquidity_shadow_trading_value_min", 1e8)
        ),
        low_liquidity_shadow_turnover_proxy_min=float(
            raw.get("low_liquidity_shadow_turnover_proxy_min", 0.002)
        ),
        shadow_only=bool(raw.get("shadow_only", False)),
        entry_max_price_age_sec=float(raw.get("entry_max_price_age_sec", 3.0)),
        entry_max_board_age_sec=float(raw.get("entry_max_board_age_sec", 3.0)),
        max_entries_per_scan=int(raw.get("max_entries_per_scan", 1) or 1),
        entry_scan_window_sec=float(raw.get("entry_scan_window_sec", 2.0)),
        entry_freshness_guard_enabled=bool(raw.get("entry_freshness_guard_enabled", True)),
        entry_freshness_board_fallback_enabled=bool(
            raw.get("entry_freshness_board_fallback_enabled", False)
        ),
        entry_freshness_board_fallback_max_spread_bps=float(
            raw.get("entry_freshness_board_fallback_max_spread_bps", 50.0)
        ),
        entry_scan_batch_enabled=bool(raw.get("entry_scan_batch_enabled", True)),
        freshness_semantics_v2_enabled=bool(raw.get("freshness_semantics_v2_enabled", False)),
        event_stale_threshold_sec=float(raw.get("event_stale_threshold_sec", 3.0)),
        board_stale_threshold_sec=float(raw.get("board_stale_threshold_sec", 3.0)),
        trade_stale_threshold_sec=float(raw.get("trade_stale_threshold_sec", 10.0)),
        trade_stale_mode=str(raw.get("trade_stale_mode", "tag_only") or "tag_only"),
        or_overlay_enabled=bool(raw.get("or_overlay_enabled", False)),
        cap_pbv2=int(raw.get("cap_pbv2", 4) or 4),
        cap_or=int(raw.get("cap_or", 1) or 1),
        or_max_update_count=int(raw.get("or_max_update_count", 8) or 8),
        or_open_strength_rank_max=int(raw.get("or_open_strength_rank_max", 10) or 10),
        or_open_strength_mins_max=float(raw.get("or_open_strength_mins_max", 90.0) or 90.0),
        raw=raw,
    )


def config_file_sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def resolve_output_dir(config: SmallPaperPilotConfig, *, repo_root: Path, day_key: str) -> Path:
    base = Path(config.output_base_dir)
    if not base.is_absolute():
        base = repo_root / base
    return base / day_key


def resolve_live_session_dir(
    config: SmallPaperPilotConfig,
    *,
    repo_root: Path,
    day_key: str,
    session_stamp: str,
) -> Path:
    return resolve_output_dir(config, repo_root=repo_root, day_key=day_key) / f"live_session_{session_stamp}"


def resolve_live_full_session_dir(
    config: SmallPaperPilotConfig,
    *,
    repo_root: Path,
    day_key: str,
    session_stamp: str,
) -> Path:
    return (
        resolve_output_dir(config, repo_root=repo_root, day_key=day_key)
        / f"live_full_session_{session_stamp}"
    )


def resolve_push_replay_dir(
    config: SmallPaperPilotConfig,
    *,
    repo_root: Path,
    day_key: str,
    session_stamp: str,
) -> Path:
    return (
        resolve_output_dir(config, repo_root=repo_root, day_key=day_key)
        / f"push_replay_{session_stamp}"
    )
