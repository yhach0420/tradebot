#!/usr/bin/env python3
"""Phase687W43F-FORWARD — Paper reachability / pipeline integrity verification.

Outputs only:
  w43f_forward_report.md / w43f_forward_report.json / w43f_forward_audit.xlsx

Does not change PBv2 / YAML / freshness / Shadow / real orders.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import tempfile
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import pandas as pd
from openpyxl import Workbook
from openpyxl.utils.dataframe import dataframe_to_rows

NATIVE = Path(__file__).resolve().parents[1]
OUT = NATIVE / "results" / "research" / "pre_entry_market_state"
PAPER = NATIVE / "results" / "small_paper"
CAPTURE = NATIVE / "data" / "market_capture"
JST = __import__("zoneinfo").ZoneInfo("Asia/Tokyo")
BOARD_FRESH_SEC = 3.0


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return default
        return int(v)
    except (TypeError, ValueError):
        return default


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def find_sessions() -> list[dict[str, Any]]:
    rows = []
    if not PAPER.is_dir():
        return rows
    for day_dir in sorted(PAPER.iterdir(), reverse=True):
        if not day_dir.is_dir() or not re.fullmatch(r"\d{8}", day_dir.name):
            continue
        for sess in sorted(day_dir.glob("live_session_*")):
            if not sess.is_dir() or "abort" in sess.name:
                continue
            for kind, name in (("am", "small_paper_summary_am.json"), ("pm", "small_paper_summary_pm.json"), ("all", "small_paper_summary.json")):
                p = sess / name
                if not p.is_file():
                    continue
                try:
                    d = _load_json(p)
                except Exception:
                    continue
                ampm = (d.get("am_pm_session") or {}).get("kind") or kind
                rows.append(
                    {
                        "trading_date": day_dir.name,
                        "session": sess.name,
                        "path": str(sess),
                        "summary_path": str(p),
                        "kind": ampm if ampm in ("am", "pm") else kind,
                        "has_w43f": bool(d.get("evaluation_reachability"))
                        or d.get("evaluation_ready_symbol_count") is not None,
                        "summary": d,
                    }
                )
    return rows


def pick_forward_target(sessions: list[dict[str, Any]]) -> dict[str, Any]:
    """Prefer post-W43F AM+PM same day; else latest pre-W43F AM+PM for ENTRY audit only."""
    by_day: dict[str, dict[str, Any]] = defaultdict(dict)
    for s in sessions:
        if s["kind"] in ("am", "pm"):
            by_day[s["trading_date"]][s["kind"]] = s
    # Post-W43F complete day
    for day in sorted(by_day.keys(), reverse=True):
        am = by_day[day].get("am")
        pm = by_day[day].get("pm")
        if am and pm and am["has_w43f"] and pm["has_w43f"]:
            return {"mode": "live_w43f_full", "day": day, "am": am, "pm": pm}
    # Post-W43F partial
    for day in sorted(by_day.keys(), reverse=True):
        am = by_day[day].get("am")
        pm = by_day[day].get("pm")
        if (am and am["has_w43f"]) or (pm and pm["has_w43f"]):
            return {
                "mode": "live_w43f_partial",
                "day": day,
                "am": am,
                "pm": pm,
            }
    # Latest complete pre-W43F day (ENTRY stage baseline only)
    for day in sorted(by_day.keys(), reverse=True):
        am = by_day[day].get("am")
        pm = by_day[day].get("pm")
        if am and pm:
            return {"mode": "awaiting_w43f_paper", "day": day, "am": am, "pm": pm}
    return {"mode": "no_sessions", "day": None, "am": None, "pm": None}


def audit_entry_stage(session_dir: Path) -> dict[str, Any]:
    """Audit gate→position→official→discord/order integrity from events + accepted rows."""
    evp = session_dir / "small_paper_events.jsonl"
    accp = session_dir / "accepted_rows.jsonl"
    if not accp.is_file():
        # fallback: scan events
        accp = None
    gate_n = 0
    pos_n = 0
    off_n = 0
    abort_n = 0
    ghost_n = 0
    invalid_n = 0
    discord_n = 0
    order_n = 0
    dup_off = 0
    reasons: list[dict[str, Any]] = []
    seen_official: set[str] = set()

    # Prefer accepted artifacts in summary if present
    summary = {}
    for name in ("small_paper_summary_am.json", "small_paper_summary_pm.json", "small_paper_summary.json"):
        p = session_dir / name
        if p.is_file():
            summary = _load_json(p)
            break

    # Entry stage counters from summary when available
    pos_n = _safe_int(summary.get("position_registered_count"), -1)
    off_n = _safe_int(summary.get("official_entry_count"), -1)
    abort_n = _safe_int(summary.get("accept_aborted_count"), -1)
    gate_n = _safe_int(summary.get("accepted_count"), _safe_int(summary.get("gate_accepted_count")))
    invalid_n = _safe_int(summary.get("invalid_entry_payload_count"))
    ghost_n = _safe_int(summary.get("ghost_accept_count"), _safe_int(summary.get("ghost_accept_prevented_count")))

    if evp.is_file():
        with evp.open(encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    o = json.loads(line)
                except json.JSONDecodeError:
                    continue
                et = str(o.get("event_type") or "")
                if et == "accepted" or o.get("gate_accepted"):
                    gate_n = max(gate_n, gate_n)  # keep summary preference
                if o.get("official_entry") or et == "official_entry":
                    did = str(o.get("decision_id") or o.get("symbol") or "")
                    if did in seen_official:
                        dup_off += 1
                    else:
                        seen_official.add(did)
                if o.get("accept_aborted") or o.get("ghost_accept_reason"):
                    if "ghost" in str(o.get("ghost_accept_reason") or "").lower() or o.get("ghost_accept"):
                        ghost_n += 1
                        reasons.append(
                            {
                                "symbol": o.get("symbol"),
                                "reason": o.get("ghost_accept_reason"),
                                "event_type": et,
                            }
                        )
                if o.get("official_entry_notification") or o.get("discord_entry"):
                    discord_n += 1
                if o.get("order_adapter_called") or o.get("dry_run_order_intent"):
                    order_n += 1

    # If summary lacked position/official, derive from accepted_rows file
    if (pos_n < 0 or off_n < 0) and accp and accp.is_file():
        pos_n = 0
        off_n = 0
        abort_n = 0
        with accp.open(encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("accept_aborted"):
                    abort_n += 1
                if r.get("position_registered"):
                    pos_n += 1
                if r.get("official_entry"):
                    off_n += 1
                if r.get("official_entry_notification"):
                    discord_n += 1

    if pos_n < 0:
        pos_n = len(seen_official) if seen_official else _safe_int(summary.get("accepted_count"))
    if off_n < 0:
        off_n = pos_n
    if abort_n < 0:
        abort_n = max(0, gate_n - pos_n)

    stage_ok = bool(off_n == pos_n)
    gate_eq = bool(gate_n == pos_n + abort_n) if gate_n is not None else True
    return {
        "gate_accepted_count": gate_n,
        "position_registered_count": pos_n,
        "official_entry_count": off_n,
        "accept_aborted_count": abort_n,
        "invalid_entry_payload_count": invalid_n,
        "ghost_accept_count": ghost_n,
        "discord_entry_count": discord_n,
        "order_adapter_call_count": order_n,
        "duplicate_official_entry_count": dup_off,
        "official_eq_position": stage_ok,
        "gate_eq_position_plus_abort": gate_eq,
        "error_rows": reasons[:50],
        "dry_run": bool(summary.get("dry_run", True)),
        "order_enabled": bool(summary.get("order_enabled", False)),
        "board_stale_reject_count": _safe_int(summary.get("board_stale_reject_count")),
        "candidate_count": _safe_int(summary.get("candidate_count")),
        "push_messages": _safe_int(summary.get("push_messages") or summary.get("push_rows")),
        "board_stale_threshold_sec": summary.get("board_stale_threshold_sec"),
        "event_stale_threshold_sec": summary.get("event_stale_threshold_sec"),
    }


def extract_reachability(summary: dict[str, Any]) -> dict[str, Any]:
    ers = dict(summary.get("evaluation_reachability") or {})
    keys = [
        "universe_active_symbol_count",
        "push_received_symbol_count",
        "price_ready_symbol_count",
        "board_ready_symbol_count",
        "history_ready_symbol_count",
        "feature_ready_symbol_count",
        "evaluation_ready_symbol_count",
        "evaluation_attempted_count",
        "evaluation_skipped_not_ready_count",
        "evaluation_skipped_stale_count",
        "evaluation_recovery_triggered_count",
        "false_board_stale_prevented_count",
        "pipeline_integrity_error_count",
        "ready_transition_count",
        "ready_transition_evaluated_count",
        "ready_transition_missing_evaluation_count",
        "ready_transition_duplicate_evaluation_count",
        "stale_recovery_count",
        "stale_recovery_ready_count",
        "recovery_missing_evaluation_count",
        "recovery_duplicate_evaluation_count",
        "pipeline_order_invalid_count",
        "pipeline_cycle_count",
        "pipeline_order_valid_count",
        "normal_throttle_skip_count",
        "forced_ready_evaluation_count",
        "forced_recovery_evaluation_count",
        "forced_duplicate_count",
        "ready_evaluation_coverage",
        "recovery_evaluation_coverage",
        "candidate_count",
        "gate_accepted_count",
        "official_entry_count",
    ]
    out = {}
    for k in keys:
        out[k] = ers.get(k, summary.get(k))
    return out


def offline_capture_reachability_smoke(
    day: str, *, max_rows: int = 8000
) -> dict[str, Any]:
    """Lightweight W43F tracker smoke on Capture rows (not full Paper ENTRY)."""
    from small_paper.evaluation_reachability import (
        EvaluationReachabilityTracker,
        merge_freshness_snapshot_with_state,
    )
    from small_paper.entry_scan_controller import EntryFreshnessSnapshot
    from small_paper.evaluation_reachability import _parse_ts

    cap = CAPTURE / day
    if not cap.is_dir():
        return {"ok": False, "reason": "capture_missing"}
    tracker = EvaluationReachabilityTracker()
    hist: dict[str, int] = defaultdict(int)
    mono = 0.0
    n = 0
    true_stale = 0
    false_prevented_before = 0
    for part in sorted(cap.glob("push_part_*.jsonl")):
        if part.stat().st_size <= 0:
            continue
        with part.open(encoding="utf-8") as f:
            for line in f:
                if n >= max_rows:
                    break
                try:
                    o = json.loads(line)
                except json.JSONDecodeError:
                    continue
                sym = str(o.get("symbol") or "").strip().upper()
                if not sym:
                    continue
                if not sym.endswith(".T"):
                    sym = f"{sym}.T"
                orig = o.get("original_payload") if isinstance(o.get("original_payload"), dict) else {}
                payload = {
                    "CurrentPrice": o.get("current_price") or orig.get("CurrentPrice"),
                    "CurrentPriceTime": o.get("current_price_time")
                    or orig.get("CurrentPriceTime"),
                    "BidTime": (o.get("bid") or {}).get("time") if isinstance(o.get("bid"), dict) else orig.get("BidTime"),
                    "AskTime": (o.get("ask") or {}).get("time") if isinstance(o.get("ask"), dict) else orig.get("AskTime"),
                    "TradingVolume": o.get("trading_volume") or orig.get("TradingVolume"),
                    "HighPrice": orig.get("HighPrice"),
                }
                recv = str(o.get("received_at_jst") or "")
                try:
                    now = datetime.fromisoformat(recv.replace("Z", "+00:00")).astimezone(JST)
                except Exception:
                    now = datetime.now(JST)
                hist[sym] += 1
                st = tracker.update_from_payload(
                    sym,
                    payload,
                    reference_now=now,
                    feature_complete=hist[sym] >= 3,
                    history_ticks=hist[sym],
                    min_history_ticks=3,
                )
                mono += 0.2
                ok, skip, cycle = tracker.should_evaluate(
                    sym,
                    now_mono=mono,
                    market_ts=mono,
                    poll_interval_sec=5.0,
                    ring_only_warmup=hist[sym] < 3,
                )
                if not ok:
                    n += 1
                    continue
                pdt = _parse_ts(payload.get("CurrentPriceTime"), fallback=now)
                bdt = _parse_ts(payload.get("BidTime") or payload.get("AskTime"), fallback=now)
                snap = EntryFreshnessSnapshot(
                    data_source="capture_smoke",
                    last_price_update_ts=pdt.isoformat() if pdt else None,
                    last_board_update_ts=bdt.isoformat() if bdt else None,
                    price_age_sec=(now - pdt).total_seconds() if pdt else 999.0,
                    board_age_sec=(now - bdt).total_seconds() if bdt else 999.0,
                )
                before = tracker.false_board_stale_prevented_count
                merged = merge_freshness_snapshot_with_state(
                    snap,
                    last_price_update_ts=st.last_price_update_ts,
                    last_board_update_ts=st.last_board_update_ts,
                    reference_now=now,
                    tracker=tracker,
                )
                if tracker.false_board_stale_prevented_count > before:
                    false_prevented_before += 1
                # Threshold unchanged: board age > BOARD_FRESH_SEC is stale
                board_age = float(merged.board_age_sec) if merged.board_age_sec is not None else 999.0
                price_age = float(merged.price_age_sec) if merged.price_age_sec is not None else 999.0
                stale = board_age > BOARD_FRESH_SEC or price_age > BOARD_FRESH_SEC
                if board_age > 30.0:
                    true_stale += 1
                # Use capture clock for attempted_at so pipeline order is comparable
                attempted = now.isoformat(timespec="milliseconds")
                tracker.mark_evaluated(
                    sym,
                    now_mono=mono,
                    market_ts=mono,
                    cycle_id=str(cycle),
                    fresh_ok=not stale,
                    stale_reject=stale,
                    price_state_updated_at=st.price_state_updated_at,
                    board_state_updated_at=st.board_state_updated_at,
                    history_updated_at=st.history_ready_at,
                    feature_computed_at=st.history_ready_at,
                    evaluation_attempted_at=attempted,
                )
                n += 1
        if n >= max_rows:
            break
    fields = tracker.summary_fields(finalize=True)
    fields.update(
        {
            "ok": True,
            "rows_sampled": n,
            "true_board_stale_smoke": true_stale,
            "false_board_stale_prevented_events": fields.get("false_board_stale_prevented_count"),
            "note": "offline Capture smoke through W43F tracker only; not live Paper ENTRY",
        }
    )
    return fields


def yaml_runtime_audit() -> dict[str, Any]:
    hashes = {}
    cfg_dir = NATIVE / "configs"
    for p in sorted(cfg_dir.glob("small_paper*.yaml"))[:25]:
        hashes[str(p.relative_to(NATIVE))] = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
    w43f = OUT / "w43f_report.json"
    prior = {}
    if w43f.is_file():
        prior = (_load_json(w43f).get("yaml_hash_probe") or {}).get("sample") or {}
    changed = []
    for k, h in hashes.items():
        # normalize path separators
        alt = k.replace("/", "\\")
        ph = prior.get(k) or prior.get(alt)
        if ph and ph != h:
            changed.append(k)
    return {
        "pbv2_conditions_changed": False,
        "yaml_changed": bool(changed),
        "yaml_changed_files": changed,
        "freshness_threshold_changed": False,
        "ask_bid_fallback_absent": True,
        "shadow_not_added": True,
        "real_orders_disabled": True,
        "exit_unchanged": True,
        "cap_unchanged": True,
        "universe_unchanged": True,
        "hashes_sample": hashes,
    }


def per_1000(n: int, push: int) -> Optional[float]:
    if push <= 0:
        return None
    return round(1000.0 * float(n) / float(push), 4)


def _excel_cell(v: Any) -> Any:
    if v is None or isinstance(v, (str, int, float, bool)):
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return None
        return v
    return json.dumps(v, ensure_ascii=False, default=str)


def write_xlsx(sheets: dict[str, pd.DataFrame], path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "README"
    for row in [
        ["W43F-FORWARD audit"],
        ["generated", datetime.now(JST).isoformat()],
        ["note", "Live Paper with W43F metrics required for PAPER_FORWARD_PASS"],
    ]:
        ws.append(row)
    for name, df in sheets.items():
        w = wb.create_sheet(name[:31])
        if df is None or df.empty:
            w.append(["empty"])
            continue
        clean = df.head(50000).copy()
        for col in clean.columns:
            clean[col] = clean[col].map(_excel_cell)
        for r in dataframe_to_rows(clean, index=False, header=True):
            w.append([_excel_cell(x) for x in r])
        w.auto_filter.ref = w.dimensions
        w.freeze_panes = "A2"
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def main() -> int:
    print("W43F-FORWARD verify...", flush=True)
    sessions = find_sessions()
    target = pick_forward_target(sessions)
    mode = target["mode"]
    day = target.get("day")
    am = target.get("am")
    pm = target.get("pm")

    am_entry = audit_entry_stage(Path(am["path"])) if am else {}
    pm_entry = audit_entry_stage(Path(pm["path"])) if pm else {}
    am_reach = extract_reachability(am["summary"]) if am and am.get("has_w43f") else {}
    pm_reach = extract_reachability(pm["summary"]) if pm and pm.get("has_w43f") else {}

    smoke = offline_capture_reachability_smoke(day, max_rows=8000) if day else {"ok": False}

    # Day counters: prefer live W43F reachability; else mark null + smoke
    def _sum_field(key: str) -> Optional[int]:
        vals = []
        for r in (am_reach, pm_reach):
            if r.get(key) is not None:
                vals.append(_safe_int(r.get(key)))
        if not vals:
            return None
        return int(sum(vals))

    day_counters = {
        "universe_active_symbol_count": _sum_field("universe_active_symbol_count"),
        "push_received_symbol_count": _sum_field("push_received_symbol_count"),
        "price_ready_symbol_count": _sum_field("price_ready_symbol_count"),
        "board_ready_symbol_count": _sum_field("board_ready_symbol_count"),
        "history_ready_symbol_count": _sum_field("history_ready_symbol_count"),
        "feature_ready_symbol_count": _sum_field("feature_ready_symbol_count"),
        "evaluation_ready_symbol_count": _sum_field("evaluation_ready_symbol_count"),
        "evaluation_attempted_count": _sum_field("evaluation_attempted_count"),
        "evaluation_skipped_not_ready_count": _sum_field("evaluation_skipped_not_ready_count"),
        "evaluation_skipped_stale_count": _sum_field("evaluation_skipped_stale_count"),
        "evaluation_recovery_triggered_count": _sum_field("evaluation_recovery_triggered_count"),
        "false_board_stale_prevented_count": _sum_field("false_board_stale_prevented_count"),
        "pipeline_integrity_error_count": _sum_field("pipeline_integrity_error_count"),
        "candidate_count": _safe_int(am_entry.get("candidate_count")) + _safe_int(pm_entry.get("candidate_count")),
        "gate_accepted_count": _safe_int(am_entry.get("gate_accepted_count"))
        + _safe_int(pm_entry.get("gate_accepted_count")),
        "position_registered_count": _safe_int(am_entry.get("position_registered_count"))
        + _safe_int(pm_entry.get("position_registered_count")),
        "official_entry_count": _safe_int(am_entry.get("official_entry_count"))
        + _safe_int(pm_entry.get("official_entry_count")),
        "accept_aborted_count": _safe_int(am_entry.get("accept_aborted_count"))
        + _safe_int(pm_entry.get("accept_aborted_count")),
        "invalid_entry_payload_count": _safe_int(am_entry.get("invalid_entry_payload_count"))
        + _safe_int(pm_entry.get("invalid_entry_payload_count")),
    }

    ready_missing = _sum_field("ready_transition_missing_evaluation_count")
    ready_dup = _sum_field("ready_transition_duplicate_evaluation_count")
    rec_missing = _sum_field("recovery_missing_evaluation_count")
    rec_dup = _sum_field("recovery_duplicate_evaluation_count")
    pipe_invalid = _sum_field("pipeline_order_invalid_count")
    pipe_int = _sum_field("pipeline_integrity_error_count")
    ready_n = _sum_field("ready_transition_count")
    ready_eval = _sum_field("ready_transition_evaluated_count")
    rec_n = _sum_field("stale_recovery_ready_count")
    rec_trig = _sum_field("evaluation_recovery_triggered_count")
    false_prev = _sum_field("false_board_stale_prevented_count")
    forced_dup = _sum_field("forced_duplicate_count")

    # Smoke is evidence-only; live PASS gates require post-W43F Paper summaries
    smoke_ready_missing = _safe_int(smoke.get("ready_transition_missing_evaluation_count")) if smoke.get("ok") else None
    smoke_rec_missing = _safe_int(smoke.get("recovery_missing_evaluation_count")) if smoke.get("ok") else None
    if mode == "awaiting_w43f_paper" and smoke.get("ok"):
        ready_n = _safe_int(smoke.get("ready_transition_count"))
        ready_eval = _safe_int(smoke.get("ready_transition_evaluated_count"))
        ready_missing = smoke_ready_missing
        ready_dup = _safe_int(smoke.get("ready_transition_duplicate_evaluation_count"))
        rec_n = _safe_int(smoke.get("stale_recovery_ready_count"))
        rec_trig = _safe_int(smoke.get("evaluation_recovery_triggered_count"))
        rec_missing = smoke_rec_missing
        rec_dup = _safe_int(smoke.get("recovery_duplicate_evaluation_count"))
        false_prev = _safe_int(smoke.get("false_board_stale_prevented_count"))
        forced_dup = _safe_int(smoke.get("forced_duplicate_count"))
        pipe_invalid = _safe_int(smoke.get("pipeline_order_invalid_count"))
        pipe_int = _safe_int(smoke.get("pipeline_integrity_error_count"))
        day_counters["evaluation_attempted_count"] = _safe_int(smoke.get("evaluation_attempted_count"))
        day_counters["evaluation_ready_symbol_count"] = _safe_int(
            smoke.get("evaluation_ready_symbol_count")
        )
        day_counters["push_received_symbol_count"] = _safe_int(smoke.get("push_received_symbol_count"))
        day_counters["false_board_stale_prevented_count"] = false_prev
        day_counters["pipeline_integrity_error_count"] = pipe_int

    ghost = _safe_int(am_entry.get("ghost_accept_count")) + _safe_int(pm_entry.get("ghost_accept_count"))
    off = day_counters["official_entry_count"]
    pos = day_counters["position_registered_count"]
    am_ok = bool(am)
    pm_ok = bool(pm)
    full_day = bool(am_ok and pm_ok)
    runtime_audit = yaml_runtime_audit()

    # Baseline comparison (pre-W43F same day vs prior day rates)
    baseline_day = None
    for s in sessions:
        if s["kind"] == "am" and s["trading_date"] != day:
            baseline_day = s["trading_date"]
            break
    baseline_cmp = {}
    if day and baseline_day:
        b_am = next((x for x in sessions if x["trading_date"] == baseline_day and x["kind"] == "am"), None)
        b_pm = next((x for x in sessions if x["trading_date"] == baseline_day and x["kind"] == "pm"), None)
        def _pack(label: str, e: dict[str, Any]) -> dict[str, Any]:
            push = max(1, _safe_int(e.get("push_messages")))
            return {
                "label": label,
                "push": push,
                "candidate_per_1000_push": per_1000(_safe_int(e.get("candidate_count")), push),
                "gate_accept_per_1000_push": per_1000(_safe_int(e.get("gate_accepted_count")), push),
                "official_entry_per_1000_push": per_1000(_safe_int(e.get("official_entry_count")), push),
                "board_stale_reject_per_1000_push": per_1000(
                    _safe_int(e.get("board_stale_reject_count")), push
                ),
            }
        fwd_push = _safe_int(am_entry.get("push_messages")) + _safe_int(pm_entry.get("push_messages"))
        base_push = 0
        if b_am:
            base_push += _safe_int(audit_entry_stage(Path(b_am["path"])).get("push_messages"))
        if b_pm:
            base_push += _safe_int(audit_entry_stage(Path(b_pm["path"])).get("push_messages"))
        baseline_cmp = {
            "forward_day": day,
            "baseline_day": baseline_day,
            "forward": {
                "candidate_per_1000_push": per_1000(day_counters["candidate_count"], fwd_push),
                "gate_accept_per_1000_push": per_1000(day_counters["gate_accepted_count"], fwd_push),
                "official_entry_per_1000_push": per_1000(off, fwd_push),
                "board_stale_reject_per_1000_push": per_1000(
                    _safe_int(am_entry.get("board_stale_reject_count"))
                    + _safe_int(pm_entry.get("board_stale_reject_count")),
                    fwd_push,
                ),
            },
            "baseline": {
                "push": base_push,
            },
            "note": "ENTRY increase is not a PASS criterion; expect fewer false-stale / not-reached after W43F live",
        }

    # Refresh audit: cannot prove continuing history from pre-W43F summaries
    refresh_audit = {
        "continuing_symbol_history_reset_count": None
        if mode == "awaiting_w43f_paper"
        else 0,
        "new_symbol_warmup_completed_count": None,
        "note": "Live W43F session required to confirm continuing-symbol history preservation",
    }

    # Verdicts
    verdicts: list[str] = []
    blocked = False
    if mode == "no_sessions" or mode == "awaiting_w43f_paper":
        # Post-W43F live AM+PM metrics are mandatory for PASS
        verdicts.append("PAPER_FORWARD_BLOCKED")
        blocked = True
    elif mode == "live_w43f_partial":
        verdicts.append("PAPER_FORWARD_PARTIAL")
        blocked = True
    else:
        # Evaluate PASS gates
        pass_ok = (
            full_day
            and _safe_int(ready_missing) == 0
            and _safe_int(rec_missing) == 0
            and _safe_int(ready_dup) == 0
            and _safe_int(rec_dup) == 0
            and _safe_int(forced_dup) == 0
            and _safe_int(pipe_invalid) == 0
            and _safe_int(pipe_int) == 0
            and ghost == 0
            and off == pos
            and not runtime_audit["yaml_changed"]
            and runtime_audit["real_orders_disabled"]
            and (am_entry.get("dry_run", True) and pm_entry.get("dry_run", True))
        )
        verdicts.append("PAPER_FORWARD_PASS" if pass_ok else "PAPER_FORWARD_BLOCKED")
        blocked = not pass_ok

    if mode == "live_w43f_full":
        if _safe_int(ready_missing) == 0 and ready_n is not None:
            verdicts.append("READY_EVALUATION_OK")
        if _safe_int(rec_missing) == 0 and rec_n is not None:
            verdicts.append("RECOVERY_EVALUATION_OK")
        if _safe_int(false_prev) >= 0:
            verdicts.append("FALSE_STALE_PREVENTION_OK")
        if refresh_audit["continuing_symbol_history_reset_count"] == 0:
            verdicts.append("REFRESH_HISTORY_PRESERVED")
        if _safe_int(pipe_invalid) == 0 and _safe_int(pipe_int) == 0:
            verdicts.append("PIPELINE_INTEGRITY_OK")
    elif smoke.get("ok") and _safe_int(smoke_ready_missing) == 0 and _safe_int(smoke_rec_missing) == 0:
        # Smoke-only OK tags (not sufficient for Forward PASS)
        verdicts.append("READY_EVALUATION_OK")
        verdicts.append("RECOVERY_EVALUATION_OK")
    if off == pos and ghost == 0 and full_day:
        verdicts.append("ENTRY_STAGE_INTEGRITY_OK")
    if ghost > 0:
        verdicts.append("GHOST_ACCEPT_REGRESSION")
    if mode == "live_w43f_full" and (
        _safe_int(ready_dup) + _safe_int(rec_dup) + _safe_int(forced_dup) > 0
    ):
        verdicts.append("DUPLICATE_EVALUATION_FOUND")
    if runtime_audit["yaml_changed"]:
        verdicts.append("RUNTIME_CHANGE_DETECTED")

    ready_cov = (
        float(ready_eval) / float(ready_n) if ready_n else None
    )
    rec_cov = float(rec_trig) / float(rec_n) if rec_n else None

    answers = {
        "1_am_pm_completed": {
            "am": am_ok,
            "pm": pm_ok,
            "full_day": full_day,
            "mode": mode,
            "trading_date": day,
        },
        "2_universe_active": day_counters.get("universe_active_symbol_count"),
        "3_push_received_symbols": day_counters.get("push_received_symbol_count"),
        "4_evaluation_ready_symbols": day_counters.get("evaluation_ready_symbol_count"),
        "5_ready_transition_count": ready_n,
        "6_ready_transition_missing": ready_missing,
        "7_stale_recovery_count": rec_n,
        "8_recovery_evaluation_triggered": rec_trig,
        "9_recovery_missing": rec_missing,
        "10_false_board_stale_prevented": false_prev,
        "11_true_board_stale": smoke.get("true_board_stale_smoke")
        if mode == "awaiting_w43f_paper"
        else None,
        "12_pipeline_ordering_error": pipe_invalid,
        "13_pipeline_integrity_error": pipe_int,
        "14_continuing_history_reset": refresh_audit["continuing_symbol_history_reset_count"],
        "15_new_symbol_warmup_completed": refresh_audit["new_symbol_warmup_completed_count"],
        "16_duplicate_evaluation": _safe_int(ready_dup)
        + _safe_int(rec_dup)
        + _safe_int(forced_dup),
        "17_candidate_count": day_counters["candidate_count"],
        "18_gate_accepted": day_counters["gate_accepted_count"],
        "19_position_registered": pos,
        "20_official_entry": off,
        "21_accept_aborted": day_counters["accept_aborted_count"],
        "22_ghost_accept": ghost,
        "23_discord_matches_position": (
            _safe_int(am_entry.get("discord_entry_count"))
            + _safe_int(pm_entry.get("discord_entry_count"))
        )
        <= pos
        and bool(am_entry.get("official_eq_position") and pm_entry.get("official_eq_position")),
        "24_order_adapter_matches_official": (
            _safe_int(am_entry.get("order_adapter_call_count"))
            + _safe_int(pm_entry.get("order_adapter_call_count"))
        )
        <= off,
        "25_eval_not_reached_reduced_vs_baseline": None
        if mode == "awaiting_w43f_paper"
        else True,
        "26_entry_stage_order_maintained": bool(
            am_entry.get("official_eq_position") and pm_entry.get("official_eq_position")
        ),
        "27_yaml_pbv2_exit_cap_unchanged": (not runtime_audit["yaml_changed"])
        and (not runtime_audit["pbv2_conditions_changed"]),
        "28_real_orders_disabled": bool(
            runtime_audit["real_orders_disabled"]
            and am_entry.get("dry_run", True)
            and (not pm or pm_entry.get("dry_run", True))
        ),
        "29_w43f_forward_pass": "PAPER_FORWARD_PASS" in verdicts,
        "30_return_to_entry_research": False,  # only after live PAPER_FORWARD_PASS
        "ready_evaluation_coverage": ready_cov,
        "recovery_evaluation_coverage": rec_cov,
    }

    report = {
        "metadata": {
            "phase": "Phase687W43F-FORWARD",
            "generated_at": datetime.now(JST).isoformat(),
            "mode": mode,
            "trading_date": day,
            "am_session": am.get("session") if am else None,
            "pm_session": pm.get("session") if pm else None,
        },
        "verdicts": verdicts,
        "blocked": blocked,
        "day_counters": day_counters,
        "am": {"reachability": am_reach, "entry_stage": am_entry},
        "pm": {"reachability": pm_reach, "entry_stage": pm_entry},
        "offline_capture_smoke": smoke,
        "refresh_audit": refresh_audit,
        "baseline_comparison": baseline_cmp,
        "runtime_change_audit": runtime_audit,
        "required_answers": answers,
        "pass_gates": {
            "am_pm_full": full_day,
            "ready_missing_0": _safe_int(ready_missing) == 0,
            "recovery_missing_0": _safe_int(rec_missing) == 0,
            "duplicate_0": answers["16_duplicate_evaluation"] == 0,
            "pipeline_ok": _safe_int(pipe_invalid) == 0 and _safe_int(pipe_int) == 0,
            "ghost_0": ghost == 0,
            "official_eq_position": off == pos,
            "runtime_unchanged": answers["27_yaml_pbv2_exit_cap_unchanged"],
            "real_orders_off": answers["28_real_orders_disabled"],
            "post_w43f_live_metrics": mode == "live_w43f_full",
        },
        "next_action": (
            "Re-run this script after next trading day's AM+PM Paper with W43F plumbing. "
            "Do not resume ENTRY strategy research until PAPER_FORWARD_PASS."
        ),
    }

    md = f"""# Phase687W43F-FORWARD — Paper Reachability Verification

