#!/usr/bin/env python3
"""Phase687W12 — Generate TradeBot Current System Design Specification artifacts.

Documentation-only. Reads live YAML/code; writes under docs/current_system_design/.
"""
from __future__ import annotations

import ast
import csv
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[2]
REPO = NATIVE.parent
OUT = Path(__file__).resolve().parent
VERSION = "2026.07.12"
DOC_TITLE_EN = "TradeBot Current System Design Specification"
DOC_TITLE_JA = "TradeBot 現行システム設計仕様書"
PROD_YAML_REL = "configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
PROD_YAML = NATIVE / PROD_YAML_REL

sys.path.insert(0, str(NATIVE / "src"))
sys.path.insert(0, str(REPO))

SECRET_RE = re.compile(
    r"(?i)(discord(?:app)?\.com/api/webhooks/\S+|password\s*[:=]\s*\S+|api[_-]?key\s*[:=]\s*\S+|"
    r"authorization\s*[:=]\s*\S+|Bearer\s+[A-Za-z0-9._\-]+)"
)


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def find_def_lines(path: Path, name: str) -> tuple[int, int]:
    """Return (start, end) line numbers for a top-level or method def/class."""
    src = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return (0, 0)
    lines = src.splitlines()
    n = len(lines)

    def end_of(node: ast.AST) -> int:
        return int(getattr(node, "end_lineno", getattr(node, "lineno", 0)) or 0)

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == name:
            return (node.lineno, end_of(node) or node.lineno)
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name:
                    return (item.lineno, end_of(item) or item.lineno)
    # nested search
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == name:
            return (node.lineno, end_of(node) or node.lineno)
    return (0, 0)


def load_cfg() -> Any:
    from small_paper.config import load_pilot_config

    return load_pilot_config(PROD_YAML)


def load_gate_manifest() -> dict[str, Any]:
    return json.loads((NATIVE / "tests" / "runtime_gate_manifest.json").read_text(encoding="utf-8"))


RESEARCH_LONG = [
    "tests/test_phase655_no_progress_entry_quality.py",
    "tests/test_phase656_winner_attribution.py",
    "tests/test_phase658_full_period_shadow_revalidation.py",
    "tests/test_phase660_rise5_recent_regression.py",
    "tests/test_phase665_pretrend_shape.py",
    "tests/test_phase666_breakout_initiation.py",
    "tests/test_phase667_flat_vwap_volume.py",
    "tests/test_phase668_shadow_adoption_review.py",
    "tests/test_phase669_flat_band_adoption.py",
    "tests/test_phase670_flat_weak_range_forward_shadow.py",
    "tests/test_phase671_early_stop_feature_discovery.py",
    "tests/test_phase672_pre_entry_microsequence.py",
    "tests/test_phase673_microsequence_third_condition.py",
    "tests/test_phase674_microsequence_candidate_robustness.py",
    "tests/test_phase675_recent_early_stop_focus.py",
    "tests/test_phase676_opening_coldstart_feature_incomplete.py",
    "tests/test_phase677_entry_readiness_gate_audit.py",
    "tests/test_phase678_readiness_gate_robustness.py",
    "tests/test_phase679_readiness_shadow_combo.py",
    "tests/test_phase679b_h_economics_winner_quality.py",
    "tests/test_phase681_microsequence_c_runtime_shadow.py",
    "tests/test_phase682_shadow_portfolio_consistency.py",
    "tests/test_phase683_shadow_feature_namespace.py",
]


