"""
Phase489 — Runtime Observability Audit (research only).

Inventories current Discord / summary / shadow observability and gaps vs
Phase483–488 analysis needs. No Runtime / YAML / Entry / Exit / Order changes.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _resolve_reports(repo_root: Path) -> Path:
    kabu = repo_root / "kabu_native"
    if (kabu / "results").is_dir():
        return kabu / "results" / "reports"
    return repo_root / "results" / "reports"


def _write_csv(path: Path, fields: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(fields), extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(dict(row))


NOTIFICATION_FIELDS = [
    "channel",
    "event_tag",
    "trigger",
    "key_fields",
    "default_enabled",
    "notes",
]

GAP_FIELDS = [
    "gap_id",
    "category",
    "missing_item",
    "impact",
    "blocks_analysis",
    "data_source_available",
]

CANDIDATE_FIELDS = [
    "rank",
    "candidate_id",
    "target_surface",
    "description",
    "runtime_change_required",
    "effort",
    "phase_reference",
]

CURRENT_NOTIFICATIONS: list[dict[str, Any]] = [
    {
        "channel": "trade_notify",
        "event_tag": "ENTRY",
        "trigger": "accepted entry (structural CAP)",
        "key_fields": "symbol, score_v2, entry reasons, slot usage, stop price, momentum_continuation",
        "default_enabled": True,
        "notes": "Operator-readable bullets; no board_tier label",
    },
    {
        "channel": "cap_blocked",
        "event_tag": "CAP BLOCKED",
        "trigger": "max_concurrent reject with score>=5",
        "key_fields": "symbol, score_v2, active positions, deferred reason",
        "default_enabled": True,
        "notes": "Separate webhook from trade_notify",
    },
    {
        "channel": "trade_notify",
        "event_tag": "EXIT",
        "trigger": "structural exit only (is_structural_exit)",
        "key_fields": "pnl_yen_100, MFE/MAE, hold, exit_reason, board_dynamic tier/activate/giveback",
        "default_enabled": True,
        "notes": "No stop_low_mfe flag; no entry feature snapshot",
    },
    {
        "channel": "legacy",
        "event_tag": "HOLD",
        "trigger": "periodic / quality delta",
        "key_fields": "continuation components, unrealized pnl",
        "default_enabled": True,
        "notes": "High volume; not in trade_notify channel",
    },
    {
        "channel": "legacy",
        "event_tag": "TAKE",
        "trigger": "quality drop signal",
        "key_fields": "observer signal only disclaimer",
        "default_enabled": True,
        "notes": "Not an exit order",
    },
    {
        "channel": "trade_notify",
        "event_tag": "Universe Refresh",
        "trigger": "10:00 / 14:30 refresh",
        "key_fields": "added/removed symbols with names",
        "default_enabled": True,
        "notes": "",
    },
    {
        "channel": "trade_notify",
        "event_tag": "Universe Screening",
        "trigger": "AM/PM watch list build",
        "key_fields": "watch symbol list",
        "default_enabled": True,
        "notes": "",
    },
    {
        "channel": "trade_notify",
        "event_tag": "Daily Summary",
        "trigger": "session end",
        "key_fields": "trade_count, PF, win_rate, pnl, stop_rate, best/worst, max_concurrent",
        "default_enabled": True,
        "notes": "AM/PM variants; Research Shadow field appended",
    },
    {
        "channel": "legacy",
        "event_tag": "HEARTBEAT",
        "trigger": "every N min",
        "key_fields": "runtime_sec, entry/holding/exited counts, api_errors",
        "default_enabled": True,
        "notes": "No feature health block",
    },
    {
        "channel": "legacy",
        "event_tag": "REJECT",
        "trigger": "gate reject",
        "key_fields": "reject_reason, quality",
        "default_enabled": False,
        "notes": "send_rejects=false default — most rejects invisible on Discord",
    },
    {
        "channel": "legacy",
        "event_tag": "ERROR",
        "trigger": "api / pipeline errors",
        "key_fields": "error_type",
        "default_enabled": True,
        "notes": "",
    },
]

GAPS: list[dict[str, Any]] = [
    {
        "gap_id": "G01",
        "category": "Daily Summary",
        "missing_item": "Symbol PnL attribution (6976/4062/focus 7)",
        "impact": "Cannot spot concentration risk same-day",
        "blocks_analysis": "Phase488 6976 62% share invisible until offline replay",
        "data_source_available": "canonical_summary + trade log",
    },
    {
        "gap_id": "G02",
        "category": "Daily Summary",
        "missing_item": "Exit reason breakdown (stop_hit / no_progress / trailing / session_close)",
        "impact": "Exit tuning blind without CSV export",
        "blocks_analysis": "Phase485/486 board_high grid needs manual post-hoc",
        "data_source_available": "observer_exit events",
    },
    {
        "gap_id": "G03",
        "category": "Daily Summary",
        "missing_item": "stop_low_mfe count (MFE<0.5% at stop)",
        "impact": "Phase483 root cause not visible daily",
        "blocks_analysis": "Yes — primary loss cluster hidden",
        "data_source_available": "events + price path (replay)",
    },
    {
        "gap_id": "G04",
        "category": "Discord ENTRY",
        "missing_item": "board_tier (high/low) + momentum_cutoff distance",
        "impact": "PBv2 gate context missing at entry time",
        "blocks_analysis": "Moderate — score_v2 only",
        "data_source_available": "entry_order_book_imbalance, momentum_continuation_score",
    },
    {
        "gap_id": "G05",
        "category": "Discord EXIT",
        "missing_item": "Peak MFE vs exit PnL giveback (trailing efficiency)",
        "impact": "Trailing tuning feedback delayed",
        "blocks_analysis": "Phase486 grid post-hoc only",
        "data_source_available": "exit context mfe_pct",
    },
    {
        "gap_id": "G06",
        "category": "Shadow Summary",
        "missing_item": "Unified Shadow Summary section (board_dynamic delta, guard deltas)",
        "impact": "Shadow counters scattered in JSON; Discord shows research shadows only",
        "blocks_analysis": "Moderate",
        "data_source_available": "small_paper_summary.json fields",
    },
    {
        "gap_id": "G07",
        "category": "Runtime Health",
        "missing_item": "Dedicated Runtime Health block (stale_tick, data_gap, feature complete rate)",
        "impact": "Data quality issues buried in JSON",
        "blocks_analysis": "Yes — bad entries from stale data undetected",
        "data_source_available": "summary JSON live_feature_* fields",
    },
    {
        "gap_id": "G08",
        "category": "Feature Health",
        "missing_item": "Per-feature missing rate (r5/r10/r15, vwap_dev, board_change)",
        "impact": "Phase484 guards may silently skip (null features)",
        "blocks_analysis": "Yes — guard research unreliable without coverage",
        "data_source_available": "events + live_feature_bridge stats",
    },
    {
        "gap_id": "G09",
        "category": "Daily Summary",
        "missing_item": "Reject funnel top-5 (high_drift, late_chase, momentum, cap)",
        "impact": "Entry funnel opacity",
        "blocks_analysis": "Moderate",
        "data_source_available": "reject_reason_counts in summary",
    },
    {
        "gap_id": "G10",
        "category": "Daily Summary",
        "missing_item": "Rolling N-day cumulative PnL vs Phase488 baseline band",
        "impact": "No context if today is outlier",
        "blocks_analysis": "Moderate",
        "data_source_available": "historical canonical summaries",
    },
    {
        "gap_id": "G11",
        "category": "Discord",
        "missing_item": "late_chase_guard reject detail (r10, day_high_distance)",
        "impact": "Only reject count in Research Shadow lines",
        "blocks_analysis": "Low",
        "data_source_available": "rejects.csv / events",
    },
    {
        "gap_id": "G12",
        "category": "Shadow Summary",
        "missing_item": "Phase487 guard shadow (A2/B2) forward log",
        "impact": "Cannot monitor guard candidates without replay",
        "blocks_analysis": "Research-only gap",
        "data_source_available": "not logged today",
    },
]

CANDIDATES: list[dict[str, Any]] = [
    {
        "rank": 1,
        "candidate_id": "C01_symbol_pnl_daily",
        "target_surface": "Daily Summary",
        "description": "Top 5 symbol PnL + focus 6976/4062 share of day PnL",
        "runtime_change_required": False,
        "effort": "low",
        "phase_reference": "Phase488",
    },
    {
        "rank": 2,
        "candidate_id": "C02_exit_reason_breakdown",
        "target_surface": "Daily Summary",
        "description": "stop_hit / no_progress / trailing / session_close counts + PnL",
        "runtime_change_required": False,
        "effort": "low",
        "phase_reference": "Phase485/486",
    },
    {
        "rank": 3,
        "candidate_id": "C03_runtime_health_block",
        "target_surface": "Daily Summary + HEARTBEAT",
        "description": "stale_tick, data_gap, api_errors, feature_complete%, config_sha256 tail",
        "runtime_change_required": False,
        "effort": "low",
        "phase_reference": "ops",
    },
    {
        "rank": 4,
        "candidate_id": "C04_feature_health_block",
        "target_surface": "Daily Summary",
        "description": "Feature coverage: r5/r10/r15, vwap_dev, board_imbalance missing %",
        "runtime_change_required": False,
        "effort": "medium",
        "phase_reference": "Phase484",
    },
    {
        "rank": 5,
        "candidate_id": "C05_stop_low_mfe_counter",
        "target_surface": "Daily Summary + EXIT tag",
        "description": "Daily stop_low_mfe count; EXIT line if MFE<0.5% at stop",
        "runtime_change_required": False,
        "effort": "medium",
        "phase_reference": "Phase483",
    },
    {
        "rank": 6,
        "candidate_id": "C06_reject_funnel",
        "target_surface": "Daily Summary",
        "description": "Top reject reasons with counts from reject_reason_counts",
        "runtime_change_required": False,
        "effort": "low",
        "phase_reference": "ops",
    },
    {
        "rank": 7,
        "candidate_id": "C07_shadow_summary_unified",
        "target_surface": "Shadow Summary",
        "description": "board_dynamic delta_yen, guard reject counts, research shadow verdicts",
        "runtime_change_required": False,
        "effort": "medium",
        "phase_reference": "Phase488",
    },
    {
        "rank": 8,
        "candidate_id": "C08_entry_board_tier",
        "target_surface": "Discord ENTRY",
        "description": "Show board_high/low + mom score vs 0.2546 cutoff",
        "runtime_change_required": False,
        "effort": "low",
        "phase_reference": "Phase472",
    },
    {
        "rank": 9,
        "candidate_id": "C09_rolling_pnl_band",
        "target_surface": "Daily Summary",
        "description": "5-day cumulative PnL + Phase488 replay expectation band",
        "runtime_change_required": False,
        "effort": "medium",
        "phase_reference": "Phase488",
    },
    {
        "rank": 10,
        "candidate_id": "C10_guard_shadow_forward",
        "target_surface": "Shadow Summary",
        "description": "Forward log late_chase/A2/B2 guard would-block count (shadow only)",
        "runtime_change_required": False,
        "effort": "high",
        "phase_reference": "Phase487",
    },
]

DISCORD_MOCKUP = """\
【Daily Summary】 20260619 PM
━━━━━━━━━━━━━━━━━━━━
■ 本日成績 (100株)
trade_count: 48 | win_rate: 56% | PF: 1.24
total_pnl: +25,900円 | stop_rate: 8%
best: 6920 +47,000 | worst: 6920 -60,000

