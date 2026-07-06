"""
Phase652: Shadow Registry and Dashboard (research / audit only).

Inventories runtime shadows, research counterfactuals, and forward-shadow auto jobs
from code, production YAML, session summaries, and ops docs.
"""

from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import yaml

from research.market_sector_heat import _write_csv
from research.phase451_entry_shape_tournament import _now_iso

PHASE652_VERDICT = "phase652_shadow_registry_done"
REPORT_DIR_NAME = "phase652_shadow_registry"

NATIVE_ROOT = Path(__file__).resolve().parents[2]
SMALL_PAPER_ROOT = NATIVE_ROOT / "results" / "small_paper"
PRODUCTION_YAML = (
    NATIVE_ROOT
    / "configs"
    / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
)
OPS_DOCS = NATIVE_ROOT / "docs" / "operations"

REGISTRY_COLUMNS = [
    "shadow_id",
    "phase",
    "name",
    "category",
    "runtime_or_research",
    "entry_or_exit",
    "target_pool",
    "enabled",
    "config_keys",
    "implementation_files",
    "summary_fields",
    "discord_section",
    "status",
    "decision",
    "last_evaluated_date",
    "mainline_effect",
    "owner_layer",
    "risk_if_left_enabled",
    "recommended_next_action",
]

SUMMARY_COLUMNS = [
    "day",
    "session",
    "shadow_id",
    "enabled",
    "block_count",
    "target_count",
    "net_effect_yen",
    "blocked_winners",
    "blocked_losers",
    "delta_yen",
    "latest_status",
]


@dataclass
class ShadowDef:
    shadow_id: str
    phase: str
    name: str
    category: str
    runtime_or_research: str
    entry_or_exit: str
    target_pool: str = "ALL"
    config_keys: list[str] = field(default_factory=list)
    implementation_files: list[str] = field(default_factory=list)
    summary_fields: list[str] = field(default_factory=list)
    discord_section: str = ""
    status: str = "unknown"
    decision: str = "unknown"
    mainline_effect: str = "none"
    owner_layer: str = "small_paper"
    risk_if_left_enabled: str = "low"
    recommended_next_action: str = "observe"
    yaml_enabled_key: str = ""
    yaml_enabled_default: bool = False
    adopted_mainline: bool = False
    deprecated_candidate: bool = False
    dashboard_prefix: str = ""
    nested_summary_key: str = ""


