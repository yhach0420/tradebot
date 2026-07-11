"""
Phase644c: Order latency trace root cause audit (research only).

Diagnoses missing order_latency_dryrun_trace.jsonl in live sessions.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase451_entry_shape_tournament import _now_iso
from small_paper.order_latency_dryrun_trace import TRACE_FILENAME

PHASE644C_VERDICT = "phase644c_order_latency_trace_root_cause_done"
REPORT_DIR_NAME = "phase644c_order_latency_trace_root_cause"

NATIVE_ROOT = Path(__file__).resolve().parents[2]
SMALL_PAPER_ROOT = NATIVE_ROOT / "results" / "small_paper"
PRODUCTION_YAML = (
    NATIVE_ROOT
    / "configs"
    / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
)

SESSION_AUDIT_FIELDS = [
    "day",
    "session",
    "trace_file_exists",
    "trace_line_count",
    "summary_trace_enabled",
    "summary_sample_count",
    "summary_reached_count",
    "live_order_api_wiring_enabled",
    "live_order_adapter_enabled",
    "accepted_count",
    "source",
    "diagnosis",
]


@dataclass
class SessionAuditRow:
    day: str
    session: str
    trace_path: Path
    summary: Mapping[str, Any]
    diagnosis: str


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _count_jsonl_lines(path: Path) -> int:
    if not path.is_file():
        return 0
    n = 0
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                n += 1
    return n


def discover_live_sessions(
    small_paper_root: Path,
    *,
    day_filter: Optional[str] = None,
    limit: int = 50,
) -> list[tuple[str, str, Path]]:
    """Return (day, session_name, session_dir) newest first."""
    found: list[tuple[str, str, Path]] = []
    if not small_paper_root.is_dir():
        return found
    day_dirs = sorted(
        [d for d in small_paper_root.iterdir() if d.is_dir() and len(d.name) == 8 and d.name.isdigit()],
        reverse=True,
    )
    for day_dir in day_dirs:
        if day_filter and day_dir.name != day_filter:
            continue
        for sess in sorted(day_dir.glob("live_session_*"), reverse=True):
            found.append((day_dir.name, sess.name, sess))
            if len(found) >= limit:
                return found
    return found


def diagnose_session(day: str, session: str, session_dir: Path) -> SessionAuditRow:
    trace_path = session_dir / TRACE_FILENAME
    summary = _load_json(session_dir / "small_paper_summary.json")
    trace_exists = trace_path.is_file()
    line_count = _count_jsonl_lines(trace_path)
    sample_count = int(summary.get("order_latency_dryrun_sample_count") or 0)
    reached = int(summary.get("order_latency_dryrun_reached_count") or 0)
    trace_enabled = bool(summary.get("order_latency_dryrun_trace_enabled"))
    wiring = bool(summary.get("live_order_api_wiring_enabled"))
    adapter = bool(summary.get("live_order_adapter_enabled"))
    accepted = int(summary.get("accepted_count") or 0)
    source = str(summary.get("source") or "")

    if trace_exists and line_count > 0:
        diagnosis = "ok_trace_present"
    elif trace_exists and line_count == 0:
        diagnosis = "empty_trace_file"
    elif sample_count == 0 and trace_enabled and accepted > 0 and adapter and wiring:
        diagnosis = "root_cause_wiring_skipped_by_legacy_guard"
    elif sample_count == 0 and trace_enabled and accepted > 0 and not wiring:
        diagnosis = "wiring_disabled_in_config"
    elif sample_count == 0 and not trace_enabled:
        diagnosis = "trace_disabled_in_session"
    elif sample_count == 0 and accepted == 0:
        diagnosis = "no_accepted_entries"
    elif sample_count == 0:
        diagnosis = "trace_never_emitted_unknown"
    else:
        diagnosis = "summary_only_no_file"

    return SessionAuditRow(
        day=day,
        session=session,
        trace_path=trace_path,
        summary=summary,
        diagnosis=diagnosis,
    )


def build_mandatory_answers(
    rows: Sequence[SessionAuditRow],
    *,
    phase644b_trace_sources: int,
) -> dict[str, Any]:
    latest = rows[0] if rows else None
    missing = [r for r in rows if not r.trace_path.is_file() or _count_jsonl_lines(r.trace_path) == 0]
    wiring_skip = [r for r in rows if r.diagnosis == "root_cause_wiring_skipped_by_legacy_guard"]

    if wiring_skip:
        root_cause = "trace_not_generated"
        root_detail = (
            "_execute_accepted_entry returned before process_entry_wiring when "
            "live_order_adapter_enabled=true (_legacy_live_order_hooks_enabled=false). "
            "Trace session was initialized but finish_wiring/_emit never ran."
        )
        fix_location = "pilot_runner._execute_accepted_entry — call _maybe_record_live_order_wiring_entry before legacy guard"
        aggregation_miss = False
    elif missing and phase644b_trace_sources == 0:
        root_cause = "trace_not_generated"
        root_detail = "No trace files under results/small_paper; Phase644b correctly reports zero sources."
        fix_location = "see per-session diagnosis"
        aggregation_miss = False
    elif not missing:
        root_cause = "ok"
        root_detail = "Traces present"
        fix_location = "none"
        aggregation_miss = phase644b_trace_sources < len([r for r in rows if r.trace_path.is_file()])
    else:
        root_cause = "trace_not_generated"
        root_detail = "Mixed or unknown causes — see session audit CSV"
        fix_location = "per-session diagnosis"
        aggregation_miss = False

    return {
        "1_trace_missing_vs_aggregation": (
            "trace_not_generated" if missing else ("aggregation_miss" if aggregation_miss else "ok")
        ),
        "2_root_cause": root_cause,
        "2_root_cause_detail": root_detail,
        "3_fix_location": fix_location,
        "4_existing_session_backfill_possible": False,
        "4_existing_session_note": (
            "Existing sessions cannot gain trace rows without re-run/replay; "
            "_emit only runs at live accept/reject time."
        ),
        "5_sessions_audited": len(rows),
        "5_sessions_missing_trace": len(missing),
        "5_wiring_guard_sessions": len(wiring_skip),
        "6_latest_session": f"{latest.day}/{latest.session}" if latest else None,
        "6_latest_diagnosis": latest.diagnosis if latest else None,
        "7_phase644b_trace_sources": phase644b_trace_sources,
    }


def run(
    *,
    report_dir: Optional[Path] = None,
    day_filter: Optional[str] = None,
    session_limit: int = 30,
) -> dict[str, Any]:
    out_dir = report_dir or (NATIVE_ROOT / "results" / "reports" / REPORT_DIR_NAME)
    out_dir.mkdir(parents=True, exist_ok=True)

    sessions = discover_live_sessions(SMALL_PAPER_ROOT, day_filter=day_filter, limit=session_limit)
    audits = [diagnose_session(d, s, p) for d, s, p in sessions]

    from research.phase644b_live_order_latency_measurement import load_live_traces

    _, phase644b_sources = load_live_traces(SMALL_PAPER_ROOT)
    mandatory = build_mandatory_answers(audits, phase644b_trace_sources=len(phase644b_sources))

    csv_rows: list[dict[str, Any]] = []
    for a in audits:
        sm = a.summary
        csv_rows.append(
            {
                "day": a.day,
                "session": a.session,
                "trace_file_exists": a.trace_path.is_file(),
                "trace_line_count": _count_jsonl_lines(a.trace_path),
                "summary_trace_enabled": sm.get("order_latency_dryrun_trace_enabled"),
                "summary_sample_count": sm.get("order_latency_dryrun_sample_count"),
                "summary_reached_count": sm.get("order_latency_dryrun_reached_count"),
                "live_order_api_wiring_enabled": sm.get("live_order_api_wiring_enabled"),
                "live_order_adapter_enabled": sm.get("live_order_adapter_enabled"),
                "accepted_count": sm.get("accepted_count"),
                "source": sm.get("source"),
                "diagnosis": a.diagnosis,
            }
        )

    audit_csv = out_dir / "phase644c_session_audit.csv"
    _write_csv(audit_csv, SESSION_AUDIT_FIELDS, csv_rows)

    report = {
        "phase": "644c",
        "verdict": PHASE644C_VERDICT,
        "generated_at": _now_iso(),
        "production_yaml": str(PRODUCTION_YAML),
        "mandatory_answers": mandatory,
        "execution_path_notes": {
            "init": "run_live_dry_run -> _init_order_latency_dryrun (line ~5337)",
            "push": "_process_push_payload -> begin_push / mark_decision_end",
            "accept": "_stage5_execute_entry -> _execute_accepted_entry -> _maybe_record_live_order_wiring_entry -> process_entry_wiring -> finish_wiring/_emit",
            "reject": "_stage6_record_reject -> finish_reject/_emit",
            "bug": "pre-fix: wiring call was after _legacy_live_order_hooks_enabled early return when live_order_adapter_enabled=true",
        },
        "artifacts": {
            "session_audit_csv": str(audit_csv),
        },
    }
    report_path = out_dir / "phase644c_report.json"
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