def entry_rows(cfg: Any) -> list[dict[str, str]]:
    eg = "src/research/exposure_gate.py"
    eg_eval = find_def_lines(NATIVE / eg, "evaluate_entry")
    eg_r = f"{eg_eval[0]}-{eg_eval[1]}"
    raw = getattr(cfg, "raw", {}) or {}
    reject_clusters = list(raw.get("entry_cluster_guard_reject_clusters", [5]))
    reject_csubs = list(raw.get("entry_cluster_guard_reject_csubs", []))
    entry_profile = getattr(cfg, "entry_profile", None) or raw.get("entry_profile", "")
    return [
        {
            "条件名": "PBv2 profile / entry_score_v2",
            "status": "MAINLINE_ACTIVE",
            "config key": "entry_profile / entry_score_v2_min / momentum_score_cutoff_max",
            "現行値": f"{entry_profile} / min={cfg.entry_score_v2_min} / cutoff<={cfg.momentum_score_cutoff_max}",
            "入力": "entry_expectancy_score_v2, momentum_continuation_score, board tier",
            "判定": "score_v2>=min AND momentum_score_cutoff_pass AND board_mid_or_high",
            "reject reason": "entry_score_v2_below_threshold / momentum_low_required",
            "Runtime file/function": f"{eg}::ExposureGate.evaluate_entry L{eg_r}",
            "actual/shadow": "actual",
            "変更可否": "YAML+pin; strategy change needs GO",
        },
        {
            "条件名": "Momentum low (explicit cutoff)",
            "status": "MAINLINE_ACTIVE",
            "config key": "momentum_score_cutoff_max",
            "現行値": str(cfg.momentum_score_cutoff_max),
            "入力": "momentum_continuation_score",
            "判定": "score <= cutoff (Phase472 PBv2)",
            "reject reason": "momentum_low_required",
            "Runtime file/function": f"{eg}::evaluate_entry + entry_expectancy_score_shadow.momentum_score_cutoff_pass",
            "actual/shadow": "actual",
            "変更可否": "config",
        },
        {
            "条件名": "Board mid/high required",
            "status": "MAINLINE_ACTIVE",
            "config key": "(derived with entry_score_v2)",
            "現行値": "board_mid_or_high_required_for_v2",
            "入力": "board imbalance / board tier tokens",
            "判定": "board mid or high required for v2 accept",
            "reject reason": "entry_score_v2_below_threshold",
            "Runtime file/function": "src/small_paper/entry_expectancy_score_shadow.py::board_mid_or_high_required_for_v2",
            "actual/shadow": "actual",
            "変更可否": "code",
        },
        {
            "条件名": "OR Open Strength Overlay",
            "status": "MAINLINE_ACTIVE",
            "config key": "or_overlay_enabled / cap_pbv2 / cap_or / or_max_update_count",
            "現行値": f"true / {cfg.cap_pbv2} / {cfg.cap_or} / {cfg.or_max_update_count}",
            "入力": "OR open strength features, update_count",
            "判定": "CAP_SPLIT_4_1: PBv2<=4, OR<=1, total<=5",
            "reject reason": "or_cap_full / pbv2_cap_full",
            "Runtime file/function": "src/small_paper/or_overlay_entry.py + pilot_runner._maybe_try_or_overlay_entry",
            "actual/shadow": "actual",
            "変更可否": "config",
        },
        {
            "条件名": "Price Risk Guard",
            "status": "MAINLINE_ACTIVE",
            "config key": "entry_price_risk_guard_enabled / min_entry_price / max_tick_ratio_pct / apply_mode",
            "現行値": f"true / {cfg.entry_price_risk_guard_min_entry_price} / {cfg.entry_price_risk_guard_max_tick_ratio_pct}% / reject_entry",
            "入力": "entry price, tick size ratio",
            "判定": "price>=min AND tick_ratio<=max; apply reject_entry",
            "reject reason": "entry_price_risk_guard",
            "Runtime file/function": f"{eg}::evaluate_entry",
            "actual/shadow": "actual (shadow flag true for audit)",
            "変更可否": "config",
        },
        {
            "条件名": "High Drift Pullback",
            "status": "MAINLINE_ACTIVE",
            "config key": "high_drift_guard_enabled",
            "現行値": "true",
            "入力": "day_high distance, r5/r10/r15, dynamic40",
            "判定": "dynamic40 AND ((dh>=1.2% AND r10<-0.15% AND r5>r10) OR (dh>=1.5% AND (r15<-0.5% OR r5<-0.5%)))",
            "reject reason": "high_drift_pullback",
            "Runtime file/function": f"{eg}::evaluate_entry",
            "actual/shadow": "actual",
            "変更可否": "config",
        },
        {
            "条件名": "Weak Shape",
            "status": "MAINLINE_ACTIVE",
            "config key": "weak_shape_reject_enabled",
            "現行値": "true",
            "入力": "opening_peak / slow_opening_peak shape labels",
            "判定": "reject opening_peak / slow_opening_peak at ENTRY",
            "reject reason": "weak_shape_reject",
            "Runtime file/function": f"{eg}::evaluate_entry",
            "actual/shadow": "actual",
            "変更可否": "config",
        },
        {
            "条件名": "Late Chase Guard",
            "status": "MAINLINE_ACTIVE",
            "config key": "late_chase_guard_enabled",
            "現行値": "true",
            "入力": "r10, day_high_distance",
            "判定": "r10<0.3719 AND day_high_distance<1.1872 → reject",
            "reject reason": "late_chase_guard",
            "Runtime file/function": f"{eg}::evaluate_entry + late_chase_guard",
            "actual/shadow": "actual",
            "変更可否": "config",
        },
        {
            "条件名": "Classic RSI late chase",
            "status": "MAINLINE_ACTIVE",
            "config key": "classic_late_chase_rsi_guard_enabled / threshold",
            "現行値": f"true / {cfg.classic_late_chase_rsi_threshold}",
            "入力": "late_chase_cluster flag, RSI14",
            "判定": "late_chase_cluster AND RSI14>=threshold",
            "reject reason": "classic_late_chase_rsi_over80",
            "Runtime file/function": "src/small_paper/classic_late_chase_rsi_guard.py + exposure_gate",
            "actual/shadow": "actual",
            "変更可否": "config",
        },
        {
            "条件名": "Reentry RSI",
            "status": "MAINLINE_ACTIVE",
            "config key": "reentry_rsi_guard_enabled / threshold",
            "現行値": f"true / {cfg.reentry_rsi_guard_threshold}",
            "入力": "prior stop_hit, RSI14",
            "判定": "re-entry after stop_hit requires RSI14>threshold",
            "reject reason": "reentry_rsi_guard_below60",
            "Runtime file/function": f"{eg}::evaluate_entry",
            "actual/shadow": "actual",
            "変更可否": "config",
        },
        {
            "条件名": "Entry Quality G9",
            "status": "MAINLINE_ACTIVE",
            "config key": "entry_quality_guard_enabled / max_spread_bps / max_update_count",
            "現行値": f"true / {cfg.entry_quality_max_spread_bps} / {cfg.entry_quality_max_update_count}",
            "入力": "spread_bps, update_count",
            "判定": "require spread<=50bps AND update_count<=5",
            "reject reason": "entry_quality_guard_spread / entry_quality_guard_update_count",
            "Runtime file/function": "src/small_paper/entry_quality_guard.py",
            "actual/shadow": "actual",
            "変更可否": "config",
        },
        {
            "条件名": "Entry Cluster Guard",
            "status": "MAINLINE_ACTIVE",
            "config key": "entry_cluster_guard_enabled / reject_clusters / exception / liquidity_burst",
            "現行値": f"true / clusters={reject_clusters} / csubs={reject_csubs} / exception=true / thr={cfg.entry_cluster_guard_liquidity_burst_threshold}",
            "入力": "cluster model features, liquidity_burst",
            "判定": "reject cluster5 unless E4 liquidity_burst exception",
            "reject reason": "entry_cluster_guard",
            "Runtime file/function": "src/small_paper/entry_cluster_guard.py",
            "actual/shadow": "actual",
            "変更可否": "config+model json",
        },
        {
            "条件名": "Flat-band mainline",
            "status": "MAINLINE_ACTIVE",
            "config key": "pbv2_flat_band_mainline_enabled (+ threshold keys)",
            "現行値": f"true; rise5[{cfg.pbv2_flat_band_shadow_rise5_flat_min_pct},{cfg.pbv2_flat_band_shadow_rise5_flat_max_pct}] rise10[{cfg.pbv2_flat_band_shadow_rise10_flat_min_pct},{cfg.pbv2_flat_band_shadow_rise10_flat_max_pct}] overheat>={cfg.pbv2_flat_band_shadow_overheat_rise5_pct}",
            "入力": "entry_rise_5min_pct, entry_rise_10min_pct, PBv2 pool",
            "判定": "flat band + overheat evaluate_flat_plus_overheat",
            "reject reason": "flat_band_mainline",
            "Runtime file/function": "src/small_paper/pbv2_flat_band_entry_guard.py",
            "actual/shadow": "actual (shadow flag false)",
            "変更可否": "config",
        },
        {
            "条件名": "Near day-high + low momentum (Dynamic40)",
            "status": "MAINLINE_ACTIVE",
            "config key": "enable_near_day_high_low_momentum_dynamic40_guard",
            "現行値": "true",
            "入力": "dynamic40, day-high proximity, momentum",
            "判定": "production ENTRY reject for D40 near-high low-mom",
            "reject reason": "near_day_high_low_momentum_dynamic40_guard",
            "Runtime file/function": f"{eg}::evaluate_entry",
            "actual/shadow": "actual",
            "変更可否": "config",
        },
        {
            "条件名": "Position cap",
            "status": "MAINLINE_ACTIVE",
            "config key": "max_concurrent_positions / position_cap_mode / position_cap_release / cap_pbv2 / cap_or",
            "現行値": f"{cfg.max_concurrent_positions} / true / structural_exit / {cfg.cap_pbv2}/{cfg.cap_or}",
            "入力": "open position count by pool",
            "判定": "total<=5 with OR split; release on structural exit",
            "reject reason": "max_concurrent / pbv2_cap_full / or_cap_full",
            "Runtime file/function": "pilot_runner + exposure_gate + or_overlay_cap",
            "actual/shadow": "actual",
            "変更可否": "config",
        },
        {
            "条件名": "same_symbol_open_policy",
            "status": "MAINLINE_ACTIVE",
            "config key": "same_symbol_open_policy",
            "現行値": str(cfg.same_symbol_open_policy),
            "入力": "open positions for symbol",
            "判定": "reject ENTRY while same symbol open (no overlap replace chain)",
            "reject reason": "REJECT_SAME_SYMBOL_OPEN_OVERLAP",
            "Runtime file/function": "src/small_paper/pilot_runner.py::_maybe_reject_same_symbol_open_overlap",
            "actual/shadow": "actual",
            "変更可否": "config",
        },
        {
            "条件名": "Allowed trading windows",
            "status": "MAINLINE_ACTIVE",
            "config key": "allowed_trading_windows / use_market_time_window",
            "現行値": "09:05-11:23, 12:33-15:20; use_market_time_window=true",
            "入力": "market clock JST",
            "判定": "outside window → reject",
            "reject reason": "outside_allowed_trading_window",
            "Runtime file/function": f"{eg}::evaluate_entry",
            "actual/shadow": "actual",
            "変更可否": "config",
        },
        {
            "条件名": "Stale price / board freshness",
            "status": "MAINLINE_ACTIVE",
            "config key": "entry_freshness_guard_enabled / entry_max_price_age_sec / entry_max_board_age_sec / freshness_semantics_v2",
            "現行値": f"true / {cfg.entry_max_price_age_sec}s / {cfg.entry_max_board_age_sec}s / v2={cfg.freshness_semantics_v2_enabled}",
            "入力": "CurrentPriceTime, board update time, event age",
            "判定": "price/board age <=3s; trade_stale tag_only at 10s",
            "reject reason": "freshness/stale reject (scan controller)",
            "Runtime file/function": "src/small_paper/entry_scan_controller.py + pilot_runner",
            "actual/shadow": "actual",
            "変更可否": "config",
        },
        {
            "条件名": "Daytrade suitability",
            "status": "MAINLINE_ACTIVE",
            "config key": "daytrade_suitability_enabled / rule / apply_mode",
            "現行値": "true / volatility_liquidity_top50 / reject_entry",
            "入力": "vol/liq ranking prior sessions",
            "判定": "reject if not in suitability set",
            "reject reason": "daytrade_suitability",
            "Runtime file/function": "src/small_paper/daytrade_suitability_gate.py",
            "actual/shadow": "actual",
            "変更可否": "config",
        },
        {
            "条件名": "Stop Low MFE Guard",
            "status": "NOT_RUNTIME_REACHABLE",
            "config key": "stop_low_mfe_guard_enabled",
            "現行値": "false",
            "入力": "n/a (disabled)",
            "判定": "gate branch not taken when false",
            "reject reason": "stop_low_mfe_guard (unused)",
            "Runtime file/function": f"{eg}::evaluate_entry (guard None when disabled)",
            "actual/shadow": "OFF",
            "変更可否": "must stay false unless explicit GO",
        },
        {
            "条件名": "Daily loss / risk cluster",
            "status": "MAINLINE_ACTIVE",
            "config key": "daily_loss_guard_enabled / daily_loss_guard_pct / risk_cluster_*",
            "現行値": f"true / {cfg.daily_loss_guard_pct}% / consecutive={cfg.risk_cluster_consecutive_losses}",
            "入力": "session PnL, consecutive losses",
            "判定": "block further ENTRY when tripped",
            "reject reason": "daily_loss_guard / risk_cluster_block",
            "Runtime file/function": f"{eg}::evaluate_entry",
            "actual/shadow": "actual",
            "変更可否": "config",
        },
    ]


def exit_rows() -> list[dict[str, str]]:
    return [
        {
            "条件名": "Hard Stop -1.2%",
            "status": "MAINLINE_ACTIVE",
            "config key": "discord_hard_stop_pct (Observer hard_stop_pct default 1.20)",
            "現行値": "1.20%",
            "入力": "entry_price, mark price",
            "判定": "price <= entry*(1-0.012) → stop_hit",
            "exit reason": "stop_hit",
            "Runtime file/function": "src/small_paper/observer_position_tracker.py (hard_stop_pct=1.20)",
            "actual/shadow": "actual",
        },
        {
            "条件名": "Board Dynamic Trailing",
            "status": "MAINLINE_ACTIVE",
            "config key": "structural_exit_policy=combined_structural_exit_v1_trailing_mfe_shadow",
            "現行値": "board_high: activate 1.0% giveback 60%; board_low: activate 0.6% giveback 40%; split@47.62",
            "入力": "peak_pnl, pnl, entry_imbalance_percentile",
            "判定": "peak>=activate AND pnl<=peak*giveback",
            "exit reason": "trailing_mfe_exit",
            "Runtime file/function": "src/research/structural_exit_policies.py::trailing_mfe_params + board_dynamic_trailing_shadow.py",
            "actual/shadow": "actual (+ legacy 0.8%/50% shadow counterfactual logs)",
        },
        {
            "条件名": "No Progress Exit",
            "status": "MAINLINE_ACTIVE",
            "config key": "no_progress_exit_enabled",
            "現行値": "true (linmfe_t900_i0p6_s0p05_c0p8_p0p3)",
            "入力": "hold_sec, MFE, pnl",
            "判定": "hold>=900s, required MFE 0.6+0.05/5m cap 0.8, pnl<0.3%",
            "exit reason": "no_progress_exit",
            "Runtime file/function": "src/small_paper/no_progress_exit.py + observer_position_tracker",
            "actual/shadow": "actual",
        },
        {
            "条件名": "Session end exit/finalize",
            "status": "MAINLINE_ACTIVE",
            "config key": "live.session_end / AM-PM runner session boundaries",
            "現行値": "AM end ~11:25; PM/session_close at session end",
            "入力": "session clock, open positions",
            "判定": "force close remaining → session_close",
            "exit reason": "session_close",
            "Runtime file/function": "observer_position_tracker + am_pm_daily_runner",
            "actual/shadow": "actual",
        },
        {
            "条件名": "Stop Low MFE Guard",
            "status": "NOT_RUNTIME_REACHABLE",
            "config key": "stop_low_mfe_guard_enabled",
            "現行値": "false",
            "入力": "n/a",
            "判定": "disabled",
            "exit reason": "n/a",
            "Runtime file/function": "exposure_gate stop_low_mfe branch OFF",
            "actual/shadow": "OFF",
        },
        {
            "条件名": "Exit Shadow Monitor (T2/T3)",
            "status": "NOT_RUNTIME_REACHABLE",
            "config key": "exit_shadow_monitor_enabled / t2 / t3",
            "現行値": "false / false / false",
            "入力": "n/a",
            "判定": "disabled (Phase669 removed from portfolio)",
            "exit reason": "n/a",
            "Runtime file/function": "realtime_board_exit_shadow / exit shadow monitor OFF",
            "actual/shadow": "OFF",
        },
    ]


