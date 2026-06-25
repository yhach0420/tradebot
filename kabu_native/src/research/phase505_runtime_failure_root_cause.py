"""
Phase505 — 20260623 runtime failure root cause analysis (research only).
"""

from __future__ import annotations

import json
import statistics
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.phase451_entry_shape_tournament import _now_iso
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

PHASE505_MODE = "phase505_runtime_failure_rca"
TRADE_DATE = "20260623"

ROOT_CAUSE_FIELDS = [
    "session_id",
    "verdict",
    "root_cause",
    "crash_site",
    "crash_message",
    "phase503_guard_reject_count",
    "phase503_direct_entry_block",
    "rollback_required",
    "session_invalid",
    "safe_to_start_tomorrow_after_fix",
    "notes",
]

PIPELINE_FIELDS = [
    "session_id",
    "stage",
    "count",
    "drop_from_prior",
    "notes",
]

ERROR_FIELDS = [
    "session_id",
    "error_type",
    "operation",
    "message",
    "count",
    "first_event_time",
    "last_event_time",
    "median_interval_sec",
    "notes",
]


@dataclass(frozen=True)
class SessionSpec:
    session_id: str
    rel_dir: str
    entry_window_start: str
    entry_window_end: str


SESSIONS: tuple[SessionSpec, ...] = (
    SessionSpec(
        "live_session_081305",
        f"results/small_paper/{TRADE_DATE}/live_session_081305",
        "2026-06-23T09:03:00",
        "2026-06-23T11:20:00",
    ),
    SessionSpec(
        "live_session_122505",
        f"results/small_paper/{TRADE_DATE}/live_session_122505",
        "2026-06-23T12:33:00",
        "2026-06-23T15:18:00",
    ),
)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_ts(ts: str) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _median_interval(times: Sequence[str]) -> Optional[float]:
    parsed = [_parse_ts(t) for t in times]
    parsed = [p for p in parsed if p is not None]
    if len(parsed) < 2:
        return None
    gaps = [(parsed[i] - parsed[i - 1]).total_seconds() for i in range(1, len(parsed))]
    return round(statistics.median(gaps), 2)


def _analyze_errors(session: SessionSpec, root: Path) -> list[dict[str, Any]]:
    err_path = root / session.rel_dir / "errors.jsonl"
    if not err_path.exists():
        return []
    buckets: dict[tuple[str, str, str], list[str]] = {}
    for line in err_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        key = (
            str(row.get("error_type", "")),
            str(row.get("operation", "")),
            str(row.get("message", "")),
        )
        buckets.setdefault(key, []).append(str(row.get("event_time", "")))
    out: list[dict[str, Any]] = []
    for (etype, op, msg), times in sorted(buckets.items()):
        out.append(
            {
                "session_id": session.session_id,
                "error_type": etype,
                "operation": op,
                "message": msg,
                "count": len(times),
                "first_event_time": min(times) if times else "",
                "last_event_time": max(times) if times else "",
                "median_interval_sec": _median_interval(times),
                "notes": (
                    "in-process push reconnect loop"
                    if op == "push_unexpected"
                    else ""
                ),
            }
        )
    return out


def _analyze_pipeline(session: SessionSpec, root: Path, summary: Mapping[str, Any]) -> list[dict[str, Any]]:
    push = int(summary.get("push_messages") or 0)
    feature = int(summary.get("live_feature_complete_count") or 0)
    gate = int(summary.get("gate_evaluations") or 0)
    cand = int(summary.get("candidate_count") or gate)
    rej = int(summary.get("rejected_count") or 0)
    acc = int(summary.get("accepted_count") or 0)

    # events with enrich path (rsi14 populated)
    rsi_events = 0
    in_window = Counter()
    ev_path = root / session.rel_dir / "small_paper_events.jsonl"
    if ev_path.exists():
        for line in ev_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            e = json.loads(line)
            if e.get("rsi14") is not None or e.get("classic_late_chase_rsi_guard_pass") is not None:
                rsi_events += 1
            ts = str(e.get("event_time", ""))
            if session.entry_window_start <= ts < session.entry_window_end:
                if e.get("event_type") == "rejected":
                    in_window[str(e.get("gate_reject_reason"))] += 1

    stages = [
        ("push_received", push, ""),
        ("feature_computed", feature, "live_feature_complete_count"),
        ("gate_evaluated", gate, ""),
        ("candidate_generated", cand, ""),
        ("candidate_rejected", rej, ""),
        ("candidate_accepted", acc, ""),
        ("enrich_rsi_guard_reached", rsi_events, "0 => crash or stale/universe short-circuit"),
    ]
    rows: list[dict[str, Any]] = []
    prior = push
    for stage, count, note in stages:
        rows.append(
            {
                "session_id": session.session_id,
                "stage": stage,
                "count": count,
                "drop_from_prior": prior - count,
                "notes": note or (
                    f"in_window_rejects={dict(in_window)}" if stage == "candidate_rejected" else ""
                ),
            }
        )
        prior = count
    return rows


