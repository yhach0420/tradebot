#!/usr/bin/env python3
"""
Phase391 / System Evolution Source of Truth generator.

Canonical inputs:
  kabu_native/docs/audits/full_phase_history_audit.csv
Canonical outputs:
  kabu_native/docs/architecture/full_system_development_history.md
  kabu_native/docs/architecture/runtime_change_log.md
  kabu_native/docs/architecture/runtime_dependency_graph.md
  kabu_native/docs/architecture/runtime_adoption_funnel.md
"""

from __future__ import annotations

import csv
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
REPO = Path(__file__).resolve().parents[2]
AUDIT_CSV = REPO / "kabu_native" / "docs" / "audits" / "full_phase_history_audit.csv"
OUT_MD = REPO / "kabu_native" / "docs" / "architecture" / "full_system_development_history.md"
OUT_RUNTIME_LOG = REPO / "kabu_native" / "docs" / "architecture" / "runtime_change_log.md"
OUT_DEPENDENCY_GRAPH = REPO / "kabu_native" / "docs" / "architecture" / "runtime_dependency_graph.md"
OUT_ADOPTION_FUNNEL = REPO / "kabu_native" / "docs" / "architecture" / "runtime_adoption_funnel.md"
AUDIT_SCRIPT = REPO / "kabu_native" / "scripts" / "run_full_phase_history_audit.py"

NARRATIVE_START = "# Introduction"
NARRATIVE_CURRENT_STATE = "# Current State (Latest)"
NARRATIVE_END = "# Historical Misconceptions"

CANONICAL_MD = "kabu_native/docs/architecture/full_system_development_history.md"
CANONICAL_CSV = "kabu_native/docs/audits/full_phase_history_audit.csv"
CANONICAL_PDF = "kabu_native/docs/architecture/full_system_development_history.pdf"

KEY_MILESTONE_PHASES = [
    "55",
    "113",
    "117",
    "148",
    "153b",
    "174",
    "267",
    "314",
    "332",
    "333",
    "355",
    "364",
    "365",
    "272",
    "273",
    "274",
    "388",
    "389",
]

MILESTONE_DATES: dict[str, str] = {
    "55": "2026-05-18",
    "364": "2026-06-13",
}

MILESTONE_STATUS_OVERRIDES: dict[str, str] = {
    "55": "active",
    "364": "active",
}

MILESTONE_REASONS: dict[str, str] = {
    "55": "kabu PUSH paper observer を主 runtime に",
    "113": "vol-liq top50 universe 採用",
    "117": "volatility-liquidity universe 基盤",
    "148": "AM/PM 10:00/14:30 intraday refresh",
    "153b": "entry price risk guard 本番化",
    "174": "fixed trailing MFE shadow（後に332置換）",
    "267": "quality reject off + score_v2 min=3",
    "314": "entry score v2 簡素化（2 token）",
    "332": "board-dynamic trailing EXIT 本番化",
    "333": "canonical 100-share yen summary",
    "355": "6/12 Dynamic40 pullback guard",
    "364": "6/12 near-day-high low-mom D40 guard",
    "365": "maintain Stack C (355+364) 確定",
    "272": "lev2.0 fixed; 150万 CAP3 research recommend",
    "273": "live config forward shadow（observe）",
    "274": "auto 1.5M→2M transition shadow（observe）",
    "388": "1.5M live candidate validation",
    "389": "full-regime live candidate; CAP=2 research",
}

SUPPLEMENTAL_DECISION_ROWS: list[dict[str, str]] = [
    {
        "phase": "364",
        "date": "2026-06-13",
        "category": "Entry",
        "title": "Near day-high + low momentum Dynamic40 guard",
        "purpose": "Dynamic40-only near day-high low-momentum guard",
        "adoption_status": "adopted",
        "current_status": "active",
        "removed_or_disabled": "false",
        "related_phases": "365,363",
        "verdict": "Production runtime (Stack C)",
        "summary": "Phase365 maintain Phase355+364",
    },
    {
        "phase": "55",
        "date": "2026-05-18",
        "category": "Monitoring",
        "title": "Small paper observer runtime",
        "purpose": "kabu PUSH paper observer baseline",
        "adoption_status": "adopted",
        "current_status": "active",
        "removed_or_disabled": "false",
        "related_phases": "148,332",
        "verdict": "Production observer runtime",
        "summary": "Git Phase55 small paper observer runtime",
    },
]

EVIDENCE_BY_PHASE: dict[str, dict[str, str]] = {
    "55": {"evidence": "Git: Phase55 small paper observer runtime", "source": "git"},
    "113": {"evidence": "Production runtime Stack C universe", "source": "audit"},
    "117": {"evidence": "Production runtime Stack C universe", "source": "audit"},
    "148": {"evidence": "Phase114 12:25 superseded", "source": "audit"},
    "153b": {"evidence": "YAML entry_price_risk_guard active", "source": "config"},
    "166": {"evidence": "Evaluated and rejected for production", "source": "audit"},
    "174": {"evidence": "Superseded by Phase332 board-dynamic", "source": "phase332"},
    "267": {"evidence": "score_v2 gate; quality reject off", "source": "yaml"},
    "314": {"evidence": "Rule reduction: 2-token score only", "source": "phase314"},
    "332": {"evidence": "production_adoption_ok=true", "source": "phase332_board_dynamic_trailing_production_adoption_report.json"},
    "333": {"evidence": "kabutrade0612 canonical summary", "source": "git"},
    "355": {"evidence": "+100,400 yen vs baseline (Phase365); 6/12 D40 28 reject audit", "source": "phase365"},
    "364": {"evidence": "+140,200 yen 6/12 replay delta (Phase363); Phase365 maintain", "source": "phase363/365"},
    "365": {"evidence": "Stack C +483,110 PF 1.2607 vs baseline +160,510", "source": "phase365_production_stack_validation_summary.json"},
    "270": {"evidence": "Mixed leverage bucket", "source": "phase270"},
    "271": {"evidence": "lev1.5 non-robust on 9-day sample", "source": "phase271"},
    "272": {"evidence": "lev1.5 non-robust → lev2.0 fixed recommend", "source": "phase272"},
    "273": {"evidence": "day_count=9 adopt_not_allowed; final_equity +10.0%", "source": "phase273_live_config_shadow_summary.json"},
    "274": {"evidence": "transition_to_2000k=false; final 1,650,270", "source": "phase274_live_config_transition_summary.json"},
    "351": {"evidence": "production rejected", "source": "phase351"},
    "359": {"evidence": "shadow only rejected", "source": "phase359"},
    "368": {"evidence": "do not adopt", "source": "phase368"},
    "370": {"evidence": "evaluated rejected", "source": "phase370"},
    "371": {"evidence": "shadow only rejected", "source": "phase371"},
    "375": {"evidence": "full D40 replace rejected", "source": "phase375"},
    "388": {"evidence": "150万 CAP=2 research candidate; runtime cap3", "source": "phase388_cap1500k_report.md"},
    "389": {"evidence": "150万 candidate; CAP=2 observe; runtime 未反映", "source": "phase389 report"},
}

RUNTIME_DIFF_HISTORY: list[dict[str, str]] = [
    {
        "date": "2026-05-18",
        "name": "Observer v1",
        "universe": "pre top50",
        "entry": "quality + structural",
        "exit": "stop/session/overlap",
        "cap": "3",
        "major_change": "Phase55 small paper observer 開始",
    },
    {
        "date": "2026-05-29",
        "name": "Core10 + Dynamic40 v1",
        "universe": "113/117 top50",
        "entry": "quality≥0.70 + price risk",
        "exit": "structural + fade trials",
        "cap": "3",
        "major_change": "two-layer universe; fade EXIT 試行",
    },
    {
        "date": "2026-06-04",
        "name": "Trailing Shadow",
        "universe": "core10+d40 + AM/PM(148)",
        "entry": "quality + score shadow",
        "exit": "Phase174 fixed 0.8%/50% shadow",
        "cap": "3",
        "major_change": "trailing-MFE shadow policy 導入",
    },
    {
        "date": "2026-06-07",
        "name": "Score v2 Transition",
        "universe": "core10+d40 price-risk",
        "entry": "Phase314 score_v2≥3",
        "exit": "fixed trailing + structural",
        "cap": "3",
        "major_change": "v1 多因子から 2-token score へ",
    },
    {
        "date": "2026-06-09",
        "name": "Pre-332 Runtime",
        "universe": "core10+d40 price-risk",
        "entry": "score_v2 + 153b",
        "exit": "Phase332 replay OK (YAML pending)",
        "cap": "3",
        "major_change": "board-dynamic EXIT 採用判定 OK",
    },
    {
        "date": "2026-06-12",
        "name": "6/12 Incident Runtime",
        "universe": "core10+d40 price-risk",
        "entry": "score_v2 + 153b; 355/364 **off**",
        "exit": "174 legacy or 332 transition",
        "cap": "3",
        "major_change": "**6/12 AM incident** — D40 losses",
    },
    {
        "date": "2026-06-13",
        "name": "Stack C Production",
        "universe": "113/117/269 + refresh",
        "entry": "267/314 + 355 + 364 + freshness",
        "exit": "Phase332 board-dynamic",
        "cap": "3",
        "major_change": "kabutrade0612 — guards + 333/281",
    },
    {
        "date": "2026-06-14",
        "name": "Current Runtime",
        "universe": "Stack C unchanged",
        "entry": "Stack C unchanged",
        "exit": "Stack C unchanged",
        "cap": "3",
        "major_change": "forward shadow 273/274; CAP=2 research only",
    },
]

