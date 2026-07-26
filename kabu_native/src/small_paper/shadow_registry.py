"""Phase677 / Shadow Portfolio Cleanup — canonical Shadow registry.

Observe-only registry; no mainline ENTRY/EXIT changes.
Management classes drive Paper runtime enablement and Discord visibility.
"""
from __future__ import annotations

from typing import Any, Optional

# status vocabulary
STATUS = (
    "RUNNING_PNL_COMPLETE",
    "RUNNING_PNL_INCOMPLETE",
    "RUNNING_CLASSIFICATION_ONLY",
    "RUNNING_LOGGER_ONLY",
    "ENABLED_NO_EVENTS",
    "DISABLED",
    "ADOPTED_MAINLINE_LEGACY_SHADOW",
    "RESEARCH_ONLY",
    "BROKEN",
    "DUPLICATE",
    "DEPRECATED",
    "RETIRED",
    "DISABLED_RESEARCH",
    "ACTIVE_FORWARD",
    "TEMP_FORWARD",
    "MAINLINE_MONITOR",
    "LOGGER_ONLY",
    "MAINLINE_COMPONENT",
)

MANAGEMENT_CLASSES = (
    "ACTIVE_FORWARD",
    "TEMP_FORWARD",
    "MAINLINE_MONITOR",
    "LOGGER_ONLY",
    "MAINLINE_COMPONENT",
    "RETIRED",
    "UNKNOWN_BLOCKED",
)

DISCORD_SUMMARY_CLASSES = frozenset(
    {"ACTIVE_FORWARD", "TEMP_FORWARD", "MAINLINE_MONITOR"}
)
RUNTIME_ACTIVE_CLASSES = frozenset(
    {"ACTIVE_FORWARD", "TEMP_FORWARD", "MAINLINE_MONITOR", "LOGGER_ONLY"}
)

ShadowDef = dict[str, Any]

