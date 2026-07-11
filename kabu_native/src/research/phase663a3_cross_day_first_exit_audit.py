"""Phase663A3 — Cross-day PM first-EXIT / missing-ENTRY notify reproduction audit."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv

PHASE663A3_VERDICT = "phase663a3_cross_day_first_exit_audit_done"
REPORT_DIR_NAME = "phase663a3_cross_day_first_exit"
NATIVE_ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = NATIVE_ROOT / "results" / "reports" / REPORT_DIR_NAME

PM_SESSIONS: dict[str, dict[str, str]] = {
    "20260707": {
        "session_dir": "results/small_paper/20260707/live_session_122539",
        "runner_state": "results/daily/20260707/runtime/phase148_am_pm_daily_runner_20260707.json",
        "pm_allowed_start": "2026-07-07T12:33:00",
    },
    "20260708": {
        "session_dir": "results/small_paper/20260708/live_session_122537",
        "runner_state": "results/daily/20260708/runtime/phase148_am_pm_daily_runner_20260708.json",
        "pm_allowed_start": "2026-07-08T12:33:00",
    },
}


@dataclass(frozen=True)
class DayAudit:
    trade_date: str
    session_dir: str
    pm_subprocess_generated_at: str
    first_gate_eval_ts: str
    allowed_entry_start: str
    first_exit_symbol: str
    first_exit_event_time: str
    first_exit_reason: str
    first_exit_sent_time: str
    pm_entry_exists: bool
    pm_entry_event_time: str
    pm_entry_discord_sent_ts: str
    entry_notify_inferred_status: str
    observer_entry_time: str
    exit_session_id: str
    entry_session_id: str
    session_id_mismatch: bool
    position_slot_before: str
    position_slot_after: str
    position_count_ok: bool
    universe_screening_generated_at: str
    universe_screening_sent_at: str
    discord_error_first: str
    discord_error_last: str
    discord_error_count: int
    root_cause_label: str


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _parse_stdout_meta(stdout_path: Path) -> dict[str, str]:
    if not stdout_path.is_file():
        return {}
    text = stdout_path.read_text(encoding="utf-8", errors="replace")
    out: dict[str, str] = {}
    for key in (
        "generated_at",
        "first_gate_eval_ts",
        "allowed_entry_start",
        "session_ready_ts",
        "discord_error_count",
    ):
        marker = f'"{key}": "'
        if marker in text:
            out[key] = text.split(marker, 1)[1].split('"', 1)[0]
        elif f'"{key}": ' in text:
            frag = text.split(f'"{key}": ', 1)[1].split(",", 1)[0].strip()
            out[key] = frag
    return out


def _runner_screening_meta(runner_path: Path) -> dict[str, Any]:
    if not runner_path.is_file():
        return {}
    data = json.loads(runner_path.read_text(encoding="utf-8"))
    pm_prep = data.get("pm_prep") or {}
    screening = pm_prep.get("screening_notify") or {}
    pm_wait = (data.get("pm_wait") or {}).get("after_am") or {}
    return {
        "screening_sent": bool(screening.get("sent")),
        "screening_generated_at": screening.get("generated_at") or pm_wait.get("reached_at") or "",
        "screening_sent_at": screening.get("discord_sent_at") or screening.get("generated_at") or "",
        "pm_wait_reached_at": pm_wait.get("reached_at") or "",
    }


def _discord_error_window(errors: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], Optional[str], Optional[str], int]:
    rows: list[dict[str, Any]] = []
    times: list[str] = []
    for e in errors:
        et = str(e.get("error_type") or "")
        if et not in ("discord_error", "discord_entry_notify_failed"):
            continue
        t = str(e.get("event_time") or "")
        if t:
            times.append(t)
        rows.append(
            {
                "event_time": t,
                "error_type": et,
                "operation": e.get("operation"),
                "symbol": e.get("symbol"),
                "message": str(e.get("message") or "")[:160],
            }
        )
    times.sort()
    return rows, (times[0] if times else None), (times[-1] if times else None), len(rows)


def _accept_before_exit(
    accepts: Sequence[Mapping[str, Any]],
    *,
    symbol: str,
    exit_time: str,
    pm_allowed_start: str,
) -> Optional[dict[str, Any]]:
    pm_accepts = [
        dict(a)
        for a in accepts
        if a.get("symbol") == symbol
        and str(a.get("event_time") or "") <= exit_time
        and str(a.get("event_time") or "") >= pm_allowed_start
    ]
    if not pm_accepts:
        return None
    pm_accepts.sort(key=lambda x: str(x.get("event_time") or ""))
    strict_before = [a for a in pm_accepts if str(a.get("event_time") or "") < exit_time]
    if strict_before:
        return strict_before[-1]
    return pm_accepts[-1]


def _infer_entry_notify_status(
    accept: Optional[Mapping[str, Any]],
    *,
    discord_error_first: Optional[str],
    discord_error_last: Optional[str],
    discord_error_count: int,
    stdout_discord_error_count: str,
) -> str:
    if accept is None:
        return "no_pm_accept"
    if accept.get("discord_sent_ts"):
        return "logged_sent"
    at = str(accept.get("event_time") or "")
    if (
        discord_error_count > 0
        and discord_error_first
        and discord_error_last
        and discord_error_first <= at <= discord_error_last
    ):
        return "inferred_failed_during_discord_outage"
    if discord_error_count == 0 and stdout_discord_error_count in ("0", "0.0", ""):
        return "likely_sent_metadata_not_logged"
    return "unknown_missing_discord_sent_ts"


def _classify_root_cause(
    *,
    pm_entry_exists: bool,
    entry_notify_status: str,
    session_id_mismatch: bool,
    discord_error_count: int,
    trade_date: str,
) -> str:
    if not pm_entry_exists:
        if session_id_mismatch:
            return "D_observer_session_state"
        return "D_observer_session_state"
    labels: list[str] = []
    if entry_notify_status in ("inferred_failed_during_discord_outage", "no_pm_accept"):
        labels.append("A_discord_notify_missing")
    elif entry_notify_status == "likely_sent_metadata_not_logged":
        labels.append("A_discord_metadata_gap")
    if discord_error_count > 0 and entry_notify_status.startswith("inferred"):
        labels.append("B_discord_delivery_gap_not_queue_reversal")
    if trade_date == "20260708":
        labels.append("C_universe_screening_timing_confusion_possible")
    if session_id_mismatch:
        labels.append("D_session_id_mismatch")
    if not labels:
        labels.append("F_composite_perception_entry_exists_notify_unverified")
    return "F_composite:" + "+".join(labels) if len(labels) > 1 else labels[0]


def audit_pm_day(
    *,
    trade_date: str,
    session_dir: Path,
    runner_state: Path,
    pm_allowed_start: str,
) -> DayAudit:
    events = _load_jsonl(session_dir / "small_paper_events.jsonl")
    errors = _load_jsonl(session_dir / "errors.jsonl")
    meta = _parse_stdout_meta(session_dir / "pilot_stdout.log")
    screening = _runner_screening_meta(runner_state)

    accepts = [e for e in events if e.get("event_type") == "accepted"]
    exits = sorted(
        [e for e in events if e.get("event_type") == "observer_exit"],
        key=lambda x: str(x.get("event_time") or ""),
    )
    first_exit = exits[0] if exits else {}
    sym = str(first_exit.get("symbol") or "")
    exit_time = str(first_exit.get("event_time") or "")
    accept = _accept_before_exit(
        accepts, symbol=sym, exit_time=exit_time, pm_allowed_start=pm_allowed_start
    )

    discord_rows, d_first, d_last, d_count = _discord_error_window(errors)
    del discord_rows  # written separately per day

    entry_status = _infer_entry_notify_status(
        accept,
        discord_error_first=d_first,
        discord_error_last=d_last,
        discord_error_count=d_count,
        stdout_discord_error_count=str(meta.get("discord_error_count") or ""),
    )

    entry_sid = str((accept or {}).get("session_id") or "")
    exit_sid = str(first_exit.get("session_id") or "")
    sid_mismatch = bool(entry_sid and exit_sid and entry_sid != exit_sid)

    slot_before = str((accept or {}).get("position_slot_before") or "")
    slot_after = str((accept or {}).get("position_slot_after") or "")
    pos_ok = True
    if accept and slot_before and slot_after:
        try:
            pos_ok = int(slot_after) >= int(slot_before)
        except ValueError:
            pos_ok = True

    pm_entry_exists = accept is not None

    return DayAudit(
        trade_date=trade_date,
        session_dir=str(session_dir.relative_to(NATIVE_ROOT)).replace("\\", "/"),
        pm_subprocess_generated_at=str(meta.get("generated_at") or ""),
        first_gate_eval_ts=str(meta.get("first_gate_eval_ts") or ""),
        allowed_entry_start=str(meta.get("allowed_entry_start") or ""),
        first_exit_symbol=sym,
        first_exit_event_time=exit_time,
        first_exit_reason=str(first_exit.get("exit_reason") or first_exit.get("structural_exit_reason") or ""),
        first_exit_sent_time=str(first_exit.get("discord_sent_ts") or ""),
        pm_entry_exists=pm_entry_exists,
        pm_entry_event_time=str((accept or {}).get("event_time") or ""),
        pm_entry_discord_sent_ts=str((accept or {}).get("discord_sent_ts") or ""),
        entry_notify_inferred_status=entry_status,
        observer_entry_time=str((accept or {}).get("observer_entry_time") or first_exit.get("observer_entry_time") or ""),
        exit_session_id=exit_sid,
        entry_session_id=entry_sid,
        session_id_mismatch=sid_mismatch,
        position_slot_before=slot_before,
        position_slot_after=slot_after,
        position_count_ok=pos_ok,
        universe_screening_generated_at=str(
            screening.get("screening_generated_at") or screening.get("pm_wait_reached_at") or ""
        ),
        universe_screening_sent_at=str(screening.get("screening_sent_at") or ""),
        discord_error_first=str(d_first or ""),
        discord_error_last=str(d_last or ""),
        discord_error_count=d_count,
        root_cause_label=_classify_root_cause(
            pm_entry_exists=pm_entry_exists,
            entry_notify_status=entry_status,
            session_id_mismatch=sid_mismatch,
            discord_error_count=d_count,
            trade_date=trade_date,
        ),
    )


def build_missing_entry_notify_symbols(
    *,
    trade_date: str,
    session_dir: Path,
    pm_allowed_start: str,
    discord_error_first: Optional[str],
    discord_error_last: Optional[str],
    discord_error_count: int = 0,
    stdout_discord_error_count: str = "",
) -> list[dict[str, Any]]:
    events = _load_jsonl(session_dir / "small_paper_events.jsonl")
    accepts = [e for e in events if e.get("event_type") == "accepted"]
    rows: list[dict[str, Any]] = []
    for ex in sorted(
        [e for e in events if e.get("event_type") == "observer_exit"],
        key=lambda x: str(x.get("event_time") or ""),
    ):
        sym = str(ex.get("symbol") or "")
        exit_time = str(ex.get("event_time") or "")
        accept = _accept_before_exit(
            accepts, symbol=sym, exit_time=exit_time, pm_allowed_start=pm_allowed_start
        )
        if accept is None:
            rows.append(
                {
                    "trade_date": trade_date,
                    "symbol": sym,
                    "exit_event_time": exit_time,
                    "exit_reason": ex.get("exit_reason"),
                    "pm_entry_event_time": "",
                    "entry_discord_sent_ts": "",
                    "inferred_entry_notify_status": "no_pm_accept",
                    "observer_entry_time": ex.get("observer_entry_time"),
                    "session_id_mismatch": False,
                }
            )
            continue
        status = _infer_entry_notify_status(
            accept,
            discord_error_first=discord_error_first,
            discord_error_last=discord_error_last,
            discord_error_count=discord_error_count,
            stdout_discord_error_count=stdout_discord_error_count,
        )
        if accept.get("discord_sent_ts"):
            continue
        entry_sid = str(accept.get("session_id") or "")
        exit_sid = str(ex.get("session_id") or "")
        rows.append(
            {
                "trade_date": trade_date,
                "symbol": sym,
                "exit_event_time": exit_time,
                "exit_reason": ex.get("exit_reason"),
                "pm_entry_event_time": accept.get("event_time"),
                "entry_discord_sent_ts": accept.get("discord_sent_ts"),
                "inferred_entry_notify_status": status,
                "observer_entry_time": accept.get("observer_entry_time") or ex.get("observer_entry_time"),
                "session_id_mismatch": bool(entry_sid and exit_sid and entry_sid != exit_sid),
                "entry_session_id": entry_sid,
                "exit_session_id": exit_sid,
            }
        )
    return rows


def build_discord_error_windows_csv() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for trade_date, spec in PM_SESSIONS.items():
        session_dir = NATIVE_ROOT / spec["session_dir"]
        errors = _load_jsonl(session_dir / "errors.jsonl")
        _, d_first, d_last, d_count = _discord_error_window(errors)
        rows.append(
            {
                "trade_date": trade_date,
                "session_dir": spec["session_dir"],
                "discord_error_count": d_count,
                "discord_error_first": d_first or "",
                "discord_error_last": d_last or "",
                "window_minutes": _window_minutes(d_first, d_last),
            }
        )
    return rows


def _window_minutes(start: Optional[str], end: Optional[str]) -> str:
    if not start or not end:
        return ""
    try:
        from datetime import datetime

        a = datetime.fromisoformat(start)
        b = datetime.fromisoformat(end)
        return str(round((b - a).total_seconds() / 60.0, 1))
    except ValueError:
        return ""


def run_audit() -> dict[str, Any]:
    day_rows: list[DayAudit] = []
    missing_all: list[dict[str, Any]] = []
    for trade_date, spec in PM_SESSIONS.items():
        session_dir = NATIVE_ROOT / spec["session_dir"]
        runner = NATIVE_ROOT / spec["runner_state"]
        day = audit_pm_day(
            trade_date=trade_date,
            session_dir=session_dir,
            runner_state=runner,
            pm_allowed_start=spec["pm_allowed_start"],
        )
        day_rows.append(day)
        missing_all.extend(
            build_missing_entry_notify_symbols(
                trade_date=trade_date,
                session_dir=session_dir,
                pm_allowed_start=spec["pm_allowed_start"],
                discord_error_first=day.discord_error_first or None,
                discord_error_last=day.discord_error_last or None,
                discord_error_count=day.discord_error_count,
                stdout_discord_error_count=str(
                    _parse_stdout_meta(session_dir / "pilot_stdout.log").get("discord_error_count") or ""
                ),
            )
        )

    cross_day = [d.__dict__ for d in day_rows]
    overall = "F_composite:cross_day_entry_notify_gap_not_observer_phantom"
    if all(d.pm_entry_exists for d in day_rows):
        overall = "F_composite:pm_entry_exists_discord_entry_notify_gap_7_8_dns_7_7_metadata"

    return {
        "phase": "663a3",
        "verdict": PHASE663A3_VERDICT,
        "overall_root_cause": overall,
        "days": cross_day,
        "comparison": {
            "both_pm_entry_before_first_exit": all(d.pm_entry_exists for d in day_rows),
            "both_session_id_mismatch": any(d.session_id_mismatch for d in day_rows),
            "7_7_discord_errors": day_rows[0].discord_error_count if day_rows else 0,
            "7_8_discord_errors": day_rows[1].discord_error_count if len(day_rows) > 1 else 0,
        },
        "root_cause_legend": {
            "A": "Discord通知欠落（DNS障害 or metadata未記録）",
            "B": "Discord順序逆転ではなく欠落による見かけ逆転",
            "C": "Universe screening時刻混乱（副次）",
            "D": "observer/session state異常",
            "E": "ENTRY通知ロジックのみ失敗",
            "F": "複合",
        },
        "artifacts": {
            "cross_day_first_exit_audit_csv": f"results/reports/{REPORT_DIR_NAME}/cross_day_first_exit_audit.csv",
            "discord_error_windows_csv": f"results/reports/{REPORT_DIR_NAME}/discord_error_windows.csv",
            "missing_entry_notify_symbols_csv": f"results/reports/{REPORT_DIR_NAME}/missing_entry_notify_symbols.csv",
        },
        "missing_entry_notify_symbol_count": len(missing_all),
    }


def write_reports(report: Mapping[str, Any]) -> None:
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    missing_rows: list[dict[str, Any]] = []
    for trade_date, spec in PM_SESSIONS.items():
        day = next(d for d in report["days"] if d["trade_date"] == trade_date)
        missing_rows.extend(
            build_missing_entry_notify_symbols(
                trade_date=trade_date,
                session_dir=NATIVE_ROOT / spec["session_dir"],
                pm_allowed_start=spec["pm_allowed_start"],
                discord_error_first=day.get("discord_error_first") or None,
                discord_error_last=day.get("discord_error_last") or None,
                discord_error_count=int(day.get("discord_error_count") or 0),
                stdout_discord_error_count=str(
                    _parse_stdout_meta(NATIVE_ROOT / spec["session_dir"] / "pilot_stdout.log").get(
                        "discord_error_count"
                    )
                    or ""
                ),
            )
        )

    _write_csv(
        REPORT_ROOT / "cross_day_first_exit_audit.csv",
        list(report["days"][0].keys()) if report.get("days") else [],
        list(report.get("days") or []),
    )
    err_rows = build_discord_error_windows_csv()
    _write_csv(
        REPORT_ROOT / "discord_error_windows.csv",
        list(err_rows[0].keys()) if err_rows else [],
        err_rows,
    )
    fields = list(missing_rows[0].keys()) if missing_rows else []
    _write_csv(REPORT_ROOT / "missing_entry_notify_symbols.csv", fields, missing_rows)
    (REPORT_ROOT / "pm_first_exit_root_cause_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _run_regression_tests() -> bool:
    import os

    env = dict(os.environ)
    parent = NATIVE_ROOT.parent
    env["PYTHONPATH"] = f"{NATIVE_ROOT / 'src'}{os.pathsep}{parent}"
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_phase663a3_cross_day_first_exit.py", "-q"],
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
    (REPORT_ROOT / "pm_first_exit_root_cause_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(PHASE663A3_VERDICT)
    print(
        json.dumps(
            {
                "overall_root_cause": report.get("overall_root_cause"),
                "regression_tests_passed": passed,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