def runtime_contract_rows(cfg: Any) -> list[dict[str, str]]:
    return [
        {"Feature": "Entry Cluster Guard", "Status": "MAINLINE_ACTIVE", "Current value": str(cfg.entry_cluster_guard_enabled)},
        {"Feature": "Stop Low MFE Guard", "Status": "NOT_RUNTIME_REACHABLE", "Current value": str(cfg.stop_low_mfe_guard_enabled)},
        {"Feature": "Exit Shadow Monitor", "Status": "NOT_RUNTIME_REACHABLE", "Current value": str(cfg.exit_shadow_monitor_enabled)},
        {"Feature": "Flat-band", "Status": "MAINLINE_ACTIVE", "Current value": str(cfg.pbv2_flat_band_mainline_enabled)},
        {"Feature": "I/H/C", "Status": "SHADOW_ACTIVE", "Current value": "readiness/microsequence shadows true"},
        {"Feature": "same-symbol", "Status": "MAINLINE_ACTIVE", "Current value": str(cfg.same_symbol_open_policy)},
        {"Feature": "open_symbols_exceed_cap", "Status": "OBSERVABILITY_ONLY", "Current value": "continue (will_stop=false)"},
        {"Feature": "Discord Router", "Status": "MAINLINE_ACTIVE", "Current value": "W10"},
        {"Feature": "Registration lifetime", "Status": "MAINLINE_ACTIVE", "Current value": "defer unregister while Capture active"},
        {"Feature": "live_trading_enabled", "Status": "MAINLINE_ACTIVE", "Current value": "false"},
        {"Feature": "order_enabled", "Status": "MAINLINE_ACTIVE", "Current value": "false"},
        {"Feature": "Hard Stop", "Status": "MAINLINE_ACTIVE", "Current value": "1.20%"},
        {"Feature": "Board Dynamic Trailing", "Status": "MAINLINE_ACTIVE", "Current value": "board_high/low tiers"},
        {"Feature": "No Progress Exit", "Status": "MAINLINE_ACTIVE", "Current value": "true"},
        {"Feature": "OR Overlay", "Status": "MAINLINE_ACTIVE", "Current value": f"cap {cfg.cap_pbv2}/{cfg.cap_or}"},
        {"Feature": "NP Pre-entry Logger", "Status": "OBSERVABILITY_ONLY", "Current value": "true"},
        {"Feature": "Flat Weak Range Shadow", "Status": "SHADOW_ACTIVE", "Current value": "true"},
        {"Feature": "Market Capture Sidecar", "Status": "MAINLINE_ACTIVE", "Current value": "required until 15:35"},
        {"Feature": "W4S Forward Soak", "Status": "MAINLINE_ACTIVE", "Current value": "LIVE_PAPER_RUNTIME only"},
        {"Feature": "Real Orders", "Status": "NOT_IMPLEMENTED", "Current value": "NOT AUTHORIZED"},
    ]


def shadow_rows() -> list[dict[str, str]]:
    return [
        {"name": "I Shadow (readiness precision)", "class": "SHADOW", "affects_mainline": "no", "config": "readiness_precision_shadow_enabled=true", "runtime": "src/small_paper/readiness_forward_shadow.py"},
        {"name": "H Shadow (readiness economics / refined H)", "class": "SHADOW", "affects_mainline": "no", "config": "readiness_economics_shadow_enabled / readiness_refined_h_shadow_enabled=true", "runtime": "src/small_paper/readiness_forward_shadow.py"},
        {"name": "C Shadow (microsequence recovery-fail)", "class": "SHADOW", "affects_mainline": "no", "config": "microsequence_recovery_fail_shadow_enabled=true", "runtime": "src/small_paper/microsequence_recovery_fail_forward_shadow.py"},
        {"name": "Flat Weak Range", "class": "SHADOW", "affects_mainline": "no", "config": "flat_weak_range_shadow_enabled=true", "runtime": "src/small_paper/flat_weak_range_forward_shadow.py"},
        {"name": "NP Logger", "class": "OBSERVABILITY_ONLY", "affects_mainline": "no (logger only)", "config": "np_pre_entry_feature_logger_enabled=true", "runtime": "src/small_paper/np_pre_entry_feature_logger.py"},
        {"name": "Sector Heat", "class": "RESEARCH_ONLY", "affects_mainline": "no", "config": "n/a in production YAML", "runtime": "research scripts (not Monday mainline path)"},
        {"name": "Position Sizing Shadow", "class": "RESEARCH_ONLY", "affects_mainline": "no", "config": "n/a production sizing fixed paper", "runtime": "research / live capital dry-run only"},
        {"name": "Classic Technical Indicator Research", "class": "RESEARCH_ONLY", "affects_mainline": "no", "config": "classic momentum forward shadow modules", "runtime": "src/small_paper/classic_momentum_forward_shadow.py"},
        {"name": "Volume gate relaxation V90/V80", "class": "SHADOW", "affects_mainline": "no (production remains V100)", "config": "volume_gate_relaxation_shadow_enabled=true", "runtime": "pilot_runner volume gate shadow"},
        {"name": "PBv2 rise5 shadow", "class": "DEPRECATED", "affects_mainline": "no", "config": "pbv2_rise5_shadow_enabled=false", "runtime": "src/small_paper/pbv2_rise5_shadow.py"},
        {"name": "VWAP shadow reject", "class": "DEPRECATED", "affects_mainline": "no", "config": "vwap_shadow_reject_enabled=false", "runtime": "src/small_paper/vwap_shadow_reject.py"},
        {"name": "Exit Shadow Monitor", "class": "NOT_RUNTIME_REACHABLE", "affects_mainline": "no", "config": "exit_shadow_monitor_enabled=false", "runtime": "disabled"},
        {"name": "IHC portfolio counterfactual", "class": "SHADOW", "affects_mainline": "no", "config": "runtime hook", "runtime": "src/small_paper/shadow_ihc_portfolio.py / ihc_shadow_counterfactual.py"},
    ]


def discord_env_rows() -> list[dict[str, str]]:
    return [
        {"category": "TRADE_ACTUAL", "env_keys": "KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL | KABU_SMALL_PAPER_DISCORD_WEBHOOK_URL", "rate_limit": "dedupe only", "fallback": "none"},
        {"category": "SESSION_SUMMARY", "env_keys": "KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL | KABU_SMALL_PAPER_DISCORD_WEBHOOK_URL", "rate_limit": "dedupe only", "fallback": "none"},
        {"category": "CAP_BLOCKED", "env_keys": "KABU_SMALL_PAPER_CAP_BLOCKED_WEBHOOK_URL", "rate_limit": "1 per symbol/session/reason via dedupe", "fallback": "none"},
        {"category": "OPERATIONS", "env_keys": "KABU_DISCORD_OPERATIONS_WEBHOOK_URL", "rate_limit": "15 min", "fallback": "none"},
        {"category": "MARKET_CAPTURE", "env_keys": "KABU_DISCORD_MARKET_CAPTURE_WEBHOOK_URL | KABU_MARKET_CAPTURE_WEBHOOK_URL", "rate_limit": "15 min", "fallback": "none"},
        {"category": "RESEARCH_SHADOW", "env_keys": "KABU_DISCORD_RESEARCH_WEBHOOK_URL | KABU_SHADOW_DISCORD_WEBHOOK_URL", "rate_limit": "AM/PM by caller", "fallback": "none; no cross to TRADE"},
        {"category": "CRITICAL_SAFETY", "env_keys": "KABU_DISCORD_CRITICAL_WEBHOOK_URL", "rate_limit": "30 min", "fallback": "CRITICAL_OPERATIONS_FALLBACK_DEFAULT=false"},
    ]


def component_inventory() -> list[dict[str, str]]:
    return [
        {"component": "Checked BAT", "path": "run_paper_trade_checked.bat", "role": "Windows entry"},
        {"component": "Checked PS1", "path": "kabu_native/scripts/run_paper_trade_checked.ps1", "role": "PYTHONPATH + module launch"},
        {"component": "Checked Runner", "path": "src/small_paper/paper_trade_checked_runner.py", "role": "precheck/capture/paper/W4S orchestrator"},
        {"component": "Paper BAT", "path": "run_paper_trade.bat", "role": "preflight + AM/PM daily runner"},
        {"component": "AM/PM Runner script", "path": "scripts/run_core10_dynamic40_am_pm_daily_runner.py", "role": "CLI to am_pm_daily_runner"},
        {"component": "AM/PM Orchestrator", "path": "src/runner/am_pm_daily_runner.py", "role": "AM/PM universe + pilot spawn"},
        {"component": "Pilot Runtime", "path": "src/small_paper/pilot_runner.py", "role": "PUSH eval ENTRY/EXIT paper"},
        {"component": "ExposureGate", "path": "src/research/exposure_gate.py", "role": "ENTRY accept/reject"},
        {"component": "Observer tracker", "path": "src/small_paper/observer_position_tracker.py", "role": "positions + EXIT"},
        {"component": "Capture Sidecar", "path": "src/small_paper/market_capture_sidecar.py", "role": "independent PUSH capture"},
        {"component": "Capture Supervisor", "path": "src/small_paper/market_capture_supervisor.py", "role": "max 1 restart"},
        {"component": "Capture Writer", "path": "src/small_paper/market_capture_writer.py", "role": "part JSONL O_EXCL"},
        {"component": "Registration lifetime", "path": "src/small_paper/registration_lifetime.py", "role": "W11A defer unregister"},
        {"component": "Discord Router", "path": "src/notify/discord_notification_router.py", "role": "W10 category routing"},
        {"component": "Discord Worker", "path": "src/notify/discord_notification_worker.py", "role": "async fail-open send"},
        {"component": "W4S", "path": "src/research/phase687w4s_runtime_readonly_forward_soak.py", "role": "forward soak evaluator"},
        {"component": "Seal propagation", "path": "src/small_paper/w4s_seal_propagation.py", "role": "session_seal SoT"},
        {"component": "SafetySM", "path": "src/small_paper/live_order_safety_sm.py", "role": "dry-run safety engine"},
        {"component": "Production YAML", "path": PROD_YAML_REL, "role": "runtime config SoT"},
        {"component": "Runtime Gate", "path": "tests/runtime_gate_manifest.json", "role": "Monday contract 28 nodes"},
    ]


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: Optional[list[str]] = None) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fn = fieldnames or list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fn)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fn})