RUNTIME_CHANGE_LOG: list[dict[str, str]] = [
    {
        "date": "2026-05-06",
        "version": "Genesis",
        "universe": "manual CSV / screening",
        "entry": "prototype gate",
        "exit": "not integrated",
        "cap": "—",
        "reason": "first commit; Discord / screening foundation",
    },
    {
        "date": "2026-05-18",
        "version": "Observer v1",
        "universe": "pre top50",
        "entry": "quality + structural",
        "exit": "stop/session/overlap",
        "cap": "3",
        "reason": "Phase55 small paper observer",
    },
    {
        "date": "2026-05-29",
        "version": "Core10+Dynamic40 v1",
        "universe": "113/117 top50",
        "entry": "quality≥0.70 + price risk",
        "exit": "structural + fade trials",
        "cap": "3",
        "reason": "two-layer universe",
    },
    {
        "date": "2026-06-04",
        "version": "Trailing Shadow",
        "universe": "core10+d40 + AM/PM(148)",
        "entry": "quality + score shadow",
        "exit": "Phase174 fixed 0.8%/50% shadow",
        "cap": "3",
        "reason": "trailing-MFE shadow policy",
    },
    {
        "date": "2026-06-07",
        "version": "ScoreV2 Transition",
        "universe": "core10+d40 price-risk",
        "entry": "Phase314 score_v2≥3",
        "exit": "fixed trailing + structural",
        "cap": "3",
        "reason": "depart v1 multi-factor score",
    },
    {
        "date": "2026-06-09",
        "version": "Pre-332",
        "universe": "core10+d40 price-risk",
        "entry": "score_v2 + 153b",
        "exit": "Phase332 replay OK (YAML pending)",
        "cap": "3",
        "reason": "board-dynamic EXIT adoption OK",
    },
    {
        "date": "2026-06-12",
        "version": "6/12 Incident Runtime",
        "universe": "core10+d40 price-risk",
        "entry": "score_v2 + 153b; guards off",
        "exit": "174 legacy or 332 transition",
        "cap": "3",
        "reason": "6/12 AM Dynamic40 losses",
    },
    {
        "date": "2026-06-13",
        "version": "Stack C",
        "universe": "113/117/269 + refresh",
        "entry": "267/314 + 355 + 364 + freshness",
        "exit": "Phase332 board-dynamic",
        "cap": "3",
        "reason": "kabutrade0612 recovery commit",
    },
    {
        "date": "2026-06-14",
        "version": "Current",
        "universe": "Stack C unchanged",
        "entry": "Stack C unchanged",
        "exit": "Stack C unchanged",
        "cap": "3",
        "reason": "forward shadows; CAP=2 research only",
    },
]

STACK_EVOLUTION: list[dict[str, str]] = [
    {
        "stack": "Stack A",
        "period": "20260518–20260612 (research counterfactual)",
        "universe": "core10+d40 price-risk (observed trades)",
        "entry": "score_v2 + price risk; **no guards**",
        "exit": "trailing-MFE + stop 1.2%",
        "cap": "3",
        "adopt_reason": "Phase365 baseline for guard delta measurement",
        "replace_reason": "superseded by Stack B/C guard analysis",
        "status": "research baseline",
    },
    {
        "stack": "Stack B",
        "period": "20260518–20260612 (research counterfactual)",
        "universe": "same as A",
        "entry": "A + Phase355 pullback D40 guard only",
        "exit": "same as A",
        "cap": "3",
        "adopt_reason": "Phase355 isolated effect (+100k vs A)",
        "replace_reason": "superseded by Stack C (+364 incremental)",
        "status": "research superseded",
    },
    {
        "stack": "Stack C",
        "period": "2026-06-13–present (production)",
        "universe": "113/117/269 + AM/PM refresh",
        "entry": "267/314 + 355 + 364 + freshness + 153b",
        "exit": "Phase332 board-dynamic + structural",
        "cap": "3",
        "adopt_reason": "Phase365 maintain; +483k vs baseline",
        "replace_reason": "— (current production)",
        "status": "**active production**",
    },
]

STACK_C_DEPENDENCIES: dict[str, list[str]] = {
    "Universe": ["113", "117", "148", "269"],
    "Entry": ["153b", "267", "314", "355", "364", "NP-entry-scan"],
    "Exit": ["332", "structural v1"],
    "Position": ["q070_cap3"],
    "Risk": ["YAML daily_loss", "risk_cluster"],
    "Discord": ["281", "333"],
    "Monitoring": ["55", "148", "317", "376", "377", "373"],
    "Shadow": ["255", "256", "262", "266", "273", "274", "387"],
    "Research": ["272", "273", "274", "388", "389"],
}

ADOPTED_THEN_REMOVED: list[dict[str, str]] = [
    {
        "phase": "13",
        "adopted": "2026-05-17",
        "removed": "2026-05-18",
        "replacement": "148",
        "reason": "no_entry_until 09:30 → session window management",
    },
    {
        "phase": "114",
        "adopted": "2026-05-27",
        "removed": "2026-05-29",
        "replacement": "148",
        "reason": "12:25 PM regen → 10:00/14:30 intraday refresh",
    },
    {
        "phase": "174",
        "adopted": "2026-06-04",
        "removed": "2026-06-13",
        "replacement": "332",
        "reason": "fixed 0.8%/50% trailing → board-dynamic trailing",
    },
    {
        "phase": "270",
        "adopted": "2026-06-14",
        "removed": "2026-06-14",
        "replacement": "272",
        "reason": "mixed leverage bucket → lev2.0 fixed",
    },
    {
        "phase": "271",
        "adopted": "2026-06-14",
        "removed": "2026-06-14",
        "replacement": "272",
        "reason": "lev1.5 non-robust on 9-day sample",
    },
    {
        "phase": "273",
        "adopted": "2026-06-04",
        "removed": "2026-06-14",
        "replacement": "274",
        "reason": "static bucket shadow superseded by auto-transition shadow",
    },
]

RUNTIME_DELTA_TIMELINE: list[dict[str, str]] = [
    {
        "date": "2026-05-18",
        "added": "Phase55 observer",
        "removed": "—",
        "replaced": "—",
    },
    {
        "date": "2026-05-29",
        "added": "Phase113/117 top50",
        "removed": "—",
        "replaced": "—",
    },
    {
        "date": "2026-06-04",
        "added": "Phase174 trailing shadow",
        "removed": "—",
        "replaced": "—",
    },
    {
        "date": "2026-06-07",
        "added": "Phase314 score_v2",
        "removed": "quality≥0.70 reject (267 path)",
        "replaced": "—",
    },
    {
        "date": "2026-06-09",
        "added": "Phase332 EXIT (replay OK)",
        "removed": "—",
        "replaced": "—",
    },
    {
        "date": "2026-06-12",
        "added": "—",
        "removed": "—",
        "replaced": "— (incident; guards not yet applied)",
    },
    {
        "date": "2026-06-13",
        "added": "Phase355, Phase364, Phase333, Phase281, NP-scan",
        "removed": "Phase174 production trailing",
        "replaced": "Phase174 → Phase332",
    },
    {
        "date": "2026-06-14",
        "added": "Phase273/274 forward shadow hooks",
        "removed": "—",
        "replaced": "Phase270/271 → Phase272 (research)",
    },
]