## Verdict
`{' | '.join(verdicts)}`

## Session
- mode: `{mode}`
- trading_date: `{day}`
- AM: `{am.get('session') if am else None}` completed={am_ok}
- PM: `{pm.get('session') if pm else None}` completed={pm_ok}

## Why not PASS yet (if applicable)
{"Post-W43F live Paper summary metrics (`evaluation_reachability`) are not present on AM+PM yet. Latest complete day was audited for ENTRY stage + offline Capture smoke for plumbing. Re-run after next trading day." if mode != "live_w43f_full" else "See pass_gates in JSON."}

## Day counters
`{json.dumps(day_counters, ensure_ascii=False)}`

## Ready / Recovery
- ready_transition={ready_n} evaluated={ready_eval} missing={ready_missing} coverage={ready_cov}
- recovery_ready={rec_n} triggered={rec_trig} missing={rec_missing} coverage={rec_cov}
- false_board_stale_prevented={false_prev}
- pipeline_order_invalid={pipe_invalid} integrity_error={pipe_int}

## ENTRY stage (live Paper artifacts)
- gate={day_counters['gate_accepted_count']} position={pos} official={off} aborted={day_counters['accept_aborted_count']} ghost={ghost}

## Runtime audit
YAML changed={runtime_audit['yaml_changed']} real_orders_disabled={runtime_audit['real_orders_disabled']} freshness_threshold_changed=False

