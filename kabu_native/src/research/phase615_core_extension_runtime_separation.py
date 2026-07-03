"""
Phase615 — Core / Extension runtime separation design (research only).
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from xml.sax.saxutils import escape

from research.market_sector_heat import _write_csv
from research.phase451_entry_shape_tournament import _now_iso
from research.phase610_runtime_structure_diff_audit import PUSH_TO_PBV2_STEPS, PRE625_COMMIT
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.pre625_runtime_structure_mode import PRE625_RUNTIME_STRUCTURE_OFF

VERDICT = "phase615_core_extension_runtime_separation_done"

RUNTIME_VARIANTS = ("pre625", "head", "pre625_mode")

# ---------------------------------------------------------------------------
# Phase call chains (ENTRY前 / 中 / 後)
# ---------------------------------------------------------------------------

PHASE_FUNCTIONS: dict[str, list[dict[str, str]]] = {
    "ENTRY前": [
        {"order": "1", "function": "push.iter_messages", "class": "", "file": "storage/push_stream.py", "layer": "Core"},
        {"order": "2", "function": "_process_payload", "class": "", "file": "small_paper/pilot_runner.py", "layer": "Core"},
        {"order": "3", "function": "_process_push_payload", "class": "", "file": "small_paper/pilot_runner.py", "layer": "Core"},
        {"order": "4", "function": "EntryScanController.begin_symbol_eval", "class": "EntryScanController", "file": "small_paper/entry_scan_controller.py", "layer": "Core"},
        {"order": "5", "function": "EntryLatencyTraceSession.begin_push", "class": "EntryLatencyTraceSession", "file": "small_paper/entry_latency_trace.py", "layer": "Extension"},
        {"order": "6", "function": "RealtimeBoardExitShadow.record_push_board_tick", "class": "RealtimeBoardExitShadow", "file": "small_paper/realtime_board_exit_shadow.py", "layer": "Extension"},
        {"order": "7", "function": "append_price_tick", "class": "", "file": "small_paper/extended_entry_shadow.py", "layer": "Core"},
        {"order": "8", "function": "ClassicMomentumForwardShadow.on_price_tick", "class": "ClassicMomentumForwardShadow", "file": "small_paper/classic_momentum_forward_shadow.py", "layer": "Extension"},
        {"order": "9", "function": "OrOverlayEntry.record_day_tick", "class": "OrOverlayEntry", "file": "small_paper/or_overlay_entry.py", "layer": "Core"},
        {"order": "10", "function": "LiveFeatureBridge.update", "class": "LiveFeatureBridge", "file": "small_paper/live_feature_bridge.py", "layer": "Core"},
        {"order": "11", "function": "StopLowMfeGuard.ingest_push", "class": "StopLowMfeGuard", "file": "small_paper/stop_low_mfe_guard.py", "layer": "Core"},
        {"order": "12", "function": "LiveFeatureBridge.enrich_payload", "class": "LiveFeatureBridge", "file": "small_paper/live_feature_bridge.py", "layer": "Core"},
        {"order": "13", "function": "_candidate_trade_from_push", "class": "", "file": "small_paper/pilot_runner.py", "layer": "Core"},
        {"order": "14", "function": "compute_entry_high_break_recent_field", "class": "", "file": "small_paper/extended_entry_shadow.py", "layer": "Core"},
        {"order": "15", "function": "compute_entry_order_book_imbalance_field", "class": "", "file": "small_paper/board_imbalance_shadow.py", "layer": "Core"},
        {"order": "16", "function": "ObserverPositionTracker.on_tick", "class": "ObserverPositionTracker", "file": "small_paper/observer_position_tracker.py", "layer": "Core"},
        {"order": "17", "function": "AmPmSessionPolicy.entry_allowed_now", "class": "AmPmSessionPolicy", "file": "small_paper/am_pm_session_policy.py", "layer": "Core"},
        {"order": "18", "function": "compute_entry_expectancy_score_fields", "class": "", "file": "small_paper/entry_expectancy_score_shadow.py", "layer": "Core"},
        {"order": "19", "function": "compute_entry_freshness", "class": "", "file": "small_paper/entry_scan_controller.py", "layer": "Core"},
        {"order": "20", "function": "evaluate_entry_data_freshness", "class": "", "file": "small_paper/entry_scan_controller.py", "layer": "Core"},
    ],
    "ENTRY中": [
        {"order": "1", "function": "_enrich_trade_for_pullback_guard", "class": "", "file": "small_paper/pilot_runner.py", "layer": "Core"},
        {"order": "2", "function": "_enrich_trade_for_entry_guards", "class": "", "file": "small_paper/pilot_runner.py", "layer": "Core"},
        {"order": "3", "function": "_evaluate_gate_entry", "class": "", "file": "small_paper/pilot_runner.py", "layer": "Core"},
        {"order": "4", "function": "ExposureGate.evaluate_entry", "class": "ExposureGate", "file": "research/exposure_gate.py", "layer": "Core"},
        {"order": "5", "function": "_maybe_try_or_overlay_entry", "class": "", "file": "small_paper/pilot_runner.py", "layer": "Core"},
        {"order": "6", "function": "OrOverlayEntry.try_overlay", "class": "OrOverlayEntry", "file": "small_paper/or_overlay_entry.py", "layer": "Core"},
        {"order": "7", "function": "_event_from_gate(candidate)", "class": "", "file": "small_paper/pilot_runner.py", "layer": "Core"},
        {"order": "8", "function": "EntryScanController.record_symbol_eval", "class": "EntryScanController", "file": "small_paper/entry_scan_controller.py", "layer": "Extension"},
        {"order": "9", "function": "_maybe_record_volume_gate_shadow", "class": "", "file": "small_paper/pilot_runner.py", "layer": "Extension"},
        {"order": "10", "function": "EntryLatencyTraceSession.finish", "class": "EntryLatencyTraceSession", "file": "small_paper/entry_latency_trace.py", "layer": "Extension"},
    ],
    "ENTRY後": [
        {"order": "1", "function": "EntryScanController.queue_accepted_candidate", "class": "EntryScanController", "file": "small_paper/entry_scan_controller.py", "layer": "Core"},
        {"order": "2", "function": "_execute_accepted_entry", "class": "", "file": "small_paper/pilot_runner.py", "layer": "Core"},
        {"order": "3", "function": "_maybe_reject_same_symbol_open_overlap", "class": "", "file": "small_paper/pilot_runner.py", "layer": "Core"},
        {"order": "4", "function": "gate.record_accepted", "class": "ExposureGate", "file": "research/exposure_gate.py", "layer": "Core"},
        {"order": "5", "function": "ObserverPositionTracker.register_entry", "class": "ObserverPositionTracker", "file": "small_paper/observer_position_tracker.py", "layer": "Core"},
        {"order": "6", "function": "LiveSessionWriter.append_event", "class": "LiveSessionWriter", "file": "small_paper/live_writer.py", "layer": "Core"},
        {"order": "7", "function": "process_paper_entry", "class": "LiveOrderAdapterSession", "file": "small_paper/live_order_adapter.py", "layer": "Extension"},
        {"order": "8", "function": "_maybe_record_live_capital_check_entry", "class": "", "file": "small_paper/pilot_runner.py", "layer": "Extension"},
        {"order": "9", "function": "_maybe_record_live_order_entry", "class": "", "file": "small_paper/pilot_runner.py", "layer": "Extension"},
        {"order": "10", "function": "discord notify_entry", "class": "DiscordNotifier", "file": "small_paper/discord_notifier.py", "layer": "Extension"},
        {"order": "11", "function": "shadow recorders (*_shadow)", "class": "various", "file": "small_paper/*_shadow*.py", "layer": "Extension"},
        {"order": "12", "function": "_log_and_dispatch_observer_events", "class": "", "file": "small_paper/pilot_runner.py", "layer": "Core"},
        {"order": "13", "function": "process_paper_exit", "class": "LiveOrderAdapterSession", "file": "small_paper/live_order_adapter.py", "layer": "Extension"},
    ],
}

VARIANT_DIFF: dict[str, dict[str, Any]] = {
    "pre625": {
        "label": "6/25以前 Runtime (commit f50c5a7)",
        "description": "Pre-6/25 wiring: no vol_liq_startup_cache, no live_order stack, no board_fallback, no latency trace.",
        "extension_active": [],
        "extension_inactive": [
            "vol_liq_startup_cache",
            "live_order_adapter",
            "live_order_dry_run",
            "live_order_api_wiring",
            "live_capital_manager",
            "live_order_notifier",
            "entry_latency_trace",
            "volume_gate_relaxation_shadow",
            "entry_freshness_board_fallback",
        ],
        "pbv2_path": "SAME as HEAD (phase610 confirmed)",
    },
    "head": {
        "label": "現HEAD Runtime",
        "description": "Full production wiring: all extension modules enabled per YAML defaults.",
        "extension_active": [
            "vol_liq_startup_cache (gate build)",
            "live_order_adapter",
            "live_order_dry_run",
            "live_order_api_wiring",
            "live_capital_manager",
            "live_order_notifier",
            "volume_gate_relaxation_shadow",
            "entry_scan_audit",
            "entry_latency_trace (opt-in)",
            "realtime_board_exit_shadow",
            "classic_momentum_forward_shadow",
            "post_entry_forward_shadow",
            "many entry/exit shadows",
        ],
        "extension_inactive": ["entry_freshness_board_fallback (default false)"],
        "pbv2_path": "SAME order as pre625",
    },
    "pre625_mode": {
        "label": "Phase612A pre625_runtime_structure_mode",
        "description": "HEAD code with 9 flags forced OFF via pre625_runtime_structure_mode.py.",
        "extension_active": [
            "entry_scan_audit",
            "realtime_board_exit_shadow",
            "classic_momentum_forward_shadow",
            "post_entry_forward_shadow",
            "entry_latency_trace (opt-in)",
            "shadow recorders (non-disabled)",
        ],
        "extension_inactive": list(PRE625_RUNTIME_STRUCTURE_OFF.keys()),
        "pbv2_path": "SAME as HEAD; wiring subset matches pre625",
    },
}

FILE_MAP: list[dict[str, str]] = [
    {"file": "pilot_runner.py", "location": "src/small_paper/", "responsibility": "Live/replay orchestrator: PUSH loop, entry gate, accept path, session finalize", "layer": "Core", "entry_before": "yes", "entry_after_only": "partial"},
    {"file": "config.py", "location": "src/small_paper/", "responsibility": "SmallPaperPilotConfig, YAML load, gate factory", "layer": "Core", "entry_before": "yes", "entry_after_only": "no"},
    {"file": "live_feature_bridge.py", "location": "src/small_paper/", "responsibility": "REST feature snapshot + payload enrich", "layer": "Core", "entry_before": "yes", "entry_after_only": "no"},
    {"file": "entry_scan_controller.py", "location": "src/small_paper/", "responsibility": "Freshness guard, scan batch, audit JSONL", "layer": "Core", "entry_before": "yes", "entry_after_only": "partial"},
    {"file": "observer_position_tracker.py", "location": "src/small_paper/", "responsibility": "Virtual paper positions, EXIT policies, PnL", "layer": "Core", "entry_before": "yes", "entry_after_only": "partial"},
    {"file": "live_writer.py", "location": "src/small_paper/", "responsibility": "events.csv, rejects, summary, audit writer", "layer": "Core", "entry_before": "no", "entry_after_only": "partial"},
    {"file": "or_overlay_entry.py", "location": "src/small_paper/", "responsibility": "OR overlay entry after PBv2 reject", "layer": "Core", "entry_before": "yes", "entry_after_only": "no"},
    {"file": "or_overlay_cap.py", "location": "src/small_paper/", "responsibility": "OR cap / pool routing", "layer": "Core", "entry_before": "no", "entry_after_only": "yes"},
    {"file": "extended_entry_shadow.py", "location": "src/small_paper/", "responsibility": "Price ring, HBRecent field (core data path)", "layer": "Core", "entry_before": "yes", "entry_after_only": "partial"},
    {"file": "board_imbalance_shadow.py", "location": "src/small_paper/", "responsibility": "Order book imbalance field for gate", "layer": "Core", "entry_before": "yes", "entry_after_only": "partial"},
    {"file": "entry_expectancy_score_shadow.py", "location": "src/small_paper/", "responsibility": "Entry score v2 fields (PBv2 input)", "layer": "Core", "entry_before": "yes", "entry_after_only": "partial"},
    {"file": "exposure_gate.py", "location": "src/research/", "responsibility": "PBv2 evaluate_entry, guards stack", "layer": "Core", "entry_before": "no", "entry_after_only": "no"},
    {"file": "daytrade_suitability_gate.py", "location": "src/small_paper/", "responsibility": "Vol/liq suitability threshold (gate build)", "layer": "Core", "entry_before": "yes", "entry_after_only": "no"},
    {"file": "vol_liq_startup_cache.py", "location": "src/small_paper/", "responsibility": "Startup cache for vol/liq thresholds", "layer": "Extension", "entry_before": "yes", "entry_after_only": "no"},
    {"file": "pre625_runtime_structure_mode.py", "location": "src/small_paper/", "responsibility": "Force OFF post-625 extension flags", "layer": "Extension", "entry_before": "yes", "entry_after_only": "no"},
    {"file": "live_order_adapter.py", "location": "src/small_paper/", "responsibility": "Post-accept paper order pipeline", "layer": "Extension", "entry_before": "no", "entry_after_only": "yes"},
    {"file": "live_order_dry_run_adapter.py", "location": "src/small_paper/", "responsibility": "Legacy dry-run order hooks", "layer": "Extension", "entry_before": "no", "entry_after_only": "yes"},
    {"file": "live_order_api_wiring.py", "location": "src/small_paper/", "responsibility": "API wiring JSONL", "layer": "Extension", "entry_before": "no", "entry_after_only": "yes"},
    {"file": "live_capital_manager.py", "location": "src/small_paper/", "responsibility": "Capital check on accept", "layer": "Extension", "entry_before": "no", "entry_after_only": "yes"},
    {"file": "live_order_notifier.py", "location": "src/small_paper/", "responsibility": "Order notifier JSONL", "layer": "Extension", "entry_before": "no", "entry_after_only": "yes"},
    {"file": "entry_latency_trace.py", "location": "src/small_paper/", "responsibility": "Per-push latency JSONL trace", "layer": "Extension", "entry_before": "yes", "entry_after_only": "no"},
    {"file": "volume_gate_relaxation_shadow.py", "location": "src/small_paper/", "responsibility": "Post-eval vol gate shadow", "layer": "Extension", "entry_before": "no", "entry_after_only": "partial"},
    {"file": "realtime_board_exit_shadow.py", "location": "src/small_paper/", "responsibility": "Board tick for exit shadow", "layer": "Extension", "entry_before": "yes", "entry_after_only": "partial"},
    {"file": "classic_momentum_forward_shadow.py", "location": "src/small_paper/", "responsibility": "Momentum forward shadow on tick", "layer": "Extension", "entry_before": "yes", "entry_after_only": "partial"},
    {"file": "post_entry_forward_shadow.py", "location": "src/small_paper/", "responsibility": "Post-entry forward counterfactual", "layer": "Extension", "entry_before": "no", "entry_after_only": "yes"},
    {"file": "discord_notifier.py", "location": "src/small_paper/", "responsibility": "Discord entry/exit notifications", "layer": "Extension", "entry_before": "no", "entry_after_only": "yes"},
    {"file": "live_pipeline_preflight.py", "location": "src/small_paper/", "responsibility": "Startup preflight checks", "layer": "Extension", "entry_before": "yes", "entry_after_only": "no"},
    {"file": "canonical_summary.py", "location": "src/small_paper/", "responsibility": "Summary JSON builder", "layer": "Core", "entry_before": "no", "entry_after_only": "yes"},
]

# Shadow-only files (all Extension, entry_after or session-end)
_SHADOW_FILES = [
    "pullback_misread_entry_guard_shadow.py", "near_day_high_low_mom_entry_guard_shadow.py",
    "gap_up_fade_entry_guard_shadow.py", "k10_stop_chain_a1_entry_guard_shadow.py",
    "symbol_reentry_cluster_entry_guard_shadow.py", "limit_up_proximity_entry_guard_shadow.py",
    "quality_formula_shadow.py", "trading_value_shadow_gate.py", "vwap_shadow_reject.py",
    "board_dynamic_trailing_shadow.py", "exit_candidate_shadow.py", "exit_shadow_monitor.py",
    "board_failure_exit_shadow.py", "post_entry_forward_shadow_auto.py",
    "classic_momentum_forward_shadow_auto.py", "boundary_forward_shadow_auto.py",
    "live_config_forward_shadow_auto.py", "live_config_transition_shadow_auto.py",
    "equity_dynamic_stop_shadow_auto.py", "risk_sizing_forward_shadow_auto.py",
    "sector_heat_forward_shadow_auto.py",
]
for sf in _SHADOW_FILES:
    FILE_MAP.append({
        "file": sf,
        "location": "src/small_paper/",
        "responsibility": "Research shadow / counterfactual (non-blocking)",
        "layer": "Extension",
        "entry_before": "partial",
        "entry_after_only": "yes",
    })

# Guard files (Core)
_GUARD_FILES = [
    ("entry_price_risk_guard.py", "Price risk guard in PBv2"),
    ("pullback_misread_dynamic40_entry_guard.py", "Pullback misread dynamic40"),
    ("near_day_high_low_momentum_dynamic40_entry_guard.py", "Near day high/low momentum"),
    ("high_drift_pullback_entry_guard.py", "High drift pullback"),
    ("weak_shape_reject_entry_guard.py", "Weak shape reject"),
    ("late_chase_entry_guard.py", "Late chase"),
    ("classic_late_chase_rsi_guard.py", "Classic late chase RSI"),
    ("reentry_rsi_guard.py", "Reentry RSI"),
    ("entry_quality_guard.py", "Entry quality"),
    ("entry_cluster_guard.py", "Entry cluster"),
    ("stop_low_mfe_guard.py", "Stop low MFE"),
    ("symbol_cooloff.py", "Symbol cooloff"),
    ("position_cap_mode.py", "Position cap mode"),
    ("am_pm_session_policy.py", "AM/PM entry stop policy"),
]
for gf, desc in _GUARD_FILES:
    FILE_MAP.append({
        "file": gf,
        "location": "src/small_paper/",
        "responsibility": desc,
        "layer": "Core",
        "entry_before": "partial",
        "entry_after_only": "no",
    })

MANDATORY_ANSWERS = {
    "1_core_only_paper_trade_viable": True,
    "1_rationale": "Universe+PUSH+rings+freshness+PBv2+OR+EXIT+observer+LiveSessionWriter suffice for virtual paper trade.",
    "2_extension_all_off_pbv2_runs": True,
    "2_rationale": "ExposureGate.evaluate_entry path unchanged; phase610/612A confirm PBv2 order identical when live_order/cache off.",
    "3_extension_entry_before_intrusion": [
        "EntryLatencyTraceSession.begin_push/mark_* (pilot_runner ENTRY前)",
        "RealtimeBoardExitShadow.record_push_board_tick (every PUSH)",
        "ClassicMomentumForwardShadow.on_price_tick (every PUSH)",
        "OrOverlayEntry.record_day_tick (OR enabled)",
        "StopLowMfeGuard.ingest_push (gate-attached ingest)",
        "vol_liq_startup_cache at gate build (session start, affects threshold)",
        "EntryScanController.begin_symbol_eval + audit path (scan batch side effects)",
        "compute_entry_expectancy_score_fields (pre-freshness; Core input but shadow-named module)",
    ],
    "4_move_to_core": [
        "extended_entry_shadow price ring + HBRecent (already Core path)",
        "board_imbalance field compute (gate input)",
        "entry_expectancy_score fields (rename de-shadow)",
    ],
    "5_move_to_extension": [
        "entry_scan_audit.jsonl heavy per-eval writes → async Extension bus",
        "entry_latency_trace → Extension-only hook",
        "realtime_board_exit_shadow pre-tick ingest → EXIT Extension bus",
        "classic_momentum_forward_shadow on_price_tick → Extension",
        "live_order_* entire stack (already post-accept)",
        "volume_gate_relaxation_shadow post-eval",
        "session-end *_shadow_auto finalize packs",
    ],
    "6_pilot_runner_separable_duties": [
        "_init_live_order_* / _init_live_capital_manager → ExtensionBootstrap",
        "_maybe_record_live_order_* → LiveOrderExtension",
        "shadow *_summary_fields / *_finalize → ShadowExtension",
        "_enrich_accept_audit_fields → AuditExtension",
        "discord dispatch → NotifyExtension",
        "EntryLatencyTrace wiring → TraceExtension",
        "run_live_dry_run extension inits block → CoreRuntime.start() vs ExtensionRuntime.attach()",
    ],
    "7_pre625_to_core_runtime_mode": True,
    "7_rationale": "Replace ad-hoc pre625 flag bundle with CoreRuntimeMode enum: CORE_ONLY | CORE_PLUS_AUDIT | FULL_EXTENSION; maps cleanly to extension registry.",
    "8_recommended_architecture": (
        "PaperTradeCore: PushIngest → FeatureEnrich → FreshnessGate → PBv2Gate → OrOverlay → "
        "AcceptRouter → ObserverPaperBook → LiveWriter. "
        "ExtensionBus (optional): LiveOrder, Capital, AuditJSONL, Shadows, Trace, Discord, StartupCache. "
        "Hooks: on_push_tick (Extension read-only), on_post_eval (audit), on_post_accept (order/shadow), on_session_end."
    ),
}


def _drawio_page(name: str, cells_xml: str) -> str:
    return f"""  <diagram id="{escape(name)}" name="{escape(name)}">
    <mxGraphModel dx="1200" dy="800" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="1600" pageHeight="1200" math="0" shadow="0">
      <root>
        <mxCell id="0"/>
        <mxCell id="1" parent="0"/>
{cells_xml}
      </root>
    </mxGraphModel>
  </diagram>"""


def _cell(cid: str, value: str, x: int, y: int, w: int, h: int, *, style: str = "rounded=1;whiteSpace=wrap;html=1;", parent: str = "1") -> str:
    return (
        f'        <mxCell id="{cid}" value="{escape(value)}" style="{style}" vertex="1" parent="{parent}">'
        f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>'
    )


def _edge(eid: str, src: str, tgt: str, label: str = "") -> str:
    lbl = f' value="{escape(label)}"' if label else ""
    return (
        f'        <mxCell id="{eid}"{lbl} style="edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;" '
        f'edge="1" parent="1" source="{src}" target="{tgt}">'
        f'<mxGeometry relative="1" as="geometry"/></mxCell>'
    )


def _state_diagram_pages() -> str:
    pages = []
    layouts = {
        "pre625": ("6/25以前", "#dae8fc"),
        "head": ("現HEAD", "#fff2cc"),
        "pre625_mode": ("612A pre625_mode", "#d5e8d4"),
    }
    for vid, (title, color) in layouts.items():
        diff = VARIANT_DIFF[vid]
        ext_off = ", ".join(diff.get("extension_inactive", [])[:4])
        cells = [
            _cell(f"{vid}_s0", "SESSION_INIT\nverify_kabu, gate build", 40, 40, 200, 60, style=f"rounded=1;fillColor={color};"),
            _cell(f"{vid}_s1", "PUSH_LOOP\niter_messages", 40, 140, 200, 60, style=f"rounded=1;fillColor={color};"),
            _cell(f"{vid}_s2", "ENTRY_EVAL\nfreshness→PBv2→OR", 40, 240, 200, 70, style=f"rounded=1;fillColor={color};"),
            _cell(f"{vid}_s3", "CANDIDATE\nrecord event", 300, 200, 160, 50),
            _cell(f"{vid}_s4", "ACCEPT\n_execute_accepted_entry", 300, 280, 180, 60, style="rounded=1;fillColor=#e1d5e7;"),
            _cell(f"{vid}_s5", "REJECT\nrecord rejected", 300, 120, 160, 50),
            _cell(f"{vid}_s6", "OPEN_POSITION\nobserver.on_tick", 540, 200, 180, 60),
            _cell(f"{vid}_s7", "EXIT\nstructural exit", 540, 300, 160, 50),
            _cell(f"{vid}_s8", "SESSION_END\nsummary finalize", 40, 380, 200, 60),
            _cell(f"{vid}_note", f"EXT OFF: {ext_off}...", 540, 40, 220, 80, style="rounded=0;fillColor=#f8cecc;"),
            _edge(f"{vid}_e01", f"{vid}_s0", f"{vid}_s1"),
            _edge(f"{vid}_e12", f"{vid}_s1", f"{vid}_s2"),
            _edge(f"{vid}_e23", f"{vid}_s2", f"{vid}_s3"),
            _edge(f"{vid}_e34", f"{vid}_s3", f"{vid}_s4", "accept"),
            _edge(f"{vid}_e35", f"{vid}_s3", f"{vid}_s5", "reject"),
            _edge(f"{vid}_e46", f"{vid}_s4", f"{vid}_s6"),
            _edge(f"{vid}_e67", f"{vid}_s6", f"{vid}_s7"),
            _edge(f"{vid}_e71", f"{vid}_s7", f"{vid}_s1", "next tick"),
            _edge(f"{vid}_e18", f"{vid}_s1", f"{vid}_s8", "market close"),
        ]
        pages.append(_drawio_page(f"State_{title}", "\n".join(cells)))
    return "\n".join(pages)


def _flowchart_pages() -> str:
    pages = []
    for vid, title in [("pre625", "6/25以前"), ("head", "現HEAD"), ("pre625_mode", "612A")]:
        ext_box = "Extension hooks ON" if vid == "head" else "Extension hooks REDUCED"
        cells = [
            _cell(f"{vid}_p", "PUSH received", 40, 40, 140, 40),
            _cell(f"{vid}_e", "enrich_payload", 220, 40, 140, 40),
            _cell(f"{vid}_x", ext_box, 400, 20, 160, 80, style="rounded=1;fillColor=#f8cecc;"),
            _cell(f"{vid}_f", "freshness check", 40, 140, 140, 40, style="rounded=1;fillColor=#ffe6cc;"),
            _cell(f"{vid}_g", "PBv2 evaluate_entry", 220, 140, 160, 40, style="rounded=1;fillColor=#d5e8d4;"),
            _cell(f"{vid}_o", "OR overlay\n(if PBv2 reject)", 420, 140, 140, 50),
            _cell(f"{vid}_c", "candidate event", 40, 240, 120, 40),
            _cell(f"{vid}_a", "accept path", 220, 240, 120, 40),
            _cell(f"{vid}_l", "live_order/shadow\n(ENTRY後 only)", 400, 230, 160, 60, style="rounded=1;fillColor=#e1d5e7;"),
            _edge(f"{vid}_e1", f"{vid}_p", f"{vid}_e"),
            _edge(f"{vid}_e2", f"{vid}_e", f"{vid}_x"),
            _edge(f"{vid}_e3", f"{vid}_e", f"{vid}_f"),
            _edge(f"{vid}_e4", f"{vid}_f", f"{vid}_g", "pass"),
            _edge(f"{vid}_e5", f"{vid}_g", f"{vid}_o", "reject"),
            _edge(f"{vid}_e6", f"{vid}_g", f"{vid}_c"),
            _edge(f"{vid}_e7", f"{vid}_c", f"{vid}_a", "accept"),
            _edge(f"{vid}_e8", f"{vid}_a", f"{vid}_l"),
        ]
        pages.append(_drawio_page(f"Flow_{title}", "\n".join(cells)))
    return "\n".join(pages)


def _sequence_pages() -> str:
    pages = []
    actors = ["Push", "PilotRunner", "FeatureBridge", "Freshness", "PBv2Gate", "Observer", "Extension"]
    for vid, title in [("pre625", "6/25以前"), ("head", "現HEAD"), ("pre625_mode", "612A")]:
        cells = []
        x0 = 40
        for i, a in enumerate(actors):
            cells.append(_cell(f"{vid}_actor_{i}", a, x0 + i * 120, 20, 100, 30, style="rounded=0;fillColor=#dae8fc;"))
        y = 80
        steps = [
            (0, 1, "push payload"),
            (1, 2, "update+enrich"),
            (1, 6, "shadow tick (HEAD)" if vid == "head" else "shadow tick (reduced)"),
            (1, 3, "compute freshness"),
            (3, 4, "evaluate_entry"),
            (4, 1, "decision"),
            (1, 5, "register_entry / on_tick"),
            (1, 6, "live_order (HEAD post-accept)" if vid == "head" else "skip live_order"),
        ]
        for i, (s, t, lbl) in enumerate(steps):
            y += 50
            cells.append(_edge(f"{vid}_seq_{i}", f"{vid}_actor_{s}", f"{vid}_actor_{t}", lbl))
        pages.append(_drawio_page(f"Seq_{title}", "\n".join(cells)))
    return "\n".join(pages)


def _write_drawio(path: Path, pages_xml: str) -> None:
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<mxfile host="app.diagrams.net" modified="2026-06-30T00:00:00.000Z" agent="phase615" version="22.0.0">\n'
        f"{pages_xml}\n"
        "</mxfile>\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _core_vs_extension_rows() -> list[dict[str, str]]:
    rows = []
    for comp, desc in [
        ("Universe", "Symbol refresh / eligible set"),
        ("PUSH", "kabu WebSocket push stream"),
        ("price ring", "extended_entry_shadow tick ring"),
        ("board ring", "board times in payload + exit shadow ingest"),
        ("freshness", "entry_scan_controller evaluate_entry_data_freshness"),
        ("PBv2", "ExposureGate.evaluate_entry"),
        ("OR", "or_overlay_entry after PBv2 reject"),
        ("EXIT", "observer_position_tracker policies"),
        ("paper order", "virtual hold via observer (not live_order)"),
        ("summary", "LiveSessionWriter + canonical_summary"),
    ]:
        rows.append({"component": comp, "layer": "Core", "description": desc})
    for comp, desc in [
        ("LiveOrder", "live_order_adapter/dry_run/api_wiring"),
        ("Capital", "live_capital_manager"),
        ("Notifier", "live_order_notifier, discord"),
        ("Audit", "entry_scan_audit.jsonl, execution audit fields"),
        ("JSONL", "heavy order/jsonl outputs"),
        ("Counterfactual", "post_entry_forward_shadow"),
        ("Shadow", "*_shadow* research modules"),
        ("Runtime Trace", "entry_latency_trace"),
        ("Report", "phase packs, quality_top_debug"),
        ("Startup Cache", "vol_liq_startup_cache"),
        ("Config SHA", "session config fingerprint"),
        ("Preflight", "live_pipeline_preflight"),
        ("Volume Shadow", "volume_gate_relaxation_shadow"),
    ]:
        rows.append({"component": comp, "layer": "Extension", "description": desc})
    return rows


def _responsibility_matrix_rows() -> list[dict[str, str]]:
    rows = []
    for phase, items in PHASE_FUNCTIONS.items():
        for item in items:
            rows.append({
                "phase": phase,
                "call_order": item["order"],
                "function": item["function"],
                "class": item["class"],
                "file": item["file"],
                "layer": item["layer"],
            })
    for step in PUSH_TO_PBV2_STEPS:
        rows.append({
            "phase": "PUSH_TO_PBV2_CANONICAL",
            "call_order": str(step[0]),
            "function": step[2],
            "class": "",
            "file": step[1],
            "layer": "Core" if "live_order" not in step[2].lower() else "Extension",
        })
    return rows


def run_phase615(repo_root: Optional[Path] = None) -> dict[str, Any]:
    repo = Path(repo_root or resolve_kabu_root(Path.cwd()))
    reports = resolve_reports_dir(repo)

    _write_drawio(reports / "phase615_runtime_state_diagram.drawio", _state_diagram_pages())
    _write_drawio(reports / "phase615_runtime_flowchart.drawio", _flowchart_pages())
    _write_drawio(reports / "phase615_runtime_sequence.drawio", _sequence_pages())

    _write_csv(reports / "phase615_core_vs_extension.csv", ["component", "layer", "description"], _core_vs_extension_rows())
    _write_csv(
        reports / "phase615_runtime_file_map.csv",
        ["file", "location", "responsibility", "layer", "entry_before", "entry_after_only"],
        FILE_MAP,
    )
    _write_csv(
        reports / "phase615_runtime_responsibility_matrix.csv",
        ["phase", "call_order", "function", "class", "file", "layer"],
        _responsibility_matrix_rows(),
    )

    report = {
        "verdict": VERDICT,
        "generated_at": _now_iso(),
        "pre625_commit": PRE625_COMMIT,
        "runtime_variants": VARIANT_DIFF,
        "phase_functions": PHASE_FUNCTIONS,
        "push_to_pbv2_steps": [
            {"step": s[0], "file": s[1], "function": s[2], "stage": s[3], "optional": s[5]}
            for s in PUSH_TO_PBV2_STEPS
        ],
        "file_map_count": len(FILE_MAP),
        "core_file_count": sum(1 for r in FILE_MAP if r["layer"] == "Core"),
        "extension_file_count": sum(1 for r in FILE_MAP if r["layer"] == "Extension"),
        "mandatory_answers": MANDATORY_ANSWERS,
        "architecture_diagrams": {
            "core_only": (
                "PushIngest → LiveFeatureBridge → FreshnessGate → ExposureGate(PBv2) → "
                "OrOverlay → ObserverPaperBook → LiveSessionWriter → Summary"
            ),
            "core_plus_extension": (
                "CoreRuntime (above) + ExtensionBus[LiveOrder, Capital, Audit, Shadows, Trace, Discord, VolLiqCache] "
                "hooked at on_push_tick (read-only), on_post_eval, on_post_accept, on_session_end"
            ),
        },
    }
    (reports / "phase615_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