CURRENT_RUNTIME_PROVENANCE: list[dict[str, str]] = [
    {"component": "Universe top50", "phase": "113, 117", "date": "2026-05-27", "evidence": "Production runtime Stack C"},
    {"component": "Core10+Dynamic40 price-risk", "phase": "269, 148", "date": "2026-05-29", "evidence": "AM/PM refresh + price-risk filter"},
    {"component": "Entry score v2", "phase": "314, 267", "date": "2026-06-07", "evidence": "Rule reduction 2-token; quality reject off"},
    {"component": "Price risk guard", "phase": "153b", "date": "2026-05-27", "evidence": "YAML entry_price_risk_guard"},
    {"component": "Pullback guard", "phase": "355", "date": "2026-06-13", "evidence": "+100,400 yen vs baseline (Phase365)"},
    {"component": "Near day-high guard", "phase": "364", "date": "2026-06-13", "evidence": "+140,200 yen 6/12 replay (Phase363)"},
    {"component": "Board dynamic exit", "phase": "332", "date": "2026-06-13", "evidence": "production_adoption_ok=true"},
    {"component": "CAP3", "phase": "q070_cap3", "date": "2026-05-18", "evidence": "runtime max_concurrent_positions=3"},
    {"component": "CAP2", "phase": "388, 389, 387", "date": "2026-06-14", "evidence": "**Research only** — runtime cap3 maintained"},
    {"component": "Canonical summary", "phase": "333", "date": "2026-06-13", "evidence": "kabutrade0612 canonical 100-share yen"},
    {"component": "Cap-blocked Discord", "phase": "281", "date": "2026-06-13", "evidence": "Discord channel split"},
]

SINCE_612_DELTA: list[dict[str, str]] = [
    {"layer": "Entry", "before": "355 off", "after": "355 on (D40 pullback)", "phase": "355"},
    {"layer": "Entry", "before": "364 off", "after": "364 on (D40 near-high)", "phase": "364"},
    {"layer": "Entry", "before": "freshness scan partial", "after": "entry_scan freshness guard", "phase": "NP-scan"},
    {"layer": "Exit", "before": "174 legacy / transition", "after": "332 board-dynamic", "phase": "332"},
    {"layer": "Summary", "before": "legacy mixed PnL defs", "after": "333 canonical 100-share yen", "phase": "333"},
    {"layer": "Discord", "before": "single channel noise", "after": "281 cap-blocked split", "phase": "281"},
    {"layer": "Monitoring", "before": "ad-hoc review", "after": "376/377/373 production monitor", "phase": "376"},
    {"layer": "Universe", "before": "269 partial", "after": "269 price-risk AM/PM stable", "phase": "269"},
    {"layer": "CAP", "before": "cap3", "after": "cap3 (CAP=2 research observe)", "phase": "387/388"},
]

TOP_OPEN_RISKS: list[dict[str, str]] = [
    {
        "priority": "1",
        "id": "Risk S1",
        "title": "PUSH replay fidelity",
        "description": "Board/PUSH dependent EXIT/ENTRY not fully reproducible offline",
        "mitigation": "Forward shadow; Risk S1 documented (Phase381)",
        "owner": "381",
    },
    {
        "priority": "2",
        "id": "Risk S2",
        "title": "Period A recurrence",
        "description": "Stack C Period A still -772k; guards Period B oriented",
        "mitigation": "377/389 regime monitoring",
        "owner": "377, 389",
    },
    {
        "priority": "3",
        "id": "Risk A1",
        "title": "low-MFE stop_hit",
        "description": "stop_hit after minimal MFE persists post-guards",
        "mitigation": "379 research; no production fix",
        "owner": "379",
    },
    {
        "priority": "4",
        "id": "Risk A2",
        "title": "CAP=2 runtime未検証",
        "description": "388/389 research positive; runtime cap3 maintained",
        "mitigation": "387 shadow + live session confirm",
        "owner": "387, 388",
    },
    {
        "priority": "5",
        "id": "Risk A3",
        "title": "Forward shadow <10日",
        "description": "273/274/266 day_count=9 adopt_not_allowed",
        "mitigation": "Continue forward accumulation",
        "owner": "273, 274",
    },
]

ADOPTION_MATRIX_CATEGORIES = [
    "Universe",
    "Entry",
    "Exit",
    "Position",
    "Risk",
    "Sizing",
    "Capital",
    "Discord",
    "Monitoring",
    "Data",
    "Replay",
    "Documentation",
]

GENESIS_ROWS: list[dict[str, str]] = [
    {
        "phase": "001",
        "date": "2026-05-06",
        "category": "Monitoring",
        "title": "Genesis — screening / Discord / entry prototype",
        "purpose": "first commit foundation",
        "adoption_status": "adopted",
        "current_status": "active",
        "removed_or_disabled": "false",
        "verdict": "Foundation for all subsequent phases",
    },
]

PRODUCTION_TRUTH: list[tuple[str, str, str]] = [
    ("Universe", "core10-dynamic40-price-risk-filter-shadow + vol-liq top50", "113, 117, 269, 148"),
    ("Entry", "entry_score_v2_min=3 (Momentum:low + Board:mid); quality reject off", "314, 267"),
    ("Entry", "entry_price_risk_guard", "153b"),
    ("Entry", "pullback_misread_dynamic40_guard (Dynamic40 only)", "355"),
    ("Entry", "near_day_high_low_momentum_dynamic40_guard (Dynamic40 only)", "364"),
    ("Entry", "entry_scan freshness / batch guard", "NP-entry-scan"),
    ("Exit", "board-dynamic trailing-MFE (high 1.0%/60%, low 0.6%/40%)", "332"),
    ("Exit", "hard_stop 1.2%, overlap_replaced, session_close", "structural v1"),
    ("Position", "max_concurrent_positions=3 (100-share observer)", "q070_cap3"),
    ("CAP", "runtime cap=3; CAP=2 research only", "388, 389, 387"),
    ("Risk", "daily_loss -2.5%, risk_cluster block, maintenance ratio sim", "YAML"),
    ("Discord", "canonical 100-share yen summary + cap-blocked webhook", "333, 281"),
    ("Monitoring", "AM/PM daily runner, preflight 317, post-close 376/377/373", "148, 317, 376"),
    (
        "Shadow",
        "forward post-session: 255/256, 262, 266, 273, 274",
        "255, 262, 266, 273, 274",
    ),
    ("Research", "capital path 267–274 forward; scaling 374–389 (runtime 未反映)", "272, 273, 388"),
    (
        "Live Candidate",
        "eq1500k_lev2p0_cap3_fixed_stop_1p2 → 2M+ CAP5 dynamic (research)",
        "272, 274",
    ),
]

RUNTIME_SNAPSHOTS: list[dict[str, str]] = [
    {
        "date": "2026-05-06",
        "name": "Genesis",
        "universe": "manual CSV / screening",
        "entry": "prototype gate",
        "exit": "not integrated",
        "cap": "—",
        "notes": "first commit; Discord notice only",
    },
    {
        "date": "2026-05-18",
        "name": "Observer v1",
        "universe": "pre top50 exploration",
        "entry": "quality + structural gates",
        "exit": "stop / session / overlap",
        "cap": "3",
        "notes": "Phase55 small paper observer",
    },
    {
        "date": "2026-05-29",
        "name": "Core10 + Dynamic40 v1",
        "universe": "113/117 vol-liq top50",
        "entry": "quality≥0.70 + price risk",
        "exit": "structural + fade trials",
        "cap": "3",
        "notes": "two-layer universe; fade exit trials",
    },
    {
        "date": "2026-06-04",
        "name": "Trailing Shadow",
        "universe": "core10+d40 + AM/PM refresh (148)",
        "entry": "quality + score shadow",
        "exit": "Phase174 fixed 0.8%/50% shadow",
        "cap": "3",
        "notes": "trailing-MFE shadow policy name",
    },
    {
        "date": "2026-06-07",
        "name": "Score v2 Transition",
        "universe": "core10+d40 price-risk",
        "entry": "Phase314 score_v2≥3 migration",
        "exit": "fixed trailing + structural",
        "cap": "3",
        "notes": "departing from v1 multi-factor score",
    },
    {
        "date": "2026-06-09",
        "name": "Pre-332 Runtime",
        "universe": "core10+d40 price-risk",
        "entry": "score_v2≥3 + 153b price risk",
        "exit": "Phase332 board-dynamic (replay OK, YAML pending)",
        "cap": "3",
        "notes": "production_adoption_ok; YAML sync on 6/13",
    },
    {
        "date": "2026-06-12",
        "name": "6/12 Incident Runtime",
        "universe": "core10+d40 price-risk",
        "entry": "score_v2≥3 + 153b; **355/364 off**",
        "exit": "trailing-MFE (332 or 174 transition)",
        "cap": "3",
        "notes": "**6/12 AM incident** — Dynamic40 losses; guards not applied",
    },
    {
        "date": "2026-06-13",
        "name": "Stack C Production",
        "universe": "113/117/269 + AM/PM refresh",
        "entry": "267/314 + 355 + 364 + freshness",
        "exit": "Phase332 board-dynamic",
        "cap": "3",
        "notes": "kabutrade0612; 333/281 ops",
    },
    {
        "date": "2026-06-14",
        "name": "Current Runtime",
        "universe": "same as Stack C",
        "entry": "same as Stack C",
        "exit": "same as Stack C",
        "cap": "3",
        "notes": "forward shadows 273/274; CAP=2 research only",
    },
]

