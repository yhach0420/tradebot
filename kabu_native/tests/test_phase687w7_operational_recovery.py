"""Phase687W7 — Operational recovery dry-run tests."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from small_paper.kabu_order_request_builder import actual_broker_submit_count
from small_paper.live_order_safety_sm import KabuBrokerAdapter
from small_paper.operational_recovery import (
    ClockState,
    DiskState,
    JournalIntegrityStatus,
    OperatorAckStatus,
    RecoveryMode,
    check_journal_integrity,
    classify_disk,
    create_session_manifest,
    diagnose_clock,
    disk_guard_report,
    dryrun_ready_evidence,
    evaluate_recovery_readiness,
    finalize_session_manifest,
    mode_allows_entry,
    recovery_mode_matrix,
    run_fault_injection_matrix,
    run_file_failure_tests,
    run_kill_switch_drills,
    run_restart_drills,
    sample_operator_recovery_ack,
    validate_session_manifest,
    verify_session_seal,
    write_session_seal,
)
from small_paper.check_live_order_recovery_readiness import main as recovery_cli_main

JST = ZoneInfo("Asia/Tokyo")


def test_recovery_modes_fail_closed_entry():
    assert mode_allows_entry(RecoveryMode.NORMAL.value) is True
    assert mode_allows_entry(RecoveryMode.KILL_SWITCH_ACTIVE.value) is False
    assert mode_allows_entry(RecoveryMode.JOURNAL_RECOVERY_REQUIRED.value) is False
    assert mode_allows_entry("UNKNOWN_MODE") is False
    assert len(recovery_mode_matrix()) == 8


def test_session_manifest_create_update_seal(tmp_path: Path):
    create_session_manifest(session_id="s1", output_dir=tmp_path, config_sha="abc")
    v1 = validate_session_manifest(tmp_path / "session_manifest.json")
    assert v1["valid"]
    create_session_manifest(session_id="s1", output_dir=tmp_path)  # restart++
    man = json.loads((tmp_path / "session_manifest.json").read_text(encoding="utf-8"))
    assert man["restart_count"] >= 1
    finalize_session_manifest(tmp_path, canonical_entry_count=2, submit_count=0, cancel_count=0)
    man2 = json.loads((tmp_path / "session_manifest.json").read_text(encoding="utf-8"))
    assert man2["sealed"] is True
    assert man2["live_trading_enabled"] is False
    assert man2["production_approval_status"] == "NOT_AUTHORIZED"


def test_session_seal_hash_and_mismatch(tmp_path: Path):
    (tmp_path / "events.jsonl").write_text('{"x":1}\n', encoding="utf-8")
    write_session_seal(tmp_path)
    ok = verify_session_seal(tmp_path / "session_seal.json", tmp_path)
    assert ok["valid"]
    (tmp_path / "events.jsonl").write_text('{"x":2}\n', encoding="utf-8")
    bad = verify_session_seal(tmp_path / "session_seal.json", tmp_path)
    assert bad["valid"] is False


def test_journal_partial_tail_preserves_original(tmp_path: Path):
    p = tmp_path / "order_intents.jsonl"
    original = '{"sequence":1}\n{"sequence":2,"partial'
    p.write_text(original, encoding="utf-8")
    ji = check_journal_integrity(p)
    assert ji.status == JournalIntegrityStatus.JOURNAL_PARTIAL_TAIL.value
    assert ji.entry_blocked
    assert ji.original_preserved
    assert Path(ji.recovery_copy_path).is_file()
    assert p.read_text(encoding="utf-8") == original


def test_journal_gap_and_duplicate_block_entry(tmp_path: Path):
    g = tmp_path / "gap.jsonl"
    g.write_text('{"sequence":1}\n{"sequence":3}\n', encoding="utf-8")
    assert check_journal_integrity(g).status == JournalIntegrityStatus.JOURNAL_SEQUENCE_GAP.value
    d = tmp_path / "dup.jsonl"
    d.write_text('{"sequence":1}\n{"sequence":1}\n', encoding="utf-8")
    assert check_journal_integrity(d).status == JournalIntegrityStatus.JOURNAL_DUPLICATE.value


def test_kill_switch_drills(tmp_path: Path):
    r = run_kill_switch_drills(tmp_path)
    assert r["pass"]
    assert r["submit_delta"] == 0
    assert r["A"]["entry_allowed"] is False
    assert r["B"]["real_cancel_sent"] is False
    assert r["E"]["kill_switch_restored"] is True


def test_restart_drills_no_resubmit(tmp_path: Path):
    r = run_restart_drills(tmp_path)
    assert r["pass"]
    assert r["submit_delta"] == 0
    assert all(c["resubmit"] is False for c in r["cases"])


def test_file_failure_blocks_would_submit(tmp_path: Path):
    rows = run_file_failure_tests(tmp_path)
    assert rows
    assert all(r["would_submit_forbidden"] and r["pass"] for r in rows)


def test_disk_guard_no_auto_delete():
    r = disk_guard_report(".")
    assert r["auto_delete_forbidden"] is True
    assert r["raw_push_auto_delete"] is False
    assert classify_disk(83.0) == DiskState.WARNING.value
    assert classify_disk(91.0) == DiskState.CRITICAL.value


def test_clock_future_and_rollback():
    fut = diagnose_clock(samples=[{"wall_time": (datetime.now(JST) + timedelta(hours=2)).isoformat()}])
    assert fut["clock_state"] == ClockState.FUTURE_TIMESTAMP.value
    assert fut["latency_samples_valid"] is False
    assert fut["os_clock_not_modified"] is True


def test_operator_ack_sample_only():
    ack = sample_operator_recovery_ack()
    assert ack["acknowledgment_status"] == OperatorAckStatus.SAMPLE_ONLY.value
    assert ack["production_authorization"] == "FORBIDDEN"


def test_dryrun_ready_exit_0_not_authorized():
    r = evaluate_recovery_readiness(dryrun_ready_evidence())
    assert r["exit_code"] == 0
    assert r["recovery_ready"] is True
    assert r["production_authorized"] is False


def test_cli_demo_ready():
    assert recovery_cli_main(["--demo-ready"]) == 0


def test_fault_injection_matrix(tmp_path: Path):
    rows = run_fault_injection_matrix(tmp_path)
    cases = {r["case"] for r in rows}
    for required in (
        "journal_partial_tail",
        "journal_missing_sequence",
        "duplicate_sequence",
        "duplicate_intent",
        "state_conflict",
        "kill_switch_restart",
        "disk_critical",
        "clock_rollback",
        "hash_mismatch",
        "operator_ack_missing",
        "config_sha_mismatch",
    ):
        assert required in cases
    assert all(r["submit_count"] == 0 and r["cancel_count"] == 0 for r in rows)
    assert all(r["pass"] for r in rows)


def test_network_isolation_hard_fail():
    assert actual_broker_submit_count() == 0
    try:
        KabuBrokerAdapter().submit_entry_order({"symbol": "X", "quantity": 1})
        assert False
    except RuntimeError as exc:
        assert "HARD_FAIL" in str(exc)
    try:
        KabuBrokerAdapter().cancel_order("OID")
        assert False
    except RuntimeError as exc:
        assert "HARD_FAIL" in str(exc)
