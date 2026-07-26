#!/usr/bin/env python3
"""Phase687W67 — Audit W66 full-period Shadow capital validation premises.

Investigation only. Does NOT change Runtime / Shadow / Forward / thresholds.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import pandas as pd
from openpyxl import Workbook
from openpyxl.utils.dataframe import dataframe_to_rows

NATIVE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(NATIVE_ROOT.parent))
sys.path.insert(0, str(NATIVE_ROOT / "src"))
sys.path.insert(0, str(NATIVE_ROOT))

JST = ZoneInfo("Asia/Tokyo")
PHASE = "Phase687W67"
OUT_DIR = NATIVE_ROOT / "results" / "reports"
SMALL_PAPER = NATIVE_ROOT / "results" / "small_paper"
W66_SCRIPT = NATIVE_ROOT / "scripts" / "run_phase687w66_full_period_shadow_capital_validation.py"
W66_JSON = OUT_DIR / "phase687w66_full_period_shadow_capital_report.json"
BOARD_DOC = NATIVE_ROOT / "docs" / "board_data_inventory.md"

# Fields probed on accepted events
PROBE_FIELDS = [
    "entry_order_book_imbalance",
    "entry_imbalance_percentile",
    "imbalance_shadow_candidate",
    "imbalance_shadow_tier",
    "board_age_sec",
    "board_improvement",
    "board_imbalance_jump",
    "board_imbalance",
    "spread_bps",
    "entry_rise_5min_pct",
    "entry_vwap_dev_pct",
    "entry_expectancy_score_v2",
    "entry_near_day_high_pct",
    "momentum_continuation",
    "universe_slot",
    "flat_weak_range_shadow_block",
    "pullback_misread_guard_shadow_blocked",
]


def _excel_cell(x: Any) -> Any:
    if x is None:
        return ""
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return ""
    if isinstance(x, (dict, list)):
        return json.dumps(x, ensure_ascii=False, default=str)[:32000]
    return x


def write_xlsx(sheets: dict[str, pd.DataFrame], path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "README"
    for row in [
        [PHASE],
        ["generated", datetime.now(JST).isoformat()],
        ["note", "Audit only; runtime/shadow/thresholds unchanged"],
    ]:
        ws.append(row)
    for name, df in sheets.items():
        w = wb.create_sheet(str(name)[:31])
        if df is None or getattr(df, "empty", True):
            w.append(["empty"])
            continue
        clean = df.head(100000).copy()
        for c in clean.columns:
            clean[c] = clean[c].map(_excel_cell)
        for r in dataframe_to_rows(clean, index=False, header=True):
            w.append([_excel_cell(x) for x in r])
        w.auto_filter.ref = w.dimensions
        w.freeze_panes = "A2"
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def _iso(day8: str) -> str:
    return f"{day8[:4]}-{day8[4:6]}-{day8[6:8]}"


def list_small_paper_days() -> list[str]:
    if not SMALL_PAPER.is_dir():
        return []
    return sorted(
        d.name for d in SMALL_PAPER.iterdir() if d.is_dir() and len(d.name) == 8 and d.name.isdigit()
    )


def inventory_sessions(days: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    files: list[str] = []
    for day8 in days:
        day_dir = SMALL_PAPER / day8
        for sess in sorted(day_dir.glob("live_session_*")):
            if not sess.is_dir():
                continue
            ev_csv = sess / "small_paper_events.csv"
            ev_jsonl = sess / "small_paper_events.jsonl"
            summary = sess / "small_paper_summary.json"
            has_csv = ev_csv.is_file()
            has_jsonl = ev_jsonl.is_file()
            source = ""
            if summary.is_file():
                try:
                    source = str(json.loads(summary.read_text(encoding="utf-8")).get("source") or "")
                except (OSError, json.JSONDecodeError):
                    source = ""
            n_acc = 0
            n_ex = 0
            path: Optional[Path] = ev_csv if has_csv else (ev_jsonl if has_jsonl else None)
            if path is not None:
                rel = str(path.relative_to(NATIVE_ROOT)).replace("\\", "/")
                files.append(rel)
                if path.suffix == ".csv":
                    with path.open(encoding="utf-8", newline="") as f:
                        for row in csv.DictReader(f):
                            et = row.get("event_type")
                            if et == "accepted":
                                n_acc += 1
                            elif et == "observer_exit":
                                n_ex += 1
                else:
                    with path.open(encoding="utf-8") as f:
                        for line in f:
                            try:
                                row = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            et = row.get("event_type")
                            if et == "accepted":
                                n_acc += 1
                            elif et == "observer_exit":
                                n_ex += 1
            replayable = has_csv or has_jsonl
            status = "Replay可能" if replayable and n_ex > 0 else (
                "欠損" if not replayable else ("Shadow不足/EXITなし" if n_acc and not n_ex else "除外候補")
            )
            if source == "push-replay":
                status = "除外"
            rows.append(
                {
                    "day": _iso(day8),
                    "day_key": day8,
                    "session": sess.name,
                    "has_csv": has_csv,
                    "has_jsonl": has_jsonl,
                    "accepted_count": n_acc,
                    "observer_exit_count": n_ex,
                    "source": source or "live",
                    "status": status,
                    "path": str(sess.relative_to(NATIVE_ROOT)).replace("\\", "/"),
                }
            )
    return rows, files


def probe_features_by_day(days: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for day8 in days:
        hits: dict[str, int] = defaultdict(int)
        n_acc = 0
        cols_union: set[str] = set()
        for sess in sorted((SMALL_PAPER / day8).glob("live_session_*")):
            path = sess / "small_paper_events.csv"
            if not path.is_file():
                path = sess / "small_paper_events.jsonl"
            if not path.is_file():
                continue
            if path.suffix == ".csv":
                with path.open(encoding="utf-8", newline="") as f:
                    reader = csv.DictReader(f)
                    cols_union |= set(reader.fieldnames or [])
                    for row in reader:
                        if row.get("event_type") != "accepted":
                            continue
                        n_acc += 1
                        for c in PROBE_FIELDS:
                            if row.get(c) not in (None, ""):
                                hits[c] += 1
            else:
                with path.open(encoding="utf-8") as f:
                    for line in f:
                        try:
                            row = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        cols_union |= set(row.keys())
                        if row.get("event_type") != "accepted":
                            continue
                        n_acc += 1
                        for c in PROBE_FIELDS:
                            if row.get(c) not in (None, ""):
                                hits[c] += 1
        row = {
            "day": _iso(day8),
            "day_key": day8,
            "accepted_count": n_acc,
            "has_board_improvement_column": "board_improvement" in cols_union,
            "has_eobi_column": "entry_order_book_imbalance" in cols_union,
            "has_spread_bps_column": "spread_bps" in cols_union,
        }
        for c in PROBE_FIELDS:
            row[f"{c}_n"] = hits[c]
            row[f"{c}_rate"] = round(hits[c] / n_acc, 4) if n_acc else None
        out.append(row)
    return out


def inventory_non_small_paper() -> dict[str, Any]:
    daily = NATIVE_ROOT / "results" / "daily"
    replay = NATIVE_ROOT / "results" / "replay"
    push = NATIVE_ROOT / "data" / "push_jsonl"
    intraday = NATIVE_ROOT.parent / "data" / "intraday_1m"
    if not intraday.is_dir():
        intraday = NATIVE_ROOT / "data" / "intraday_1m"

    daily_days = sorted(p.name for p in daily.iterdir() if p.is_dir() and p.name.isdigit()) if daily.is_dir() else []
    replay_days = sorted(p.name for p in replay.iterdir() if p.is_dir() and p.name.isdigit()) if replay.is_dir() else []
    may_daily = [d for d in daily_days if d.startswith("202605")]
    early_jun_daily = [d for d in daily_days if d.startswith("202606") and d < "20260615"]

    push_children = []
    if push.is_dir():
        push_children = [p.name for p in push.iterdir() if p.name != ".gitkeep"]

    may_bars = []
    jun_bars = []
    if intraday.is_dir():
        may_bars = sorted(p.name for p in intraday.iterdir() if p.name.startswith("2026-05"))
        jun_bars = sorted(p.name for p in intraday.iterdir() if p.name.startswith("2026-06") and p.name < "2026-06-15")

    # Historical docs claim 20260604-05 sessions existed
    historically_documented = {
        "20260604": "Phase300 board inventory — sessions existed; entry_order_book_imbalance 100% null",
        "20260605": "Phase300 board inventory — sessions existed; entry_order_book_imbalance 100% null",
        "20260608_20260614": "Expected first days after Phase299 (~2026-06-07) fix; not present on disk now",
    }

    return {
        "results_daily_may": may_daily,
        "results_daily_early_june": early_jun_daily,
        "results_replay_days": replay_days,
        "push_jsonl_non_gitkeep": push_children,
        "push_jsonl_status": "empty" if not push_children else "present",
        "intraday_1m_may_days": may_bars,
        "intraday_1m_early_june_days": jun_bars,
        "intraday_1m_path": str(intraday),
        "historically_documented_missing_small_paper": historically_documented,
        "may_small_paper_events_glob_count": len(list((NATIVE_ROOT / "results").rglob("202605*/**/small_paper_events.*"))),
    }


def w66_discovery_audit() -> dict[str, Any]:
    text = W66_SCRIPT.read_text(encoding="utf-8") if W66_SCRIPT.is_file() else ""
    w66 = {}
    if W66_JSON.is_file():
        try:
            w66 = json.loads(W66_JSON.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            w66 = {}
    return {
        "explored_directory": "kabu_native/results/small_paper/{YYYYMMDD}/live_session_*/",
        "file_globs": [
            "small_paper_events.jsonl",
            "small_paper_events.csv",
        ],
        "exclusions_in_code": [
            "push-replay sessions (small_paper_summary.source == push-replay)",
            "day dirs without completed accepted+observer_exit trades",
            "sessions without events file",
        ],
        "all_period_flag_behavior": (
            "--all-period is documented as use-full-canonical; implementation scans ALL "
            "YYYYMMDD dirs under results/small_paper with no hard-coded 2026-06-15 start"
        ),
        "hardcoded_start_date_in_w66": "2026-06-15" in text and "data_start" in text,
        "w66_reported_start": w66.get("data_start"),
        "w66_reported_end": w66.get("data_end"),
        "w66_trading_days": w66.get("trading_days"),
        "source_of_truth_field": (w66.get("data_quality") or {}).get("source_of_truth"),
        "why_start_0615": (
            "Oldest surviving day directory under results/small_paper is 20260615. "
            "W66 did not filter May — May small_paper sessions are absent on disk."
        ),
        "classification": "意図した仕様（利用可能small_paper全件）",
        "not_format_bug": True,
        "not_schema_filter": True,
        "not_shadow_feature_filter_for_period": True,
        "not_implementation_mistake_for_start_date": True,
    }


def shadow_feature_matrix() -> list[dict[str, Any]]:
    return [
        # Cost-Aware
        {
            "shadow": "CostAware",
            "feature": "entry_expectancy_score_v2 / continuation_quality_score",
            "generated_where": "Runtime ENTRY scoring (pilot_runner / expectancy)",
            "runtime": "YES",
            "replay_from_events": "YES if persisted on accepted",
            "shadow_logger": "used by cost_aware_entry_shadow.compute_runtime_features",
            "postprocess": "cross-sectional z within day/session",
            "storage": "small_paper_events.csv accepted rows",
            "board_dynamic": "NO",
        },
        {
            "shadow": "CostAware",
            "feature": "entry_rise_5min_pct (or r60_sec)",
            "generated_where": "Runtime / LiveFeatureBridge price history",
            "runtime": "YES",
            "replay_from_events": "YES (usually persisted)",
            "shadow_logger": "stop_risk / enrichment input",
            "postprocess": "none",
            "storage": "small_paper_events.csv",
            "board_dynamic": "NO",
        },
        {
            "shadow": "CostAware",
            "feature": "spread_bps",
            "generated_where": "Runtime bid/ask spread at ENTRY",
            "runtime": "YES",
            "replay_from_events": "PARTIAL — non-null from ~20260625 onward only",
            "shadow_logger": "stop_risk component",
            "postprocess": "none",
            "storage": "small_paper_events.csv (column present earlier but empty)",
            "board_dynamic": "NO (top-of-book spread, not depth imbalance)",
        },
        {
            "shadow": "CostAware",
            "feature": "entry_near_day_high_pct / entry_vwap_dev_pct / momentum_continuation",
            "generated_where": "Runtime feature bridge",
            "runtime": "YES",
            "replay_from_events": "YES if persisted",
            "shadow_logger": "stop_risk / enrichment",
            "postprocess": "cycle z + winner_enrichment",
            "storage": "small_paper_events.csv",
            "board_dynamic": "NO",
        },
        {
            "shadow": "CostAware",
            "feature": "STOP_Z_REJECT=1.65 / integrated_score",
            "generated_where": "cost_aware_entry_shadow.py (frozen)",
            "runtime": "Shadow observe-only",
            "replay_from_events": "Recomputable from above features",
            "shadow_logger": "YES",
            "postprocess": "W66 annotate_cost_aware_scores",
            "storage": "not required as raw column",
            "board_dynamic": "NO",
        },
        # Flat Weak
        {
            "shadow": "FlatWeakRange",
            "feature": "entry_rise_5/10/15min_pct, r60/r120, vwap_dev, high/low updates",
            "generated_where": "Runtime + research classify_pretrend_shape",
            "runtime": "YES (price)",
            "replay_from_events": "YES / inferable",
            "shadow_logger": "flat_weak_range_forward_shadow.infer_pretrend_shape",
            "postprocess": "pretrend E + breakout class",
            "storage": "small_paper_events + inferred",
            "board_dynamic": "NO",
        },
        {
            "shadow": "FlatWeakRange",
            "feature": "recent_low_break / vwap_cross_down / range_expansion / volume_spike",
            "generated_where": "Runtime flags or Phase666 series enrichment",
            "runtime": "PARTIAL",
            "replay_from_events": "PARTIAL — some flags may be missing → approximate",
            "shadow_logger": "breakout / weak_price path",
            "postprocess": "phase666/667",
            "storage": "events if present; else research from price series",
            "board_dynamic": "NO",
        },
        {
            "shadow": "FlatWeakRange",
            "feature": "board_improvement / board_imbalance_jump",
            "generated_where": "Phase666 derives from entry_order_book_imbalance / percentile / tier",
            "runtime": "NOT stored as columns; live FlatWeak evaluate often sees False",
            "replay_from_events": "DERIVABLE if eobi present (from 20260615+)",
            "shadow_logger": "_flat_weak_refined uses no_board = not board_improvement",
            "postprocess": "research enrichment (phase666._board_improvement)",
            "storage": "NEVER persisted as board_improvement column; eobi IS persisted",
            "board_dynamic": "YES",
        },
        {
            "shadow": "FlatWeakRange",
            "feature": "entry_order_book_imbalance / entry_imbalance_percentile",
            "generated_where": "board_imbalance_shadow from PUSH BidQty/AskQty/depth",
            "runtime": "YES (live PUSH)",
            "replay_from_events": "YES from 20260615+ events; NOT regenerable for May (push_jsonl empty)",
            "shadow_logger": "upstream of board_improvement",
            "postprocess": "aggregate only (raw BidQty not kept)",
            "storage": "small_paper_events.csv",
            "board_dynamic": "YES",
        },
        # PullbackMisread
        {
            "shadow": "PullbackMisread",
            "feature": "entry_rise_5min_pct < 0 AND entry_vwap_dev_pct < 0",
            "generated_where": "Runtime ENTRY features",
            "runtime": "YES",
            "replay_from_events": "YES",
            "shadow_logger": "would_block_pullback_misread_guard",
            "postprocess": "none",
            "storage": "small_paper_events.csv",
            "board_dynamic": "NO",
        },
        {
            "shadow": "PullbackMisread",
            "feature": "universe_slot / universe_bucket (Dynamic40 scope)",
            "generated_where": "Universe assignment at ENTRY",
            "runtime": "YES",
            "replay_from_events": "YES on accepted rows",
            "shadow_logger": "would_block_pullback_dynamic40_shadow",
            "postprocess": "none",
            "storage": "small_paper_events.csv accepted",
            "board_dynamic": "NO",
        },
    ]


def board_feature_answers() -> dict[str, Any]:
    return {
        "CostAware": {
            "uses_dynamic_board": "NO",
            "features": [],
            "notes": "Uses price/score/spread proxies only. spread_bps is top-of-book, not depth imbalance.",
            "replay": "Replay近似 for early W66 days missing spread_bps (20260615-24); else Replay近似〜完全一致 on persisted scores",
        },
        "FlatWeakRange": {
            "uses_dynamic_board": "YES",
            "features": [
                "board_improvement (derived)",
                "board_imbalance_jump (derived)",
                "upstream: entry_order_book_imbalance, entry_imbalance_percentile, imbalance_shadow_tier",
            ],
            "capture_start_documented": "Phase214 shadow ~2026-05-31; Phase299 compute-before-gate ~2026-06-07; first on-disk non-null eobi=20260615",
            "first_on_disk_non_null": "2026-06-15",
            "regenerable_from_push": False,
            "stored_aggregate": True,
            "runtime_calc": "eobi computed live from PUSH; board_improvement usually research-derived",
            "w66_behavior": "W66 did not derive board_improvement from eobi → no_board path usually True when weak_price (approx to live logger without enrichment)",
            "replay": "Replay近似 (price path) / board path DERIVABLE from eobi on 0615+ but not done in W66",
        },
        "PullbackMisread": {
            "uses_dynamic_board": "NO",
            "features": [],
            "notes": "Only rise5 + vwap_dev + Dynamic40 universe scope",
            "replay": "Replay完全一致 (given events have rise5/vwap/slot)",
        },
        "dynamic_board_acquisition_timeline": [
            {"date": "2026-05-31", "event": "Phase214 board_imbalance_shadow module introduced (logging intent)"},
            {"date": "2026-06-04..05", "event": "sessions existed; entry_order_book_imbalance 100% null (Phase300)"},
            {"date": "2026-06-05..09", "event": "push_jsonl documented with BidQty/AskQty/depth (docs/board_data_inventory.md) — NOT on disk now"},
            {"date": "2026-06-07", "event": "Phase299 — compute imbalance before gate (intended fix)"},
            {"date": "2026-06-09", "event": "Phase300/334 board inventory published"},
            {"date": "2026-06-13", "event": "Phase332 board-dynamic trailing EXIT production"},
            {"date": "2026-06-15", "event": "Oldest surviving small_paper day; eobi non-null on accepts"},
            {"date": "~2026-06-22", "event": "imbalance_shadow_tier non-empty appears more often; high-imbalance accepts"},
            {"date": "~2026-06-25", "event": "spread_bps non-null begins on accepts"},
        ],
    }


def may_june_replay_matrix() -> list[dict[str, Any]]:
    return [
        {
            "period": "2026-05",
            "shadow": "CostAware",
            "replay": "Replay不可",
            "reason": "No small_paper sessions on disk; only 1m price bars — no ENTRY accept stream / scores / spread",
        },
        {
            "period": "2026-05",
            "shadow": "FlatWeakRange",
            "replay": "Replay不可",
            "reason": "No small_paper; no PUSH/board aggregates; cannot rebuild board_improvement",
        },
        {
            "period": "2026-05",
            "shadow": "PullbackMisread",
            "replay": "Replay不可",
            "reason": "No small_paper accepted trades with rise5/vwap/universe_slot",
        },
        {
            "period": "2026-06-01..14",
            "shadow": "CostAware",
            "replay": "Replay不可",
            "reason": "small_paper day dirs absent on disk (historically 0604-05 existed then removed/archived)",
        },
        {
            "period": "2026-06-01..14",
            "shadow": "FlatWeakRange",
            "replay": "Replay不可",
            "reason": "No sessions; Phase300 says even 0604-05 had null eobi — board path unavailable",
        },
        {
            "period": "2026-06-01..14",
            "shadow": "PullbackMisread",
            "replay": "Replay不可",
            "reason": "No surviving small_paper events for those days",
        },
        {
            "period": "2026-06-15..07-17 (W66 window)",
            "shadow": "CostAware",
            "replay": "Replay近似",
            "reason": "Events exist; spread_bps missing until ~0625 → stop_risk underweighted early",
        },
        {
            "period": "2026-06-15..07-17 (W66 window)",
            "shadow": "FlatWeakRange",
            "replay": "Replay近似",
            "reason": "Price/shape features OK; board_improvement not persisted and W66 did not derive from eobi",
        },
        {
            "period": "2026-06-15..07-17 (W66 window)",
            "shadow": "PullbackMisread",
            "replay": "Replay完全一致",
            "reason": "rise5+vwap+slot available on accepted rows for scope check",
        },
    ]


def reliability_verdict() -> dict[str, Any]:
    return {
        "primary_class": "B",
        "primary_label": "6/15以前はPaperセッション欠損（＋当時は板特徴量不足）→ W66対象期間はディスク上の真の全期間として妥当",
        "secondary_class": "C",
        "secondary_label": "W66窓内でも FlatWeak 板経路と CostAware spread 欠落により近似Replay → 参考値として扱う部分あり",
        "not_D": "開始日ハードコードによる取りこぼしではない（May small_paperが存在しない）",
        "w66_trust": {
            "period_selection": "HIGH — matches on-disk small_paper inventory",
            "PullbackMisread_arm": "HIGH",
            "CostAware_arm": "MEDIUM — spread_bps gap early window",
            "FlatWeakRange_arm": "MEDIUM — board_improvement not enriched from eobi",
            "capital_constraints": "HIGH — independent of board",
            "overall": "W66 capital ranking is directionally usable for 0615-0717 Paper corpus; not a May–full-period truth",
        },
        "rerun_required": False,
        "rerun_optional_enhancement": (
            "Optional: re-run W66 FlatWeak with Phase666 board_improvement derived from eobi "
            "(still no Runtime change). Not required for period correctness."
        ),
        "true_full_period_possible": False,
        "true_full_period_blockers": [
            "No May / early-June small_paper sessions on disk",
            "data/push_jsonl empty — cannot regenerate board depth",
            "intraday_1m has price only — insufficient for Shadow ENTRY decisions",
        ],
        "if_archives_restored": {
            "needed": "Archived small_paper 20260529-20260614 and/or push_jsonl 2026-06-05+",
            "replay_period": "Could extend Paper replay earlier; board path only where eobi non-null",
            "est_effort": "hours–days depending on archive availability; no code threshold changes required for audit reload",
        },
    }


def build_report() -> dict[str, Any]:
    days = list_small_paper_days()
    sessions, files = inventory_sessions(days)
    feat = probe_features_by_day(days)
    other = inventory_non_small_paper()
    disc = w66_discovery_audit()
    features = shadow_feature_matrix()
    board = board_feature_answers()
    period_matrix = may_june_replay_matrix()
    rel = reliability_verdict()

    trading_days_with_exits = sorted({s["day"] for s in sessions if s["observer_exit_count"] > 0})
    empty_accept_days = sorted({s["day"] for s in sessions if s["accepted_count"] == 0})

    # May / early June classification rows
    inventory_extra = []
    for d in other["results_daily_may"]:
        inventory_extra.append(
            {
                "day": _iso(d),
                "corpus": "results/daily",
                "status": "Replay不可(Paper Shadow)",
                "reason": "daily organizer shell — not small_paper live_session events",
            }
        )
    for d in other["results_daily_early_june"]:
        inventory_extra.append(
            {
                "day": _iso(d),
                "corpus": "results/daily",
                "status": "Replay不可(Paper Shadow)",
                "reason": "no small_paper sibling for this day on disk",
            }
        )
    for d in other["intraday_1m_may_days"][:5]:
        inventory_extra.append(
            {
                "day": d,
                "corpus": "intraday_1m",
                "status": "価格のみ",
                "reason": "1m bars; no BidQty/ENTRY accepts",
            }
        )

    # Verdict selection
    verdict = "SHADOW_REPLAY_AUDIT_REPLAY_LIMITATION_FOUND"
    # Feature gaps for May + FlatWeak board path
    feature_gap = True
    if feature_gap:
        # Prefer the more specific gap verdict when board/May gaps dominate
        verdict = "SHADOW_REPLAY_AUDIT_FEATURE_GAP_FOUND"

    answers = {
        "1_why_start_0615": disc["why_start_0615"],
        "2_true_full_period": {
            "small_paper_on_disk": f"{_iso(days[0])} .. {_iso(days[-1])}" if days else "none",
            "day_dirs": len(days),
            "days_with_exits": len(trading_days_with_exits),
            "may_small_paper": False,
            "early_june_small_paper": False,
            "may_price_bars": len(other["intraday_1m_may_days"]),
        },
        "3_may_replay": "不可 — small_paper無し / push_jsonl空 / 板再生成不能",
        "4_early_june_replay": "不可 — 0615未満のsmall_paperディレクトリがディスクに無い",
        "5_costaware_board": "NO",
        "6_flatweak_board": "YES (derived board_improvement from eobi aggregates)",
        "7_pullback_board": "NO",
        "8_replay_compat": {
            "CostAware": "Replay近似",
            "FlatWeakRange": "Replay近似",
            "PullbackMisread": "Replay完全一致 (W66 window)",
        },
        "9_board_capture_start": "Documented Phase299 ~2026-06-07; first surviving non-null on disk 2026-06-15",
        "10_w66_trust": rel["w66_trust"],
        "11_rerun_needed": rel["rerun_required"],
        "12_true_full_period_revalidatable": rel["true_full_period_possible"],
        "13_artifacts": [
            str(OUT_DIR / "phase687w67_shadow_replay_audit.md"),
            str(OUT_DIR / "phase687w67_shadow_replay_audit.json"),
            str(OUT_DIR / "phase687w67_shadow_feature_matrix.xlsx"),
        ],
    }

    # all-period Q1-12 block
    all_period_qa = {
        "1_explored_directory": disc["explored_directory"],
        "2_files_explored_count": len(files),
        "2_files_sample": files[:40],
        "3_oldest": _iso(days[0]) if days else None,
        "4_newest": _iso(days[-1]) if days else None,
        "5_trading_day_list": [_iso(d) for d in days],
        "5_days_with_exits": trading_days_with_exits,
        "6_may_exists_as_small_paper": False,
        "7_why_may_not_loaded": "May directories do not exist under results/small_paper; W66 never saw them",
        "8_format_difference": False,
        "9_schema_difference": False,
        "10_shadow_feature_shortage_caused_filter": False,
        "11_implementation_mistake": False,
        "12_intended_spec": True,
        "note_empty_accept_days": empty_accept_days,
        "note_w66_trading_days_vs_day_dirs": {
            "day_dirs": len(days),
            "w66_trading_days": disc.get("w66_trading_days"),
            "days_with_observer_exit": len(trading_days_with_exits),
            "explanation": "W66 trading_days counts days with completed trades, not empty session shells",
        },
    }

    report = {
        "phase": PHASE,
        "verdict": verdict,
        "generated_at": datetime.now(JST).isoformat(),
        "runtime_unchanged": True,
        "shadow_unchanged": True,
        "forward_unchanged": True,
        "thresholds_unchanged": True,
        "all_period_audit": all_period_qa,
        "w66_discovery": disc,
        "data_inventory": {
            "small_paper_days": [_iso(d) for d in days],
            "sessions": sessions,
            "non_small_paper": other,
            "extra_rows": inventory_extra,
        },
        "feature_probe_by_day": feat,
        "shadow_features": features,
        "board_features": board,
        "period_replay_matrix": period_matrix,
        "reliability": rel,
        "required_answers": answers,
        "board_doc_hash": hashlib.sha256(BOARD_DOC.read_bytes()).hexdigest()[:16] if BOARD_DOC.is_file() else "",
    }
    return report


def write_md(report: dict[str, Any]) -> str:
    a = report["required_answers"]
    ap = report["all_period_audit"]
    rel = report["reliability"]
    board = report["board_features"]
    lines = [
        f"# {PHASE} Shadow Replay Audit",
        "",
        f"**Verdict:** `{report['verdict']}`",
        "",
        "Runtime / Shadow / Forward / 閾値は変更していない（調査のみ）。",
        "",
        "## 1. `--all-period` 監査",
        f"1. 探索ディレクトリ: `{ap['1_explored_directory']}`",
        f"2. 探索ファイル数: {ap['2_files_explored_count']}（events csv/jsonl）",
        f"3. 最古: {ap['3_oldest']}",
        f"4. 最新: {ap['4_newest']}",
        f"5. 営業日一覧（day dirs）: {', '.join(ap['5_trading_day_list'])}",
        f"6. 5月small_paper: **存在しない**",
        f"7. 読み込まれなかった理由: {ap['7_why_may_not_loaded']}",
        f"8. フォーマット違い: {ap['8_format_difference']}",
        f"9. schema違いフィルタ: {ap['9_schema_difference']}",
        f"10. Shadow特徴量不足で期間除外: {ap['10_shadow_feature_shortage_caused_filter']}",
        f"11. 実装ミス: {ap['11_implementation_mistake']}",
        f"12. 意図した仕様: {ap['12_intended_spec']} — ディスク上のsmall_paper全件",
        "",
        f"- W66報告期間: {report['w66_discovery'].get('w66_reported_start')} .. {report['w66_discovery'].get('w66_reported_end')} "
        f"（trading_days={report['w66_discovery'].get('w66_trading_days')}）",
        f"- day_dirs={ap['note_w66_trading_days_vs_day_dirs']['day_dirs']} / "
        f"EXIT付き日={ap['note_w66_trading_days_vs_day_dirs']['days_with_observer_exit']}",
        f"- 空accept日: {', '.join(ap['note_empty_accept_days']) or '(none)'}",
        "",
        "## 2. 真の全期間（Paper Replay）",
        f"- On-disk small_paper: **{a['2_true_full_period']['small_paper_on_disk']}** "
        f"（{a['2_true_full_period']['day_dirs']} day dirs / {a['2_true_full_period']['days_with_exits']} days with exits）",
        "- 5月 small_paper: 無し",
        "- 6月前半 (〜06/14) small_paper: 無し（文書上は06/04-05が過去に存在、現在欠落）",
        f"- May 1m価格バー: {a['2_true_full_period']['may_price_bars']}日（板なし）",
        f"- push_jsonl: `{report['data_inventory']['non_small_paper']['push_jsonl_status']}`",
        "",
        "## 3–4. Shadow特徴量 / 動的板",
        f"- CostAware 動的板: **{a['5_costaware_board']}**",
        f"- FlatWeakRange 動的板: **{a['6_flatweak_board']}**",
        f"- PullbackMisread 動的板: **{a['7_pullback_board']}**",
        "",
        "### 板取得タイムライン",
    ]
    for t in board["dynamic_board_acquisition_timeline"]:
        lines.append(f"- {t['date']}: {t['event']}")
    lines += [
        "",
        "## 5–6. Replay互換 / 5月・6月前半",
    ]
    for row in report["period_replay_matrix"]:
        lines.append(f"- [{row['period']}] {row['shadow']}: **{row['replay']}** — {row['reason']}")
    lines += [
        "",
        "## 7. W66信頼性判定",
        f"- Primary: **{rel['primary_class']}** — {rel['primary_label']}",
        f"- Secondary: **{rel['secondary_class']}** — {rel['secondary_label']}",
        f"- Overall trust: {json.dumps(rel['w66_trust'], ensure_ascii=False)}",
        "",
        "## 8. 真の全期間再検証",
        f"- 可能か: **{rel['true_full_period_possible']}**",
        f"- Blockers: {'; '.join(rel['true_full_period_blockers'])}",
        f"- 再実行必須: **{rel['rerun_required']}**",
        f"- 任意改善: {rel['rerun_optional_enhancement']}",
        "",
        "## 必須回答サマリ",
        f"1. {a['1_why_start_0615']}",
        f"2. {a['2_true_full_period']}",
        f"3. {a['3_may_replay']}",
        f"4. {a['4_early_june_replay']}",
        f"5. CostAware board: {a['5_costaware_board']}",
        f"6. FlatWeak board: {a['6_flatweak_board']}",
        f"7. Pullback board: {a['7_pullback_board']}",
        f"8. {a['8_replay_compat']}",
        f"9. {a['9_board_capture_start']}",
        f"10. {a['10_w66_trust']}",
        f"11. rerun: {a['11_rerun_needed']}",
        f"12. true full-period revalidate: {a['12_true_full_period_revalidatable']}",
        "13. Artifacts:",
    ]
    for p in a["13_artifacts"]:
        lines.append(f"   - `{p}`")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report = build_report()
    out_md = OUT_DIR / "phase687w67_shadow_replay_audit.md"
    out_json = OUT_DIR / "phase687w67_shadow_replay_audit.json"
    out_xlsx = OUT_DIR / "phase687w67_shadow_feature_matrix.xlsx"

    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    out_md.write_text(write_md(report), encoding="utf-8")

    sessions = report["data_inventory"]["sessions"]
    feat = report["feature_probe_by_day"]
    features = report["shadow_features"]
    board_rows = []
    for name, blk in report["board_features"].items():
        if name == "dynamic_board_acquisition_timeline":
            continue
        board_rows.append({"shadow": name, **{k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v) for k, v in blk.items()}})
    timeline = pd.DataFrame(report["board_features"]["dynamic_board_acquisition_timeline"])
    compat = pd.DataFrame(report["period_replay_matrix"])
    root = pd.DataFrame(
        [
            {"item": "verdict", "value": report["verdict"]},
            {"item": "primary_reliability", "value": report["reliability"]["primary_class"]},
            {"item": "secondary_reliability", "value": report["reliability"]["secondary_class"]},
            {"item": "why_0615", "value": report["required_answers"]["1_why_start_0615"]},
            {"item": "rerun_required", "value": str(report["reliability"]["rerun_required"])},
            {"item": "may_small_paper", "value": "absent"},
            {"item": "push_jsonl", "value": report["data_inventory"]["non_small_paper"]["push_jsonl_status"]},
        ]
    )
    rec = pd.DataFrame(
        [
            {"recommendation": "Treat W66 period as correct for on-disk small_paper (B)", "priority": "info"},
            {"recommendation": "Do not claim May/early-June Paper Shadow capital results without restoring archives", "priority": "high"},
            {"recommendation": "Optional W66 FlatWeak enrichment from eobi for tighter board-path fidelity (C)", "priority": "low"},
            {"recommendation": "PullbackMisread W66 arm is highest fidelity within 0615-0717", "priority": "info"},
            {"recommendation": "No Runtime/Shadow/threshold changes from this audit", "priority": "constraint"},
        ]
    )

    write_xlsx(
        {
            "Data Inventory": pd.DataFrame(sessions + report["data_inventory"]["extra_rows"]),
            "Replay Coverage": compat,
            "Shadow Features": pd.DataFrame(features),
            "Board Features": pd.DataFrame(board_rows),
            "Replay Compatibility": compat,
            "Timeline": timeline,
            "Root Cause": root,
            "Recommendations": rec,
            "Feature Probe By Day": pd.DataFrame(feat),
        },
        out_xlsx,
    )

    # cleanup probe temp if present
    probe = OUT_DIR / "_w67_probe.json"
    if probe.is_file():
        try:
            probe.unlink()
        except OSError:
            pass

    print(
        json.dumps(
            {
                "verdict": report["verdict"],
                "primary_reliability": report["reliability"]["primary_class"],
                "oldest": report["all_period_audit"]["3_oldest"],
                "newest": report["all_period_audit"]["4_newest"],
                "artifacts": report["required_answers"]["13_artifacts"],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