SHADOW_EVOLUTION: list[dict[str, str]] = [
    {
        "start": "2026-06-04",
        "shadow": "Phase255/256 SectorHeat",
        "purpose": "sector heat negative filter forward accumulation",
        "status": "active",
        "verdict": "observe",
    },
    {
        "start": "2026-06-14",
        "shadow": "Phase262 RiskSizing",
        "purpose": "risk-aware sizing forward vs fixed_100",
        "status": "active",
        "verdict": "observe",
    },
    {
        "start": "2026-06-03",
        "shadow": "Phase266 EquityDynamicStop",
        "purpose": "equity dynamic stop vs fixed 1.2%",
        "status": "active",
        "verdict": "observe",
    },
    {
        "start": "2026-06-04",
        "shadow": "Phase273 LiveConfig",
        "purpose": "Phase272 live config bucket forward equity curves",
        "status": "active",
        "verdict": "observe",
    },
    {
        "start": "2026-06-14",
        "shadow": "Phase274 AutoTransition",
        "purpose": "1.5M start → equity≥2M auto CAP5/dynamic",
        "status": "active",
        "verdict": "observe",
    },
    {
        "start": "2026-06-14",
        "shadow": "Phase387 CAP2 Shadow",
        "purpose": "CAP=2 vs CAP=3 production monitoring",
        "status": "active",
        "verdict": "observe",
    },
    {
        "start": "2026-06-09",
        "shadow": "Phase332 legacy trailing",
        "purpose": "fixed 0.8%/50% counterfactual vs board-dynamic",
        "status": "active",
        "verdict": "superseded",
    },
    {
        "start": "2026-06-13",
        "shadow": "Phase355 pullback shadow",
        "purpose": "pullback guard reject logging",
        "status": "active",
        "verdict": "adopted",
    },
    {
        "start": "2026-06-12",
        "shadow": "Phase351 limit-up",
        "purpose": "limit-up proximity guard trial",
        "status": "active",
        "verdict": "rejected",
    },
    {
        "start": "2026-06-04",
        "shadow": "Phase335/214/186/230 inline",
        "purpose": "board exit / imbalance / vwap / score shadow",
        "status": "active",
        "verdict": "observe",
    },
]

FAILURE_ARCHIVE: list[dict[str, str]] = [
    {
        "date": "20260518–27",
        "failure": "Period A Loss",
        "root": "regime + universe + overlap + exit composite",
        "resolution": "Period A/B split monitoring (377); guards target Period B",
        "state": "active risk",
    },
    {
        "date": "20260612",
        "failure": "6/12 Incident",
        "root": "Dynamic40 pullback misread + near-high low-mom entries",
        "resolution": "Phase355 + Phase364 adopted (Stack C)",
        "state": "partially mitigated",
    },
    {
        "date": "2026-06-04",
        "failure": "Phase174 Fixed Trailing",
        "root": "board regime blindness (0.8%/50% universal)",
        "resolution": "Phase332 board-dynamic trailing",
        "state": "superseded",
    },
    {
        "date": "2026-06-06",
        "failure": "Research PF Misinterpretation",
        "root": "unconstrained PF positive but capital path negative",
        "resolution": "Phase268 dual-layer; final_equity primary",
        "state": "active policy",
    },
    {
        "date": "2026-06-13",
        "failure": "Phase362-B (C03 all symbols)",
        "root": "PF max but Core10 side-effect + single-day dependence",
        "resolution": "Phase365 → Dynamic40-only Phase364",
        "state": "superseded",
    },
    {
        "date": "2026-06-14",
        "failure": "Leverage 1.5 Hypothesis",
        "root": "9-day sample non-robust (271)",
        "resolution": "Phase272 lev2.0 fixed",
        "state": "superseded",
    },
    {
        "date": "2026-06-14",
        "failure": "CAP2 Misunderstanding",
        "root": "research positive at 1.5M ≠ runtime adopt",
        "resolution": "cap3 maintained; 387/388 observe",
        "state": "runtime 未採用",
    },
    {
        "date": "2026-05-27",
        "failure": "Fade / Momentum Exit Path",
        "root": "PF unstable; board-dependent",
        "resolution": "removed from production EXIT",
        "state": "removed",
    },
    {
        "date": "2026-06-08",
        "failure": "Yahoo Replay Fidelity Gap",
        "root": "board/PUSH dependent logic not reproducible offline",
        "resolution": "Risk S1 documented; forward shadow required",
        "state": "active risk",
    },
    {
        "date": "2026-06-14",
        "failure": "Low-MFE stop_hit Residual",
        "root": "stop_hit after minimal MFE post-guards",
        "resolution": "379 research ongoing; no production fix",
        "state": "unresolved",
    },
]

MISCONCEPTIONS: list[dict[str, str]] = [
    {
        "date": "2026-05-27",
        "misconception": "momentum fade EXIT improves PF",
        "believed": "fade/hybrid exit trials",
        "invalidated": "Phase71",
        "verdict": "production rejected",
        "status": "removed",
    },
    {
        "date": "2026-06-04",
        "misconception": "fixed trailing 0.8%/50% optimal for all symbols",
        "believed": "Phase174 shadow gain",
        "invalidated": "Phase332",
        "verdict": "board-dynamic replace",
        "status": "superseded",
    },
    {
        "date": "2026-06-06",
        "misconception": "PF > 1 implies runtime adoption",
        "believed": "unconstrained replay",
        "invalidated": "Phase267/268",
        "verdict": "final_equity primary",
        "status": "active",
    },
    {
        "date": "2026-06-06",
        "misconception": "CAP=2 optimal at 1.5M",
        "believed": "slot efficiency",
        "invalidated": "Phase267",
        "verdict": "CAP=3 research recommend",
        "status": "observe",
    },
    {
        "date": "2026-06-06",
        "misconception": "rejected trades can be ignored in PF",
        "believed": "simplified PF",
        "invalidated": "Phase268",
        "verdict": "dual-layer required",
        "status": "active",
    },
    {
        "date": "2026-06-07",
        "misconception": "entry score v1 multi-factor explains edge",
        "believed": "Phase230–245",
        "invalidated": "Phase314/266",
        "verdict": "v2 two-token only",
        "status": "superseded",
    },
    {
        "date": "2026-06-07",
        "misconception": "quality≥0.70 is core ENTRY gate",
        "believed": "YAML default",
        "invalidated": "Phase267",
        "verdict": "score_v2≥3",
        "status": "superseded",
    },
    {
        "date": "2026-06-07",
        "misconception": "adding rules always improves system",
        "believed": "phase count = progress",
        "invalidated": "Phase314",
        "verdict": "rule reduction",
        "status": "active",
    },
    {
        "date": "2026-06-08",
        "misconception": "offline EXIT replay equals live",
        "believed": "structural replay",
        "invalidated": "Phase381",
        "verdict": "PUSH/board gap",
        "status": "active",
    },
    {
        "date": "2026-06-12",
        "misconception": "limit-up guard fixes 6/12",
        "believed": "Phase351",
        "invalidated": "Phase351 rollout",
        "verdict": "rejected",
        "status": "rejected",
    },
    {
        "date": "2026-06-12",
        "misconception": "gap-up fade guard fixes AM loss",
        "believed": "Phase359",
        "invalidated": "Phase359 review",
        "verdict": "rejected",
        "status": "rejected",
    },
    {
        "date": "2026-06-12",
        "misconception": "C03 all-symbol guard is production best",
        "believed": "Phase362-B",
        "invalidated": "Phase365",
        "verdict": "D40-only 364",
        "status": "superseded",
    },
    {
        "date": "2026-06-13",
        "misconception": "Stack C profit is ENTRY-guard-only",
        "believed": "post-adoption narrative",
        "invalidated": "Phase381",
        "verdict": "EXIT also contributes",
        "status": "active",
    },
    {
        "date": "2026-06-13",
        "misconception": "355+364 fixes Period A",
        "believed": "guard universal efficacy",
        "invalidated": "Phase377",
        "verdict": "Period B oriented",
        "status": "active",
    },
    {
        "date": "2026-06-13",
        "misconception": "12:25 PM regen sufficient",
        "believed": "Phase114",
        "invalidated": "Phase148",
        "verdict": "10:00/14:30 refresh",
        "status": "superseded",
    },
    {
        "date": "2026-06-13",
        "misconception": "TAKE event equals EXIT",
        "believed": "Phase54",
        "invalidated": "Phase54 review",
        "verdict": "TAKE≠EXIT",
        "status": "removed",
    },
    {
        "date": "2026-06-14",
        "misconception": "lev1.5 bucket is live optimal",
        "believed": "Phase270",
        "invalidated": "Phase271/272",
        "verdict": "lev2.0 fixed",
        "status": "superseded",
    },
    {
        "date": "2026-06-14",
        "misconception": "9-day forward shadow sufficient for adopt",
        "believed": "Phase273 early results",
        "invalidated": "adopt_not_allowed",
        "verdict": "≥10-day gate",
        "status": "observe",
    },
    {
        "date": "2026-06-14",
        "misconception": "equity≥2M transition fires immediately",
        "believed": "Phase274 design",
        "invalidated": "Phase274 9-day run",
        "verdict": "transition false",
        "status": "observe",
    },
    {
        "date": "2026-06-14",
        "misconception": "sector heat filter ready for universe",
        "believed": "Phase254/255 delta",
        "invalidated": "Phase255 sample",
        "verdict": "insufficient_sample",
        "status": "observe",
    },
    {
        "date": "2026-06-14",
        "misconception": "risk_2pct sizing beats fixed_100",
        "believed": "Phase262",
        "invalidated": "low_price_overexpansion",
        "verdict": "observe",
        "status": "observe",
    },
    {
        "date": "2026-06-14",
        "misconception": "CAP=2 research → immediate runtime",
        "believed": "Phase388/389",
        "invalidated": "live confirm pending",
        "verdict": "cap3 maintained",
        "status": "observe",
    },
    {
        "date": "2026-06-14",
        "misconception": "rank_21_40 full replace improves alpha",
        "believed": "Phase375",
        "invalidated": "Phase375 review",
        "verdict": "rejected",
        "status": "rejected",
    },
    {
        "date": "2026-06-14",
        "misconception": "price cap constraint is profit source",
        "believed": "unconstrained vs cap confusion",
        "invalidated": "Phase268/267",
        "verdict": "capital path matters",
        "status": "active",
    },
    {
        "date": "2026-06-04",
        "misconception": "fade exit effective in production",
        "believed": "Phase71/166",
        "invalidated": "Phase166 rejected",
        "verdict": "shadow only",
        "status": "removed",
    },
]

