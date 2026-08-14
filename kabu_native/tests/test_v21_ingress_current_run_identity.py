"""V21 current-run Ingress identity + certification artifact scope."""
from __future__ import annotations

import json
import time
from pathlib import Path

from small_paper.ingress_run_identity import (
    CURRENT_INGRESS_NOT_READY,
    ROLE_MARKET_INGRESS_SERVICE,
    STALE_INGRESS_STATUS_REJECTED,
    STATUS_SCHEMA_VERSION,
    artifact_matches_scope,
    evaluate_current_run_online,
    stamp_execution_scope,
)
from small_paper.market_ingress_spawn import wait_ingress_online
from small_paper.paper_full_day_certification import copy_scoped_run_snapshot

DAY = "20260812"
NONCE = "launch-nonce-current"
RUN_ID = "ingrun_20260812_launch-nonce-cu"
ACT_ID = "V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V21"
ACT_SHA = "sha-v21-test"
START = "20260815045900.123456+540"
BUS = f"tcp://127.0.0.1:18730|{DAY}|{NONCE}"


def _status(**over: object) -> dict:
    body = {
        "status_schema_version": STATUS_SCHEMA_VERSION,
        "activation_id": ACT_ID,
        "activation_sha": ACT_SHA,
        "ingress_run_id": RUN_ID,
        "launch_nonce": NONCE,
        "pid": 4242,
        "process_start_identity": START,
        "trading_date": DAY,
        "role": ROLE_MARKET_INGRESS_SERVICE,
        "bus_identity": BUS,
        "state": "WAITING_FIRST_PUSH",
        "status_written_unix": time.time(),
        "status_written_monotonic": 1.0,
        "heartbeat_monotonic_age": 0.0,
        "registered_symbol_count": 50,
        "desired_symbol_count": 50,
    }
    body.update(over)
    return body


def _expected(**over: object) -> dict:
    body = {
        "launch_nonce": NONCE,
        "ingress_run_id": RUN_ID,
        "activation_id": ACT_ID,
        "activation_sha": ACT_SHA,
        "trading_date": DAY,
        "pid": 4242,
        "process_start_identity": START,
        "bus_identity": BUS,
    }
    body.update(over)
    return body


def _alive(pid: int = 4242, start: str = START) -> dict:
    return {"exists": True, "create_time": start, "pid": pid, "cmdline": "market_ingress_service"}


def _write_status(day_dir: Path, payload: dict) -> None:
    day_dir.mkdir(parents=True, exist_ok=True)
    (day_dir / "ingress_status.json").write_text(json.dumps(payload), encoding="utf-8")


def test_case_a_dead_pid_rejected(tmp_path: Path) -> None:
    ev = evaluate_current_run_online(
        _status(),
        expected=_expected(),
        query_fn=lambda _pid: {"exists": False, "create_time": ""},
    )
    assert ev["ok"] is False
    assert ev["reject_code"] == "pid_dead"
    assert ev["reason"] == STALE_INGRESS_STATUS_REJECTED


def test_case_b_alive_old_launch_nonce_rejected() -> None:
    ev = evaluate_current_run_online(
        _status(launch_nonce="old-nonce"),
        expected=_expected(),
        query_fn=lambda _pid: _alive(),
    )
    assert ev["ok"] is False
    assert ev["reject_code"] == "launch_nonce_mismatch"


def test_case_c_pid_reuse_start_mismatch_rejected() -> None:
    ev = evaluate_current_run_online(
        _status(),
        expected=_expected(),
        query_fn=lambda _pid: _alive(start="REUSED-PID-NEW-START"),
    )
    assert ev["ok"] is False
    assert ev["reject_code"] == "process_start_identity_mismatch"


def test_case_d_old_activation_rejected() -> None:
    ev = evaluate_current_run_online(
        _status(activation_sha="old-sha"),
        expected=_expected(),
        query_fn=lambda _pid: _alive(),
    )
    assert ev["ok"] is False
    assert ev["reject_code"] == "activation_sha_mismatch"


def test_case_e_heartbeat_stale_rejected() -> None:
    ev = evaluate_current_run_online(
        _status(status_written_unix=time.time() - 120),
        expected=_expected(),
        query_fn=lambda _pid: _alive(),
        heartbeat_max_age_sec=20.0,
    )
    assert ev["ok"] is False
    assert ev["reject_code"] == "heartbeat_stale"


def test_case_f_current_run_online_pass() -> None:
    ev = evaluate_current_run_online(
        _status(state="RUNNING"),
        expected=_expected(),
        query_fn=lambda _pid: _alive(),
    )
    assert ev["ok"] is True
    assert ev["reason"] == "CURRENT_RUN_ONLINE"