SHADOW_REGISTRY: list[ShadowDef] = [
    {
        "canonical_shadow_id": "e1_x5_forward_shadow",
        "display_name": "E1_X5 Forward Shadow",
        "phase": "E1X5-FWD",
        "category": "ENTRY_EXIT_STRATEGY",
        "implementation_file": "src/small_paper/e1_x5_forward_shadow.py",
        "config_key": None,
        "env_key": "E1_X5_FORWARD_SHADOW",
        "default_enabled": True,
        "observe_only": True,
        "mainline_effect": False,
        "pnl_applicable": True,
        "pnl_semantics": "Paper default ON; independent CAP5 ask→bid 5bps; no PBv2 CAP; Live forced OFF",
        "join_key": "virtual_position",
        "summary_prefix": "e1_x5_forward_shadow_",
        "discord_section": "E1_X5 Shadow",
        "aliases": ["E1_X5", "e1x5"],
    },
    {
        "canonical_shadow_id": "flat_weak_range_shadow",
        "display_name": "Flat Weak + Range",
        "phase": "670",
        "category": "ENTRY_DECISION",
        "implementation_file": "src/small_paper/flat_weak_range_forward_shadow.py",
        "config_key": "flat_weak_range_shadow_enabled",
        "env_key": None,
        "default_enabled": True,
        "observe_only": True,
        "mainline_effect": False,
        "pnl_applicable": True,
        "pnl_semantics": "block→shadow_pnl=0 else actual; delta=shadow-runtime",
        "join_key": "position_id",
        "summary_prefix": "flat_weak_range_shadow_",
        "discord_section": "Flat Weak + Range",
        "aliases": ["flat_weak_range_forward_shadow"],
    },
    {
        "canonical_shadow_id": "board_imbalance_reversal_shadow",
        "display_name": "Board Imbalance Reversal",
        "phase": "H_BOARD_TS",
        "category": "ENTRY_DECISION",
        "implementation_file": "src/small_paper/board_imbalance_reversal_shadow.py",
        "config_key": None,
        "env_key": "BOARD_IMBALANCE_REVERSAL_SHADOW",
        "default_enabled": True,
        "observe_only": True,
        "mainline_effect": False,
        "pnl_applicable": True,
        "pnl_semantics": "H_board_ts virtual reject; CF delta vs PBv2; Paper default ON; Live OFF",
        "join_key": "position_id",
        "summary_prefix": "board_imbalance_reversal_",
        "discord_section": None,
        "aliases": ["H_board_ts", "board_imbalance_reversal"],
        "notes": "TEMP_FORWARD; not Discord Summary (evidence gate); distinct from retired board_imbalance_shadow",
    },
    {
        "canonical_shadow_id": "pullback_misread_guard_shadow",
        "display_name": "Pullback Misread",
        "phase": "multiple",
        "category": "ENTRY_DECISION",
        "implementation_file": "src/small_paper/pullback_misread_entry_guard_shadow.py",
        "config_key": "pullback_misread_guard_shadow_enabled",
        "env_key": None,
        "default_enabled": True,
        "observe_only": True,
        "mainline_effect": False,
        "pnl_applicable": True,
        "pnl_semantics": "block→0 else actual",
        "join_key": "position_id",
        "summary_prefix": "pullback_misread_guard_shadow_",
        "discord_section": "PullbackMisread",
        "aliases": ["pullback_misread_entry_guard_shadow"],
    },
    {
        "canonical_shadow_id": "cost_aware_entry_shadow",
        "display_name": "Cost-Aware Entry (W54-FIX)",
        "phase": "W54",
        "category": "ENTRY_DECISION",
        "implementation_file": "src/small_paper/cost_aware_entry_shadow.py",
        "config_key": "cost_aware_entry_shadow.enabled",
        "env_key": "COST_AWARE_ENTRY_SHADOW",
        "default_enabled": True,
        "observe_only": True,
        "mainline_effect": False,
        "pnl_applicable": True,
        "pnl_semantics": "virtual Cap5 30m ±5bps; runtime_compatible optional",
        "join_key": "virtual_position",
        "summary_prefix": "cost_aware_entry_shadow",
        "discord_section": "Cost-Aware",
        "aliases": ["W54-FIX", "cost_aware_entry"],
    },
    {
        "canonical_shadow_id": "cost_aware_entry_v2_shadow",
        "display_name": "Cost-Aware Entry V2",
        "phase": "CostAwareV2",
        "category": "ENTRY_DECISION",
        "implementation_file": "src/small_paper/cost_aware_entry_v2_shadow.py",
        "config_key": "cost_aware_entry_v2_shadow.enabled",
        "env_key": "COST_AWARE_ENTRY_V2_SHADOW",
        "default_enabled": True,
        "observe_only": True,
        "mainline_effect": False,
        "pnl_applicable": True,
        "pnl_semantics": "counterfactual reject→0 vs runtime; observe-only; not mixed into canonical",
        "join_key": "position_id",
        "summary_prefix": "cost_aware_entry_v2_shadow",
        "discord_section": "Cost-Aware V2 Shadow",
        "aliases": ["CostAwareV2", "cost_aware_v2"],
    },
    {
        "canonical_shadow_id": "board_dynamic_trailing_shadow",
        "display_name": "Board Dynamic Trailing",
        "phase": "332",
        "category": "EXIT_DECISION",
        "implementation_file": "src/small_paper/board_dynamic_trailing_shadow.py",
        "config_key": "board_dynamic_shadow_enabled",
        "env_key": None,
        "default_enabled": True,
        "observe_only": True,
        "mainline_effect": True,
        "pnl_applicable": True,
        "pnl_semantics": "actual_vs_shadow_delta_yen (legacy CF vs board-dynamic)",
        "join_key": "position_id",
        "summary_prefix": "board_dynamic_shadow_",
        "discord_section": "BoardDynamic",
        "aliases": [],
    },
    {
        "canonical_shadow_id": "pbv2_rise5_shadow",
        "display_name": "PBv2 Rise5",
        "phase": "635",
        "category": "ENTRY_DECISION",
        "implementation_file": "src/small_paper/pbv2_rise5_shadow.py",
        "config_key": "pbv2_rise5_shadow_enabled",
        "env_key": None,
        "default_enabled": False,
        "observe_only": True,
        "mainline_effect": False,
        "pnl_applicable": True,
        "pnl_semantics": "block→0",
        "join_key": "position_id",
        "summary_prefix": "pbv2_rise5_shadow_",
        "discord_section": "Rise5",
        "aliases": [],
    },
    {
        "canonical_shadow_id": "pbv2_flat_band_shadow",
        "display_name": "PBv2 Flat-band (legacy shadow)",
        "phase": "650",
        "category": "ENTRY_DECISION",
        "implementation_file": "src/small_paper/pbv2_flat_band_guard_shadow.py",
        "config_key": "pbv2_flat_band_shadow_enabled",
        "env_key": None,
        "default_enabled": False,
        "observe_only": True,
        "mainline_effect": True,
        "pnl_applicable": False,
        "pnl_semantics": "ADOPTED_MAINLINE_LEGACY_SHADOW",
        "join_key": "position_id",
        "summary_prefix": "pbv2_flat_band_shadow_",
        "discord_section": "Flat-band",
        "aliases": [],
        "notes": "ADOPTED_MAINLINE_LEGACY_SHADOW — excluded from Runtime PnL audit",
        "status_override": "ADOPTED_MAINLINE_LEGACY_SHADOW",
    },
    {
        "canonical_shadow_id": "board_imbalance_shadow",
        "display_name": "Board Imbalance",
        "phase": "multiple",
        "category": "ENTRY_DECISION",
        "implementation_file": "src/small_paper/board_imbalance_shadow.py",
        "config_key": "imbalance_shadow_enabled",
        "env_key": None,
        "default_enabled": True,
        "observe_only": True,
        "mainline_effect": False,
        "pnl_applicable": True,
        "pnl_semantics": "candidate join yen_100 (Phase678)",
        "join_key": "position_id",
        "summary_prefix": "imbalance_shadow_",
        "discord_section": "BoardImbalance",
        "aliases": ["imbalance_shadow"],
    },
    {
        "canonical_shadow_id": "limit_up_proximity_entry_guard_shadow",
        "display_name": "Limit-up Proximity",
        "phase": "351",
        "category": "ENTRY_DECISION",
        "implementation_file": "src/small_paper/limit_up_proximity_entry_guard_shadow.py",
        "config_key": "limit_up_proximity_guard_shadow_enabled",
        "env_key": None,
        "default_enabled": True,
        "observe_only": True,
        "mainline_effect": False,
        "pnl_applicable": True,
        "pnl_semantics": "block→0",
        "join_key": "position_id",
        "summary_prefix": "limit_up_proximity_guard_shadow_",
        "discord_section": "LimitUpProximity",
        "aliases": [],
    },
    {
        "canonical_shadow_id": "extended_entry_shadow",
        "display_name": "Extended Entry",
        "phase": "183",
        "category": "ENTRY_DECISION",
        "implementation_file": "src/small_paper/extended_entry_shadow.py",
        "config_key": None,
        "env_key": None,
        "default_enabled": True,
        "observe_only": True,
        "mainline_effect": False,
        "pnl_applicable": False,
        "pnl_semantics": "CLASSIFICATION_FEATURE_ONLY (pct estimate, not yen join)",
        "join_key": "flag",
        "summary_prefix": "extended_entry_shadow_",
        "discord_section": "ExtendedEntry",
        "aliases": [],
        "status_override": "RUNNING_CLASSIFICATION_ONLY",
    },
    {
        "canonical_shadow_id": "readiness_precision_shadow",
        "display_name": "Readiness I",
        "phase": "679",
        "category": "ENTRY_DECISION",
        "implementation_file": "src/small_paper/readiness_forward_shadow.py",
        "config_key": "readiness_precision_shadow_enabled",
        "env_key": None,
        "default_enabled": True,
        "observe_only": True,
        "mainline_effect": False,
        "pnl_applicable": True,
        "pnl_semantics": "IHC bundle",
        "join_key": "position_id",
        "summary_prefix": "readiness_precision_shadow_",
        "discord_section": "Readiness",
        "aliases": ["I_block"],
    },
    {
        "canonical_shadow_id": "readiness_economics_shadow",
        "display_name": "Readiness H",
        "phase": "679",
        "category": "ENTRY_DECISION",
        "implementation_file": "src/small_paper/readiness_forward_shadow.py",
        "config_key": "readiness_economics_shadow_enabled",
        "env_key": None,
        "default_enabled": True,
        "observe_only": True,
        "mainline_effect": False,
        "pnl_applicable": True,
        "pnl_semantics": "IHC bundle",
        "join_key": "position_id",
        "summary_prefix": "readiness_economics_shadow_",
        "discord_section": "Readiness",
        "aliases": ["H_block"],
    },
    {
        "canonical_shadow_id": "readiness_refined_h_shadow",
        "display_name": "Readiness refined-H",
        "phase": "680",
        "category": "RESEARCH_ONLY",
        "implementation_file": "src/small_paper/readiness_forward_shadow.py",
        "config_key": "readiness_refined_h_shadow_enabled",
        "env_key": None,
        "default_enabled": True,
        "observe_only": True,
        "mainline_effect": False,
        "pnl_applicable": False,
        "pnl_semantics": "CLASSIFICATION_ONLY / research_only",
        "join_key": "position_id",
        "summary_prefix": "readiness_refined_h_shadow_",
        "discord_section": None,
        "aliases": [],
    },
    {
        "canonical_shadow_id": "microsequence_recovery_fail_shadow",
        "display_name": "Microsequence C",
        "phase": "681",
        "category": "ENTRY_DECISION",
        "implementation_file": "src/small_paper/microsequence_recovery_fail_forward_shadow.py",
        "config_key": "microsequence_recovery_fail_shadow_enabled",
        "env_key": None,
        "default_enabled": True,
        "observe_only": True,
        "mainline_effect": False,
        "pnl_applicable": True,
        "pnl_semantics": "IHC C rule",
        "join_key": "position_id",
        "summary_prefix": "microsequence_recovery_fail_shadow_",
        "discord_section": "IHC",
        "aliases": [],
    },
    {
        "canonical_shadow_id": "exit_shadow_monitor_t2_t3",
        "display_name": "EXIT T2/T3 Monitor",
        "phase": "563",
        "category": "EXIT_DECISION",
        "implementation_file": "src/small_paper/exit_shadow_monitor.py",
        "config_key": "exit_shadow_monitor_enabled",
        "env_key": None,
        "default_enabled": False,
        "observe_only": True,
        "mainline_effect": False,
        "pnl_applicable": True,
        "pnl_semantics": "t2/t3 delta",
        "join_key": "position_id",
        "summary_prefix": "exit_shadow_monitor_",
        "discord_section": "EXIT monitor",
        "aliases": ["exit_shadow_t2", "exit_shadow_t3"],
    },
    {
        "canonical_shadow_id": "vwap_shadow_reject",
        "display_name": "VWAP Reject",
        "phase": "186",
        "category": "ENTRY_DECISION",
        "implementation_file": "src/small_paper/vwap_shadow_reject.py",
        "config_key": "vwap_shadow_reject_enabled",
        "env_key": None,
        "default_enabled": False,
        "observe_only": True,
        "mainline_effect": False,
        "pnl_applicable": True,
        "pnl_semantics": "candidate pnl",
        "join_key": "position_id",
        "summary_prefix": "vwap_shadow_",
        "discord_section": None,
        "aliases": [],
    },
    {
        "canonical_shadow_id": "volume_gate_relaxation_shadow",
        "display_name": "Volume Gate Relaxation",
        "phase": "590",
        "category": "LOGGER_ONLY",
        "implementation_file": "src/small_paper/volume_gate_relaxation_shadow.py",
        "config_key": "volume_gate_relaxation_shadow_enabled",
        "env_key": None,
        "default_enabled": False,
        "observe_only": True,
        "mainline_effect": False,
        "pnl_applicable": False,
        "pnl_semantics": "CLASSIFICATION_ONLY",
        "join_key": None,
        "summary_prefix": "volume_gate_relaxation_shadow_",
        "discord_section": None,
        "aliases": [],
    },
    {
        "canonical_shadow_id": "pullback_volume_forward",
        "display_name": "Pullback Volume Forward",
        "phase": "W57",
        "category": "LOGGER_ONLY",
        "implementation_file": "src/small_paper/pullback_volume_forward_logger.py",
        "config_key": None,
        "env_key": "PULLBACK_VOLUME_FORWARD",
        "default_enabled": True,
        "observe_only": True,
        "mainline_effect": False,
        "pnl_applicable": False,
        "pnl_semantics": "CLASSIFICATION_ONLY (bucket rates)",
        "join_key": None,
        "summary_prefix": "pullback_volume_forward",
        "discord_section": "PullbackVolume",
        "aliases": ["w43f_pullback_volume"],
    },
    {
        "canonical_shadow_id": "post_entry_forward_shadow",
        "display_name": "Post Entry Forward",
        "phase": "500",
        "category": "EXIT_DECISION",
        "implementation_file": "src/small_paper/post_entry_forward_shadow.py",
        "config_key": None,
        "env_key": None,
        "default_enabled": True,
        "observe_only": True,
        "mainline_effect": False,
        "pnl_applicable": True,
        "pnl_semantics": "score bucket pnl (summary fields)",
        "join_key": "position_id",
        "summary_prefix": "post_entry_shadow_",
        "discord_section": "PostEntry",
        "aliases": [],
    },
    {
        "canonical_shadow_id": "classic_momentum_forward_shadow",
        "display_name": "Classic Momentum Forward",
        "phase": "513",
        "category": "ENTRY_DECISION",
        "implementation_file": "src/small_paper/classic_momentum_forward_shadow.py",
        "config_key": None,
        "env_key": None,
        "default_enabled": True,
        "observe_only": True,
        "mainline_effect": False,
        "pnl_applicable": True,
        "pnl_semantics": "virtual yen_100 (summary classic_momentum_shadow_pnl_yen_100)",
        "join_key": "virtual",
        "summary_prefix": "classic_momentum_shadow_",
        "discord_section": "ClassicMomentum",
        "aliases": [],
    },
    {
        "canonical_shadow_id": "entry_expectancy_score_shadow",
        "display_name": "Entry Expectancy Score",
        "phase": "230",
        "category": "ENTRY_DECISION",
        "implementation_file": "src/small_paper/entry_expectancy_score_shadow.py",
        "config_key": "entry_expectancy_score_shadow_enabled",
        "env_key": None,
        "default_enabled": True,
        "observe_only": False,
        "mainline_effect": True,
        "pnl_applicable": False,
        "pnl_semantics": "ADOPTED_MAINLINE",
        "join_key": None,
        "summary_prefix": "phase230_entry_expectancy_shadow",
        "discord_section": None,
        "aliases": [],
    },
    {
        "canonical_shadow_id": "entry_price_risk_guard_shadow",
        "display_name": "Entry Price Risk Guard",
        "phase": "153b",
        "category": "ENTRY_DECISION",
        "implementation_file": "src/small_paper/entry_price_risk_guard.py",
        "config_key": "entry_price_risk_guard_shadow",
        "env_key": None,
        "default_enabled": True,
        "observe_only": False,
        "mainline_effect": True,
        "pnl_applicable": False,
        "pnl_semantics": "ADOPTED_MAINLINE",
        "join_key": None,
        "summary_prefix": "entry_price_risk_guard_shadow",
        "discord_section": None,
        "aliases": [],
    },
    {
        "canonical_shadow_id": "realtime_board_exit_shadow",
        "display_name": "Realtime Board EXIT",
        "phase": "335",
        "category": "EXIT_DECISION",
        "implementation_file": "src/small_paper/realtime_board_exit_shadow.py",
        "config_key": None,
        "env_key": None,
        "default_enabled": False,
        "observe_only": True,
        "mainline_effect": False,
        "pnl_applicable": True,
        "pnl_semantics": "virtual exit yen",
        "join_key": "position_id",
        "summary_prefix": "realtime_board_exit_",
        "discord_section": None,
        "aliases": [],
    },
    {
        "canonical_shadow_id": "quality_formula_shadow",
        "display_name": "Quality Formula",
        "phase": "multiple",
        "category": "LOGGER_ONLY",
        "implementation_file": "src/small_paper/quality_formula_shadow.py",
        "config_key": None,
        "env_key": None,
        "default_enabled": True,
        "observe_only": True,
        "mainline_effect": False,
        "pnl_applicable": False,
        "pnl_semantics": "CLASSIFICATION_ONLY",
        "join_key": None,
        "summary_prefix": "quality_formula_",
        "discord_section": None,
        "aliases": [],
    },
    {
        "canonical_shadow_id": "trading_value_shadow_gate",
        "display_name": "Trading Value Gate",
        "phase": "multiple",
        "category": "LOGGER_ONLY",
        "implementation_file": "src/small_paper/trading_value_shadow_gate.py",
        "config_key": None,
        "env_key": None,
        "default_enabled": True,
        "observe_only": True,
        "mainline_effect": False,
        "pnl_applicable": False,
        "pnl_semantics": "CLASSIFICATION_ONLY",
        "join_key": None,
        "summary_prefix": "trading_value_",
        "discord_section": None,
        "aliases": [],
    },
    {
        "canonical_shadow_id": "np_pre_entry_feature_logger",
        "display_name": "NP Pre-Entry Feature Logger",
        "phase": "687",
        "category": "LOGGER_ONLY",
        "implementation_file": "src/small_paper/np_pre_entry_feature_logger.py",
        "config_key": "np_pre_entry_feature_logger_enabled",
        "env_key": None,
        "default_enabled": True,
        "observe_only": True,
        "mainline_effect": False,
        "pnl_applicable": False,
        "pnl_semantics": "CLASSIFICATION_ONLY",
        "join_key": None,
        "summary_prefix": "np_pre_entry_",
        "discord_section": None,
        "aliases": [],
    },
    {
        "canonical_shadow_id": "sector_heat_forward_shadow",
        "display_name": "Sector Heat Forward",
        "phase": "research",
        "category": "UNIVERSE",
        "implementation_file": "src/small_paper/sector_heat_forward_shadow_auto.py",
        "config_key": None,
        "env_key": None,
        "default_enabled": False,
        "observe_only": True,
        "mainline_effect": False,
        "pnl_applicable": False,
        "pnl_semantics": "PNL_NOT_EVALUABLE_MISSING_COUNTERFACTUAL_PRICE_PATH unless logged",
        "join_key": None,
        "summary_prefix": "sector_heat_",
        "discord_section": None,
        "aliases": [],
    },
    {
        "canonical_shadow_id": "risk_sizing_forward_shadow",
        "display_name": "Risk Sizing Forward",
        "phase": "research",
        "category": "POSITION_SIZING",
        "implementation_file": "src/small_paper/risk_sizing_forward_shadow_auto.py",
        "config_key": None,
        "env_key": None,
        "default_enabled": False,
        "observe_only": True,
        "mainline_effect": False,
        "pnl_applicable": True,
        "pnl_semantics": "sized vs fixed 100",
        "join_key": None,
        "summary_prefix": "risk_sizing_",
        "discord_section": None,
        "aliases": [],
    },
    {
        "canonical_shadow_id": "equity_dynamic_stop_shadow",
        "display_name": "Equity Dynamic Stop",
        "phase": "research",
        "category": "EXIT_DECISION",
        "implementation_file": "src/small_paper/equity_dynamic_stop_shadow_auto.py",
        "config_key": None,
        "env_key": None,
        "default_enabled": False,
        "observe_only": True,
        "mainline_effect": False,
        "pnl_applicable": True,
        "pnl_semantics": "policy CF",
        "join_key": None,
        "summary_prefix": "equity_dynamic_stop_",
        "discord_section": None,
        "aliases": [],
    },
    {
        "canonical_shadow_id": "live_config_forward_shadow",
        "display_name": "Live Config Forward",
        "phase": "research",
        "category": "POSITION_SIZING",
        "implementation_file": "src/small_paper/live_config_forward_shadow_auto.py",
        "config_key": None,
        "env_key": None,
        "default_enabled": False,
        "observe_only": True,
        "mainline_effect": False,
        "pnl_applicable": True,
        "pnl_semantics": "scenario equity",
        "join_key": None,
        "summary_prefix": "live_config_",
        "discord_section": None,
        "aliases": [],
    },
    {
        "canonical_shadow_id": "live_config_transition_shadow",
        "display_name": "Live Config Transition",
        "phase": "research",
        "category": "POSITION_SIZING",
        "implementation_file": "src/small_paper/live_config_transition_shadow_auto.py",
        "config_key": None,
        "env_key": None,
        "default_enabled": False,
        "observe_only": True,
        "mainline_effect": False,
        "pnl_applicable": True,
        "pnl_semantics": "transition CF",
        "join_key": None,
        "summary_prefix": "live_config_transition_",
        "discord_section": None,
        "aliases": [],
    },
    {
        "canonical_shadow_id": "w43f_evaluation_reachability",
        "display_name": "W43F Evaluation Reachability",
        "phase": "W43F",
        "category": "DATA_QUALITY",
        "implementation_file": "src/small_paper/evaluation_reachability.py",
        "config_key": None,
        "env_key": None,
        "default_enabled": True,
        "observe_only": True,
        "mainline_effect": False,
        "pnl_applicable": False,
        "pnl_semantics": "CLASSIFICATION_ONLY",
        "join_key": None,
        "summary_prefix": "evaluation_",
        "discord_section": None,
        "aliases": [],
    },
    {
        "canonical_shadow_id": "low_liquidity_shadow",
        "display_name": "Low Liquidity",
        "phase": "179c",
        "category": "ENTRY_DECISION",
        "implementation_file": "src/small_paper/pilot_runner.py",
        "config_key": "low_liquidity_shadow_enabled",
        "env_key": None,
        "default_enabled": False,
        "observe_only": True,
        "mainline_effect": False,
        "pnl_applicable": False,
        "pnl_semantics": "DEPRECATED",
        "join_key": None,
        "summary_prefix": "low_liquidity_shadow",
        "discord_section": None,
        "aliases": [],
    },
]