■ Symbol Attribution          ← NEW (C01)
6976: +12,000 (3T, 46% of day) ⚠
4062: -3,500 (2T)
top3_share: 72%

■ Exit Breakdown              ← NEW (C02)
stop_hit: 4 (-18,000)
no_progress: 6 (-22,000)
trailing_mfe: 12 (+41,000)
session_close: 26 (+24,900)
stop_low_mfe: 2 (-15,000)      ← NEW (C05)

■ Runtime Health              ← NEW (C03)
api_errors: 1 | stale_ticks: 3089 | data_gaps: 38
feature_complete: 94.8% | config: …3c45
peak_slots: 5/5 | session: OK

■ Feature Health              ← NEW (C04)
r15_missing: 12% | vwap_dev_missing: 0%
board_change_10m_missing: 31%

■ Reject Funnel (top)         ← NEW (C06)
high_drift: 4385 | stale_price: 31901
late_chase: 12 | max_concurrent: 1658

■ Research Shadow (existing)
LateChase Guard: reject=12
HighDrift Guard: reject=4385
NoProgress Exit: count=6
BoardDynamic Shadow: delta=-1,400
...
"""

DAILY_SUMMARY_MOCKUP = DISCORD_MOCKUP

SHADOW_SUMMARY_MOCKUP = """\
Shadow Summary — 20260619 (session aggregate)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Guard rejects (would-block, production gates):
  high_drift_pullback:     4385
  late_chase_guard:          12  ← count only today; no r10/dh detail
  near_day_high_low_mom:    369
  weak_shape:               (in gate, not in Discord)