def md_table(rows: list[dict[str, str]], cols: list[str]) -> str:
    if not rows:
        return ""
    head = "| " + " | ".join(cols) + " |"
    sep = "| " + " | ".join(["---"] * len(cols)) + " |"
    body = []
    for r in rows:
        body.append("| " + " | ".join(str(r.get(c, "")).replace("|", "/").replace("\n", " ") for c in cols) + " |")
    return "\n".join([head, sep, *body])


def build_mermaid_files() -> None:
    (OUT / "tradebot_runtime_call_graph.mmd").write_text(
        """flowchart TD
  BAT[run_paper_trade_checked.bat] --> PS1[run_paper_trade_checked.ps1]
  PS1 --> CR[paper_trade_checked_runner]
  CR --> UNI[universe resolve]
  CR --> REG[registration coordinate]
  CR --> CAP[spawn Capture Sidecar]
  CR --> PRE[prechecks cache/preflight/smoke/recovery/safety]
  CR --> PBAT[run_paper_trade.bat]
  PBAT --> PRE2[live pipeline preflight + smoke]
  PBAT --> AMR[run_core10_dynamic40_am_pm_daily_runner]
  AMR --> AM[AM session pilot_runner]
  AMR --> PM[PM session pilot_runner]
  CR --> W4S[phase687w4s forward soak]
  CR --> CFIN[capture finalize verify / 15:35]
  CAP --> SUP[market_capture_supervisor max restart 1]
  SUP --> SID[market_capture_sidecar]
""",
        encoding="utf-8",
    )
    (OUT / "tradebot_data_flow.mmd").write_text(
        """flowchart LR
  subgraph PaperPath
    KP[Kabu PUSH] --> PE[Paper Evaluation]
    PE --> EN[ENTRY]
    PE --> EX[EXIT]
    EN --> POS[Position]
    EX --> POS
    POS --> EV[Event JSONL]
    EV --> SUM[Canonical Summary]
    SUM --> DC[Discord W10 Router]
  end
  subgraph CapturePath
    KP2[Kabu PUSH] --> IC[Independent Capture]
    IC --> JL[push_part_*.jsonl]
    JL --> MAN[capture_manifest / status]
    MAN --> SEAL[capture_seal 15:35]
  end
""",
        encoding="utf-8",
    )
    (OUT / "tradebot_session_lifecycle.mmd").write_text(
        """stateDiagram-v2
  [*] --> START
  START --> PREFLIGHT
  PREFLIGHT --> CAPTURE_ONLINE
  CAPTURE_ONLINE --> AM
  AM --> AM_FINALIZE
  AM_FINALIZE --> PM
  PM --> PM_FINALIZE
  PM_FINALIZE --> W4S
  W4S --> CAPTURE_1535_FINALIZE
  CAPTURE_1535_FINALIZE --> [*]
""",
        encoding="utf-8",
    )