LESSONS: list[tuple[str, str, str]] = [
    ("Rule addition ≠ improvement", "314, 377", "Simplify runtime rules; measure by regime"),
    ("Research PF ≠ Final Equity", "267, 268", "Capital path + reject decomposition mandatory"),
    ("Dynamic40 and Core10 are separate problems", "355, 364, 365", "Guards target D40 only"),
    ("EXIT without board data is dangerous", "332, 381", "Board-linked trailing; replay gap Risk S1"),
    ("Do not push research directly to runtime", "273, 274, 388", "Forward shadow + ≥10-day gate"),
    ("Period regime must be read separately", "377, 389", "Period A losses persist under Stack C"),
    ("Single-day replay delta is insufficient", "363, 365", "Maintain with single-day share monitoring"),
    ("EXIT contributes beyond ENTRY guards", "381", "trailing-MFE is part of Stack C edge"),
    ("CAP change is capital-path not trade-PF", "267, 385–389", "CAP=2 research ≠ runtime cap3"),
    ("Leverage buckets need robustness check", "271, 272", "Fixed lev2.0 after short-sample failure"),
    ("Observer-only must stay observer-only", "all runtime", "order_enabled=false maintained"),
    ("Discord PnL needs one canonical definition", "333", "100-share yen from observer_exit"),
    ("AM session concentrates losses", "355 audit, 377", "AM/PM decomposition in monitoring"),
    ("low-MFE stop_hit survives guards", "379", "Unresolved; research continues"),
    ("Documentation follows adoption events", "390", "Update this SoT on adopt/reject/supersede"),
]


def _phase_sort_key(phase: str) -> tuple[int, str]:
    m = re.match(r"(\d+)([a-z]?|343p)?", phase, re.I)
    if not m:
        return (99999, phase)
    return (int(m.group(1)), m.group(2) or "")


def _esc_cell(text: str, max_len: int = 120) -> str:
    t = (text or "").replace("|", "\\|").replace("\n", " ").strip()
    if len(t) > max_len:
        return t[: max_len - 3] + "..."
    return t


def _load_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise FileNotFoundError(f"audit csv not found: {path}")
    return list(csv.DictReader(path.open(encoding="utf-8")))


def _matrix_category(row: dict[str, str]) -> str:
    cat = row.get("category") or "Monitoring"
    if cat == "Config":
        return "Documentation"
    if cat != "Sizing":
        return cat
    blob = f"{row.get('title', '')} {row.get('purpose', '')}".lower()
    if any(k in blob for k in ("capital", "equity curve", "live config", "leverage", "scaling")):
        return "Capital"
    return "Sizing"


def _appendix_status(row: dict[str, str]) -> str:
    ad = (row.get("adoption_status") or "").lower()
    cs = (row.get("current_status") or "").lower()
    if (row.get("removed_or_disabled") or "").lower() == "true" or cs == "removed":
        return "removed"
    if ad == "rejected":
        return "rejected"
    if ad == "superseded":
        return "superseded"
    if ad == "adopted" and cs == "active":
        return "active"
    return "observe"


def _verdict_label(row: dict[str, str]) -> str:
    ad = row.get("adoption_status") or ""
    if ad:
        return ad
    return _esc_cell(row.get("verdict") or "observe", 40)


def _extract_narrative_parts(existing: Path) -> tuple[str, str]:
    if not existing.is_file():
        return "", ""
    text = existing.read_text(encoding="utf-8")
    start = text.find(NARRATIVE_START)
    cs = text.find(NARRATIVE_CURRENT_STATE)
    hist = text.find(NARRATIVE_END)
    if start == -1:
        return "", ""
    body_end = cs if cs != -1 and cs > start else hist
    if body_end == -1:
        return text[start:].rstrip(), ""
    body = text[start:body_end].rstrip()
    tail = ""
    if cs != -1 and hist != -1 and hist > cs:
        tail = text[cs:hist].rstrip()
    return body, tail


def _row_by_phase(rows: Sequence[dict[str, str]], phase: str) -> dict[str, str] | None:
    for row in rows:
        if row.get("phase") == phase:
            return row
    return None


def _build_production_stack_definition() -> str:
    sections: dict[str, list[str]] = defaultdict(list)
    layer_map = {
        "Universe": "Universe",
        "Entry": "Entry",
        "Exit": "Exit",
        "Position": "Position",
        "CAP": "Position",
        "Risk": "Risk",
        "Discord": "Discord",
        "Monitoring": "Monitoring",
        "Shadow": "Shadow",
        "Research": "Research",
        "Live Candidate": "Live Candidate",
    }
    for layer, truth, phase in PRODUCTION_TRUTH:
        key = layer_map.get(layer, layer)
        sections[key].append(f"- {truth} *(Phase {phase})*")
    lines = ["# Production Stack Definition", "", "## Runtime (Current)", ""]
    order = [
        "Universe",
        "Entry",
        "Exit",
        "Position",
        "Risk",
        "Discord",
        "Monitoring",
        "Shadow",
        "Research",
        "Live Candidate",
    ]
    for name in order:
        items = sections.get(name) or []
        if not items:
            continue
        lines.append(f"### {name}")
        lines.append("")
        lines.extend(items)
        lines.append("")
    lines.append(
        "**Config:** `small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml`  "
    )
    lines.append("**Mode:** paper_only · shadow_only · order_enabled=false")
    return "\n".join(lines)