# Shadow Portfolio Cleanup classification (exactly one per registry entry).
_PORTFOLIO_CLASS: dict[str, str] = {
    "e1_x5_forward_shadow": "ACTIVE_FORWARD",
    "flat_weak_range_shadow": "TEMP_FORWARD",
    "board_imbalance_reversal_shadow": "TEMP_FORWARD",
    "board_dynamic_trailing_shadow": "MAINLINE_MONITOR",
    "w43f_evaluation_reachability": "LOGGER_ONLY",
    "pullback_volume_forward": "LOGGER_ONLY",
    "entry_price_risk_guard_shadow": "MAINLINE_COMPONENT",
    "entry_expectancy_score_shadow": "MAINLINE_COMPONENT",
    "pbv2_flat_band_shadow": "MAINLINE_COMPONENT",
    # Cost-Aware: v1 retired; v2 lacks active Forward Gate (OFFLINE_CANDIDATE_ONLY)
    "cost_aware_entry_shadow": "RETIRED",
    "cost_aware_entry_v2_shadow": "RETIRED",
    # REMOVE-decided
    "pbv2_rise5_shadow": "RETIRED",
    "exit_shadow_monitor_t2_t3": "RETIRED",
    "vwap_shadow_reject": "RETIRED",
    "low_liquidity_shadow": "RETIRED",
    # Default-OFF cleanup
    "pullback_misread_guard_shadow": "RETIRED",
    "board_imbalance_shadow": "RETIRED",
    "extended_entry_shadow": "RETIRED",
    "limit_up_proximity_entry_guard_shadow": "RETIRED",
    "readiness_precision_shadow": "RETIRED",
    "readiness_economics_shadow": "RETIRED",
    "readiness_refined_h_shadow": "RETIRED",
    "microsequence_recovery_fail_shadow": "RETIRED",
    "post_entry_forward_shadow": "RETIRED",
    "classic_momentum_forward_shadow": "RETIRED",
    "realtime_board_exit_shadow": "RETIRED",
    "sector_heat_forward_shadow": "RETIRED",
    "risk_sizing_forward_shadow": "RETIRED",
    "equity_dynamic_stop_shadow": "RETIRED",
    "live_config_forward_shadow": "RETIRED",
    "live_config_transition_shadow": "RETIRED",
    "volume_gate_relaxation_shadow": "RETIRED",
    "quality_formula_shadow": "RETIRED",
    "trading_value_shadow_gate": "RETIRED",
    "np_pre_entry_feature_logger": "RETIRED",
}