def test_wait_rejects_dead_pid_status_then_times_out(tmp_path: Path) -> None:
    native = tmp_path / "kabu_native"
    day_dir = native / "data" / "market_capture" / DAY
    _write_status(day_dir, _status(pid=31200, state="WAITING_FIRST_PUSH"))
    wait = wait_ingress_online(
        native,
        DAY,
        timeout_sec=0.6,
        expected_launch_nonce=NONCE,
        expected_ingress_run_id=RUN_ID,
        expected_activation_id=ACT_ID,
        expected_activation_sha=ACT_SHA,
        expected_pid=4242,
        expected_process_start_identity=START,
        expected_bus_identity=BUS,
        query_fn=lambda _pid: {"exists": False},
    )
    assert wait["ok"] is False
    assert wait["reason"] == CURRENT_INGRESS_NOT_READY
    assert int(wait.get("stale_status_rejected_count") or 0) >= 1
    audit = day_dir / "ingress_wait_audit.jsonl"
    assert audit.is_file()
    assert STALE_INGRESS_STATUS_REJECTED in audit.read_text(encoding="utf-8")


def test_restart_waiter_accepts_only_b(tmp_path: Path) -> None:
    native = tmp_path / "kabu_native"
    day_dir = native / "data" / "market_capture" / DAY
    status_a = _status(launch_nonce="nonce-A", ingress_run_id="run-A", pid=111, process_start_identity="start-A")
    _write_status(day_dir, status_a)
    wait_a = wait_ingress_online(
        native,
        DAY,
        timeout_sec=0.4,
        expected_launch_nonce="nonce-B",
        expected_ingress_run_id="run-B",
        expected_activation_id=ACT_ID,
        expected_activation_sha=ACT_SHA,
        expected_pid=222,
        expected_process_start_identity="start-B",
        expected_bus_identity=BUS,
        query_fn=lambda pid: _alive(pid=222, start="start-B") if int(pid) == 222 else {"exists": False},
    )
    assert wait_a["ok"] is False
    assert int(wait_a.get("stale_status_rejected_count") or 0) >= 1

    status_b = _status(
        launch_nonce="nonce-B",
        ingress_run_id="run-B",
        pid=222,
        process_start_identity="start-B",
        bus_identity=BUS,
        state="RUNNING",
    )
    _write_status(day_dir, status_b)
    wait_b = wait_ingress_online(
        native,
        DAY,
        timeout_sec=1.0,
        expected_launch_nonce="nonce-B",
        expected_ingress_run_id="run-B",
        expected_activation_id=ACT_ID,
        expected_activation_sha=ACT_SHA,
        expected_pid=222,
        expected_process_start_identity="start-B",
        expected_bus_identity=BUS,
        query_fn=lambda pid: _alive(pid=222, start="start-B"),
    )
    assert wait_b["ok"] is True
    assert wait_b["pid"] == 222
    assert wait_b["launch_nonce"] == "nonce-B"


def test_stale_artifact_excluded_from_failed_tests(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    old = {
        "verdict": "preflight_blocked",
        "stopped_reason": "preflight_blocked",
        "preflight": {"safety": {"failed_check_ids": ["kabu_station_connection"]}},
        "certification_run_id": "old-cert",
        "stage_run_id": "old-stage",
        "activation_sha": "old-sha",
    }
    (reports / f"phase148_am_pm_daily_runner_{DAY}.json").write_text(json.dumps(old), encoding="utf-8")
    (reports / f"small_paper_safety_{DAY}.json").write_text(
        json.dumps(
            {
                "failed_check_ids": ["kabu_station_connection"],
                "certification_run_id": "old-cert",
                "stage_run_id": "old-stage",
                "activation_sha": "old-sha",
            }
        ),
        encoding="utf-8",
    )
    dest = tmp_path / "snap"
    copied = copy_scoped_run_snapshot(
        dest=dest,
        reports_dir=reports,
        day=DAY,
        expected_scope={
            "certification_run_id": "current-cert",
            "stage_run_id": "current-stage",
            "activation_sha": ACT_SHA,
        },
    )
    assert copied["stale_artifact_excluded_count"] >= 2
    assert not copied["copied"]
    assert not (dest / f"phase148_am_pm_daily_runner_{DAY}.json").is_file()


def test_current_artifact_copied() -> None:
    doc = {"verdict": "am_pm_daily_runner_ready"}
    stamp_execution_scope(
        doc,
        environ={
            "TRADEBOT_CERTIFICATION_RUN_ID": "c1",
            "TRADEBOT_CERT_STAGE_RUN_ID": "s1",
            "TRADEBOT_INGRESS_ACTIVATION_ID": ACT_ID,
            "TRADEBOT_INGRESS_ACTIVATION_SHA": ACT_SHA,
        },
    )
    assert artifact_matches_scope(
        doc,
        {"certification_run_id": "c1", "stage_run_id": "s1", "activation_sha": ACT_SHA},
    )


def test_partial_json_not_online(tmp_path: Path) -> None:
    ev = evaluate_current_run_online(
        {"state": "RUNNING", "pid": 1},
        expected=_expected(),
        query_fn=lambda _pid: _alive(),
    )
    assert ev["ok"] is False
    assert str(ev.get("reject_code") or "").startswith("missing_field:")


def test_heartbeat_uses_unix_not_session_clock() -> None:
    # Fresh unix write must pass even if session `at` looks old.
    ev = evaluate_current_run_online(
        _status(at="2026-08-12T08:50:00.000+09:00", last_push_at="2026-08-12T08:50:00.000+09:00"),
        expected=_expected(),
        query_fn=lambda _pid: _alive(),
        now_unix=time.time(),
    )
    assert ev["ok"] is True
