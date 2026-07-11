"""Phase663A2 — PM startup / universe notification / position state ordering audit."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv

PHASE663A2_VERDICT = "phase663a2_pm_notification_ordering_audit_done"
REPORT_DIR_NAME = "phase663a2_pm_notification_ordering"
NATIVE_ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = NATIVE_ROOT / "results" / "reports" / REPORT_DIR_NAME

PM_SESSION_DIR = NATIVE_ROOT / "results" / "small_paper" / "20260708" / "live_session_122537"
RUNNER_STATE = NATIVE_ROOT / "results" / "daily" / "20260708" / "runtime" / "phase148_am_pm_daily_runner_20260708.json"

WINDOW_START = "2026-07-08T13:00:00"
WINDOW_END = "2026-07-08T13:30:00"


@dataclass
class SymbolNotificationAudit:
    symbol: str
    entry_event: Optional[dict[str, Any]]
    exit_event: Optional[dict[str, Any]]
    entry_discord_sent: Optional[str]
    exit_discord_sent: Optional[str]


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def _in_window(ts: str, *, start: str, end: str) -> bool:
    return bool(ts) and start <= ts <= end


def _first_event(
    events: Sequence[Mapping[str, Any]],
    *,
    symbol: str,
    event_type: str,
) -> Optional[dict[str, Any]]:
    for e in events:
        if e.get("symbol") == symbol and e.get("event_type") == event_type:
            return dict(e)
    return None


def _discord_error_windows(errors: Sequence[Mapping[str, Any]]) -> tuple[Optional[str], Optional[str]]:
    discord = [str(e.get("event_time") or "") for e in errors if e.get("error_type") == "discord_error"]
    discord = [t for t in discord if t]
    if not discord:
        return None, None
    return min(discord), max(discord)


def build_event_timeline(
    events: Sequence[Mapping[str, Any]],
    *,
    start: str = WINDOW_START,
    end: str = WINDOW_END,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for e in events:
        t = str(e.get("event_time") or "")
        if not _in_window(t, start=start, end=end):
            continue
        et = str(e.get("event_type") or "")
        if et not in ("accepted", "observer_exit", "candidate", "rejected"):
            continue
        rows.append(
            {
                "event_time": t,
                "event_type": et,
                "symbol": e.get("symbol"),
                "exit_reason": e.get("exit_reason") or e.get("structural_exit_reason") or "",
                "position_slot_before": e.get("position_slot_before"),
                "position_slot_after": e.get("position_slot_after"),
                "observer_entry_time": e.get("observer_entry_time"),
                "accepted_at": e.get("accepted_at"),
                "market_entry_time": e.get("market_entry_time"),
                "position_id": e.get("position_id"),
                "session_id": e.get("session_id"),
                "discord_sent_ts": e.get("discord_sent_ts"),
            }
        )
    rows.sort(key=lambda r: str(r.get("event_time") or ""))
    return rows


def build_position_count_timeline(
    events: Sequence[Mapping[str, Any]],
    *,
    start: str = WINDOW_START,
    end: str = WINDOW_END,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    open_syms: set[str] = set()
    for e in sorted(events, key=lambda x: str(x.get("event_time") or "")):
        t = str(e.get("event_time") or "")
        if not t or t > end:
            break
        sym = str(e.get("symbol") or "")
        et = str(e.get("event_type") or "")
        if et == "accepted" and sym:
            open_syms.add(sym)
            if _in_window(t, start=start, end=end):
                rows.append(
                    {
                        "event_time": t,
                        "action": "entry_accept",
                        "symbol": sym,
                        "pre_count": e.get("position_slot_before"),
                        "post_count": e.get("position_slot_after"),
                        "observer_open_symbols": len(open_syms),
                        "open_symbols": ",".join(sorted(open_syms)),
                    }
                )
        elif et == "observer_exit" and sym in open_syms:
            open_syms.discard(sym)
            if _in_window(t, start=start, end=end):
                rows.append(
                    {
                        "event_time": t,
                        "action": "observer_exit",
                        "symbol": sym,
                        "pre_count": "",
                        "post_count": len(open_syms),
                        "observer_open_symbols": len(open_syms),
                        "open_symbols": ",".join(sorted(open_syms)),
                    }
                )
    return rows


def build_discord_notification_timeline(
    events: Sequence[Mapping[str, Any]],
    errors: Sequence[Mapping[str, Any]],
    *,
    start: str = WINDOW_START,
    end: str = WINDOW_END,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for e in events:
        t = str(e.get("event_time") or "")
        if not _in_window(t, start=start, end=end):
            continue
        et = str(e.get("event_type") or "")
        if et == "accepted":
            rows.append(
                {
                    "kind": "ENTRY_event",
                    "event_time": t,
                    "symbol": e.get("symbol"),
                    "discord_sent_ts": e.get("discord_sent_ts"),
                    "inferred_discord_delivery": "missing_in_event_log"
                    if not e.get("discord_sent_ts")
                    else "logged",
                    "position_slot_before": e.get("position_slot_before"),
                    "position_slot_after": e.get("position_slot_after"),
                }
            )
        elif et == "observer_exit":
            rows.append(
                {
                    "kind": "EXIT_event",
                    "event_time": t,
                    "symbol": e.get("symbol"),
                    "exit_reason": e.get("exit_reason"),
                    "discord_sent_ts": e.get("discord_sent_ts"),
                    "inferred_discord_delivery": "missing_in_event_log"
                    if not e.get("discord_sent_ts")
                    else "logged",
                }
            )
    for err in errors:
        t = str(err.get("event_time") or "")
        if not _in_window(t, start=start, end=end):
            continue
        et = str(err.get("error_type") or "")
        if et not in ("discord_error", "discord_entry_notify_failed"):
            continue
        rows.append(
            {
                "kind": "discord_failure",
                "event_time": t,
                "symbol": err.get("symbol"),
                "error_type": et,
                "operation": err.get("operation"),
                "message": str(err.get("message") or "")[:120],
            }
        )
    rows.sort(key=lambda r: str(r.get("event_time") or ""))
    return rows


def infer_root_cause(
    *,
    discord_error_first: Optional[str],
    discord_error_last: Optional[str],
    pm_screening_runner_sent: bool,
    pm_subprocess_generated_at: str,
    first_gate_eval_ts: str,
    entry_discord_logged: bool,
) -> str:
    parts: list[str] = []
    if discord_error_first and discord_error_last:
        parts.append(
            f"discord_dns_outage_{discord_error_first[11:16]}_to_{discord_error_last[11:16]}"
        )
    if pm_screening_runner_sent and pm_subprocess_generated_at:
        parts.append(
            f"runner_screening_sent_before_pilot_start_pilot_at_{pm_subprocess_generated_at[11:16]}"
        )
    if not entry_discord_logged:
        parts.append("entry_discord_sent_ts_not_persisted_historical_session")
    if parts:
        return "F_composite:" + "+".join(parts)
    return "F_composite:discord_delivery_gap_and_notification_metadata_missing"


def run_audit(
    *,
    pm_session_dir: Path = PM_SESSION_DIR,
    runner_state_path: Path = RUNNER_STATE,
) -> dict[str, Any]:
    events = _load_jsonl(pm_session_dir / "small_paper_events.jsonl")
    errors = _load_jsonl(pm_session_dir / "errors.jsonl")

    summary: dict[str, Any] = {}
    stdout_path = pm_session_dir / "pilot_stdout.log"
    if stdout_path.is_file():
        text = stdout_path.read_text(encoding="utf-8", errors="replace")
        for key in ("generated_at", "session_ready_ts", "first_gate_eval_ts", "allowed_entry_start"):
            marker = f'"{key}": "'
            if marker in text:
                summary[key] = text.split(marker, 1)[1].split('"', 1)[0]

    runner: dict[str, Any] = {}
    if runner_state_path.is_file():
        runner = json.loads(runner_state_path.read_text(encoding="utf-8"))

    discord_first, discord_last = _discord_error_windows(errors)
    screening = (runner.get("pm_prep") or {}).get("screening_notify") or {}
    pm_wait = (runner.get("pm_wait") or {}).get("after_am") or {}

    entry_7220 = _first_event(events, symbol="7220.T", event_type="accepted")
    exit_7220 = _first_event(
        [e for e in events if str(e.get("event_time") or "") >= "2026-07-08T13:23:00"],
        symbol="7220.T",
        event_type="observer_exit",
    )

    sym_audit = SymbolNotificationAudit(
        symbol="7220.T",
        entry_event=entry_7220,
        exit_event=exit_7220,
        entry_discord_sent=str((entry_7220 or {}).get("discord_sent_ts") or ""),
        exit_discord_sent=str((exit_7220 or {}).get("discord_sent_ts") or ""),
    )

    event_timeline = build_event_timeline(events)
    position_timeline = build_position_count_timeline(events)
    discord_timeline = build_discord_notification_timeline(events, errors)

    root_cause = infer_root_cause(
        discord_error_first=discord_first,
        discord_error_last=discord_last,
        pm_screening_runner_sent=bool(screening.get("sent")),
        pm_subprocess_generated_at=str(summary.get("generated_at") or ""),
        first_gate_eval_ts=str(summary.get("first_gate_eval_ts") or ""),
        entry_discord_logged=bool(sym_audit.entry_discord_sent),
    )

    return {
        "phase": "663a2",
        "verdict": PHASE663A2_VERDICT,
        "pm_session_dir": str(pm_session_dir.relative_to(NATIVE_ROOT)).replace("\\", "/"),
        "pm_subprocess_generated_at": summary.get("generated_at"),
        "session_ready_ts": summary.get("session_ready_ts"),
        "first_gate_eval_ts": summary.get("first_gate_eval_ts"),
        "allowed_entry_start": summary.get("allowed_entry_start"),
        "pm_screening_wait_reached_at": pm_wait.get("reached_at"),
        "universe_screening_generated_at": pm_wait.get("reached_at"),
        "universe_screening_discord_sent_at": "runner_pm_prep_screening_sent_true_no_sent_ts_logged"
        if screening.get("sent")
        else "not_sent",
        "universe_screening_runner_sent": bool(screening.get("sent")),
        "pilot_duplicate_screening_at_subprocess_start": "~"
        + str(summary.get("session_ready_ts") or summary.get("generated_at") or "")[11:19],
        "discord_error_first": discord_first,
        "discord_error_last": discord_last,
        "discord_error_count_session": sum(1 for e in errors if e.get("error_type") == "discord_error"),
        "7220_entry_event": entry_7220,
        "7220_exit_event": exit_7220,
        "7220_entry_discord_sent": sym_audit.entry_discord_sent or None,
        "7220_exit_discord_sent": sym_audit.exit_discord_sent or None,
        "7220_entry_accept_time": (entry_7220 or {}).get("event_time"),
        "7220_exit_time": (exit_7220 or {}).get("event_time"),
        "5801_entry_132345_slot_before": next(
            (
                e.get("position_slot_before")
                for e in events
                if e.get("symbol") == "5801.T"
                and e.get("event_type") == "accepted"
                and e.get("event_time") == "2026-07-08T13:23:45+09:00"
            ),
            None,
        ),
        "5801_entry_132345_slot_after": next(
            (
                e.get("position_slot_after")
                for e in events
                if e.get("symbol") == "5801.T"
                and e.get("event_type") == "accepted"
                and e.get("event_time") == "2026-07-08T13:23:45+09:00"
            ),
            None,
        ),
        "root_cause": root_cause,
        "root_cause_labels": {
            "A_universe_screening_delay": "runner_sent_~12:25_user_saw_~13:23_due_to_discord_outage_or_scroll_context",
            "B_entry_notify_missing": "7220_entry_13:08_failed_during_dns_outage_no_retry_queue",
            "C_discord_order_inversion": "exit_delivered_after_outage_before_missing_entry_notify",
            "D_position_count": "logged_post_register_3/5_not_4/5_for_5801_13:23:45",
            "E_observer_state": "not_primary; exits_match_observer_positions",
        },
        "artifacts": {
            "discord_notification_timeline_csv": f"results/reports/{REPORT_DIR_NAME}/discord_notification_timeline.csv",
            "event_timeline_csv": f"results/reports/{REPORT_DIR_NAME}/event_timeline_13_00_13_30.csv",
            "position_count_timeline_csv": f"results/reports/{REPORT_DIR_NAME}/position_count_timeline.csv",
        },
        "symbol_audit_7220": asdict(sym_audit),
    }


def _csv_fields(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    keys: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for k in row.keys():
            if k not in seen:
                seen.add(k)
                keys.append(str(k))
    return keys


def write_reports(report: Mapping[str, Any], *, report_root: Path = REPORT_ROOT) -> None:
    report_root.mkdir(parents=True, exist_ok=True)
    pm_dir = NATIVE_ROOT / str(report.get("pm_session_dir") or PM_SESSION_DIR.relative_to(NATIVE_ROOT))
    events = _load_jsonl(pm_dir / "small_paper_events.jsonl")
    errors = _load_jsonl(pm_dir / "errors.jsonl")

    discord_rows = build_discord_notification_timeline(events, errors)
    event_rows = build_event_timeline(events)
    position_rows = build_position_count_timeline(events)

    _write_csv(report_root / "discord_notification_timeline.csv", _csv_fields(discord_rows), discord_rows)
    _write_csv(report_root / "event_timeline_13_00_13_30.csv", _csv_fields(event_rows), event_rows)
    _write_csv(report_root / "position_count_timeline.csv", _csv_fields(position_rows), position_rows)

    (report_root / "phase663a2_pm_notification_ordering_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    summary_md = "\n".join(
        [
            "# Phase663A2 PM Notification Ordering Audit",
            "",
            f"- verdict: `{report.get('verdict')}`",
            f"- root_cause: `{report.get('root_cause')}`",
            f"- PM subprocess start (`generated_at`): {report.get('pm_subprocess_generated_at')}",
            f"- first gate eval: {report.get('first_gate_eval_ts')}",
            f"- allowed_entry_start: {report.get('allowed_entry_start')}",
            f"- runner PM screening wait: {report.get('pm_screening_wait_reached_at')}",
            f"- discord errors: {report.get('discord_error_first')} .. {report.get('discord_error_last')} "
            f"({report.get('discord_error_count_session')} total)",
            f"- 7220 ENTRY accept: {report.get('7220_entry_accept_time')}",
            f"- 7220 EXIT: {report.get('7220_exit_time')}",
            f"- 7220 ENTRY discord logged: {report.get('7220_entry_discord_sent')}",
            f"- 5801 13:23:45 slots: {report.get('5801_entry_132345_slot_before')} → "
            f"{report.get('5801_entry_132345_slot_after')}",
            "",
            "## Fix applied (observability)",
            "- Universe Screening embed shows `generated_at` / `sent_at` / `sequence_id`",
            "- ENTRY/EXIT embeds show `event_time`, `sent_time`, `session_id`, `position_id`",
            "- ENTRY slot display uses `pre_count → post_count / max`",
            "- Failed ENTRY Discord posts log `discord_entry_notify_failed` to `errors.jsonl`",
        ]
    )
    (report_root / "phase663a2_fix_summary.md").write_text(summary_md + "\n", encoding="utf-8")


def _run_regression_tests() -> bool:
    import os

    env = dict(os.environ)
    parent = NATIVE_ROOT.parent
    env["PYTHONPATH"] = f"{NATIVE_ROOT / 'src'}{os.pathsep}{parent}"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_phase663a2_pm_notification_ordering.py",
            "-q",
        ],
        cwd=NATIVE_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0


def main() -> int:
    report = run_audit()
    write_reports(report)
    passed = _run_regression_tests()
    report["regression_tests_passed"] = passed
    write_reports(report)
    print(PHASE663A2_VERDICT)
    print(
        json.dumps(
            {
                "root_cause": report.get("root_cause"),
                "7220_entry": report.get("7220_entry_accept_time"),
                "discord_outage_end": report.get("discord_error_last"),
                "regression_tests_passed": passed,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