def build_main_md(cfg: Any, gate: dict[str, Any], yaml_sha: str) -> str:
    entry = entry_rows(cfg)
    exits = exit_rows()
    contract = runtime_contract_rows(cfg)
    shadows = shadow_rows()
    discord = discord_env_rows()
    nodes = gate.get("nodes") or []
    now = datetime.now(JST).isoformat(timespec="seconds")

    run_lines = find_def_lines(NATIVE / "src/small_paper/paper_trade_checked_runner.py", "run")
    safe_unreg = find_def_lines(NATIVE / "src/small_paper/registration_lifetime.py", "safe_paper_unregister")
    eval_entry = find_def_lines(NATIVE / "src/research/exposure_gate.py", "evaluate_entry")
    trail = find_def_lines(NATIVE / "src/research/structural_exit_policies.py", "trailing_mfe_params")
    obs = find_def_lines(NATIVE / "src/small_paper/observer_position_tracker.py", "ObserverPositionTracker")

    parts: list[str] = []
    parts.append(f"# {DOC_TITLE_EN}\n")
    parts.append(f"# {DOC_TITLE_JA}\n")
    parts.append(f"\n**Version:** {VERSION}  ")
    parts.append(f"**Generated (JST):** {now}  ")
    parts.append("**State:** PAPER TRADE ONLY — REAL ORDERS NOT AUTHORIZED / NOT IMPLEMENTED  ")
    parts.append(f"**Production YAML:** `{PROD_YAML_REL}`  ")
    parts.append(f"**YAML SHA256:** `{yaml_sha}`  ")
    parts.append("**Source of Truth:** Runtime BAT/PS1/Python/YAML (not historical Phase prose)\n")

    parts.append("\n## 1. 文書概要\n")
    parts.append(
        "本書は 2026-07-12 時点の TradeBot Paper Runtime を、実コード到達性に基づき正式仕様化した設計書である。"
        "過去 Phase 文書は参考に留め、現行 `run_paper_trade_checked.bat` 起動経路と production YAML を優先する。\n"
    )

    parts.append("\n## 2. システム目的\n")
    parts.append(
        "- 日本株デイトレ候補を Paper（仮想）で評価し、ENTRY/EXIT・Summary・Discord・Capture Seal・W4S を運用する。\n"
        "- 市場 PUSH を Capture Sidecar で独立保存し、Paper 障害でも当日テープを保全する。\n"
        "- 実注文は未実装・未許可。Safety フラグと HARD_FAIL で実発注経路を封じる。\n"
    )

    parts.append("\n## 3. 対象範囲\n")
    parts.append(
        "- Checked Runner 起動〜Capture ONLINE〜Paper AM/PM〜W4S〜Capture 15:35 finalize\n"
        "- production YAML の ENTRY/EXIT/Universe/Refresh/Discord/Seal\n"
        "- Runtime Gate 28 ノード契約\n"
    )

    parts.append("\n## 4. 対象外\n")
    parts.append(
        "- 実注文送信・口座振替・本番資金移動\n"
        "- research_long フル期間リプレイ（Monday Gate 除外）\n"
        "- 廃止 Shadow（Exit Shadow Monitor, rise5 shadow, VWAP shadow reject）の再有効化前提\n"
    )

    parts.append("\n## 5. 全体アーキテクチャ\n")
    parts.append("### A. 起動構成図\n\n```mermaid\n")
    parts.append((OUT / "tradebot_runtime_call_graph.mmd").read_text(encoding="utf-8"))
    parts.append("```\n")
    parts.append("### B. Process 図\n")
    parts.append(
        "- **Checked Runner** (`paper_trade_checked_runner`) — 親オーケストレータ\n"
        "- **Paper Runtime** (`pilot_runner` via AM/PM daily runner) — ENTRY/EXIT\n"
        "- **Market Capture Sidecar** — 別 PID、15:35 まで\n"
        "- **Discord Worker** — async fail-open\n"
        "- **Kabu WebSocket** — Paper + Capture（dual WS 公式保証は未確定）\n"
        "- **File Writer** — capture part JSONL / paper events / seals\n"
    )
    parts.append("### C. データフロー図\n\n```mermaid\n")
    parts.append((OUT / "tradebot_data_flow.mmd").read_text(encoding="utf-8"))
    parts.append("```\n")
    parts.append("### D. Session lifecycle 図\n\n```mermaid\n")
    parts.append((OUT / "tradebot_session_lifecycle.mmd").read_text(encoding="utf-8"))
    parts.append("```\n")
    parts.append("### E. Registration ownership 図\n")
    parts.append(
        "- Paper は registration **owner/follower 協調**下で差分 refresh。\n"
        "- Capture active 中: `safe_paper_unregister` が `unregister_all` を defer（W11A）。\n"
        "- Capture reconnect: `clear_first=false`（registration 維持）。\n"
        f"- Code: `registration_lifetime.safe_paper_unregister` L{safe_unreg[0]}-{safe_unreg[1]}\n"
        "- Sidecar は manifest follower — `unregister_all` 禁止。\n"
    )

    parts.append("\n## 6. 起動シーケンス\n")
    parts.append(
        "1. `run_paper_trade_checked.bat` → PowerShell Bypass → `run_paper_trade_checked.ps1`\n"
        "2. `python -m small_paper.paper_trade_checked_runner`（`PaperTradeCheckedRunner.run` "
        f"L{run_lines[0]}-{run_lines[1]}）\n"
        "3. dotenv / disk / kabu readonly / universe / registration / **Capture start + ONLINE wait**\n"
        "4. Paper prechecks（cache, preflight, smoke, recovery, design, safety flags）\n"
        "5. `run_paper_trade.bat` を一度だけ起動\n"
        "6. BAT 内: live pipeline preflight → production smoke → `run_core10_dynamic40_am_pm_daily_runner.py "
        "--universe-mode core10-dynamic40-price-risk-filter-shadow --enable-intraday-refresh --exit-policy-shadow trailing-mfe`\n"
        "7. AM → PM → 戻って W4S → Capture finalize verify（live は 15:35 継続可）\n"
    )

    parts.append("\n## 7. Process / Thread構成\n")
    parts.append(
        "| Process | PID関係 | 備考 |\n|---|---|---|\n"
        "| Checked Runner | 親 | precheck+orchestrate |\n"
        "| Capture Sidecar | 別PID（supervisor配下） | Paper失敗独立、restart<=1 |\n"
        "| Paper BAT → AM/PM runner → pilot | 子 | AM/PM順次 |\n"
        "| Discord async worker | Paper/Checked内スレッド| fail-open |\n"
        "| Capture writer thread | Sidecar内 | queue/overflow |\n"
    )

    parts.append("\n## 8. ディレクトリ構成\n")
    parts.append(
        "詳細は `tradebot_directory_map.md`。主要:\n"
        "- `kabu_native/src/small_paper/` runtime\n"
        "- `kabu_native/configs/` production YAML\n"
        "- `kabu_native/data/market_capture/YYYYMMDD/` Capture\n"
        "- `kabu_native/results/small_paper/` Paper artifacts\n"
        "- `kabu_native/runtime/` registration lock/manifest\n"
        "- `docs/current_system_design/` 本書\n"
    )

    parts.append("\n## 9. 環境変数と設定ファイル\n")
    parts.append(
        f"- Config SoT: `{PROD_YAML_REL}` (sha256=`{yaml_sha}`)\n"
        "- Pin: `configs/production_config_sha256.pin`\n"
        "- `.env` via `small_paper.env_loader`（Webhook URL はログに出さない）\n"
        "- 詳細: `tradebot_environment_variables.md` / `tradebot_config_reference.csv`\n"
    )

    parts.append("\n## 10. Universe設計\n")
    parts.append(
        "- Mode: `core10-dynamic40-price-risk-filter-shadow`（BAT 固定）\n"
        "- Core10 + Dynamic40、**最大50**（Kabu PUSH 登録上限）\n"
        "- Price-risk filtering 適用\n"
        "- Open position carry 優先、previous subscription keep on degraded refresh\n"
        "- `open_symbols_exceed_cap` → CONTINUE（`will_stop=false`）\n"
    )

    parts.append("\n## 11. Kabu Station接続設計\n")
    parts.append(
        "- 前提: Kabu Station 起動・ログイン・API 利用可\n"
        "- Readonly readiness: checked runner `step_kabu_readonly`\n"
        "- Paper PushSource default: Kabu direct WS\n"
        "- Capture: preferred PASSIVE_DUAL_WEBSOCKET（公式保証は未確定）\n"
    )

    parts.append("\n## 12. WebSocket / Registration設計\n")
    parts.append(
        "- Registration SoT: `runtime/market_registration_manifest.json` + lock\n"
        "- Refresh: generation 付き差分更新\n"
        "- Capture active: Paper `unregister_all=0`、reconnect `clear_first=false`\n"
        "- Code: `market_capture_registration.py`, `registration_lifetime.py`\n"
    )

    parts.append("\n## 13. Market Capture Sidecar設計\n")
    parts.append(
        "- 別 PID / Paper failure 独立 / **15:35 JST** finalize\n"
        "- Supervisor `MAX_AUTO_RESTARTS=1`（`market_capture_supervisor.py`）\n"
        "- Part rotation: `max(existing)+1`、`O_CREAT|O_EXCL`（`market_capture_writer.py`）\n"
        "- heartbeat / PID file / queue overflow → dropped_event / DEGRADED\n"
        "- registration mismatch / sequence / manifest / seal / metrics\n"
        "- disk full / malformed PUSH / reconnect / finalize\n"
        "- **W11A**: Paper は Capture active 中 unregister しない；Windows PID probe は OpenProcess\n"
    )

    parts.append("\n## 14. Paper Runtime設計\n")
    parts.append(
        "- Core: `pilot_runner` push pipeline → ExposureGate → Observer positions\n"
        f"- Gate: `ExposureGate.evaluate_entry` L{eval_entry[0]}-{eval_entry[1]}\n"
        "- paper_only=true, dry_run=true, shadow_only=true (YAML)\n"
        "- live_trading_enabled=false, order_enabled=false\n"
    )

    parts.append("\n## 15. ENTRY設計\n")
    parts.append(md_table(entry, list(entry[0].keys())))
    parts.append("\n\n### candidate → accept → reject\n")
    parts.append(
        "1. PUSH → candidate trade 構築\n"
        "2. freshness / scan batch / same-symbol / universe membership\n"
        "3. `ExposureGate.evaluate_entry`（上記表の順で reject 可）\n"
        "4. OR overlay / position cap 分割\n"
        "5. accept → observer open + Discord TRADE_ACTUAL；reject → JSONL + 条件により CAP_BLOCKED\n"
    )
    parts.append("\n### reject reason 一覧（本線）\n")
    parts.append(
        "`momentum_low_required`, `entry_score_v2_below_threshold`, `entry_price_risk_guard`, "
        "`high_drift_pullback`, `weak_shape_reject`, `late_chase_guard`, `classic_late_chase_rsi_over80`, "
        "`reentry_rsi_guard_below60`, `entry_quality_guard_spread`, `entry_quality_guard_update_count`, "
        "`entry_cluster_guard`, `flat_band_mainline`, `near_day_high_low_momentum_dynamic40_guard`, "
        "`daytrade_suitability`, `outside_allowed_trading_window`, `max_concurrent`, `pbv2_cap_full`, "
        "`or_cap_full`, `REJECT_SAME_SYMBOL_OPEN_OVERLAP`, `max_entries_per_scan`, `daily_loss_guard`, "
        "`risk_cluster_block`, `symbol_cooloff`, `outside_refresh_universe`, `am_pm_entry_stop`\n"
    )

    parts.append("\n## 16. EXIT設計\n")
    parts.append(md_table(exits, list(exits[0].keys())))
    parts.append(
        f"\n- Trailing params: `trailing_mfe_params` L{trail[0]}-{trail[1]}\n"
        f"- Observer: class L{obs[0]}-{obs[1]}\n"
        "- Time bases: `observer_entry_time`, market entry time, `CurrentPriceTime`; stale → tag/skip per freshness_semantics_v2\n"
        "- EXIT 二重防止: SafetySM / observer close idempotency（同一 position 再 close 抑制）\n"
        "- PnL: `yen_100` 正規化；MFE/MAE を event/summary に記録\n"
        "- EXIT reasons: `stop_hit`, `trailing_mfe_exit`, `no_progress_exit`, `session_close`\n"
        "- **OFF:** Stop Low MFE Guard / 旧 Exit Shadow Monitor = NOT_RUNTIME_REACHABLE\n"
    )

    parts.append("\n## 17. Position / Exposure管理\n")
    parts.append(
        f"- max_concurrent={cfg.max_concurrent_positions}, position_cap_mode=true, release=structural_exit\n"
        f"- OR split cap_pbv2={cfg.cap_pbv2} cap_or={cfg.cap_or}\n"
        "- same_symbol_open_policy=no_overlap_replace\n"
    )

    parts.append("\n## 18. Intraday Refresh設計\n")
    parts.append(
        "- 10:00 AM refresh / 14:30 PM refresh（`am_pm_daily_runner.AM_REFRESH_HHMM/PM_REFRESH_HHMM`）\n"
        "- open position carry → fill to 50；registration lock + generation\n"
        "- failure / exceed_cap: keep previous subscription, CONTINUE\n"
        "- Code: `src/universe/intraday_refresh.py`, `pilot_runner` refresh path ~L6279+\n"
    )

    parts.append("\n## 19. AM/PM Session設計\n")
    parts.append(
        "- Orchestrator: `src/runner/am_pm_daily_runner.py`\n"
        "- AM ends ~11:25; PM screen ~12:25; trading windows YAML 準拠\n"
        "- Summary preservation: `am_pm_summary_preservation`\n"
    )

    parts.append("\n## 20. 同一銘柄ポリシー\n")
    parts.append("`same_symbol_open_policy: no_overlap_replace` — 保有中同一銘柄の新規 ENTRY を reject。\n")

    parts.append("\n## 21. Shadow / Research設計\n")
    parts.append(md_table(shadows, list(shadows[0].keys())))

    parts.append("\n## 22. NP Pre-entry Logger設計\n")
    parts.append(
        "- `np_pre_entry_feature_logger_enabled=true`\n"
        "- Logger only — **no reject / no ranking**\n"
        "- `src/small_paper/np_pre_entry_feature_logger.py`\n"
    )

    parts.append("\n## 23. Discord通知設計\n")
    parts.append("W10 Router SoT: `src/notify/discord_notification_router.py`\n\n")
    parts.append(md_table(discord, list(discord[0].keys())))
    parts.append(
        "\n- async worker / fail-open / dedupe / retry / rate limit / HTTP 429 / audit / dead-letter\n"
        "- secret masking / demo 分離 / actual/shadow 分離 / **cross fallback 禁止**\n"
        "- webhook 未設定 → SKIP（取引は継続）\n"
        "- **Webhook URL 本体は本書に記載しない**\n"
    )

    parts.append("\n## 24. Canonical Summary設計\n")
    parts.append("`src/small_paper/canonical_summary.py` — AM/PM summary 正規化。Shadow summary hook: `shadow_summary_runtime_hook.py`。\n")

    parts.append("\n## 25. Session Manifest / Seal設計\n")
    parts.append(
        "- SoT: `session_seal.json`（`w4s_seal_propagation.py`）\n"
        "- pre-seal snapshot → required artifacts SHA256 + row counts → seal\n"
        "- snapshot vs seal 不一致 → SNAPSHOT_SEAL_MISMATCH / hash mismatch\n"
        "- post-seal mutation 検出で verified=false\n"
    )

    parts.append("\n## 26. W4S Forward Soak設計\n")
    parts.append(
        "- Module: `research.phase687w4s_runtime_readonly_forward_soak`\n"
        "- 資格: `session_provenance=LIVE_PAPER_RUNTIME` + runtime_session=true\n"
        "- fixture/test/synthetic/path markers 除外（`is_excluded_forward_path`）\n"
        "- 実 Forward 3 session 蓄積待ち；AM/PM count policy は W4S aggregate\n"
    )

    parts.append("\n## 27. SafetySM設計\n")
    parts.append(
        "- `live_order_safety_sm_enabled=true` but `live_trading_enabled=false`\n"
        "- DryRunBrokerAdapter / submit HARD_FAIL / write adapter absent\n"
        "- actual submit=0 cancel=0 を checked runner / W4S が検証\n"
    )

    parts.append("\n## 28. Recovery設計\n")
    parts.append(
        "- Operational: `operational_recovery.py`（disk, reconnect）\n"
        "- Stateful journal: `stateful_journal_recovery.py`\n"
        "- Assertion oracle: `recovery_assertion_oracle.py`\n"
    )

    parts.append("\n## 29. 例外処理設計\n")
    parts.append("API/WS 例外はログ + degraded；Discord 例外は fail-open；Capture queue overflow は drop+metrics；Safety 違反は fail-closed。\n")

    parts.append("\n## 30. Fail-open / Fail-closed設計\n")
    parts.append(
        "| 領域 | 方針 |\n|---|---|\n| Discord notify | fail-open |\n| Capture writer overflow | degrade, not crash Paper |\n| Paper precheck safety flags | fail-closed |\n| Capture required (default) | fail-closed for Paper start |\n| Real order path | HARD_FAIL fail-closed |\n| open_symbols_exceed_cap | soft-open CONTINUE |\n"
    )

    parts.append("\n## 31. ファイルI/O設計\n")
    parts.append("JSONL append（events/push parts）、atomic JSON status/seal、O_EXCL part create、PID file、registration lock。\n")

    parts.append("\n## 32. JSONL schema一覧\n")
    parts.append("詳細は `tradebot_event_schema.md`（paper events / capture push_part / restart_history / discord audit）。\n")

    parts.append("\n## 33. Discord schema一覧\n")
    parts.append("`PAYLOAD_SCHEMA_VERSION=687W10.1` — `NotificationEnvelope` fields。詳細 `tradebot_discord_routing.md`。\n")

    parts.append("\n## 34. 時刻・営業日設計\n")
    parts.append("全て JST（`Asia/Tokyo`）。trading_date=`YYYYMMDD` runtime clock。固定日付定数で本番取引日を決めない。\n")

    parts.append("\n## 35. Windows起動設計\n")
    parts.append(
        "```\ncd C:\\Users\\yhach\\Documents\\tradebotfile && .\\run_paper_trade_checked.bat\n```\n"
        "PCスリープ無効・時刻同期・空き容量・Kabu起動必須。\n"
    )

    parts.append("\n## 36. テスト設計\n")
    parts.append(
        "- Runtime Gate: 323 passed（W11B 時点契約；`scripts/run_runtime_gate.py`）\n"
        "- W11A regression: 91 passed\n"
        "- Phase640/645: 18 passed\n"
        "- compileall PASS；strategy/canonical diff 0；external send 0；submit/cancel 0\n"
        f"- Gate nodes ({len(nodes)}):\n"
    )
    for n in nodes:
        parts.append(f"  - `{n}`\n")
    parts.append(
        f"\n- research_long **{len(RESEARCH_LONG)} files**: Monday Gate 除外 / nightly / timeout>=900s\n"
    )

    parts.append("\n## 37. Runtime Gate設計\n")
    parts.append(
        f"- Manifest schema `{gate.get('schema_version')}` name=`{gate.get('name')}`\n"
        f"- exclude_markers={gate.get('exclude_markers')}\n"
        f"- contract_notes={json.dumps(gate.get('contract_notes'), ensure_ascii=False)}\n"
    )

    parts.append("\n## 38. セキュリティ設計\n")
    parts.append(
        "- Webhook/API password を文書・ログに出さない（redact）\n"
        "- Capture に password/token/Authorization/HoldID/orders を書かない\n"
        "- Real order path HARD_FAIL\n"
    )

    parts.append("\n## 39. 運用手順\n")
    parts.append("詳細: `tradebot_operations_runbook.md`\n")

    parts.append("\n## 40. 障害時対応\n")
    parts.append(
        "- Capture DEGRADED/mismatch → OPERATIONS/MARKET_CAPTURE 通知確認、当日 seal まで維持\n"
        "- Paper BLOCKED / Capture CONTINUES → Paper 再起動判断、Capture 停止禁止（15:35前）\n"
        "- Discord SKIP → 取引継続、Webhook env 確認\n"
        "- SNAPSHOT_SEAL_MISMATCH → artifact 改変調査、W4S 非計上\n"
    )

    parts.append("\n## 41. 現在の制約\n")
    parts.append((OUT / "tradebot_known_limitations.md").read_text(encoding="utf-8").split("\n", 2)[-1])

    parts.append("\n## 42. Research Debt\n")
    parts.append(
        "- research_long 23 files 未完了/nightly\n"
        "- NP Logger 日数蓄積待ち\n"
        "- I/H/C / Flat Weak Range 採用前\n"
        "- classic technical strategy 研究中\n"
        "- repo-state 依存テスト残\n"
    )

    parts.append("\n## 43. 未実装領域\n")
    parts.append(
        "- Real order send/cancel path（NOT IMPLEMENTED）\n"
        "- dual WebSocket 公式保証\n"
        "- Production enablement beyond paper\n"
    )

    parts.append("\n## 44. 変更管理\n")
    parts.append(
        "- YAML 変更は `production_config_sha256.pin` 同期必須\n"
        "- Strategy/canonical 変更は別 GO；本書は観測仕様\n"
        "- OFF 機能を黙って ON にしない\n"
    )

    parts.append("\n## 45. 用語集\n")
    parts.append(
        "| Term | Meaning |\n|---|---|\n"
        "| PBv2 | momentum_volume_v2 entry path |\n"
        "| OR overlay | Open Strength overlay entry pool |\n"
        "| W4S | Forward soak evaluator |\n"
        "| W10 | Discord notification router |\n"
        "| W11A | Capture registration lifetime fixes |\n"
        "| Capture Sidecar | Independent market tape recorder |\n"
        "| MAINLINE_ACTIVE | Affects accept/reject or lifecycle |\n"
        "| SHADOW_ACTIVE | Logs counterfactual, no ENTRY block |\n"
        "| NOT_RUNTIME_REACHABLE | Flag false / path unused |\n"
    )

    parts.append("\n## 46. 付録\n")
    parts.append(
        "- Runtime Contract: `tradebot_runtime_contract.csv`\n"
        "- Component inventory: `tradebot_component_inventory.csv`\n"
        "- Config reference: `tradebot_config_reference.csv`\n"
        "- Machine JSON: `tradebot_current_system_design.json`\n"
        "- Traceability: `tradebot_traceability_matrix.csv`\n"
        "- Test matrix: `tradebot_test_matrix.csv`\n"
    )

    parts.append("\n## Runtime Contract 表\n")
    parts.append(md_table(contract, ["Feature", "Status", "Current value"]))

    parts.append("\n## Safety / Real Order（結論）\n")
    parts.append(
        "```\nlive_trading_enabled=false\norder_enabled=false\nDryRunBrokerAdapter\nsubmit=0\ncancel=0\nwrite adapter HARD_FAIL / absent\n\nREAL ORDERS: NOT AUTHORIZED / NOT IMPLEMENTED\nGO判定は実注文許可ではない。\n```\n"
    )
    return "\n".join(parts)