def _registry_definitions() -> list[ShadowDef]:
    """Static catalog — augmented at runtime from YAML + summaries + docs."""
    return [
        ShadowDef(
            shadow_id="pbv2_rise5_shadow",
            phase="635",
            name="PBv2 Rise5 p95 guard",
            category="entry_runtime",
            runtime_or_research="runtime",
            entry_or_exit="entry",
            target_pool="PBV2_ONLY",
            config_keys=[
                "pbv2_rise5_shadow_enabled",
                "pbv2_rise5_shadow_threshold_pct",
                "pbv2_rise5_shadow_apply_pool",
            ],
            implementation_files=[
                "src/small_paper/pbv2_rise5_shadow.py",
                "src/research/phase635_pbv2_rise5_shadow.py",
            ],
            summary_fields=[
                "pbv2_rise5_shadow_block_count",
                "pbv2_rise5_shadow_target_count",
                "pbv2_rise5_shadow_net_effect_yen",
                "pbv2_rise5_shadow_blocked_winners",
                "pbv2_rise5_shadow_blocked_losers",
            ],
            discord_section="Rise5 Shadow Summary; Shadow Summary; [PBv2 Rise5 Shadow]",
            status="running",
            decision="observe",
            mainline_effect="logging_only",
            owner_layer="pilot_runner.accept_hook",
            risk_if_left_enabled="low — no ENTRY block",
            recommended_next_action="observe 5-10 sessions; promote if net_effect positive",
            yaml_enabled_key="pbv2_rise5_shadow_enabled",
            dashboard_prefix="pbv2_rise5_shadow",
        ),
        ShadowDef(
            shadow_id="pbv2_flat_band_shadow",
            phase="650",
            name="PBv2 Flat-band guard (flat_plus_overheat)",
            category="entry_runtime",
            runtime_or_research="runtime",
            entry_or_exit="entry",
            target_pool="PBV2_ONLY",
            config_keys=[
                "pbv2_flat_band_shadow_enabled",
                "pbv2_flat_band_shadow_apply_pool",
                "pbv2_flat_band_shadow_rise5_flat_min_pct",
                "pbv2_flat_band_shadow_rise5_flat_max_pct",
                "pbv2_flat_band_shadow_rise10_flat_min_pct",
                "pbv2_flat_band_shadow_rise10_flat_max_pct",
                "pbv2_flat_band_shadow_overheat_rise5_pct",
            ],
            implementation_files=[
                "src/small_paper/pbv2_flat_band_guard_shadow.py",
                "src/research/phase650_pbv2_flat_band_shadow.py",
            ],
            summary_fields=[
                "pbv2_flat_band_shadow_block_count",
                "pbv2_flat_band_shadow_target_count",
                "pbv2_flat_band_shadow_net_effect_yen",
                "pbv2_flat_band_shadow_blocked_winners",
                "pbv2_flat_band_shadow_blocked_losers",
            ],
            discord_section="Flat-band Shadow Summary; Shadow Summary; [PBv2 Flat-band Shadow]",
            status="running",
            decision="observe",
            mainline_effect="logging_only",
            owner_layer="pilot_runner.accept_hook",
            risk_if_left_enabled="low — no ENTRY block",
            recommended_next_action="observe alongside rise5; candidate for mainline after forward days",
            yaml_enabled_key="pbv2_flat_band_shadow_enabled",
            dashboard_prefix="pbv2_flat_band_shadow",
        ),
        ShadowDef(
            shadow_id="board_dynamic_trailing_shadow",
            phase="332",
            name="Board-dynamic trailing MFE vs legacy fixed",
            category="exit_runtime",
            runtime_or_research="runtime",
            entry_or_exit="exit",
            target_pool="ALL",
            config_keys=["structural_exit_policy"],
            implementation_files=["src/small_paper/board_dynamic_trailing_shadow.py"],
            summary_fields=[
                "board_dynamic_shadow_total_delta_yen",
                "board_dynamic_shadow_exit_count",
                "board_dynamic_shadow_improved_count",
            ],
            discord_section="Shadow Summary (BoardDynamic)",
            status="adopted",
            decision="adopted",
            mainline_effect="production EXIT policy; shadow compares legacy 0.8%/50%",
            owner_layer="observer_position_tracker",
            risk_if_left_enabled="n/a — production path",
            recommended_next_action="hold; monitor delta_yen",
            yaml_enabled_key="structural_exit_policy",
            adopted_mainline=True,
            dashboard_prefix="board_dynamic_shadow",
        ),
        ShadowDef(
            shadow_id="pullback_misread_guard_shadow",
            phase="353",
            name="Pullback misread guard (rise5<0 & vwap_dev<0)",
            category="entry_runtime",
            runtime_or_research="runtime",
            entry_or_exit="entry",
            target_pool="ALL",
            config_keys=[],
            implementation_files=["src/small_paper/pullback_misread_entry_guard_shadow.py"],
            summary_fields=[
                "pullback_misread_guard_shadow_blocked_count",
                "pullback_misread_guard_shadow_delta_yen",
                "pullback_misread_guard_shadow_kept_count",
            ],
            discord_section="Shadow Summary (PullbackMisread)",
            status="running",
            decision="observe",
            mainline_effect="logging_only; production guard is high_drift_guard",
            owner_layer="pilot_runner.accept_hook",
            risk_if_left_enabled="low",
            recommended_next_action="observe; compare vs high_drift production",
            dashboard_prefix="pullback_misread_guard_shadow",
        ),
        ShadowDef(
            shadow_id="exit_shadow_monitor_t2_t3",
            phase="563",
            name="EXIT shadow monitor T2/T3",
            category="exit_runtime",
            runtime_or_research="runtime",
            entry_or_exit="exit",
            target_pool="ALL",
            config_keys=[
                "exit_shadow_monitor_enabled",
                "exit_shadow_monitor_t2_enabled",
                "exit_shadow_monitor_t3_enabled",
            ],
            implementation_files=["src/small_paper/exit_shadow_monitor.py"],
            summary_fields=[
                "shadow_exit_t2_delta",
                "shadow_exit_t3_delta",
                "exit_shadow_monitor_trade_count",
                "exit_shadow_monitor_status",
            ],
            discord_section="Shadow Summary (EXIT T2·T3); format_exit_shadow_monitor_discord_lines",
            status="running",
            decision="observe",
            mainline_effect="observer_only",
            owner_layer="observer_position_tracker",
            risk_if_left_enabled="low",
            recommended_next_action="observe T3 delta; T3 primary candidate per Phase563",
            yaml_enabled_key="exit_shadow_monitor_enabled",
            dashboard_prefix="shadow_exit",
        ),
        ShadowDef(
            shadow_id="realtime_board_exit_shadow",
            phase="335",
            name="Realtime board adaptive EXIT (loss_accel/collapse/profit_protect)",
            category="exit_runtime",
            runtime_or_research="runtime",
            entry_or_exit="exit",
            target_pool="ALL",
            config_keys=[],
            implementation_files=["src/small_paper/realtime_board_exit_shadow.py"],
            summary_fields=[
                "shadow_loss_acceleration_exit_count",
                "shadow_board_collapse_profit_exit_count",
                "shadow_profit_protect_exit_count",
                "realtime_board_vs_actual_total_delta_yen",
            ],
            discord_section="phase335 lite outputs (not operator embed)",
            status="running",
            decision="observe",
            mainline_effect="tick-level counterfactual; timing unreliable in replay",
            owner_layer="extension_bus.on_push_tick",
            risk_if_left_enabled="medium — CPU/tick load",
            recommended_next_action="observe replay; do not adopt without push-live validation",
            dashboard_prefix="realtime_board",
        ),
        ShadowDef(
            shadow_id="loss_acceleration_exit",
            phase="337",
            name="EXIT candidate: loss acceleration",
            category="exit_runtime",
            runtime_or_research="runtime",
            entry_or_exit="exit",
            target_pool="ALL",
            config_keys=["enable_exit_candidate_shadow"],
            implementation_files=[
                "src/small_paper/exit_candidate_shadow.py",
                "src/small_paper/realtime_board_exit_shadow.py",
            ],
            summary_fields=["loss_acceleration_exit"],
            discord_section="none",
            status="disabled",
            decision="hold",
            mainline_effect="subsumed by realtime_board_exit_shadow",
            owner_layer="exit_candidate_shadow pack",
            risk_if_left_enabled="low when disabled",
            recommended_next_action="hold research; use realtime_board bundle",
        ),
        ShadowDef(
            shadow_id="board_collapse_profit_exit",
            phase="337",
            name="EXIT candidate: board collapse in profit",
            category="exit_runtime",
            runtime_or_research="runtime",
            entry_or_exit="exit",
            target_pool="ALL",
            config_keys=["enable_exit_candidate_shadow"],
            implementation_files=["src/small_paper/exit_candidate_shadow.py"],
            summary_fields=["board_collapse_profit_exit"],
            discord_section="none",
            status="disabled",
            decision="hold",
            mainline_effect="subsumed by realtime_board_exit_shadow",
            owner_layer="exit_candidate_shadow pack",
            risk_if_left_enabled="low when disabled",
            recommended_next_action="hold research",
        ),
        ShadowDef(
            shadow_id="profit_protect_exit",
            phase="337",
            name="EXIT candidate: profit protect",
            category="exit_runtime",
            runtime_or_research="runtime",
            entry_or_exit="exit",
            target_pool="ALL",
            config_keys=["enable_exit_candidate_shadow"],
            implementation_files=["src/small_paper/exit_candidate_shadow.py"],
            summary_fields=["profit_protect_exit"],
            discord_section="none",
            status="disabled",
            decision="hold",
            mainline_effect="subsumed by realtime_board_exit_shadow",
            owner_layer="exit_candidate_shadow pack",
            risk_if_left_enabled="low when disabled",
            recommended_next_action="hold research",
        ),
        ShadowDef(
            shadow_id="volume_gate_relaxation_shadow",
            phase="590",
            name="V90/V80 volume gate relaxation",
            category="entry_runtime",
            runtime_or_research="runtime",
            entry_or_exit="entry",
            target_pool="UNIVERSE",
            config_keys=["volume_gate_relaxation_shadow_enabled"],
            implementation_files=["src/small_paper/volume_gate_relaxation_shadow.py"],
            summary_fields=[
                "volume_shadow_v90_rescued_count",
                "volume_shadow_v80_rescued_count",
                "volume_shadow_monitor_status",
            ],
            discord_section="none (eval JSONL only)",
            status="running",
            decision="observe",
            mainline_effect="logging_only; production V100",
            owner_layer="extension_bus.on_post_eval",
            risk_if_left_enabled="low",
            recommended_next_action="observe rescue counts",
            yaml_enabled_key="volume_gate_relaxation_shadow_enabled",
            dashboard_prefix="volume_shadow",
        ),
        ShadowDef(
            shadow_id="sector_heat_forward_shadow",
            phase="255",
            name="Sector Heat forward shadow",
            category="forward_shadow",
            runtime_or_research="runtime",
            entry_or_exit="entry",
            target_pool="ALL",
            config_keys=[],
            implementation_files=[
                "src/research/market_sector_heat_forward_shadow_logger.py",
                "src/small_paper/sector_heat_forward_shadow_auto.py",
            ],
            summary_fields=["sector_heat_forward_shadow.trade_overlap_days", "sector_heat_forward_shadow.status"],
            discord_section="SectorHeat Forward Shadow",
            status="running",
            decision="observe",
            mainline_effect="session-end auto; adopt_not_allowed",
            owner_layer="pilot_runner.session_finalize",
            risk_if_left_enabled="low",
            recommended_next_action="continue collection",
            nested_summary_key="sector_heat_forward_shadow",
        ),
        ShadowDef(
            shadow_id="risk_sizing_forward_shadow",
            phase="262",
            name="Risk-Aware Sizing forward shadow",
            category="forward_shadow",
            runtime_or_research="runtime",
            entry_or_exit="entry",
            target_pool="ALL",
            config_keys=[],
            implementation_files=[
                "src/research/risk_sizing_forward_shadow_logger.py",
                "src/small_paper/risk_sizing_forward_shadow_auto.py",
            ],
            summary_fields=["risk_sizing_forward_shadow.best_policy", "risk_sizing_forward_shadow.status"],
            discord_section="RiskAware Sizing Shadow",
            status="running",
            decision="observe",
            mainline_effect="session-end auto; adopt_not_allowed",
            owner_layer="pilot_runner.session_finalize",
            risk_if_left_enabled="low",
            recommended_next_action="continue collection",
            nested_summary_key="risk_sizing_forward_shadow",
        ),
        ShadowDef(
            shadow_id="equity_dynamic_stop_shadow",
            phase="263",
            name="Equity Dynamic Stop shadow",
            category="forward_shadow",
            runtime_or_research="runtime",
            entry_or_exit="exit",
            target_pool="ALL",
            config_keys=[],
            implementation_files=[
                "src/research/equity_dynamic_stop_shadow.py",
                "src/small_paper/equity_dynamic_stop_shadow_auto.py",
            ],
            summary_fields=["equity_dynamic_stop_shadow.best_policy_5m", "equity_dynamic_stop_shadow.status"],
            discord_section="Equity Dynamic Stop Shadow",
            status="running",
            decision="observe",
            mainline_effect="session-end auto; adopt_not_allowed",
            owner_layer="pilot_runner.session_finalize",
            risk_if_left_enabled="low",
            recommended_next_action="continue collection",
            nested_summary_key="equity_dynamic_stop_shadow",
        ),
        ShadowDef(
            shadow_id="live_config_forward_shadow",
            phase="273",
            name="Live Config forward shadow",
            category="forward_shadow",
            runtime_or_research="runtime",
            entry_or_exit="entry",
            target_pool="ALL",
            config_keys=[],
            implementation_files=[
                "src/research/phase273_live_config_forward_shadow_logger.py",
                "src/small_paper/live_config_forward_shadow_auto.py",
            ],
            summary_fields=["live_config_forward_shadow.day_count", "live_config_forward_shadow.last_status"],
            discord_section="LiveConfig Shadow",
            status="running",
            decision="observe",
            mainline_effect="session-end auto",
            owner_layer="pilot_runner.session_finalize",
            risk_if_left_enabled="low",
            recommended_next_action="continue until min 10 forward days",
            nested_summary_key="live_config_forward_shadow",
        ),
        ShadowDef(
            shadow_id="live_config_transition_shadow",
            phase="274",
            name="Live Config transition shadow",
            category="forward_shadow",
            runtime_or_research="runtime",
            entry_or_exit="entry",
            target_pool="ALL",
            config_keys=[],
            implementation_files=[
                "src/research/phase274_live_config_auto_transition_shadow.py",
                "src/small_paper/live_config_transition_shadow_auto.py",
            ],
            summary_fields=["live_config_transition_shadow.last_status"],
            discord_section="LiveConfig Transition Shadow",
            status="running",
            decision="observe",
            mainline_effect="session-end auto",
            owner_layer="pilot_runner.session_finalize",
            risk_if_left_enabled="low",
            recommended_next_action="observe",
            nested_summary_key="live_config_transition_shadow",
        ),
        ShadowDef(
            shadow_id="boundary_forward_shadow",
            phase="409",
            name="Boundary / position-cap forward shadow",
            category="forward_shadow",
            runtime_or_research="runtime",
            entry_or_exit="exit",
            target_pool="ALL",
            config_keys=[],
            implementation_files=[
                "src/research/phase409_boundary_forward_shadow.py",
                "src/small_paper/boundary_forward_shadow_auto.py",
            ],
            summary_fields=[
                "boundary_forward_shadow.shadow_total_pnl_yen_100",
                "boundary_forward_shadow.delta_yen_100",
                "boundary_forward_shadow.adoption_review_allowed",
            ],
            discord_section="Boundary Shadow",
            status="running",
            decision="reject",
            mainline_effect="session-end auto; adoption_review_allowed=False",
            owner_layer="pilot_runner.session_finalize",
            risk_if_left_enabled="low",
            recommended_next_action="reject adoption; keep logging",
            nested_summary_key="boundary_forward_shadow",
        ),
        ShadowDef(
            shadow_id="post_entry_forward_shadow",
            phase="500",
            name="Post-entry 30/60/120/180s checkpoints",
            category="forward_shadow",
            runtime_or_research="runtime",
            entry_or_exit="exit",
            target_pool="ALL",
            config_keys=[],
            implementation_files=[
                "src/small_paper/post_entry_forward_shadow.py",
                "src/small_paper/post_entry_forward_shadow_auto.py",
            ],
            summary_fields=[
                "post_entry_shadow_score_ge3_count",
                "post_entry_forward_shadow.forward_days_collected",
            ],
            discord_section="PostEntry Shadow",
            status="running",
            decision="observe",
            mainline_effect="virtual checkpoints; no ENTRY adoption",
            owner_layer="extension_bus.session_end",
            risk_if_left_enabled="low",
            recommended_next_action="fix finalize_session_end if errors persist",
            nested_summary_key="post_entry_forward_shadow",
        ),
        ShadowDef(
            shadow_id="classic_momentum_forward_shadow",
            phase="513",
            name="Classic momentum virtual entries (RSI+Stoch)",
            category="forward_shadow",
            runtime_or_research="runtime",
            entry_or_exit="entry",
            target_pool="VIRTUAL",
            config_keys=[],
            implementation_files=[
                "src/small_paper/classic_momentum_forward_shadow.py",
                "src/small_paper/classic_momentum_forward_shadow_auto.py",
            ],
            summary_fields=[
                "classic_momentum_shadow_trade_count",
                "classic_momentum_shadow_pnl_yen_100",
            ],
            discord_section="Classic Momentum (research block)",
            status="running",
            decision="observe",
            mainline_effect="virtual positions only",
            owner_layer="extension_bus",
            risk_if_left_enabled="low",
            recommended_next_action="no adoption",
            nested_summary_key="classic_momentum_forward_shadow",
        ),
        ShadowDef(
            shadow_id="phase632_pbv2_profit_filter",
            phase="632",
            name="PBv2 profit filter counterfactual",
            category="research_counterfactual",
            runtime_or_research="research",
            entry_or_exit="entry",
            target_pool="PBV2_ONLY",
            implementation_files=["src/research/phase632_pbv2_profit_filter_counterfactual.py"],
            summary_fields=[],
            discord_section="none",
            status="research_only",
            decision="hold",
            mainline_effect="informed Phase633/634/635",
            owner_layer="research batch",
            risk_if_left_enabled="none",
            recommended_next_action="archive; use Phase635 runtime shadow",
        ),
        ShadowDef(
            shadow_id="phase633_combo_soft_robustness",
            phase="633",
            name="combo_soft robustness decomposition",
            category="research_counterfactual",
            runtime_or_research="research",
            entry_or_exit="entry",
            target_pool="PBV2_ONLY",
            implementation_files=["src/research/phase633_combo_soft_robustness.py"],
            summary_fields=[],
            discord_section="none",
            status="research_only",
            decision="hold",
            mainline_effect="robustness study for combo_soft",
            owner_layer="research batch",
            risk_if_left_enabled="none",
            recommended_next_action="hold unless Phase635 underperforms",
        ),
        ShadowDef(
            shadow_id="phase634_pbv2_rise5_full_period",
            phase="634",
            name="PBv2-only rise5 cap full-period",
            category="research_counterfactual",
            runtime_or_research="research",
            entry_or_exit="entry",
            target_pool="PBV2_ONLY",
            implementation_files=["src/research/phase634_pbv2_only_rise5_full_period.py"],
            summary_fields=[],
            discord_section="none",
            status="research_only",
            decision="hold",
            mainline_effect="led to Phase635 runtime shadow",
            owner_layer="research batch",
            risk_if_left_enabled="none",
            recommended_next_action="superseded by pbv2_rise5_shadow runtime",
        ),
        ShadowDef(
            shadow_id="phase647_momentum_low_trend",
            phase="647",
            name="Momentum low-trend attribution",
            category="research_counterfactual",
            runtime_or_research="research",
            entry_or_exit="entry",
            target_pool="PBV2_ONLY",
            implementation_files=["src/research/phase647_momentum_low_trend_attribution.py"],
            summary_fields=[],
            discord_section="none",
            status="research_only",
            decision="hold",
            mainline_effect="analysis only; no shadow implementation",
            owner_layer="research batch",
            risk_if_left_enabled="none",
            recommended_next_action="inform guard design only",
        ),
        ShadowDef(
            shadow_id="phase648_rise5_rise10_analysis",
            phase="648",
            name="Rise5 × Rise10 profit attribution",
            category="research_counterfactual",
            runtime_or_research="research",
            entry_or_exit="entry",
            target_pool="PBV2_ONLY",
            implementation_files=["src/research/phase648_rise5_rise10_profit_attribution.py"],
            summary_fields=[],
            discord_section="none",
            status="research_only",
            decision="hold",
            mainline_effect="informed Phase649/650",
            owner_layer="research batch",
            risk_if_left_enabled="none",
            recommended_next_action="monitor flat_band runtime shadow",
        ),
        ShadowDef(
            shadow_id="phase649_flat_band_guard",
            phase="649",
            name="Flat-band guard counterfactual",
            category="research_counterfactual",
            runtime_or_research="research",
            entry_or_exit="entry",
            target_pool="PBV2_ONLY",
            implementation_files=["src/research/phase649_flat_band_guard_counterfactual.py"],
            summary_fields=[],
            discord_section="none",
            status="research_only",
            decision="candidate",
            mainline_effect="promoted to pbv2_flat_band_shadow runtime",
            owner_layer="research batch",
            risk_if_left_enabled="none",
            recommended_next_action="track Phase650 runtime forward",
        ),
        ShadowDef(
            shadow_id="phase643_position_sizing_shadow",
            phase="643",
            name="Position sizing shadow research",
            category="research_counterfactual",
            runtime_or_research="research",
            entry_or_exit="entry",
            target_pool="ALL",
            implementation_files=["src/research/phase643_position_sizing_shadow.py"],
            summary_fields=[],
            discord_section="none",
            status="research_only",
            decision="observe",
            mainline_effect="hold 100-share mainline",
            owner_layer="research batch",
            risk_if_left_enabled="none",
            recommended_next_action="continue monitoring liquidity_tv_band",
        ),
        ShadowDef(
            shadow_id="extended_entry_shadow",
            phase="183",
            name="Extended entry feature flags",
            category="extension_shadow",
            runtime_or_research="runtime",
            entry_or_exit="entry",
            target_pool="ALL",
            config_keys=[],
            implementation_files=["src/small_paper/extended_entry_shadow.py"],
            summary_fields=["extended_entry_shadow_count", "extended_entry_shadow_pnl_estimate"],
            discord_section="none",
            status="running",
            decision="observe",
            mainline_effect="extension bus observation",
            owner_layer="extension_bus",
            risk_if_left_enabled="low",
            recommended_next_action="observe",
            dashboard_prefix="extended_entry_shadow",
        ),
        ShadowDef(
            shadow_id="entry_price_risk_guard_shadow",
            phase="153b",
            name="Price-risk entry guard shadow flag",
            category="entry_runtime",
            runtime_or_research="runtime",
            entry_or_exit="entry",
            target_pool="ALL",
            config_keys=[
                "entry_price_risk_guard_shadow",
                "entry_price_risk_guard_enabled",
                "entry_price_risk_guard_apply_mode",
            ],
            implementation_files=["src/research/entry_price_risk_guard_shadow_review.py"],
            summary_fields=["entry_price_risk_guard_reject_count"],
            discord_section="none",
            status="adopted",
            decision="adopted",
            mainline_effect="guard apply_mode=reject_entry is production",
            owner_layer="gate",
            risk_if_left_enabled="n/a",
            recommended_next_action="hold",
            yaml_enabled_key="entry_price_risk_guard_shadow",
            adopted_mainline=True,
        ),
        ShadowDef(
            shadow_id="stop_low_mfe_guard_net_shadow",
            phase="557",
            name="Stop-low-MFE guard net shadow",
            category="entry_runtime",
            runtime_or_research="runtime",
            entry_or_exit="entry",
            target_pool="PBV2_ONLY",
            config_keys=["stop_low_mfe_guard_enabled"],
            implementation_files=["src/small_paper/gate.py"],
            summary_fields=["stop_low_mfe_guard_net_shadow"],
            discord_section="none",
            status="disabled",
            decision="remove",
            mainline_effect="rolled back Phase606; net_shadow computed when guard exists",
            owner_layer="gate",
            risk_if_left_enabled="low",
            recommended_next_action="remove net_shadow field after cleanup window",
            yaml_enabled_key="stop_low_mfe_guard_enabled",
            deprecated_candidate=True,
            dashboard_prefix="stop_low_mfe_guard",
        ),
        ShadowDef(
            shadow_id="low_liquidity_shadow",
            phase="179c",
            name="Low-liquidity log-only reject",
            category="entry_runtime",
            runtime_or_research="runtime",
            entry_or_exit="entry",
            target_pool="ALL",
            config_keys=[
                "low_liquidity_shadow_enabled",
                "low_liquidity_shadow_trading_value_min",
                "low_liquidity_shadow_turnover_proxy_min",
            ],
            implementation_files=["src/small_paper/pilot_runner.py"],
            summary_fields=["low_liquidity_shadow"],
            discord_section="none",
            status="disabled",
            decision="hold",
            mainline_effect="logging only when enabled",
            owner_layer="pilot_runner.accept_enrich",
            risk_if_left_enabled="low",
            recommended_next_action="keep disabled in production YAML",
            yaml_enabled_key="low_liquidity_shadow_enabled",
        ),
        ShadowDef(
            shadow_id="vwap_shadow_reject",
            phase="186",
            name="VWAP dev shadow reject candidate",
            category="extension_shadow",
            runtime_or_research="runtime",
            entry_or_exit="entry",
            target_pool="ALL",
            config_keys=[],
            implementation_files=["src/small_paper/vwap_shadow_reject.py"],
            summary_fields=["vwap_shadow_reject_candidate_count", "vwap_shadow_candidate_total_pnl"],
            discord_section="none",
            status="running",
            decision="observe",
            mainline_effect="log-only",
            owner_layer="extension_bus",
            risk_if_left_enabled="low",
            recommended_next_action="observe",
            dashboard_prefix="vwap_shadow",
        ),
        ShadowDef(
            shadow_id="board_imbalance_shadow",
            phase="unknown",
            name="Board imbalance tier shadow",
            category="extension_shadow",
            runtime_or_research="runtime",
            entry_or_exit="entry",
            target_pool="ALL",
            config_keys=[],
            implementation_files=["src/small_paper/board_imbalance_shadow.py"],
            summary_fields=["imbalance_shadow_count", "imbalance_shadow_total_pnl"],
            discord_section="none",
            status="running",
            decision="observe",
            mainline_effect="session-end finalize",
            owner_layer="extension_bus",
            risk_if_left_enabled="low",
            recommended_next_action="observe",
            dashboard_prefix="imbalance_shadow",
        ),
        ShadowDef(
            shadow_id="quality_formula_shadow",
            phase="unknown",
            name="Quality formula shadow rank",
            category="extension_shadow",
            runtime_or_research="runtime",
            entry_or_exit="entry",
            target_pool="ALL",
            config_keys=[],
            implementation_files=["src/small_paper/quality_formula_shadow.py"],
            summary_fields=["shadow_quality_top20_pf", "shadow_quality_top20_count"],
            discord_section="none",
            status="running",
            decision="observe",
            mainline_effect="session-end finalize",
            owner_layer="extension_bus",
            risk_if_left_enabled="low",
            recommended_next_action="observe",
            dashboard_prefix="shadow_quality",
        ),
        ShadowDef(
            shadow_id="trading_value_shadow_gate",
            phase="unknown",
            name="Trading value gate shadow",
            category="extension_shadow",
            runtime_or_research="runtime",
            entry_or_exit="entry",
            target_pool="ALL",
            config_keys=[],
            implementation_files=["src/small_paper/trading_value_shadow_gate.py"],
            summary_fields=["trading_value_shadow_gate_enabled"],
            discord_section="none",
            status="running",
            decision="observe",
            mainline_effect="session-end finalize",
            owner_layer="extension_bus",
            risk_if_left_enabled="low",
            recommended_next_action="observe",
            dashboard_prefix="trading_value_shadow",
        ),
        ShadowDef(
            shadow_id="entry_expectancy_score_shadow",
            phase="230",
            name="Entry expectancy score v2 shadow",
            category="extension_shadow",
            runtime_or_research="runtime",
            entry_or_exit="entry",
            target_pool="ALL",
            config_keys=[],
            implementation_files=["src/small_paper/entry_expectancy_score_shadow.py"],
            summary_fields=["entry_expectancy_score_shadow_enabled", "phase237_entry_expectancy_score_v2_shadow"],
            discord_section="none",
            status="adopted",
            decision="adopted",
            mainline_effect="entry_score_v2 gate is production (min=3)",
            owner_layer="extension_bus",
            risk_if_left_enabled="n/a",
            recommended_next_action="hold",
            adopted_mainline=True,
        ),
        ShadowDef(
            shadow_id="limit_up_proximity_entry_guard_shadow",
            phase="unknown",
            name="Limit-up proximity guard shadow",
            category="extension_shadow",
            runtime_or_research="runtime",
            entry_or_exit="entry",
            target_pool="ALL",
            config_keys=[],
            implementation_files=["src/small_paper/limit_up_proximity_entry_guard_shadow.py"],
            summary_fields=[
                "limit_up_proximity_guard_shadow_blocked_count",
                "limit_up_proximity_guard_shadow_delta_yen",
            ],
            discord_section="none",
            status="running",
            decision="observe",
            mainline_effect="log-only",
            owner_layer="pilot_runner",
            risk_if_left_enabled="low",
            recommended_next_action="observe",
            dashboard_prefix="limit_up_proximity_guard_shadow",
        ),
    ]


