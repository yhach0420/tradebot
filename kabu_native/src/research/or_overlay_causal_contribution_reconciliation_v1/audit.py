"""OR-era paper-journal forensic. No Capture. No Stress. No trade generation from aggregates."""
from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from research.or_overlay_causal_contribution_reconciliation_v1 import (
    OR_ERA_DAYS,
    STRESS_DAYS,
    W27_OR_ERA_SESSIONS,
)
from research.or_overlay_causal_contribution_reconciliation_v1.isolation import PAPER, W27

IDENTITY_SUMMARY_KEYS = (
    "or_overlay_enabled",
    "cap_pbv2",
    "cap_or",
    "or_max_update_count",
    "structural_exit_policy",
    "config_sha256",
    "config_path",
    "same_symbol_open_policy",
    "max_concurrent_positions",
    "position_cap_mode",
    "position_cap_release",
    "source",
    "profile",
)


def _session_dir(day: str, name: str) -> Path:
    return PAPER / day / name


def recover_search() -> dict[str, Any]:
    """One-shot recovery: look for missing jsonl / 20260714 W27 sessions. No Stress paths."""
    hits_jsonl: list[str] = []
    hits_0714: list[str] = []
    searched: list[str] = []
    for day, sess, _kind in W27_OR_ERA_SESSIONS:
        if day in STRESS_DAYS:
            continue
        d = _session_dir(day, sess)
        searched.append(str(d))
        jsonl = d / "small_paper_events.jsonl"
        if jsonl.is_file():
            hits_jsonl.append(str(jsonl))
        if day == "20260714" and d.is_dir() and any(d.iterdir()):
            hits_0714.append(str(d))
    extra_0714 = PAPER / "20260714"
    if extra_0714.is_dir():
        for p in extra_0714.iterdir():
            if p.is_dir() and p.name.startswith("live_session_"):
                searched.append(str(p))
    w27_dump = W27 / "cap_cf_events.jsonl"
    return {
        "jsonl_found": hits_jsonl,
        "session_20260714_w27_found": hits_0714,
        "w27_cap_cf_dump_exists": w27_dump.is_file(),
        "searched_n": len(searched),
        "jsonl_n": len(hits_jsonl),
    }


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return obj if isinstance(obj, dict) else {}


def _csv_header(path: Path) -> list[str]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        r = csv.reader(f)
        row = next(r, [])
        return [str(c) for c in row]