_STATUS_BY_CLASS: dict[str, str] = {
    "ACTIVE_FORWARD": "ACTIVE_FORWARD",
    "TEMP_FORWARD": "TEMP_FORWARD",
    "MAINLINE_MONITOR": "MAINLINE_MONITOR",
    "LOGGER_ONLY": "LOGGER_ONLY",
    "MAINLINE_COMPONENT": "MAINLINE_COMPONENT",
    "RETIRED": "RETIRED",
    "UNKNOWN_BLOCKED": "UNKNOWN_BLOCKED",
}


def _normalize_registry() -> None:
    """Apply portfolio cleanup metadata onto SHADOW_REGISTRY (idempotent)."""
    for r in SHADOW_REGISTRY:
        cid = str(r["canonical_shadow_id"])
        mc = _PORTFOLIO_CLASS.get(cid, "UNKNOWN_BLOCKED")
        r["management_class"] = mc
        r["discord_visible"] = mc in DISCORD_SUMMARY_CLASSES
        r["counts_toward_active_shadow"] = mc in RUNTIME_ACTIVE_CLASSES
        r["counts_toward_active_pnl_shadow"] = bool(
            r.get("pnl_applicable") and mc in DISCORD_SUMMARY_CLASSES
        )
        if mc == "RETIRED":
            r["default_enabled"] = False
            if cid == "cost_aware_entry_v2_shadow":
                r["status_override"] = "DISABLED_RESEARCH"
            else:
                r["status_override"] = "RETIRED"
            r["discord_section"] = None
        elif mc == "MAINLINE_COMPONENT":
            # History / mainline; not counted as Forward Shadow
            r["discord_visible"] = False
            r["counts_toward_active_shadow"] = False
            if cid == "pbv2_flat_band_shadow":
                r["default_enabled"] = False  # legacy CF shadow off; mainline stays
                r["status_override"] = "MAINLINE_COMPONENT"
            else:
                r["default_enabled"] = True
                r["status_override"] = "MAINLINE_COMPONENT"
        elif mc == "LOGGER_ONLY":
            r["default_enabled"] = True
            r["pnl_applicable"] = False
            r["discord_visible"] = False
            r["status_override"] = "LOGGER_ONLY"
        elif mc in ("ACTIVE_FORWARD", "TEMP_FORWARD", "MAINLINE_MONITOR"):
            r["default_enabled"] = True
            r["status_override"] = _STATUS_BY_CLASS[mc]
            # Board Imbalance Reversal: TEMP_FORWARD but Discord Summary stays ≤3
            if r["canonical_shadow_id"] == "board_imbalance_reversal_shadow":
                r["discord_visible"] = False
                r["counts_toward_active_pnl_shadow"] = True
        else:
            r["default_enabled"] = False
            r["status_override"] = "UNKNOWN_BLOCKED"