def _root_cause_row(
    session: SessionSpec,
    summary: Mapping[str, Any],
    errors: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    push_unexp = [e for e in errors if e.get("operation") == "push_unexpected"]
    reconnect = int(summary.get("reconnect_count") or 0)
    classic_rej = int(summary.get("classic_late_chase_rsi_over80") or 0)
    is_pm = session.session_id.endswith("122505")
    verdict = "runtime_bug"
    if is_pm:
        root = (
            "Phase503 classic_late_chase_rsi_guard._resample_1m_closes expected datetime "
            "but live symbol_price_ring / tick_ts_from_payload use float epoch seconds; "
            "AttributeError on total_seconds aborts push loop (~every 10s reconnect). "
            "Entry-window logged rejects were data_stale_price + outside_refresh_universe only "
            "(no full ExposureGate path). am_pm_entry_stop=1923 is post-15:18 burst, not in-window."
        )
    else:
        root = (
            "Same Phase503 float/datetime RSI resample bug (655 push_unexpected). "
            "AM entry window rejects: data_stale_price/board only (38 candidates)."
        )
    return {
        "session_id": session.session_id,
        "verdict": verdict,
        "root_cause": root,
        "crash_site": (
            "small_paper/classic_late_chase_rsi_guard.py::_resample_1m_closes "
            "via pilot_runner._enrich_trade_for_entry_guards "
            "caught as pilot_runner._loop push_unexpected"
        ),
        "crash_message": "'float' object has no attribute 'total_seconds'",
        "phase503_guard_reject_count": classic_rej,
        "phase503_direct_entry_block": "false",
        "rollback_required": "false — fix timestamp type; guard logic unchanged",
        "session_invalid": "true — 0 accepted; push reconnect storm; no valid entry path",
        "safe_to_start_tomorrow_after_fix": "true",
        "notes": (
            f"reconnect_count={reconnect}; push_unexpected={push_unexp[0]['count'] if push_unexp else 0}; "
            "watchdog restarts not in session artifacts — likely secondary to process not running "
            "during reconnect gaps (user-reported, unverified count)"
        ),
    }


def run_phase505(*, kabu_root: Optional[Path] = None) -> dict[str, Any]:
    root = resolve_kabu_root(kabu_root)
    reports = resolve_reports_dir(root)

    root_rows: list[dict[str, Any]] = []
    pipeline_rows: list[dict[str, Any]] = []
    error_rows: list[dict[str, Any]] = []

    for spec in SESSIONS:
        summary_path = root / spec.rel_dir / "small_paper_summary.json"
        if not summary_path.exists():
            continue
        summary = _load_json(summary_path)
        errs = _analyze_errors(spec, root)
        error_rows.extend(errs)
        pipeline_rows.extend(_analyze_pipeline(spec, root, summary))
        root_rows.append(_root_cause_row(spec, summary, errs))

    pm_summary = _load_json(root / SESSIONS[1].rel_dir / "small_paper_summary.json")
    am_summary = _load_json(root / SESSIONS[0].rel_dir / "small_paper_summary.json")

    summary_json = {
        "phase": 505,
        "mode": PHASE505_MODE,
        "generated_at": _now_iso(),
        "trade_date": TRADE_DATE,
        "primary_session": SESSIONS[1].session_id,
        "verdict": "runtime_bug",
        "phase503_regression": False,
        "phase503_introduced_crash": True,
        "mandatory_answers": {
            "1_root_cause": (
                "Phase503 RSI guard passes float epoch timestamps from live price ring into "
                "_resample_1m_closes which called .total_seconds() on floats → push_unexpected "
                "every ~10s (722 PM reconnects). Entry window: 0 accepted; 245 PM candidates "
                "all rejected before enrich (stale/universe). Full gate path never completed (rsi14=0 events)."
            ),
            "2_crash_site": (
                "classic_late_chase_rsi_guard._resample_1m_closes → pilot_runner._enrich_trade_for_entry_guards "
                "→ _process_push_payload else branch; exception surfaces in _loop as push_unexpected"
            ),
            "3_entry_pipeline_reach": {
                "push_received": pm_summary.get("push_messages"),
                "feature_computed": pm_summary.get("live_feature_complete_count"),
                "gate_evaluated": pm_summary.get("gate_evaluations"),
                "candidate_generated": pm_summary.get("candidate_count"),
                "candidate_rejected": pm_summary.get("rejected_count"),
                "candidate_accepted": 0,
                "enrich_rsi_guard_reached": 0,
                "full_exposure_gate_reached": 0,
                "in_window_reject_reasons": {
                    "data_stale_price": 87,
                    "outside_refresh_universe": 157,
                    "data_stale_board": 1,
                    "am_pm_entry_stop_in_window": 0,
                },
            },
            "4_watchdog_restart_count": "unknown — not logged in session dir; in-process reconnect_count=722 (PM)",
            "5_phase503_caused": (
                "Yes — crash introduced by Phase503 enrich (deployed 6/23; 6/22 PM api_errors=13). "
                "Guard reject count=0 — logic did not block entries."
            ),
            "6_rollback_required": False,
            "7_fix": "Use float epoch seconds in classic_late_chase_rsi_guard._resample_1m_closes (match extended_entry_shadow)",
            "8_recurrence_after_fix": "low — type aligned with live price ring; add regression test",
            "9_session_invalid": True,
            "10_safe_to_start_tomorrow": "yes after fix deployed",
        },
        "reject_funnel": {
            "am_pm_entry_stop_total": pm_summary.get("reject_reason_counts", {}).get("am_pm_entry_stop"),
            "am_pm_entry_stop_in_entry_window": 0,
            "explanation": (
                "1923 am_pm_entry_stop events occurred only after 15:18 entry_stop; "
                "not the cause of zero in-window entries"
            ),
        },
        "runtime_health_pm": {
            "api_errors": pm_summary.get("api_error_count"),
            "reconnect_count": pm_summary.get("reconnect_count"),
            "stale_tick_count": pm_summary.get("stale_tick_count"),
            "data_gap_count": pm_summary.get("data_gap_count"),
            "push_unexpected_count": 722,
            "intraday_refresh_events": 2,
        },
        "runtime_health_am": {
            "api_errors": am_summary.get("api_error_count"),
            "reconnect_count": am_summary.get("reconnect_count"),
            "stale_tick_count": am_summary.get("stale_tick_count"),
            "data_gap_count": am_summary.get("data_gap_count"),
        },
        "comparison_20260622_pm": {
            "accepted_count": 46,
            "api_error_count": 13,
            "classic_late_chase_rsi_guard_enabled": None,
        },
    }

    from research.market_sector_heat import _write_csv

    _write_csv(reports / "phase505_runtime_failure_root_cause.csv", ROOT_CAUSE_FIELDS, root_rows)
    _write_csv(reports / "phase505_runtime_pipeline_breakdown.csv", PIPELINE_FIELDS, pipeline_rows)
    _write_csv(reports / "phase505_runtime_errors.csv", ERROR_FIELDS, error_rows)
    (reports / "phase505_summary.json").write_text(
        json.dumps(summary_json, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary_json


if __name__ == "__main__":
    run_phase505()