def _build_key_milestones(rows: Sequence[dict[str, str]]) -> str:
    body: list[list[str]] = []
    for phase in KEY_MILESTONE_PHASES:
        row = _row_by_phase(rows, phase)
        date = MILESTONE_DATES.get(phase) or (row or {}).get("date") or "—"
        title = (row or {}).get("title") or MILESTONE_REASONS.get(phase, f"Phase {phase}")
        reason = MILESTONE_REASONS.get(phase, (row or {}).get("verdict") or "—")
        if phase in MILESTONE_STATUS_OVERRIDES:
            status = MILESTONE_STATUS_OVERRIDES[phase]
        elif row:
            status = _appendix_status(row)
        else:
            status = "observe"
        body.append([f"Phase{phase}", date, _esc_cell(title, 70), _esc_cell(reason, 90), status])
    return _table(["Phase", "Date", "Title", "Reason", "Current Status"], body)


def _build_runtime_change_log() -> str:
    return _table(
        ["Date", "Runtime Version", "Universe", "Entry", "Exit", "CAP", "Reason"],
        [
            [
                r["date"],
                r["version"],
                r["universe"],
                r["entry"],
                r["exit"],
                r["cap"],
                r["reason"],
            ]
            for r in RUNTIME_CHANGE_LOG
        ],
    )


def _build_stack_evolution() -> str:
    return _table(
        [
            "Stack",
            "Period",
            "Universe",
            "Entry",
            "Exit",
            "CAP",
            "Adopt Reason",
            "Replace/Retire Reason",
            "Current Status",
        ],
        [
            [
                s["stack"],
                s["period"],
                s["universe"],
                s["entry"],
                s["exit"],
                s["cap"],
                s["adopt_reason"],
                s["replace_reason"],
                s["status"],
            ]
            for s in STACK_EVOLUTION
        ],
    )


def _dep_label(ph: str) -> str:
    if re.fullmatch(r"\d+[a-z]?", ph):
        return f"Phase{ph}"
    return ph


def _build_dependency_graph_ascii() -> str:
    lines = ["Stack C (production)", "├── Universe"]
    for i, ph in enumerate(STACK_C_DEPENDENCIES["Universe"]):
        prefix = "│   ├──" if i < len(STACK_C_DEPENDENCIES["Universe"]) - 1 else "│   └──"
        lines.append(f"{prefix} {_dep_label(ph)}")
    layer_names = [k for k in STACK_C_DEPENDENCIES if k != "Universe"]
    for li, layer in enumerate(layer_names):
        is_last_layer = li == len(layer_names) - 1
        branch = "└──" if is_last_layer else "├──"
        lines.append(f"{branch} {layer}")
        phases = STACK_C_DEPENDENCIES[layer]
        for pi, ph in enumerate(phases):
            sub_prefix = "    " if is_last_layer else "│   "
            leaf = "└──" if pi == len(phases) - 1 else "├──"
            lines.append(f"{sub_prefix}{leaf} {_dep_label(ph)}")
    lines.append("")
    lines.append("CAP=2: Phase387/388/389 → Research branch (not production runtime)")
    return "\n".join(lines)


def _funnel_bucket(row: dict[str, str]) -> str:
    ad = row.get("adoption_status") or ""
    if (row.get("removed_or_disabled") or "").lower() == "true":
        return "Removed"
    if ad == "rejected":
        return "Rejected"
    if ad == "superseded":
        return "Superseded"
    if ad == "adopted":
        return "Adopted"
    if (row.get("shadow_status") or "").lower() == "active":
        return "Shadow"
    if (row.get("research_status") or "").lower() == "active":
        return "Research"
    return "Observe"


def _build_adoption_funnel(rows: Sequence[dict[str, str]]) -> tuple[str, str]:
    all_rows = list(GENESIS_ROWS) + list(rows)
    status_counts: dict[str, int] = defaultdict(int)
    cat_counts: dict[str, dict[str, int]] = defaultdict(
        lambda: defaultdict(int)
    )
    for row in all_rows:
        bucket = _funnel_bucket(row)
        status_counts[bucket] += 1
        cat = _matrix_category(row)
        cat_counts[cat]["Total"] += 1
        cat_counts[cat][bucket] += 1

    status_body = [[k, str(status_counts[k])] for k in sorted(status_counts.keys())]
    status_body.append(["**Total**", str(len(all_rows))])
    status_table = _table(["Status", "Count"], status_body)

    buckets = ["Adopted", "Rejected", "Shadow", "Research", "Superseded", "Removed", "Observe"]
    cat_body: list[list[str]] = []
    cat_totals: dict[str, int] = defaultdict(int)
    for cat in ADOPTION_MATRIX_CATEGORIES:
        g = cat_counts[cat]
        row_vals = [cat, str(g["Total"])]
        for b in buckets:
            row_vals.append(str(g.get(b, 0)))
            cat_totals[b] += g.get(b, 0)
        cat_body.append(row_vals)
    cat_body.append(
        ["**合計**", str(len(all_rows))] + [str(cat_totals[b]) for b in buckets]
    )
    cat_table = _table(
        ["Category", "Total", "Adopted", "Rejected", "Shadow", "Research", "Superseded", "Removed", "Observe"],
        cat_body,
    )
    return status_table, cat_table


def _build_adopted_then_removed(rows: Sequence[dict[str, str]]) -> str:
    body: list[list[str]] = []
    seen: set[str] = set()
    for item in ADOPTED_THEN_REMOVED:
        phase = item["phase"]
        seen.add(phase)
        row = _row_by_phase(rows, phase)
        adopted = item.get("adopted") or (row or {}).get("date") or "—"
        body.append(
            [
                f"Phase{phase}",
                adopted,
                item.get("removed") or "—",
                f"Phase{item.get('replacement') or '—'}",
                _esc_cell(item.get("reason") or "", 90),
            ]
        )
    for row in rows:
        if row.get("adoption_status") != "superseded":
            continue
        phase = row.get("phase") or ""
        if phase in seen:
            continue
        related = row.get("related_phases") or "—"
        body.append(
            [
                f"Phase{phase}",
                row.get("date") or "—",
                row.get("date") or "—",
                related,
                _esc_cell(row.get("verdict") or "", 90),
            ]
        )
    body.sort(key=lambda r: r[0])
    return _table(
        ["Phase", "Adopted Date", "Removed Date", "Replacement", "Reason"],
        body,
    )


def _build_runtime_delta_timeline() -> str:
    return _table(
        ["Date", "Added", "Removed", "Replaced"],
        [
            [e["date"], e["added"], e["removed"], e["replaced"]]
            for e in RUNTIME_DELTA_TIMELINE
        ],
    )


def _build_runtime_provenance() -> str:
    return _table(
        ["Component", "Phase", "Adoption Date", "Evidence"],
        [
            [p["component"], p["phase"], p["date"], p["evidence"]]
            for p in CURRENT_RUNTIME_PROVENANCE
        ],
    )


def _build_phase391_sections(rows: list[dict[str, str]]) -> str:
    status_table, cat_table = _build_adoption_funnel(rows)
    gen = len(RUNTIME_CHANGE_LOG)
    return "\n".join(
        [
            "# Runtime Evolution Audit (Phase391)",
            "",
            f"**Current runtime generation:** {gen} ({RUNTIME_CHANGE_LOG[-1]['version']})",
            f"**Production stack:** Stack C | **CAP=2:** Research only (runtime cap3)",
            "",
            "---",
            "",
            "# Runtime Change Log",
            "",
            _build_runtime_change_log(),
            "",
            "---",
            "",
            "# Stack Evolution",
            "",
            _build_stack_evolution(),
            "",
            "---",
            "",
            "# Runtime Dependency Graph",
            "",
            "```",
            _build_dependency_graph_ascii(),
            "```",
            "",
            "---",
            "",
            "# Adoption Funnel",
            "",
            "## Overall Status",
            "",
            status_table,
            "",
            "## By Category",
            "",
            cat_table,
            "",
            "---",
            "",
            "# Adopted Then Removed",
            "",
            _build_adopted_then_removed(rows),
            "",
            "---",
            "",
            "# Runtime Delta Timeline",
            "",
            _build_runtime_delta_timeline(),
            "",
            "---",
            "",
            "# Current Runtime Provenance",
            "",
            _build_runtime_provenance(),
            "",
        ]
    )