Exit shadow (board dynamic vs legacy fixed):
  exits: 44 | improved: 2 | delta_yen: -1,400
  stop_hit: 3 | trailing: 10 | session_close: 31

Research forward shadows (Discord today):
  SectorHeat / RiskSizing / EquityDynStop / LiveConfig / Boundary
  → adopt_not_allowed flags only

Missing vs need (Phase487):
  A2_r15_minus_r5 guard would-block: NOT LOGGED
  B2_vwap_extension guard would-block: NOT LOGGED

Recommendation (C07): single Shadow Summary embed mirroring JSON counters
with delta_yen highlights when |delta| > 10k.
"""

RUNTIME_HEALTH_MOCKUP = """\
Runtime Health — heartbeat / EOD
━━━━━━━━━━━━━━━━━━━━━━━━
Status: 🟢 RUNNING | paper_only | order_enabled=false

Connectivity:
  api_errors: 1        reconnects: 1
  stale_tick_count: 3089  (threshold 120s)
  data_gap_count: 38

Pipeline:
  push_messages: 406,569
  gate_evaluations: 49,681
  quality_fallback_rate: 15.6%

CAP state:
  open_slots: 0/5 | peak_today: 5/5
  same_symbol_overlap_rejects: 934

Config:
  policy: q070_cap3_…_trial
  sha256: 15113c9d…3c45
  structural_exit: trailing_mfe_shadow