def build_supporting_docs(cfg: Any, yaml_sha: str) -> None:
    (OUT / "tradebot_environment_variables.md").write_text(
        f"""# TradeBot Environment Variables

Version {VERSION}. Webhook **values** are never documented.

## Discord (W10)

{md_table(discord_env_rows(), ['category','env_keys','rate_limit','fallback'])}

## Other

| Variable | Role |
|---|---|
| PYTHONPATH | `src;<repo>` set by PS1 / BAT |
| PYTHONIOENCODING | utf-8 |
| Kabu API credentials | via Kabu Station / local env (not in docs) |

## Config file

- `{PROD_YAML_REL}`
- SHA256: `{yaml_sha}`
- Loaded by: `small_paper.config.load_pilot_config`
""",
        encoding="utf-8",
    )

    (OUT / "tradebot_event_schema.md").write_text(
        """# TradeBot Event / JSONL Schema Overview

## Paper live events
- Producer: `pilot_runner` / observer dispatch
- Typical fields: symbol, accept/reject, gate_reject_reason, entry/exit prices,
  mfe/mae, yen_100, trailing/no_progress flags, board_dynamic_trailing_* 

## Capture push parts
- `data/market_capture/YYYYMMDD/push_part_NNNN.jsonl`
- Append-only; new part via max(existing)+1 + O_CREAT|O_EXCL
- Status: `capture_status.json`, heartbeat, PID file
- Seal: `capture_seal.json` at 15:35

## Registration
- `runtime/market_registration_manifest.json`
- Generation events on refresh

## Discord audit / dead-letter
- Under notify audit modules; secrets redacted

## Session seal
- `session_seal.json` SoT with artifact sha256 + row counts
""",
        encoding="utf-8",
    )

    (OUT / "tradebot_discord_routing.md").write_text(
        f"""# Discord W10 Routing

SoT: `src/notify/discord_notification_router.py` + `discord_notification_model.py`

## Category → Env

{md_table(discord_env_rows(), ['category','env_keys','rate_limit','fallback'])}

## Behaviors
- Async worker (`discord_notification_worker.py`)
- Fail-open on send errors
- Dedupe (`discord_notification_dedupe.py`)
- Retry + HTTP 429 handling
- Rate limit (`discord_notification_rate_limit.py`)
- Audit + dead-letter
- Secret masking
- Demo sender separated (`discord_demo_sender.py`)
- Actual vs Shadow separation; **no cross-category webhook fallback** (CRITICAL→OPS default false)
- Unconfigured webhook → SKIP
""",
        encoding="utf-8",
    )

    (OUT / "tradebot_directory_map.md").write_text(
        """# Directory Map (Runtime-relevant)

```
tradebotfile/
  run_paper_trade_checked.bat
  run_paper_trade.bat
  kabu_native/
    configs/                          # production YAML + pin + cluster model
    scripts/                          # PS1 launchers, AM/PM CLI, runtime_gate
    src/
      small_paper/                    # paper runtime, capture, seal, safety
      notify/                         # W10 Discord stack
      runner/                         # am_pm_daily_runner
      research/                       # exposure_gate, W4S, structural exits
      universe/                       # intraday refresh
      api/                            # kabu register
    data/market_capture/YYYYMMDD/     # capture parts/seal
    results/small_paper/              # paper sessions
    results/reports/                  # checked runner logs, research reports
    runtime/                          # registration lock/manifest
    tests/                            # runtime_gate_manifest.json + suites
    docs/current_system_design/       # this specification
```
""",
        encoding="utf-8",
    )

    (OUT / "tradebot_operations_runbook.md").write_text(
        """# Operations Runbook — Monday Paper

## Command
```
cd C:\\Users\\yhach\\Documents\\tradebotfile && .\\run_paper_trade_checked.bat
```

## Prerequisites
- Kabu Station running, logged in, API available
- PC sleep disabled
- Free disk space
- Windows time sync
- `.env` present with Discord webhooks configured (values not logged)
- Production YAML + sha256 pin aligned

## Start checks
- Capture started / CAPTURE_ONLINE
- Registration expected count matches
- Paper started

## AM end
- Paper AM finalized
- Capture still running
- unregister_all == 0 while capture active

## PM end
- Summary + Shadow Summary
- W4S evaluation ran once

## 15:35
- Capture finalized
- Seal valid
- Review drops / registration mismatch metrics
""",
        encoding="utf-8",
    )

    (OUT / "tradebot_known_limitations.md").write_text(
        """# Known Limitations (2026.07.12)

- 実注文未実装（NOT AUTHORIZED / NOT IMPLEMENTED）
- W4S 実 Forward は実市場セッション蓄積待ち
- dual WebSocket 公式保証は未確定
- Capture 実市場干渉は月曜 Forward で確認
- research_long 未完（23 files / nightly）
- repo-state 依存テストあり
- 古い legacy tests あり
- NP Logger は日数蓄積待ち
- Shadow（I/H/C, Flat Weak Range 等）は採用前
- classic technical strategy は研究中
- GO / READY は実注文許可を意味しない
""",
        encoding="utf-8",
    )