def _write_satellite_docs(now: str, rows: list[dict[str, str]]) -> None:
    status_table, cat_table = _build_adoption_funnel(rows)
    OUT_RUNTIME_LOG.parent.mkdir(parents=True, exist_ok=True)

    log_doc = "\n".join(
        [
            "# Runtime Change Log",
            "",
            f"Generated: {now} | Source: `{CANONICAL_CSV}`",
            f"Current generation: **{len(RUNTIME_CHANGE_LOG)}** ({RUNTIME_CHANGE_LOG[-1]['version']})",
            "",
            _build_runtime_change_log(),
            "",
            "## Runtime Delta Timeline",
            "",
            _build_runtime_delta_timeline(),
            "",
        ]
    )
    OUT_RUNTIME_LOG.write_text(log_doc, encoding="utf-8")

    graph_doc = "\n".join(
        [
            "# Runtime Dependency Graph",
            "",
            f"Generated: {now} | Production: **Stack C**",
            "",
            "```",
            _build_dependency_graph_ascii(),
            "```",
            "",
            "## Current Runtime Provenance",
            "",
            _build_runtime_provenance(),
            "",
        ]
    )
    OUT_DEPENDENCY_GRAPH.write_text(graph_doc, encoding="utf-8")

    funnel_doc = "\n".join(
        [
            "# Runtime Adoption Funnel",
            "",
            f"Generated: {now} | Phases: **{len(rows) + len(GENESIS_ROWS)}**",
            "",
            "## Overall Status",
            "",
            status_table,
            "",
            "## By Category",
            "",
            cat_table,
            "",
            "## Adopted Then Removed",
            "",
            _build_adopted_then_removed(rows),
            "",
            "**CAP=2 verdict:** Research (Phase387/388/389) — **not** production runtime.",
            "",
        ]
    )
    OUT_ADOPTION_FUNNEL.write_text(funnel_doc, encoding="utf-8")


def _build_runtime_diff_history() -> str:
    return _table(
        ["Date", "Runtime Name", "Universe", "Entry", "Exit", "CAP", "Major Change"],
        [
            [
                r["date"],
                r["name"],
                r["universe"],
                r["entry"],
                r["exit"],
                r["cap"],
                r["major_change"],
            ]
            for r in RUNTIME_DIFF_HISTORY
        ],
    )


def _evidence_for_phase(phase: str, row: dict[str, str]) -> str:
    meta = EVIDENCE_BY_PHASE.get(phase)
    if meta:
        return _esc_cell(meta["evidence"], 100)
    return _esc_cell(row.get("summary") or row.get("verdict") or "—", 100)


def _build_major_decisions(rows: Sequence[dict[str, str]]) -> str:
    seen: set[str] = set()
    picked: list[dict[str, str]] = []
    for row in list(rows) + SUPPLEMENTAL_DECISION_ROWS:
        phase = row.get("phase") or ""
        if phase in seen:
            continue
        ad = row.get("adoption_status") or ""
        if ad in ("adopted", "rejected", "superseded"):
            seen.add(phase)
            picked.append(row)
    picked.sort(key=lambda r: (_phase_sort_key(r["phase"]), r["phase"]))
    body: list[list[str]] = []
    for row in picked:
        phase = row["phase"]
        decision = _esc_cell(row.get("title") or row.get("purpose") or "", 80)
        why = _esc_cell(row.get("verdict") or row.get("summary") or "", 90)
        alt = _esc_cell(
            f"related: {row.get('related_phases') or '—'}; status: {row.get('adoption_status')}",
            90,
        )
        evidence = _evidence_for_phase(phase, row)
        status = _appendix_status(row)
        body.append([f"Phase{phase}", decision, why, alt, evidence, status])
    return _table(
        [
            "Phase",
            "Decision",
            "Why Adopted",
            "Why Alternatives Rejected",
            "Evidence",
            "Current Status",
        ],
        body,
    )


def _build_since_612() -> str:
    return _table(
        ["Layer", "6/12 Runtime", "Current Runtime", "Phase"],
        [[d["layer"], d["before"], d["after"], d["phase"]] for d in SINCE_612_DELTA],
    )


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_esc_cell(str(c)) for c in row) + " |")
    return "\n".join(lines)


def _build_adoption_matrix(rows: Sequence[dict[str, str]]) -> str:
    groups: dict[str, dict[str, int]] = defaultdict(
        lambda: {"Total": 0, "Adopted": 0, "Rejected": 0, "Superseded": 0, "Active": 0}
    )
    for row in rows:
        cat = _matrix_category(row)
        groups[cat]["Total"] += 1
        ad = row.get("adoption_status") or ""
        if ad == "adopted":
            groups[cat]["Adopted"] += 1
        elif ad == "rejected":
            groups[cat]["Rejected"] += 1
        elif ad == "superseded":
            groups[cat]["Superseded"] += 1
        if row.get("current_status") == "active":
            groups[cat]["Active"] += 1
    body: list[list[str]] = []
    totals = {"Total": 0, "Adopted": 0, "Rejected": 0, "Superseded": 0, "Active": 0}
    for cat in ADOPTION_MATRIX_CATEGORIES:
        g = groups[cat]
        body.append(
            [
                cat,
                str(g["Total"]),
                str(g["Adopted"]),
                str(g["Rejected"]),
                str(g["Superseded"]),
                str(g["Active"]),
            ]
        )
        for k in totals:
            totals[k] += g[k]
    body.append(
        [
            "**合計**",
            str(totals["Total"]),
            str(totals["Adopted"]),
            str(totals["Rejected"]),
            str(totals["Superseded"]),
            str(totals["Active"]),
        ]
    )
    return _table(
        ["Category", "Total", "Adopted", "Rejected", "Superseded", "Active"],
        body,
    )


def _build_top_open_risks() -> str:
    return _table(
        ["Priority", "Risk ID", "Title", "Description", "Current Mitigation", "Owner Phase"],
        [
            [r["priority"], r["id"], r["title"], r["description"], r["mitigation"], r["owner"]]
            for r in TOP_OPEN_RISKS
        ],
    )


def _build_appendix_a(rows: Sequence[dict[str, str]]) -> str:
    all_rows = list(GENESIS_ROWS) + list(rows)
    all_rows.sort(key=lambda r: (_phase_sort_key(r["phase"]), r["phase"]))
    body: list[list[str]] = []
    for row in all_rows:
        body.append(
            [
                row.get("date") or "—",
                f"Phase{row['phase']}",
                row.get("category") or "Monitoring",
                _esc_cell(row.get("title") or row.get("purpose") or "", 90),
                _verdict_label(row),
                _appendix_status(row),
            ]
        )
    return _table(
        ["Date", "Phase", "Category", "Description", "Verdict", "Current Status"],
        body,
    )