Alerts (proposed):
  🔴 if stale_tick_count > 5000/session
  🟡 if feature_complete_rate < 90%
  🔴 if api_errors > 5
"""

FEATURE_HEALTH_MOCKUP = """\
Feature Health — entry-time coverage
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Session: 20260619 PM | accepted: 48

Bridge stats (from summary JSON today):
  live_feature_complete_rate: 94.82%
  quality_fallback_rate: 15.61%

Per-feature missing on ACCEPTED entries (proposed):
  entry_rise_15min_pct:  8/48 (17%)  ← blocks A2 guard eval
  entry_rise_5min_pct:   2/48 (4%)
  vwap_dev_pct:          0/48 (0%)
  board_change_10m:     15/48 (31%)  ← blocks D2 guard eval
  momentum_continuation: 0/48 (0%)

Staleness on entry:
  data_stale_price rejects: 31901 (funnel)
  data_stale_board rejects: 341

Action: if r15 missing > 20% on accepted → flag Feature Health WARN
"""


def _verdict(gaps: Sequence[Mapping[str, Any]]) -> str:
    blocking = sum(1 for g in gaps if str(g.get("blocks_analysis", "")).lower().startswith("yes"))
    if blocking >= 3:
        return "observability_improvement_candidate"
    return "current_observability_sufficient"


def _next_actions(verdict: str) -> list[str]:
    actions = [f"Verdict: {verdict}"]
    if verdict == "observability_improvement_candidate":
        actions.append("Implement C01–C03 in discord_message_builder (Discord/Summary only, no gate change)")
        actions.append("Add stop_low_mfe counter C05 after C01 validated on 3 sessions")
        actions.append("Defer C10 guard forward shadow until Phase487 shadow design")
    else:
        actions.append("Monitor with existing Daily Summary + weekly offline replay")
    return actions


def run_phase489(*, repo_root: Path) -> dict[str, Any]:
    gaps = list(GAPS)
    candidates = list(CANDIDATES)
    verdict = _verdict(gaps)

    mandatory = {
        "1_missing_from_current_notifications": [
            "Symbol/day PnL attribution",
            "Exit reason + stop_low_mfe breakdown",
            "Reject funnel summary",
            "Runtime Health block (stale/data/api)",
            "Feature Health / missing-feature rates",
            "Board tier on ENTRY",
            "Guard shadow forward logs (Phase487)",
            "Rolling multi-day PnL context",
        ],
        "2_analysis_blocking_gaps": [
            g["missing_item"]
            for g in gaps
            if str(g.get("blocks_analysis", "")).lower().startswith("yes")
        ],
        "3_daily_metrics_to_watch": [
            "total_pnl_yen_100 + PF + stop_rate",
            "6976/4062 symbol PnL share",
            "stop_low_mfe count",
            "exit reason mix (stop / NP / trailing / close)",
            "high_drift + late_chase reject counts",
            "feature_complete_rate + stale_tick_count",
            "board_dynamic shadow delta_yen",
            "peak_open_slots / CAP utilization",
        ],
        "4_discord_improvement": "Add symbol attribution + exit breakdown to Daily Summary embed; enrich ENTRY with board_tier + mom vs cutoff; optional stop_low_mfe tag on EXIT",
        "5_summary_improvement": "Extend canonical_summary Discord block with reject funnel, runtime/feature health, 5-day rolling PnL",
        "6_runtime_health_improvement": "Surface api_errors, stale_ticks, data_gaps, config_sha, peak_slots in HEARTBEAT + EOD — alert thresholds",
        "7_feature_health_improvement": "Log per-feature missing rate on accepted entries; WARN if r15/board_change missing > 20%",
        "8_implementable_without_runtime_change": True,
        "9_implementation_priority": [c["candidate_id"] for c in candidates[:6]],
        "10_next_actions": _next_actions(verdict),
        "verdict": verdict,
        "notification_count": len(CURRENT_NOTIFICATIONS),
        "gap_count": len(gaps),
        "candidate_count": len(candidates),
    }

    return {
        "generated_at": _now_iso(),
        "verdict": verdict,
        "mandatory_answers": mandatory,
        "_notifications": CURRENT_NOTIFICATIONS,
        "_gaps": gaps,
        "_candidates": candidates,
        "_mockups": {
            "discord_daily": DISCORD_MOCKUP,
            "daily_summary": DAILY_SUMMARY_MOCKUP,
            "shadow_summary": SHADOW_SUMMARY_MOCKUP,
            "runtime_health": RUNTIME_HEALTH_MOCKUP,
            "feature_health": FEATURE_HEALTH_MOCKUP,
        },
    }


@dataclass
class Phase489Job:
    repo_root: Path

    def run(self) -> dict[str, Any]:
        return run_phase489(repo_root=self.repo_root)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = _resolve_reports(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        doc_root = self.repo_root / "kabu_native"
        if not (doc_root / "docs").is_dir():
            doc_root = self.repo_root

        paths = {
            "notifications": reports / "phase489_current_notifications.csv",
            "gaps": reports / "phase489_observability_gaps.csv",
            "candidates": reports / "phase489_observability_candidates.csv",
            "summary": reports / "phase489_summary.json",
            "report": doc_root / "docs" / "operations" / "phase489_runtime_observability_audit.md",
        }
        _write_csv(paths["notifications"], NOTIFICATION_FIELDS, list(result.get("_notifications") or []))
        _write_csv(paths["gaps"], GAP_FIELDS, list(result.get("_gaps") or []))
        _write_csv(paths["candidates"], CANDIDATE_FIELDS, list(result.get("_candidates") or []))
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        self._write_report(paths["report"], result)
        return paths

    def _write_report(self, report: Path, result: Mapping[str, Any]) -> None:
        m = result.get("mandatory_answers") or {}
        mock = result.get("_mockups") or {}
        lines = [
            "# Phase489 — Runtime Observability Audit",
            "",
            f"**Verdict:** `{result.get('verdict')}`",
            "",
            "## 1. 現在通知一覧",
            "",
            "See `phase489_current_notifications.csv` (11 event types).",
            "",
            "| Channel | Event | Default |",
            "|---------|-------|---------|",
        ]
        for n in result.get("_notifications") or []:
            lines.append(
                f"| {n.get('channel')} | {n.get('event_tag')} | {n.get('default_enabled')} |"
            )
        lines.extend(
            [
                "",
                "## 2. 不足項目一覧",
                "",
                "See `phase489_observability_gaps.csv` (12 gaps).",
                "",
                "## 3. 追加候補一覧",
                "",
                "See `phase489_observability_candidates.csv` (10 candidates, C01–C10).",
                "",
                "## 4. Discord mockup (Daily Summary enriched)",
                "",
                "```",
                str(mock.get("discord_daily", "")).strip(),
                "```",
                "",
                "## 5. Daily Summary mockup",
                "",
                "```",
                str(mock.get("daily_summary", "")).strip(),
                "```",
                "",
                "## 6. Shadow Summary mockup",
                "",
                "```",
                str(mock.get("shadow_summary", "")).strip(),
                "```",
                "",
                "## 7. Runtime Health mockup",
                "",
                "```",
                str(mock.get("runtime_health", "")).strip(),
                "```",
                "",
                "## 8. Feature Health mockup",
                "",
                "```",
                str(mock.get("feature_health", "")).strip(),
                "```",
                "",
                "## 必須回答",
                "",
                f"1. 不足情報: {m.get('1_missing_from_current_notifications')}",
                f"2. 分析阻害: {m.get('2_analysis_blocking_gaps')}",
                f"3. 毎日指標: {m.get('3_daily_metrics_to_watch')}",
                f"4. Discord改善: {m.get('4_discord_improvement')}",
                f"5. Summary改善: {m.get('5_summary_improvement')}",
                f"6. Runtime Health改善: {m.get('6_runtime_health_improvement')}",
                f"7. Feature Health改善: {m.get('7_feature_health_improvement')}",
                f"8. Runtime変更不要で可能: **{m.get('8_implementable_without_runtime_change')}**",
                f"9. 優先順位: {m.get('9_implementation_priority')}",
                f"10. 次アクション: {m.get('10_next_actions')}",
                "",
                f"**Verdict:** `{result.get('verdict')}`",
                "",
            ]
        )
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("\n".join(lines), encoding="utf-8")