def _load_yaml_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data if isinstance(data, dict) else {}


def _yaml_enabled(cfg: Mapping[str, Any], sd: ShadowDef) -> Optional[bool]:
    if sd.shadow_id == "board_dynamic_trailing_shadow":
        pol = str(cfg.get("structural_exit_policy") or "")
        return "trailing_mfe" in pol
    if not sd.yaml_enabled_key:
        if sd.runtime_or_research == "research":
            return False
        if sd.adopted_mainline:
            return True
        if sd.status in ("running",) and sd.category in ("extension_shadow", "forward_shadow"):
            return True
        if sd.shadow_id in (
            "pullback_misread_guard_shadow",
            "realtime_board_exit_shadow",
            "extended_entry_shadow",
            "vwap_shadow_reject",
            "board_imbalance_shadow",
            "quality_formula_shadow",
            "trading_value_shadow_gate",
            "limit_up_proximity_entry_guard_shadow",
        ):
            return True
        return None
    raw = cfg.get(sd.yaml_enabled_key)
    if raw is None:
        return sd.yaml_enabled_default
    if isinstance(raw, bool):
        return raw
    return bool(raw)


def _parse_ops_docs() -> dict[str, dict[str, str]]:
    """phase number -> {verdict, decision_hint, last_date}."""
    out: dict[str, dict[str, str]] = {}
    if not OPS_DOCS.is_dir():
        return out
    verdict_re = re.compile(r"verdict[:\s=]+[`']?([a-z0-9_]+)", re.I)
    date_re = re.compile(r"20\d{2}-\d{2}-\d{2}")
    for fp in sorted(OPS_DOCS.glob("phase*.md")):
        m = re.search(r"phase(\d+)", fp.stem)
        if not m:
            continue
        phase = m.group(1)
        text = fp.read_text(encoding="utf-8", errors="replace")
        verdict = ""
        vm = verdict_re.search(text)
        if vm:
            verdict = vm.group(1)
        dates = date_re.findall(text)
        last_date = dates[-1] if dates else ""
        decision_hint = ""
        for line in text.splitlines():
            low = line.lower()
            if "hold" in low and "mainline" in low:
                decision_hint = "hold"
                break
            if "adopt" in low and "recommend" in low:
                decision_hint = "candidate"
                break
            if "reject" in low and "adopt" in low:
                decision_hint = "reject"
                break
        out[phase] = {
            "verdict": verdict,
            "decision_hint": decision_hint,
            "last_date": last_date,
            "doc_path": str(fp.relative_to(NATIVE_ROOT)).replace("\\", "/"),
        }
    return out


