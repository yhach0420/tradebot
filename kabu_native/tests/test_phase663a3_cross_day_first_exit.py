"""Phase663A3 — cross-day PM first-EXIT reproduction audit tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from research.phase663a3_cross_day_first_exit_audit import (
    PHASE663A3_VERDICT,
    PM_SESSIONS,
    _accept_before_exit,
    _infer_entry_notify_status,
    audit_pm_day,
    build_discord_error_windows_csv,
    run_audit,
)

ROOT = Path(__file__).resolve().parents[1]


def test_infer_entry_notify_during_discord_outage():
    status = _infer_entry_notify_status(
        {"event_time": "2026-07-08T13:08:09+09:00"},
        discord_error_first="2026-07-08T13:00:14+09:00",
        discord_error_last="2026-07-08T13:20:20+09:00",
        discord_error_count=10,
        stdout_discord_error_count="389",
    )
    assert status == "inferred_failed_during_discord_outage"


def test_infer_entry_notify_metadata_gap_when_no_errors():
    status = _infer_entry_notify_status(
        {"event_time": "2026-07-07T12:58:53+09:00"},
        discord_error_first=None,
        discord_error_last=None,
        discord_error_count=0,
        stdout_discord_error_count="0",
    )
    assert status == "likely_sent_metadata_not_logged"


def test_20260707_first_exit_has_pm_entry():
    spec = PM_SESSIONS["20260707"]
    session_dir = ROOT / spec["session_dir"]
    if not (session_dir / "small_paper_events.jsonl").is_file():
        pytest.skip("fixture missing")
    day = audit_pm_day(
        trade_date="20260707",
        session_dir=session_dir,
        runner_state=ROOT / spec["runner_state"],
        pm_allowed_start=spec["pm_allowed_start"],
    )
    assert day.first_exit_symbol == "6327.T"
    assert day.pm_entry_exists is True
    assert day.pm_entry_event_time == "2026-07-07T12:58:53+09:00"
    assert day.discord_error_count == 0
    assert day.session_id_mismatch is False


def test_20260708_first_exit_inferred_notify_failure():
    spec = PM_SESSIONS["20260708"]
    session_dir = ROOT / spec["session_dir"]
    if not (session_dir / "small_paper_events.jsonl").is_file():
        pytest.skip("fixture missing")
    day = audit_pm_day(
        trade_date="20260708",
        session_dir=session_dir,
        runner_state=ROOT / spec["runner_state"],
        pm_allowed_start=spec["pm_allowed_start"],
    )
    assert day.first_exit_symbol == "4424.T"
    assert day.pm_entry_event_time == "2026-07-08T13:00:19+09:00"
    assert day.entry_notify_inferred_status == "inferred_failed_during_discord_outage"
    assert day.discord_error_count == 389


def test_accept_before_exit_pairs_pm_entry():
    events = [
        {"event_type": "accepted", "symbol": "6327.T", "event_time": "2026-07-07T12:58:53+09:00"},
        {"event_type": "observer_exit", "symbol": "6327.T", "event_time": "2026-07-07T12:59:23+09:00"},
    ]
    acc = _accept_before_exit(
        events,
        symbol="6327.T",
        exit_time="2026-07-07T12:59:23+09:00",
        pm_allowed_start="2026-07-07T12:33:00",
    )
    assert acc is not None
    assert acc["event_time"] == "2026-07-07T12:58:53+09:00"


def test_run_audit_produces_report():
    spec = PM_SESSIONS["20260708"]
    if not (ROOT / spec["session_dir"] / "small_paper_events.jsonl").is_file():
        pytest.skip("fixture missing")
    report = run_audit()
    assert report["verdict"] == PHASE663A3_VERDICT
    assert len(report["days"]) == 2
    assert report["comparison"]["both_pm_entry_before_first_exit"] is True
    windows = build_discord_error_windows_csv()
    assert len(windows) == 2
    assert windows[1]["discord_error_count"] == 389


def test_report_artifacts_exist_after_main():
    report_path = ROOT / "results" / "reports" / "phase663a3_cross_day_first_exit" / "pm_first_exit_root_cause_report.json"
    if not report_path.is_file():
        pytest.skip("run phase663a3 audit script first")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report.get("verdict") == PHASE663A3_VERDICT
    assert len(report.get("days") or []) == 2