_normalize_registry()


def registry_by_id() -> dict[str, ShadowDef]:
    return {r["canonical_shadow_id"]: r for r in SHADOW_REGISTRY}


def get_shadow_def(canonical_shadow_id: str) -> Optional[ShadowDef]:
    return registry_by_id().get(canonical_shadow_id)


def is_shadow_runtime_enabled(canonical_shadow_id: str) -> bool:
    """Registry default for cleanup: RETIRED/UNKNOWN → False; active classes → True."""
    r = get_shadow_def(canonical_shadow_id)
    if r is None:
        return False
    return bool(r.get("default_enabled"))


def shadows_by_management_class(management_class: str) -> list[ShadowDef]:
    return [r for r in SHADOW_REGISTRY if r.get("management_class") == management_class]


def shadow_portfolio_status() -> dict[str, Any]:
    """Normalized portfolio counters for session summary (compat fields kept)."""
    by_class: dict[str, list[str]] = {c: [] for c in MANAGEMENT_CLASSES}
    for r in SHADOW_REGISTRY:
        mc = str(r.get("management_class") or "UNKNOWN_BLOCKED")
        by_class.setdefault(mc, []).append(str(r["canonical_shadow_id"]))
    active = [
        r
        for r in SHADOW_REGISTRY
        if r.get("counts_toward_active_shadow") and r.get("default_enabled")
    ]
    active_pnl = [
        r
        for r in SHADOW_REGISTRY
        if r.get("counts_toward_active_pnl_shadow") and r.get("default_enabled")
    ]
    return {
        "shadow_portfolio_status": {
            "ACTIVE_FORWARD": by_class.get("ACTIVE_FORWARD") or [],
            "TEMP_FORWARD": by_class.get("TEMP_FORWARD") or [],
            "MAINLINE_MONITOR": by_class.get("MAINLINE_MONITOR") or [],
            "LOGGER_ONLY": by_class.get("LOGGER_ONLY") or [],
            "MAINLINE_COMPONENT": by_class.get("MAINLINE_COMPONENT") or [],
            "RETIRED": by_class.get("RETIRED") or [],
            "UNKNOWN_BLOCKED": by_class.get("UNKNOWN_BLOCKED") or [],
        },
        "active_shadow_count": len(active),
        "active_pnl_shadow_count": len(active_pnl),
        "logger_only_count": len(by_class.get("LOGGER_ONLY") or []),
        "retired_shadow_count": len(by_class.get("RETIRED") or []),
        "mainline_component_count": len(by_class.get("MAINLINE_COMPONENT") or []),
        # deprecated aliases (readers)
        "shadow_count": len(SHADOW_REGISTRY),
        "runtime_enabled_shadow_count": len(active),
    }