def _discover_session_summaries() -> list[tuple[str, str, Path]]:
    """Return (day, session_name, summary_path) for production-like sessions."""
    found: list[tuple[str, str, Path]] = []
    seen: set[str] = set()

    def add_from(base: Path, day: str) -> None:
        for sess_dir in sorted(base.glob("live_session_*")):
            sp = sess_dir / "small_paper_summary.json"
            if not sp.is_file():
                continue
            key = f"{day}/{sess_dir.name}"
            if key in seen:
                continue
            seen.add(key)
            found.append((day, sess_dir.name, sp))

    if SMALL_PAPER_ROOT.is_dir():
        for day_dir in sorted(SMALL_PAPER_ROOT.iterdir()):
            if not day_dir.is_dir() or day_dir.name.startswith("_"):
                continue
            if re.fullmatch(r"\d{8}", day_dir.name):
                add_from(day_dir, day_dir.name)
        phase630 = SMALL_PAPER_ROOT / "_phase630" / "current"
        if phase630.is_dir():
            for day_dir in sorted(phase630.iterdir()):
                if day_dir.is_dir() and re.fullmatch(r"\d{8}", day_dir.name):
                    add_from(day_dir, day_dir.name)
    return found


def _num(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _nested_get(summary: Mapping[str, Any], key: str) -> Any:
    if "." not in key:
        return summary.get(key)
    head, tail = key.split(".", 1)
    sub = summary.get(head)
    if isinstance(sub, Mapping):
        return sub.get(tail)
    return None


def _extract_dashboard_row(sd: ShadowDef, summary: Mapping[str, Any]) -> dict[str, Any]:
    prefix = sd.dashboard_prefix
    nested = sd.nested_summary_key

    def g(name: str) -> Any:
        if nested:
            sub = summary.get(nested)
            if isinstance(sub, Mapping) and name in sub:
                return sub.get(name)
        if prefix:
            return summary.get(f"{prefix}_{name}") or summary.get(f"{prefix}{name}")
        return summary.get(name)

    block_count = _num(g("block_count")) or _num(g("blocked_count"))
    target_count = _num(g("target_count")) or _num(g("eval_count")) or _num(g("trade_count"))
    net_effect = _num(g("net_effect_yen")) or _num(g("net_effect"))
    blocked_winners = _num(g("blocked_winners"))
    blocked_losers = _num(g("blocked_losers"))
    delta = (
        _num(g("delta_yen"))
        or _num(g("total_delta_yen"))
        or _num(g("t2_delta"))
        or _num(g("t3_delta"))
    )

    if sd.shadow_id == "exit_shadow_monitor_t2_t3":
        t2 = _num(summary.get("shadow_exit_t2_delta"))
        t3 = _num(summary.get("shadow_exit_t3_delta"))
        delta = (t3 or 0.0) + (t2 or 0.0) if t2 is not None or t3 is not None else None
        block_count = _num(summary.get("exit_shadow_monitor_trade_count"))

    if sd.shadow_id == "board_dynamic_trailing_shadow":
        delta = _num(summary.get("board_dynamic_shadow_total_delta_yen"))
        block_count = _num(summary.get("board_dynamic_shadow_exit_count"))

    if sd.shadow_id == "pullback_misread_guard_shadow":
        block_count = _num(summary.get("pullback_misread_guard_shadow_blocked_count"))
        delta = _num(summary.get("pullback_misread_guard_shadow_delta_yen"))

    if sd.shadow_id == "volume_gate_relaxation_shadow":
        block_count = _num(summary.get("volume_shadow_v90_rescued_count"))
        target_count = _num(summary.get("volume_shadow_eval_count"))

    if nested:
        sub = summary.get(nested)
        if isinstance(sub, Mapping):
            net_effect = _num(sub.get("delta_yen_100")) or _num(sub.get("shadow_total_pnl_yen_100"))
            delta = net_effect
            latest = sub.get("status") or sub.get("last_status") or sub.get("verdict")
        else:
            latest = None
    else:
        latest = summary.get(f"{prefix}_monitor_status" if prefix else "") or summary.get(
            "exit_shadow_monitor_status"
        )

    enabled = _yaml_enabled(summary, sd)
    if enabled is None and sd.shadow_id == "pbv2_rise5_shadow":
        enabled = bool(summary.get("pbv2_rise5_shadow_enabled"))
    if enabled is None and sd.shadow_id == "pbv2_flat_band_shadow":
        enabled = bool(summary.get("pbv2_flat_band_shadow_enabled"))

    return {
        "block_count": block_count,
        "target_count": target_count,
        "net_effect": net_effect if net_effect is not None else delta,
        "blocked_winners": blocked_winners,
        "blocked_losers": blocked_losers,
        "overlap": None,
        "delta_yen": delta,
        "latest_status": str(latest) if latest is not None else "",
        "enabled_in_summary": enabled,
    }


def _build_registry_rows(
    defs: Sequence[ShadowDef],
    cfg: Mapping[str, Any],
    ops: Mapping[str, Mapping[str, str]],
    summaries: Sequence[tuple[str, str, Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    last_day_by_shadow: dict[str, str] = {}
    for day, _, sm in summaries:
        for sd in defs:
            if sd.runtime_or_research == "research":
                continue
            if sd.nested_summary_key and isinstance(sm.get(sd.nested_summary_key), Mapping):
                last_day_by_shadow[sd.shadow_id] = max(last_day_by_shadow.get(sd.shadow_id, ""), day)
            elif sd.dashboard_prefix and any(
                k.startswith(sd.dashboard_prefix) for k in sm if isinstance(k, str)
            ):
                last_day_by_shadow[sd.shadow_id] = max(last_day_by_shadow.get(sd.shadow_id, ""), day)
            elif sd.shadow_id == "pbv2_rise5_shadow" and sm.get("pbv2_rise5_shadow_enabled"):
                last_day_by_shadow[sd.shadow_id] = max(last_day_by_shadow.get(sd.shadow_id, ""), day)

    rows: list[dict[str, Any]] = []
    for sd in defs:
        enabled = _yaml_enabled(cfg, sd)
        if enabled is None and sd.runtime_or_research == "research":
            enabled = False
        elif enabled is None:
            enabled = sd.status in ("running", "adopted")

        phase_ops = ops.get(sd.phase.replace("b", "").split("/")[0], {})
        if sd.deprecated_candidate:
            status = "deprecated"
        elif sd.adopted_mainline:
            status = "adopted"
        elif sd.runtime_or_research == "research":
            status = "research_only"
        elif enabled:
            status = "running"
        elif enabled is False:
            status = "disabled"
        else:
            status = sd.status

        decision = sd.decision
        if phase_ops.get("decision_hint"):
            decision = phase_ops["decision_hint"]
        if sd.deprecated_candidate:
            decision = "remove"

        rows.append(
            {
                "shadow_id": sd.shadow_id,
                "phase": sd.phase,
                "name": sd.name,
                "category": sd.category,
                "runtime_or_research": sd.runtime_or_research,
                "entry_or_exit": sd.entry_or_exit,
                "target_pool": sd.target_pool,
                "enabled": enabled,
                "config_keys": "|".join(sd.config_keys),
                "implementation_files": "|".join(sd.implementation_files),
                "summary_fields": "|".join(sd.summary_fields),
                "discord_section": sd.discord_section,
                "status": status,
                "decision": decision,
                "last_evaluated_date": last_day_by_shadow.get(sd.shadow_id)
                or phase_ops.get("last_date", ""),
                "mainline_effect": sd.mainline_effect,
                "owner_layer": sd.owner_layer,
                "risk_if_left_enabled": sd.risk_if_left_enabled,
                "recommended_next_action": sd.recommended_next_action,
            }
        )
    return rows


def _build_dashboard(
    defs: Sequence[ShadowDef],
    summaries: Sequence[tuple[str, str, Mapping[str, Any]]],
) -> dict[str, Any]:
    agg: dict[str, dict[str, Any]] = {}
    per_session: list[dict[str, Any]] = []

    for sd in defs:
        agg[sd.shadow_id] = {
            "shadow_id": sd.shadow_id,
            "phase": sd.phase,
            "name": sd.name,
            "runtime_or_research": sd.runtime_or_research,
            "entry_or_exit": sd.entry_or_exit,
            "sessions_with_data": 0,
            "block_count": 0.0,
            "target_count": 0.0,
            "net_effect": 0.0,
            "blocked_winners": 0.0,
            "blocked_losers": 0.0,
            "delta_yen_total": 0.0,
            "overlap": None,
            "latest_status": "",
            "last_day": "",
        }

    for day, session, sm in summaries:
        for sd in defs:
            if sd.runtime_or_research == "research":
                continue
            row = _extract_dashboard_row(sd, sm)
            has_data = any(
                row.get(k) is not None
                for k in ("block_count", "target_count", "net_effect", "delta_yen", "latest_status")
            )
            if not has_data and not row.get("enabled_in_summary"):
                continue

            a = agg[sd.shadow_id]
            a["sessions_with_data"] += 1
            a["last_day"] = max(a["last_day"], day)
            for k in ("block_count", "target_count", "net_effect", "blocked_winners", "blocked_losers"):
                v = row.get(k) if k != "net_effect" else row.get("net_effect")
                if v is not None:
                    a[k] += float(v)
            if row.get("delta_yen") is not None:
                a["delta_yen_total"] += float(row["delta_yen"])
            if row.get("latest_status"):
                a["latest_status"] = str(row["latest_status"])

            per_session.append(
                {
                    "day": day,
                    "session": session,
                    "shadow_id": sd.shadow_id,
                    "enabled": row.get("enabled_in_summary"),
                    "block_count": row.get("block_count"),
                    "target_count": row.get("target_count"),
                    "net_effect_yen": row.get("net_effect"),
                    "blocked_winners": row.get("blocked_winners"),
                    "blocked_losers": row.get("blocked_losers"),
                    "delta_yen": row.get("delta_yen"),
                    "latest_status": row.get("latest_status"),
                }
            )

    # Flat-band + rise5 overlap from summaries
    overlap_sessions = 0
    for _, _, sm in summaries:
        if sm.get("pbv2_rise5_shadow_enabled") and sm.get("pbv2_flat_band_shadow_enabled"):
            r_blocks = int(sm.get("pbv2_rise5_shadow_block_count") or 0)
            f_blocks = int(sm.get("pbv2_flat_band_shadow_block_count") or 0)
            if r_blocks > 0 and f_blocks > 0:
                overlap_sessions += 1
    if "pbv2_rise5_shadow" in agg:
        agg["pbv2_rise5_shadow"]["overlap"] = {"with_flat_band_blocked_sessions": overlap_sessions}
    if "pbv2_flat_band_shadow" in agg:
        agg["pbv2_flat_band_shadow"]["overlap"] = {"with_rise5_blocked_sessions": overlap_sessions}

    return {
        "generated_at": _now_iso(),
        "session_count": len(summaries),
        "shadows": agg,
        "per_session_rows": len(per_session),
    }


def _mandatory_answers(
    registry: Sequence[Mapping[str, Any]],
    dashboard: Mapping[str, Any],
) -> dict[str, Any]:
    runtime_entry = [
        r["shadow_id"]
        for r in registry
        if r.get("runtime_or_research") == "runtime"
        and r.get("entry_or_exit") == "entry"
        and r.get("status") == "running"
        and r.get("category") in ("entry_runtime", "extension_shadow")
    ]
    runtime_exit = [
        r["shadow_id"]
        for r in registry
        if r.get("runtime_or_research") == "runtime"
        and r.get("entry_or_exit") == "exit"
        and r.get("status") == "running"
        and r.get("category") in ("exit_runtime",)
    ]
    forward_runtime = [
        r["shadow_id"]
        for r in registry
        if r.get("category") == "forward_shadow" and r.get("status") == "running"
    ]
    research_only = [
        r["shadow_id"]
        for r in registry
        if r.get("status") == "research_only" or r.get("runtime_or_research") == "research"
    ]
    discord_shadows = [
        r["shadow_id"]
        for r in registry
        if r.get("discord_section") and str(r.get("discord_section")).lower() != "none"
    ]
    adopted = [r["shadow_id"] for r in registry if r.get("status") == "adopted"]
    observing = [r["shadow_id"] for r in registry if r.get("decision") == "observe" and r.get("status") == "running"]
    deprecate = [r["shadow_id"] for r in registry if r.get("status") == "deprecated" or r.get("decision") == "remove"]

    shadows_agg = dashboard.get("shadows", {})
    rise5_net = shadows_agg.get("pbv2_rise5_shadow", {}).get("net_effect", 0)
    flat_net = shadows_agg.get("pbv2_flat_band_shadow", {}).get("net_effect", 0)

    next_mainline_candidates = []
    if flat_net and float(flat_net) > 0:
        next_mainline_candidates.append("pbv2_flat_band_shadow")
    elif shadows_agg.get("pbv2_flat_band_shadow", {}).get("sessions_with_data", 0) < 5:
        next_mainline_candidates.append("pbv2_flat_band_shadow")  # collect forward days first
    if rise5_net and float(rise5_net) > 0:
        next_mainline_candidates.append("pbv2_rise5_shadow")
    t3_delta = shadows_agg.get("exit_shadow_monitor_t2_t3", {}).get("delta_yen_total", 0)
    if t3_delta and float(t3_delta) > 0:
        next_mainline_candidates.append("exit_shadow_monitor_t2_t3")
    if not next_mainline_candidates:
        next_mainline_candidates = ["pbv2_flat_band_shadow", "pbv2_rise5_shadow", "exit_shadow_monitor_t2_t3"]

    return {
        "1_runtime_entry_shadows": runtime_entry,
        "2_runtime_exit_shadows": runtime_exit,
        "2b_forward_shadow_auto_runtime": forward_runtime,
        "3_research_only_shadows": research_only,
        "4_discord_shadows": discord_shadows,
        "5_adopted_mainline": adopted,
        "6_observing": observing,
        "7_deprecation_candidates": deprecate,
        "8_too_many_shadows_operational_risk": len(
            [r for r in registry if r.get("status") == "running" and r.get("runtime_or_research") == "runtime"]
        )
        > 12,
        "8_risk_assessment": (
            "Moderate — many extension/forward shadows add summary/Discord surface area "
            "but most are log-only; watch tick-level realtime_board CPU and Discord length."
        ),
        "9_rules_for_adding_shadows": [
            "Must not block ENTRY/EXIT unless explicit adoption phase",
            "Require config rollback key (enabled=false)",
            "Register in phase652 registry before merge",
            "Discord section only if operator KPI exists",
            "Minimum 5 forward sessions before mainline candidacy",
            "Shadow-only YAML pin update if config keys added",
        ],
        "10_next_mainline_promotion_candidates": next_mainline_candidates[:3],
        "rise5_aggregate_net_effect_yen": rise5_net,
        "flat_band_aggregate_net_effect_yen": flat_net,
    }


def run(
    *,
    report_dir: Optional[Path] = None,
    production_yaml: Optional[Path] = None,
) -> dict[str, Any]:
    out_dir = report_dir or (NATIVE_ROOT / "results" / "reports" / REPORT_DIR_NAME)
    out_dir.mkdir(parents=True, exist_ok=True)
    yaml_path = production_yaml or PRODUCTION_YAML
    cfg = _load_yaml_config(yaml_path)
    ops = _parse_ops_docs()
    defs = _registry_definitions()

    summary_paths = _discover_session_summaries()
    summaries: list[tuple[str, str, Mapping[str, Any]]] = []
    for day, session, sp in summary_paths:
        try:
            sm = json.loads(sp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(sm, dict):
            summaries.append((day, session, sm))

    registry_rows = _build_registry_rows(defs, cfg, ops, summaries)
    dashboard = _build_dashboard(defs, summaries)
    mandatory = _mandatory_answers(registry_rows, dashboard)

    registry_csv = out_dir / "phase652_shadow_registry.csv"
    _write_csv(registry_csv, REGISTRY_COLUMNS, registry_rows)

    summary_rows: list[dict[str, Any]] = []
    for day, session, sm in summaries:
        for sd in defs:
            if sd.runtime_or_research == "research":
                continue
            row = _extract_dashboard_row(sd, sm)
            if not any(
                row.get(k) is not None
                for k in ("block_count", "target_count", "net_effect", "delta_yen")
            ):
                continue
            summary_rows.append(
                {
                    "day": day,
                    "session": session,
                    "shadow_id": sd.shadow_id,
                    "enabled": row.get("enabled_in_summary"),
                    "block_count": row.get("block_count"),
                    "target_count": row.get("target_count"),
                    "net_effect_yen": row.get("net_effect"),
                    "blocked_winners": row.get("blocked_winners"),
                    "blocked_losers": row.get("blocked_losers"),
                    "delta_yen": row.get("delta_yen"),
                    "latest_status": row.get("latest_status"),
                }
            )

    summary_csv = out_dir / "phase652_shadow_summary.csv"
    _write_csv(summary_csv, SUMMARY_COLUMNS, summary_rows)

    dashboard_json = out_dir / "phase652_shadow_dashboard.json"
    dashboard_export = {k: v for k, v in dashboard.items() if k != "per_session_rows"}
    dashboard_json.write_text(json.dumps(dashboard_export, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report = {
        "phase": "652",
        "verdict": PHASE652_VERDICT,
        "generated_at": _now_iso(),
        "production_yaml": str(yaml_path),
        "session_count": len(summaries),
        "registry_count": len(registry_rows),
        "mandatory_answers": mandatory,
        "artifacts": {
            "registry_csv": str(registry_csv),
            "dashboard_json": str(dashboard_json),
            "summary_csv": str(summary_csv),
        },
    }
    report_path = out_dir / "phase652_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report["artifacts"]["report"] = str(report_path)
    return report


def main() -> int:
    report = run()
    print(json.dumps({"verdict": report["verdict"], "paths": report["artifacts"]}, indent=2))
    print(json.dumps(report["mandatory_answers"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