def _stream_event_counts(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {
        "rows": 0,
        "event_types": {},
        "accepted_n": 0,
        "rejected_n": 0,
        "candidate_n": 0,
        "observer_exit_n": 0,
        "fill_event_n": 0,
        "slot_release_event_n": 0,
        "pbv2_internal_reason_present_n": 0,
        "pbv2_cap_reject_n": 0,
        "or_cap_full_n": 0,
        "or_overlay_not_candidate_n": 0,
        "same_symbol_reject_n": 0,
        "entry_type_or_n": 0,
        "entry_type_pbv2_n": 0,
        "entry_type_blank_accepted_n": 0,
        "reject_with_symbol_ts_n": 0,
        "reject_with_near_high_and_update_n": 0,
        "has_day_return_rank_col": False,
        "has_pbv2_internal_reason_col": False,
        "has_entry_type_col": False,
        "has_or_o_r003_col": False,
        "has_occupancy_col": False,
        "columns": [],
    }
    if not path.is_file():
        return out
    types: Counter[str] = Counter()
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        cols = list(reader.fieldnames or [])
        out["columns"] = cols
        colset = set(cols)
        out["has_day_return_rank_col"] = "day_return_rank" in colset
        out["has_pbv2_internal_reason_col"] = "pbv2_internal_reason" in colset
        out["has_entry_type_col"] = "entry_type" in colset
        out["has_or_o_r003_col"] = "or_o_r003_pass" in colset or "or_reason" in colset
        out["has_occupancy_col"] = any(
            c in colset
            for c in (
                "pbv2_open",
                "or_open",
                "or_active_positions",
                "observer_open_count",
                "open_count_pbv2",
                "open_count_or",
            )
        )
        for row in reader:
            out["rows"] += 1
            et = str(row.get("event_type") or "").strip()
            types[et] += 1
            reason = str(
                row.get("gate_reject_reason")
                or row.get("reject_reason")
                or row.get("final_reject_reason")
                or ""
            )
            internal = str(row.get("pbv2_internal_reason") or "").strip()
            if et == "accepted":
                out["accepted_n"] += 1
                etype = str(row.get("entry_type") or "").strip().upper()
                if etype in ("OR", "OR_OVERLAY"):
                    out["entry_type_or_n"] += 1
                elif etype == "PBV2":
                    out["entry_type_pbv2_n"] += 1
                else:
                    out["entry_type_blank_accepted_n"] += 1
            elif et == "rejected":
                out["rejected_n"] += 1
                if row.get("symbol") and (row.get("event_time") or row.get("entry_time")):
                    out["reject_with_symbol_ts_n"] += 1
                near = row.get("entry_near_day_high_pct") or row.get("day_high_distance_pct")
                upd = row.get("update_count_before_entry")
                if near not in (None, "") and upd not in (None, ""):
                    out["reject_with_near_high_and_update_n"] += 1
                if internal:
                    out["pbv2_internal_reason_present_n"] += 1
                if internal == "pbv2_cap_full" or reason == "pbv2_cap_full":
                    out["pbv2_cap_reject_n"] += 1
                if reason == "or_cap_full":
                    out["or_cap_full_n"] += 1
                if reason == "or_overlay_not_candidate":
                    out["or_overlay_not_candidate_n"] += 1
                if "SAME_SYMBOL" in reason.upper() or reason == "REJECT_SAME_SYMBOL_OPEN_OVERLAP":
                    out["same_symbol_reject_n"] += 1
            elif et == "candidate":
                out["candidate_n"] += 1
            elif et in ("observer_exit", "structural_exit"):
                out["observer_exit_n"] += 1
            elif et in ("fill", "filled", "execution_fill"):
                out["fill_event_n"] += 1
            elif et in ("slot_release", "slot_released"):
                out["slot_release_event_n"] += 1
    out["event_types"] = dict(types)
    return out


def _stream_structural(path: Path) -> dict[str, int]:
    kinds: Counter[str] = Counter()
    n = 0
    if not path.is_file():
        return {"rows": 0, "structural_exit_n": 0, "entry_n": 0}
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            n += 1
            kinds[str(row.get("event_kind") or "")] += 1
    return {
        "rows": n,
        "structural_exit_n": int(kinds.get("structural_exit", 0)),
        "entry_n": int(kinds.get("entry", 0)),
        "kinds": dict(kinds),
    }


def audit_session(day: str, sess: str, kind: str) -> dict[str, Any]:
    d = _session_dir(day, sess)
    summary = _load_json(d / "small_paper_summary.json")
    cfg = _load_json(d / "live_session_config.json")
    events_csv = d / "small_paper_events.csv"
    events_jsonl = d / "small_paper_events.jsonl"
    rejects = d / "small_paper_rejects.csv"
    positions = d / "small_paper_positions.csv"
    structural = d / "structural_events.csv"
    ev = _stream_event_counts(events_csv)
    st = _stream_structural(structural)
    identity = {k: summary.get(k, cfg.get(k)) for k in IDENTITY_SUMMARY_KEYS}
    identity["TRADING_DATE"] = day
    identity["session"] = sess
    identity["kind"] = kind
    identity["DAY_HIGH_NEAR_PCT"] = None
    identity["open_strength_mins_max"] = None
    identity["open_strength_rank_max"] = None
    identity["DAY_HIGH_NEAR_PCT_IN_SESSION_SNAPSHOT"] = False
    pos_header = _csv_header(positions)
    return {
        "day": day,
        "session": sess,
        "kind": kind,
        "dir_exists": d.is_dir(),
        "summary_exists": bool(summary),
        "config_exists": bool(cfg),
        "events_jsonl_exists": events_jsonl.is_file(),
        "events_csv_exists": events_csv.is_file(),
        "rejects_csv_exists": rejects.is_file(),
        "positions_csv_exists": positions.is_file(),
        "structural_csv_exists": structural.is_file(),
        "identity": identity,
        "summary_accepted": summary.get("accepted_count"),
        "summary_rejected": summary.get("rejected_count"),
        "summary_gate_evaluations": summary.get("gate_evaluations"),
        "summary_or_count": summary.get("or_count"),
        "summary_pbv2_count": summary.get("pbv2_count"),
        "summary_or_entry_count": summary.get("or_entry_count"),
        "summary_or_cap_full_count": summary.get("or_cap_full_count"),
        "summary_rejected_by_position_cap": summary.get("rejected_by_position_cap"),
        "summary_structural_exit_count": summary.get("structural_exit_count"),
        "events": ev,
        "structural": st,
        "positions_columns": pos_header,
        "positions_has_entry_type": "entry_type" in pos_header,
        "positions_has_lane_occupancy": any(
            c in pos_header for c in ("entry_type", "or_open", "pbv2_open")
        ),
    }


def completeness_for_session(row: dict[str, Any]) -> dict[str, bool]:
    ev = dict(row.get("events") or {})
    st = dict(row.get("structural") or {})
    jsonl = bool(row.get("events_jsonl_exists"))
    csv_ok = bool(row.get("events_csv_exists"))
    ident = dict(row.get("identity") or {})
    eval_n = int(ev.get("candidate_n") or 0) + int(ev.get("rejected_n") or 0) + int(ev.get("accepted_n") or 0)
    pbv2_accept_id = int(ev.get("entry_type_pbv2_n") or 0) > 0
    or_accept_id = int(ev.get("entry_type_or_n") or 0) > 0
    # Lane identity from events; summary aggregates are not trade identity.
    return {
        "stage2_pbv2_evaluation": eval_n > 0 and (jsonl or csv_ok),
        "pbv2_accept": int(ev.get("accepted_n") or 0) > 0 or pbv2_accept_id,
        "pbv2_reject": int(ev.get("rejected_n") or 0) > 0,
        "reject_reason": int(ev.get("rejected_n") or 0) > 0,
        "or_overlay_evaluation_eligibility": bool(ident.get("or_overlay_enabled"))
        and int(ev.get("or_overlay_not_candidate_n") or 0) + int(ev.get("or_cap_full_n") or 0) >= 0
        and bool(ident.get("or_overlay_enabled")),
        "or_predicate_inputs": bool(ev.get("has_or_o_r003_col"))
        and bool(ev.get("has_day_return_rank_col"))
        and int(ev.get("reject_with_near_high_and_update_n") or 0) > 0,
        "or_accept_reject": or_accept_id or (int(ev.get("or_cap_full_n") or 0) > 0 and or_accept_id),
        "event_timestamp": int(ev.get("reject_with_symbol_ts_n") or 0) > 0 or int(ev.get("accepted_n") or 0) > 0,
        "symbol": int(ev.get("reject_with_symbol_ts_n") or 0) > 0 or int(ev.get("accepted_n") or 0) > 0,
        "same_symbol_state": int(ev.get("same_symbol_reject_n") or 0) > 0,
        "pbv2_lane_occupancy": bool(ev.get("has_occupancy_col")),
        "or_lane_occupancy": bool(ev.get("has_occupancy_col")),
        "fill_event": int(ev.get("fill_event_n") or 0) > 0,
        "exit_event": int(st.get("structural_exit_n") or 0) > 0 or int(ev.get("observer_exit_n") or 0) > 0,
        "slot_release_event": int(ev.get("slot_release_event_n") or 0) > 0,
        "session_present": bool(row.get("dir_exists")) and bool(row.get("summary_exists")) and csv_ok,
        "jsonl_present": jsonl,
        "pbv2_internal_reason_column": bool(ev.get("has_pbv2_internal_reason_col")),
        "entry_type_column": bool(ev.get("has_entry_type_col")),
        "or_predicate_constants_in_snapshot": ident.get("DAY_HIGH_NEAR_PCT") is not None,
    }


def run_audit() -> dict[str, Any]:
    recovery = recover_search()
    sessions: list[dict[str, Any]] = []
    for day, sess, kind in W27_OR_ERA_SESSIONS:
        if day in STRESS_DAYS:
            raise RuntimeError(f"STRESS_DAY_IN_OR_ERA {day}")
        sessions.append(audit_session(day, sess, kind))
    return {
        "or_era_days": list(OR_ERA_DAYS),
        "w27_session_n": len(W27_OR_ERA_SESSIONS),
        "recovery": recovery,
        "sessions": sessions,
    }