def _build_document(
    now: str,
    rows: list[dict[str, str]],
    narrative_body: str,
    narrative_tail: str,
) -> str:
    max_phase = max((_phase_sort_key(r["phase"])[0] for r in rows), default=0)
    decision_count = sum(
        1 for r in rows if r.get("adoption_status") in ("adopted", "rejected", "superseded")
    )
    parts: list[str] = [
        "# TradeBot System Source of Truth **v5**",
        "",
        "**監査可能な System Evolution Source of Truth** — 履歴集ではなく runtime 世代・採用 funnel・依存関係を監査する正本。",
        f"**Canonical MD:** `{CANONICAL_MD}`",
        f"**Canonical CSV:** `{CANONICAL_CSV}`",
        f"**Canonical PDF:** `{CANONICAL_PDF}`",
        f"**Generated:** {now} | **Generator:** `scripts/run_full_system_development_history.py` (Phase391)",
        f"**Audit rows:** {len(rows)} + genesis | **Runtime generation:** {len(RUNTIME_CHANGE_LOG)} ({RUNTIME_CHANGE_LOG[-1]['version']})",
        "",
        "**Satellite docs:** `runtime_change_log.md` · `runtime_dependency_graph.md` · `runtime_adoption_funnel.md`",
        "",
        "---",
        "",
        _build_production_stack_definition(),
        "",
        "---",
        "",
        "# Current Production Truth",
        "",
        "**唯一の正** — 本番 runtime（Stack C）。矛盾時は本表を優先。",
        "",
        _table(
            ["Layer", "Current Truth", "Source Phase"],
            [[a, b, c] for a, b, c in PRODUCTION_TRUTH],
        ),
        "",
        "---",
        "",
        "# Documentation Governance",
        "",
        "**必須:** 採用・不採用・置換・削除が発生したら **両方** を更新する。",
        "",
        "| Step | Artifact | Path |",
        "| --- | --- | --- |",
        f"| 1 | Audit CSV | `{CANONICAL_CSV}` |",
        f"| 2 | Source of Truth MD | `{CANONICAL_MD}` |",
        f"| 3 | PDF (optional) | `{CANONICAL_PDF}` |",
        "",
        "| Event | Required updates |",
        "| --- | --- |",
        "| **採用** | audit overrides → CSV → PRODUCTION_TRUTH → regenerate MD |",
        "| **不採用** | audit REJECTED → CSV → regenerate MD (+ Failure/Misconception if material) |",
        "| **置換** | audit SUPERSEDED → CSV → regenerate MD |",
        "| **削除** | audit removed → CSV → regenerate MD |",
        "",
        "**Workflow:**",
        "",
        "1. `python scripts/run_full_phase_history_audit.py`",
        "2. Update script constants if runtime changed (PRODUCTION_TRUTH, EVIDENCE_BY_PHASE, …)",
        "3. `python scripts/run_full_system_development_history.py`",
        "4. `python tools/md_to_pdf.py kabu_native/docs/architecture/full_system_development_history.md`",
        "",
        "| 区分 | 正本パス | 内容 |",
        "| --- | --- | --- |",
        f"| 恒久 | `docs/architecture/` | **本書** (SoT MD/PDF) |",
        f"| 恒久 | `docs/audits/` | **full_phase_history_audit.csv** |",
        "| 一時 | `results/reports/` | Phase 検証 snapshot のみ |",
        "",
        "---",
        "",
        "# Key Milestones",
        "",
        "330 Phase を読む前に把握する重要イベント（CSV + curated reasons）。",
        "",
        _build_key_milestones(rows),
        "",
        "---",
        "",
        "# Runtime Diff History",
        "",
        "Appendix B とは別。**いつ何が変わったか** を時系列で把握。",
        "",
        _build_runtime_diff_history(),
        "",
        "---",
        "",
        _build_phase391_sections(rows),
        "",
        "---",
        "",
    ]
    if narrative_body:
        parts.append(narrative_body)
        parts.append("")
    parts.extend(
        [
            "---",
            "",
            "# Top Open Risks",
            "",
            _build_top_open_risks(),
            "",
            "---",
            "",
            "# What Changed Since 6/12",
            "",
            "6/12 Incident Runtime → Current Runtime の差分。",
            "",
            _build_since_612(),
            "",
            "---",
            "",
        ]
    )
    if narrative_tail:
        parts.append(narrative_tail)
        parts.append("")
    parts.extend(
        [
            "---",
            "",
            "# Historical Misconceptions",
            "",
            f"Curated ({len(MISCONCEPTIONS)} entries). Audit CSV: `{len(rows)}` phases.",
            "",
            _table(
                [
                    "Date",
                    "Misconception",
                    "Believed At The Time",
                    "Invalidated By",
                    "Final Verdict",
                    "Current Status",
                ],
                [
                    [
                        m["date"],
                        m["misconception"],
                        m["believed"],
                        m["invalidated"],
                        m["verdict"],
                        m["status"],
                    ]
                    for m in MISCONCEPTIONS
                ],
            ),
            "",
            "---",
            "",
            "# Phase Adoption Matrix",
            "",
            f"Generated from `{CANONICAL_CSV}`.",
            "",
            _build_adoption_matrix(rows),
            "",
            "---",
            "",
            "# Major Decisions Table (Adoption Rationale)",
            "",
            f"CSV adoption_status ∈ {{adopted, rejected, superseded}} — **{decision_count} rows**. Evidence from reports + EVIDENCE_BY_PHASE.",
            "",
            _build_major_decisions(rows),
            "",
            "---",
            "",
            "# Lessons Learned",
            "",
            _table(
                ["Lesson", "Evidence Phase", "Current Interpretation"],
                [[a, b, c] for a, b, c in LESSONS],
            ),
            "",
            "---",
            "",
            "# Appendix A — Complete Phase Timeline",
            "",
            f"Phase001 (genesis) through Phase{max_phase}. **{len(rows) + len(GENESIS_ROWS)} rows.** CSV-only.",
            "",
            _build_appendix_a(rows),
            "",
            "---",
            "",
            "# Appendix B — Runtime Snapshot History",
            "",
            "Curated runtime epochs（Appendix B 維持）。6/12 Incident Runtime 確認用。",
            "",
            _table(
                ["Date", "Runtime Name", "Universe", "Entry", "Exit", "CAP", "Notes"],
                [
                    [
                        s["date"],
                        s["name"],
                        s["universe"],
                        s["entry"],
                        s["exit"],
                        s["cap"],
                        s["notes"],
                    ]
                    for s in RUNTIME_SNAPSHOTS
                ],
            ),
            "",
            "### Stack A / B / C (Phase365 research labels)",
            "",
            _table(
                ["Stack", "ENTRY delta", "Phase365 PnL", "PF", "Status"],
                [
                    ["A baseline", "no guards", "+160,510", "1.0526", "research"],
                    ["B +355", "pullback D40", "+260,910", "1.1070", "research"],
                    ["C +355+364", "near-day-high D40", "+483,110", "1.2607", "production"],
                ],
            ),
            "",
            "---",
            "",
            "# Appendix C — Shadow Evolution History",
            "",
            _table(
                ["Start Date", "Shadow", "Purpose", "Status", "Current Verdict"],
                [
                    [s["start"], s["shadow"], s["purpose"], s["status"], s["verdict"]]
                    for s in SHADOW_EVOLUTION
                ],
            ),
            "",
            "---",
            "",
            "# Appendix D — Failure Archive",
            "",
            _table(
                ["Date", "Failure", "Root Cause", "Resolution", "Current State"],
                [
                    [f["date"], f["failure"], f["root"], f["resolution"], f["state"]]
                    for f in FAILURE_ARCHIVE
                ],
            ),
            "",
            "---",
            "",
            "# Phase391 — Generator Report",
            "",
            "## 追加章（v5）",
            "",
            "| # | Section | Output |",
            "| --- | --- | --- |",
            "| 1 | Runtime Change Log | main MD + runtime_change_log.md |",
            "| 2 | Stack Evolution | main MD |",
            "| 3 | Runtime Dependency Graph | main MD + runtime_dependency_graph.md |",
            "| 4 | Adoption Funnel | main MD + runtime_adoption_funnel.md |",
            "| 5 | Adopted Then Removed | main MD + runtime_adoption_funnel.md |",
            "| 6 | Runtime Delta Timeline | main MD + runtime_change_log.md |",
            "| 7 | Current Runtime Provenance | main MD + runtime_dependency_graph.md |",
            "",
            "## 即答チェックリスト",
            "",
            f"| Question | Answer |",
            "| --- | --- |",
            f"| 今のRuntime世代 | **{len(RUNTIME_CHANGE_LOG)}** ({RUNTIME_CHANGE_LOG[-1]['version']}) |",
            "| 6/12前後の変更 | Runtime Delta Timeline / What Changed Since 6/12 |",
            "| Stack C構成Phase | Runtime Dependency Graph |",
            "| 採用後削除・置換 | Adopted Then Removed |",
            f"| 330 Phase funnel | Adoption Funnel ({len(rows)+len(GENESIS_ROWS)} rows) |",
            "| CAP=2 | **Research** (388/389/387) — runtime **cap3** |",
            "",
            "## 変更なし確認",
            "",
            "| Layer | Changed |",
            "| --- | --- |",
            "| Runtime | **No** |",
            "| Universe | **No** |",
            "| Entry | **No** |",
            "| Exit | **No** |",
            "| YAML | **No** |",
            "",
        ]
    )
    return "\n".join(parts)


def generate(*, refresh_audit: bool = False) -> tuple[Path, int, int]:
    if refresh_audit and AUDIT_SCRIPT.is_file():
        subprocess.run([sys.executable, str(AUDIT_SCRIPT)], check=True, cwd=REPO / "kabu_native")
    rows = _load_csv(AUDIT_CSV)
    old_lines = 0
    if OUT_MD.is_file():
        old_lines = len(OUT_MD.read_text(encoding="utf-8").splitlines())
    narrative_body, narrative_tail = _extract_narrative_parts(OUT_MD)
    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")
    doc = _build_document(now, rows, narrative_body, narrative_tail)
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text(doc, encoding="utf-8")
    _write_satellite_docs(now, rows)
    new_lines = len(doc.splitlines())
    return OUT_MD, old_lines, new_lines


def main() -> int:
    refresh = "--refresh-audit" in sys.argv
    out, old_lines, new_lines = generate(refresh_audit=refresh)
    added = new_lines - old_lines if old_lines else new_lines
    print(f"wrote: {out}")
    print(f"wrote: {OUT_RUNTIME_LOG}")
    print(f"wrote: {OUT_DEPENDENCY_GRAPH}")
    print(f"wrote: {OUT_ADOPTION_FUNNEL}")
    print(f"rows: {len(_load_csv(AUDIT_CSV))}")
    print(f"lines: {new_lines} (delta {added:+d} vs prior {old_lines})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