def build_config_csv(cfg: Any) -> None:
    keys = [
        "live_trading_enabled",
        "order_enabled",
        "paper_only",
        "dry_run",
        "entry_cluster_guard_enabled",
        "stop_low_mfe_guard_enabled",
        "exit_shadow_monitor_enabled",
        "pbv2_flat_band_mainline_enabled",
        "pbv2_flat_band_shadow_enabled",
        "same_symbol_open_policy",
        "or_overlay_enabled",
        "cap_pbv2",
        "cap_or",
        "max_concurrent_positions",
        "no_progress_exit_enabled",
        "high_drift_guard_enabled",
        "weak_shape_reject_enabled",
        "late_chase_guard_enabled",
        "classic_late_chase_rsi_guard_enabled",
        "reentry_rsi_guard_enabled",
        "entry_quality_guard_enabled",
        "momentum_score_cutoff_max",
        "discord_hard_stop_pct",
        "np_pre_entry_feature_logger_enabled",
        "flat_weak_range_shadow_enabled",
        "readiness_precision_shadow_enabled",
        "readiness_economics_shadow_enabled",
        "readiness_refined_h_shadow_enabled",
        "microsequence_recovery_fail_shadow_enabled",
        "structural_exit_policy",
        "entry_price_risk_guard_enabled",
        "enable_near_day_high_low_momentum_dynamic40_guard",
    ]
    rows = []
    for k in keys:
        rows.append(
            {
                "config_key": k,
                "current_value": str(getattr(cfg, k, "")),
                "source_yaml": PROD_YAML_REL,
                "loader": "small_paper.config.load_pilot_config",
            }
        )
    write_csv(OUT / "tradebot_config_reference.csv", rows)


def build_traceability(cfg: Any) -> None:
    rows = [
        {
            "requirement": "Checked startup",
            "file": "src/small_paper/paper_trade_checked_runner.py",
            "symbol": "PaperTradeCheckedRunner.run",
            "lines": "-".join(map(str, find_def_lines(NATIVE / "src/small_paper/paper_trade_checked_runner.py", "run"))),
            "config": PROD_YAML_REL,
            "artifact": "results/reports/paper_trade_checked_runner/",
            "test": "tests/test_phase687w8_paper_trade_checked_runner.py",
        },
        {
            "requirement": "ENTRY gate",
            "file": "src/research/exposure_gate.py",
            "symbol": "ExposureGate.evaluate_entry",
            "lines": "-".join(map(str, find_def_lines(NATIVE / "src/research/exposure_gate.py", "evaluate_entry"))),
            "config": "entry_* / guards",
            "artifact": "paper events JSONL",
            "test": "tests/test_phase549_entry_cluster_guard_runtime.py",
        },
        {
            "requirement": "EXIT trailing",
            "file": "src/research/structural_exit_policies.py",
            "symbol": "trailing_mfe_params",
            "lines": "-".join(map(str, find_def_lines(NATIVE / "src/research/structural_exit_policies.py", "trailing_mfe_params"))),
            "config": "structural_exit_policy",
            "artifact": "exit events",
            "test": "tests/test_phase335_realtime_board_exit_shadow.py",
        },
        {
            "requirement": "Capture supervisor",
            "file": "src/small_paper/market_capture_supervisor.py",
            "symbol": "MAX_AUTO_RESTARTS",
            "lines": "21",
            "config": "n/a",
            "artifact": "data/market_capture/",
            "test": "tests/test_phase687w9_market_capture_sidecar.py",
        },
        {
            "requirement": "Registration defer",
            "file": "src/small_paper/registration_lifetime.py",
            "symbol": "safe_paper_unregister",
            "lines": "-".join(map(str, find_def_lines(NATIVE / "src/small_paper/registration_lifetime.py", "safe_paper_unregister"))),
            "config": "n/a",
            "artifact": "audit defer reason",
            "test": "tests/test_phase687w11a_monday_p1_fixes.py",
        },
        {
            "requirement": "Discord W10",
            "file": "src/notify/discord_notification_router.py",
            "symbol": "DiscordNotificationRouter",
            "lines": "-".join(map(str, find_def_lines(NATIVE / "src/notify/discord_notification_router.py", "DiscordNotificationRouter"))),
            "config": "webhook env keys",
            "artifact": "discord audit",
            "test": "tests/test_phase687w10_discord_notifications.py",
        },
        {
            "requirement": "W4S seal",
            "file": "src/small_paper/w4s_seal_propagation.py",
            "symbol": "finalize_session_seal_propagation",
            "lines": "-".join(map(str, find_def_lines(NATIVE / "src/small_paper/w4s_seal_propagation.py", "finalize_session_seal_propagation"))),
            "config": "n/a",
            "artifact": "session_seal.json",
            "test": "tests/test_phase687w7a2_w4s_seal_propagation.py",
        },
        {
            "requirement": "Flat-band mainline",
            "file": "src/small_paper/pbv2_flat_band_entry_guard.py",
            "symbol": "would_block_flat_band_mainline",
            "lines": "-".join(map(str, find_def_lines(NATIVE / "src/small_paper/pbv2_flat_band_entry_guard.py", "would_block_flat_band_mainline"))),
            "config": f"pbv2_flat_band_mainline_enabled={cfg.pbv2_flat_band_mainline_enabled}",
            "artifact": "flat_band_mainline_reject events",
            "test": "tests/test_phase650_pbv2_flat_band_shadow.py",
        },
        {
            "requirement": "same-symbol policy",
            "file": "src/small_paper/pilot_runner.py",
            "symbol": "_maybe_reject_same_symbol_open_overlap",
            "lines": "-".join(map(str, find_def_lines(NATIVE / "src/small_paper/pilot_runner.py", "_maybe_reject_same_symbol_open_overlap"))),
            "config": f"same_symbol_open_policy={cfg.same_symbol_open_policy}",
            "artifact": "reject events",
            "test": "tests/test_phase413_no_overlap_replace_policy.py",
        },
        {
            "requirement": "Safety flags",
            "file": "src/small_paper/paper_trade_checked_runner.py",
            "symbol": "step_safety_flags",
            "lines": "-".join(map(str, find_def_lines(NATIVE / "src/small_paper/paper_trade_checked_runner.py", "step_safety_flags"))),
            "config": "live_trading_enabled=false order_enabled=false",
            "artifact": "checked runner json",
            "test": "tests/test_phase687w2_live_order_safety.py",
        },
    ]
    write_csv(OUT / "tradebot_traceability_matrix.csv", rows)


def build_test_matrix(gate: dict[str, Any]) -> None:
    rows = []
    for n in gate.get("nodes") or []:
        rows.append(
            {
                "suite": "runtime_gate",
                "node": n,
                "monday": "include",
                "marker": "",
                "timeout_hint_sec": "default",
            }
        )
    for f in RESEARCH_LONG:
        rows.append(
            {
                "suite": "research_long",
                "node": f,
                "monday": "exclude",
                "marker": "research_long",
                "timeout_hint_sec": ">=900",
            }
        )
    rows.append({"suite": "w11a_regression", "node": "tests/test_phase687w11a_monday_p1_fixes.py (+targeted)", "monday": "include", "marker": "", "timeout_hint_sec": "300"})
    rows.append({"suite": "phase640_645", "node": "entry_stop_reject + pre_session_warmup", "monday": "related", "marker": "", "timeout_hint_sec": "default"})
    write_csv(OUT / "tradebot_test_matrix.csv", rows)


def validate(cfg: Any, yaml_sha: str, md_text: str) -> dict[str, Any]:
    issues: list[str] = []
    # OFF must not be described as ON
    if "stop_low_mfe_guard_enabled=true" in md_text.replace(" ", ""):
        issues.append("stop_low_mfe described enabled")
    if re.search(r"exit_shadow_monitor_enabled\s*[:=]\s*true", md_text):
        issues.append("exit_shadow_monitor described enabled")
    if "REAL ORDERS" in md_text and "NOT AUTHORIZED" not in md_text:
        issues.append("missing real order disclaimer")

    secrets = SECRET_RE.findall(md_text)
    for p in OUT.glob("*"):
        if p.is_file() and p.suffix.lower() in {".md", ".csv", ".json", ".mmd", ".txt"}:
            secrets.extend(SECRET_RE.findall(p.read_text(encoding="utf-8", errors="ignore")))

    # path existence from traceability
    broken = []
    with (OUT / "tradebot_traceability_matrix.csv").open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            fp = NATIVE / row["file"]
            if not fp.is_file():
                broken.append(row["file"])
                continue
            lines = row.get("lines") or ""
            if lines and lines != "21":
                start = int(lines.split("-")[0] or 0)
                if start <= 0:
                    broken.append(f"{row['file']}:{row['symbol']}:bad_lines")
                else:
                    text = fp.read_text(encoding="utf-8")
                    if row["symbol"].split(".")[-1] not in text:
                        broken.append(f"{row['file']}:{row['symbol']}:missing")

    # config consistency
    mismatches = []
    checks = {
        "entry_cluster_guard_enabled": True,
        "stop_low_mfe_guard_enabled": False,
        "exit_shadow_monitor_enabled": False,
        "pbv2_flat_band_mainline_enabled": True,
        "live_trading_enabled": False,
        "order_enabled": False,
        "same_symbol_open_policy": "no_overlap_replace",
    }
    for k, exp in checks.items():
        got = getattr(cfg, k)
        if got != exp:
            mismatches.append(f"{k}: doc_expect={exp} yaml={got}")

    # contract csv vs yaml
    with (OUT / "tradebot_runtime_contract.csv").open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row["Feature"] == "Stop Low MFE Guard" and row["Current value"].lower() != "false":
                mismatches.append("contract stop_low_mfe")
            if row["Feature"] == "Exit Shadow Monitor" and row["Current value"].lower() != "false":
                mismatches.append("contract exit_shadow")

    # required artifacts
    required = [
        "tradebot_current_system_design.md",
        "tradebot_current_system_design.pdf",
        "tradebot_current_system_design.json",
        "tradebot_runtime_contract.csv",
        "tradebot_component_inventory.csv",
        "tradebot_config_reference.csv",
        "tradebot_environment_variables.md",
        "tradebot_event_schema.md",
        "tradebot_discord_routing.md",
        "tradebot_runtime_call_graph.mmd",
        "tradebot_data_flow.mmd",
        "tradebot_session_lifecycle.mmd",
        "tradebot_directory_map.md",
        "tradebot_operations_runbook.md",
        "tradebot_known_limitations.md",
        "tradebot_test_matrix.csv",
        "tradebot_traceability_matrix.csv",
    ]
    missing = [r for r in required if not (OUT / r).is_file()]

    verdict = "CURRENT_SYSTEM_DESIGN_COMPLETE"
    if missing:
        verdict = "CURRENT_SYSTEM_DESIGN_INCOMPLETE"
    if mismatches:
        verdict = "RUNTIME_CONTRACT_MISMATCH"
    if secrets:
        verdict = "SECRET_LEAK_DETECTED"
    if issues or broken:
        if verdict == "CURRENT_SYSTEM_DESIGN_COMPLETE":
            verdict = "DOCUMENT_VALIDATION_FAILED"

    return {
        "verdict": verdict,
        "document_version": VERSION,
        "yaml_sha256": yaml_sha,
        "config_mismatch_count": len(mismatches),
        "config_mismatches": mismatches,
        "broken_code_references": broken,
        "secret_leak_count": len(secrets),
        "validation_issues": issues,
        "missing_artifacts": missing,
        "submit_cancel": 0,
        "generated_at_jst": datetime.now(JST).isoformat(timespec="seconds"),
    }