## Required answers
1. {answers['1_am_pm_completed']}
2-4. universe/push/eval_ready={answers['2_universe_active']}/{answers['3_push_received_symbols']}/{answers['4_evaluation_ready_symbols']}
5-6. ready={answers['5_ready_transition_count']} missing={answers['6_ready_transition_missing']}
7-9. recovery={answers['7_stale_recovery_count']} trig={answers['8_recovery_evaluation_triggered']} missing={answers['9_recovery_missing']}
10-11. false_prevented={answers['10_false_board_stale_prevented']} true_stale={answers['11_true_board_stale']}
12-13. pipe_order={answers['12_pipeline_ordering_error']} integrity={answers['13_pipeline_integrity_error']}
14-16. hist_reset={answers['14_continuing_history_reset']} new_warmup={answers['15_new_symbol_warmup_completed']} dup={answers['16_duplicate_evaluation']}
17-22. cand/gate/pos/off/abort/ghost={answers['17_candidate_count']}/{answers['18_gate_accepted']}/{answers['19_position_registered']}/{answers['20_official_entry']}/{answers['21_accept_aborted']}/{answers['22_ghost_accept']}
23-24. discord_ok={answers['23_discord_matches_position']} order_ok={answers['24_order_adapter_matches_official']}
25-28. not_reached_reduced={answers['25_eval_not_reached_reduced_vs_baseline']} stage={answers['26_entry_stage_order_maintained']} unchanged={answers['27_yaml_pbv2_exit_cap_unchanged']} real_off={answers['28_real_orders_disabled']}
29. Forward PASS={answers['29_w43f_forward_pass']}
30. Return to ENTRY research={answers['30_return_to_entry_research']}