def format_shadow_portfolio_startup_lines() -> list[str]:
    st = shadow_portfolio_status()["shadow_portfolio_status"]
    return [
        "Shadow Portfolio:",
        f"ACTIVE_FORWARD: {', '.join(st['ACTIVE_FORWARD']) or '(none)'}",
        f"TEMP_FORWARD: {', '.join(st['TEMP_FORWARD']) or '(none)'}",
        f"MAINLINE_MONITOR: {', '.join(st['MAINLINE_MONITOR']) or '(none)'}",
        f"LOGGER_ONLY: {', '.join(st['LOGGER_ONLY']) or '(none)'}",
        f"RETIRED: count={len(st['RETIRED'])}",
    ]


def discord_inventory_from_registry() -> list[dict[str, str]]:
    """Discord Shadow Summary inventory — max ACTIVE/TEMP/MONITOR (≤3)."""
    out: list[dict[str, str]] = []
    for r in SHADOW_REGISTRY:
        if not r.get("discord_visible"):
            continue
        if r.get("management_class") not in DISCORD_SUMMARY_CLASSES:
            continue
        cid = r["canonical_shadow_id"]
        prefix = str(r.get("summary_prefix") or "")
        enabled_key = r.get("config_key") or f"{cid}_enabled"
        if str(enabled_key).endswith(".enabled"):
            enabled_key = f"{cid}_enabled_proxy"
        count_key = {
            "e1_x5_forward_shadow": "e1_x5_forward_shadow_trades",
            "flat_weak_range_shadow": "flat_weak_range_shadow_target_count",
            "board_dynamic_trailing_shadow": "board_dynamic_shadow_exit_count",
        }.get(cid, f"{prefix}target_count" if prefix.endswith("_") else f"{prefix}_count")
        delta_key = {
            "e1_x5_forward_shadow": "e1_x5_forward_shadow_total_pnl_yen_100",
            "flat_weak_range_shadow": "flat_weak_range_shadow_delta_yen",
            "board_dynamic_trailing_shadow": "board_dynamic_shadow_total_delta_yen",
        }.get(cid, f"{prefix}delta_yen")
        enabled_key_map = {
            "e1_x5_forward_shadow": "e1_x5_forward_shadow_enabled",
            "flat_weak_range_shadow": "flat_weak_range_shadow_enabled",
            "board_dynamic_trailing_shadow": "board_dynamic_shadow_enabled",
        }
        out.append(
            {
                "name": str(r.get("discord_section") or r["display_name"]),
                "enabled_key": enabled_key_map.get(cid, str(enabled_key)),
                "count_key": count_key,
                "delta_key": delta_key,
                "canonical_shadow_id": cid,
            }
        )
    # Hard cap 3
    return out[:3]