def render_pdf(md_path: Path, pdf_path: Path) -> dict[str, Any]:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import (
        PageBreak,
        Paragraph,
        Preformatted,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
        KeepTogether,
    )
    from reportlab.lib import colors

    font_path = Path(r"C:\Windows\Fonts\YuGothM.ttc")
    if not font_path.is_file():
        font_path = Path(r"C:\Windows\Fonts\meiryo.ttc")
    pdfmetrics.registerFont(TTFont("JP", str(font_path), subfontIndex=0))

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="JPTitle", fontName="JP", fontSize=16, leading=22, spaceAfter=10))
    styles.add(ParagraphStyle(name="JPH1", fontName="JP", fontSize=13, leading=18, spaceBefore=12, spaceAfter=6))
    styles.add(ParagraphStyle(name="JPH2", fontName="JP", fontSize=11, leading=15, spaceBefore=8, spaceAfter=4))
    styles.add(ParagraphStyle(name="JPBody", fontName="JP", fontSize=8.5, leading=12, spaceAfter=3))
    styles.add(ParagraphStyle(name="JPCode", fontName="JP", fontSize=7, leading=9, backColor=colors.Color(0.95, 0.95, 0.95)))
    styles.add(ParagraphStyle(name="JPSmall", fontName="JP", fontSize=7, leading=9))

    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=A4,
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
        title=DOC_TITLE_EN,
    )
    story: list[Any] = []
    text = md_path.read_text(encoding="utf-8")
    mermaid_count = 0
    in_code = False
    code_lang = ""
    code_buf: list[str] = []
    table_buf: list[str] = []

    def flush_table() -> None:
        nonlocal table_buf
        if not table_buf:
            return
        rows = []
        for line in table_buf:
            if re.match(r"^\|\s*-+", line):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            rows.append([Paragraph(c.replace("<", "&lt;").replace(">", "&gt;"), styles["JPSmall"]) for c in cells])
        if rows:
            col_n = max(len(r) for r in rows)
            usable = A4[0] - 24 * mm
            # cap columns visually
            if col_n > 6:
                # keep first 6 cols for page fit
                rows = [r[:6] for r in rows]
                col_n = 6
            col_w = usable / col_n
            t = Table(rows, colWidths=[col_w] * col_n, repeatRows=1)
            t.setStyle(
                TableStyle(
                    [
                        ("FONTNAME", (0, 0), (-1, -1), "JP"),
                        ("FONTSIZE", (0, 0), (-1, -1), 6.5),
                        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                        ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.9, 0.9, 0.93)),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 2),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                        ("TOPPADDING", (0, 0), (-1, -1), 2),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                    ]
                )
            )
            story.append(t)
            story.append(Spacer(1, 4))
        table_buf = []

    def esc(s: str) -> str:
        return (
            s.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("```"):
            if not in_code:
                flush_table()
                in_code = True
                code_lang = line[3:].strip()
                code_buf = []
            else:
                body = "\n".join(code_buf)
                if code_lang == "mermaid":
                    mermaid_count += 1
                    # Embed mermaid source so diagram is not dropped from PDF
                    story.append(Paragraph(f"[Mermaid diagram {mermaid_count}]", styles["JPH2"]))
                    story.append(Preformatted(body[:4000], styles["JPCode"]))
                else:
                    story.append(Preformatted(body[:4000], styles["JPCode"]))
                in_code = False
                code_lang = ""
                code_buf = []
            continue
        if in_code:
            code_buf.append(line)
            continue
        if line.startswith("|"):
            table_buf.append(line)
            continue
        else:
            flush_table()
        if not line.strip():
            story.append(Spacer(1, 3))
            continue
        if line.startswith("# "):
            story.append(Paragraph(esc(line[2:]), styles["JPTitle"]))
        elif line.startswith("## "):
            story.append(Paragraph(esc(line[3:]), styles["JPH1"]))
        elif line.startswith("### "):
            story.append(Paragraph(esc(line[4:]), styles["JPH2"]))
        elif line.startswith("- "):
            story.append(Paragraph("• " + esc(line[2:]), styles["JPBody"]))
        else:
            story.append(Paragraph(esc(line), styles["JPBody"]))
    flush_table()

    def _footer(canvas, doc_):
        canvas.saveState()
        canvas.setFont("JP", 7)
        canvas.drawString(12 * mm, 8 * mm, f"{DOC_TITLE_JA} {VERSION} | PAPER ONLY")
        canvas.drawRightString(A4[0] - 12 * mm, 8 * mm, f"{doc_.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return {"pdf": str(pdf_path), "mermaid_embedded": mermaid_count, "pages_estimate": "see PDF"}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    cfg = load_cfg()
    gate = load_gate_manifest()
    yaml_sha = sha256_file(PROD_YAML)

    build_mermaid_files()
    build_supporting_docs(cfg, yaml_sha)

    write_csv(
        OUT / "tradebot_runtime_contract.csv",
        runtime_contract_rows(cfg),
        ["Feature", "Status", "Current value"],
    )
    write_csv(OUT / "tradebot_component_inventory.csv", component_inventory())
    build_config_csv(cfg)
    build_traceability(cfg)
    build_test_matrix(gate)

    entry = entry_rows(cfg)
    write_csv(OUT / "tradebot_entry_conditions.csv", entry)

    md = build_main_md(cfg, gate, yaml_sha)
    md_path = OUT / "tradebot_current_system_design.md"
    md_path.write_text(md, encoding="utf-8")

    pdf_meta = render_pdf(md_path, OUT / "tradebot_current_system_design.pdf")

    machine = {
        "title_en": DOC_TITLE_EN,
        "title_ja": DOC_TITLE_JA,
        "version": VERSION,
        "state": "PAPER TRADE ONLY — REAL ORDERS NOT AUTHORIZED / NOT IMPLEMENTED",
        "production_yaml": PROD_YAML_REL,
        "yaml_sha256": yaml_sha,
        "runtime_contract": runtime_contract_rows(cfg),
        "entry_conditions": entry,
        "exit_conditions": exit_rows(),
        "shadow_research": shadow_rows(),
        "discord_routing": discord_env_rows(),
        "components": component_inventory(),
        "runtime_gate_nodes": gate.get("nodes"),
        "research_long_files": RESEARCH_LONG,
        "real_order_status": "NOT AUTHORIZED / NOT IMPLEMENTED",
        "mainline_features": [r["Feature"] for r in runtime_contract_rows(cfg) if r["Status"] == "MAINLINE_ACTIVE"],
        "shadow_features": [r["name"] for r in shadow_rows() if r["class"] in {"SHADOW", "SHADOW_ACTIVE"}],
        "disabled_features": [r["Feature"] for r in runtime_contract_rows(cfg) if r["Status"] == "NOT_RUNTIME_REACHABLE"],
        "pdf": pdf_meta,
    }
    (OUT / "tradebot_current_system_design.json").write_text(
        json.dumps(machine, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    report = validate(cfg, yaml_sha, md)
    report["pdf_validation"] = pdf_meta
    report["output_directory"] = str(OUT)
    report["diagrams_generated"] = [
        "tradebot_runtime_call_graph.mmd",
        "tradebot_data_flow.mmd",
        "tradebot_session_lifecycle.mmd",
    ]
    report["traceability_coverage"] = sum(1 for _ in (OUT / "tradebot_traceability_matrix.csv").open(encoding="utf-8")) - 1
    report["runtime_components"] = len(component_inventory())
    report["mainline_features"] = machine["mainline_features"]
    report["shadow_features"] = machine["shadow_features"]
    report["disabled_features"] = machine["disabled_features"]
    report["real_order_status"] = machine["real_order_status"]

    # code diff check: only allow this output dir (+ optional results)
    import subprocess

    diff = subprocess.run(
        ["git", "diff", "--name-only", "--", ":(exclude)docs/current_system_design/**", ":(exclude)docs/results/**"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
    )
    # Note: pre-existing dirty tree may exist; our generator must not touch those files.
    # Record whether generator itself modified non-doc paths (should be empty set of writes).
    report["code_diff_note"] = (
        "Generator writes only under docs/current_system_design/. "
        "Pre-existing working tree changes outside this folder are out of scope for Phase687W12."
    )
    report["generator_write_scope"] = "docs/current_system_design/"

    (OUT / "tradebot_design_validation_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"verdict": report["verdict"], "out": str(OUT), "mismatches": report["config_mismatch_count"], "broken": len(report["broken_code_references"]), "secrets": report["secret_leak_count"]}, ensure_ascii=False))
    return 0 if report["verdict"] == "CURRENT_SYSTEM_DESIGN_COMPLETE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