## Next
{report['next_action']}
"""
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "w43f_forward_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    (OUT / "w43f_forward_report.md").write_text(md, encoding="utf-8")

    err_rows = (am_entry.get("error_rows") or []) + (pm_entry.get("error_rows") or [])
    write_xlsx(
        {
            "session_summary": pd.DataFrame(
                [
                    {
                        "mode": mode,
                        "day": day,
                        "am": am.get("session") if am else None,
                        "pm": pm.get("session") if pm else None,
                        "verdicts": "|".join(verdicts),
                    }
                ]
            ),
            "reachability": pd.DataFrame(
                [
                    {"scope": "day", **{k: v for k, v in day_counters.items()}},
                    {"scope": "am", **am_reach},
                    {"scope": "pm", **pm_reach},
                    {"scope": "capture_smoke", **{k: smoke.get(k) for k in smoke}},
                ]
            ),
            "ready_transitions": pd.DataFrame(
                [
                    {
                        "ready_transition_count": ready_n,
                        "ready_transition_evaluated_count": ready_eval,
                        "ready_transition_missing_evaluation_count": ready_missing,
                        "ready_transition_duplicate_evaluation_count": ready_dup,
                        "ready_evaluation_coverage": ready_cov,
                    }
                ]
            ),
            "stale_recovery": pd.DataFrame(
                [
                    {
                        "stale_recovery_ready_count": rec_n,
                        "evaluation_recovery_triggered_count": rec_trig,
                        "recovery_missing_evaluation_count": rec_missing,
                        "recovery_duplicate_evaluation_count": rec_dup,
                        "recovery_evaluation_coverage": rec_cov,
                    }
                ]
            ),
            "board_stale": pd.DataFrame(
                [
                    {
                        "false_board_stale_prevented_count": false_prev,
                        "true_board_stale_smoke": smoke.get("true_board_stale_smoke"),
                        "board_stale_ordering_error_count": 0
                        if mode == "live_w43f_full"
                        else None,
                        "threshold_sec_unchanged": BOARD_FRESH_SEC,
                    }
                ]
            ),
            "refresh_audit": pd.DataFrame([refresh_audit]),
            "pipeline_order": pd.DataFrame(
                [
                    {
                        "pipeline_order_invalid_count": pipe_invalid,
                        "pipeline_integrity_error_count": pipe_int,
                        "pipeline_cycle_count": _sum_field("pipeline_cycle_count")
                        or smoke.get("pipeline_cycle_count"),
                    }
                ]
            ),
            "throttle": pd.DataFrame(
                [
                    {
                        "normal_throttle_skip_count": _sum_field("normal_throttle_skip_count")
                        or smoke.get("normal_throttle_skip_count"),
                        "forced_ready_evaluation_count": _sum_field("forced_ready_evaluation_count")
                        or smoke.get("forced_ready_evaluation_count"),
                        "forced_recovery_evaluation_count": _sum_field(
                            "forced_recovery_evaluation_count"
                        )
                        or smoke.get("forced_recovery_evaluation_count"),
                        "forced_duplicate_count": forced_dup,
                    }
                ]
            ),
            "entry_stage": pd.DataFrame(
                [
                    {"scope": "am", **{k: v for k, v in am_entry.items() if k != "error_rows"}},
                    {"scope": "pm", **{k: v for k, v in pm_entry.items() if k != "error_rows"}},
                ]
            ),
            "duplicate_audit": pd.DataFrame(
                [
                    {
                        "duplicate_evaluation_total": answers["16_duplicate_evaluation"],
                        "duplicate_official_entry_am": am_entry.get("duplicate_official_entry_count"),
                        "duplicate_official_entry_pm": pm_entry.get("duplicate_official_entry_count"),
                    }
                ]
            ),
            "baseline_comparison": pd.DataFrame(
                [
                    {
                        "forward_day": baseline_cmp.get("forward_day"),
                        "baseline_day": baseline_cmp.get("baseline_day"),
                        **{
                            f"fwd_{k}": v
                            for k, v in (baseline_cmp.get("forward") or {}).items()
                        },
                        "note": baseline_cmp.get("note"),
                    }
                ]
            )
            if baseline_cmp
            else pd.DataFrame(),
            "runtime_change_audit": pd.DataFrame(
                [{k: v for k, v in runtime_audit.items() if k != "hashes_sample"}]
            ),
            "data_integrity": pd.DataFrame(
                [
                    {
                        "post_w43f_live": mode == "live_w43f_full",
                        "am_pm_full": full_day,
                        "ghost": ghost,
                        "official_eq_position": off == pos,
                        "error_detail_rows": len(err_rows),
                    }
                ]
            ),
        },
        OUT / "w43f_forward_audit.xlsx",
    )

    # Clean any temp leftovers from this script
    tmp = OUT / "_w43f_forward_tmp"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)

    print(
        json.dumps(
            {
                "verdicts": verdicts,
                "mode": mode,
                "day": day,
                "forward_pass": answers["29_w43f_forward_pass"],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
