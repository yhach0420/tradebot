"""Phase687W7A — Stateful journal recovery + session seal tests."""

from __future__ import annotations

import json
from pathlib import Path

from small_paper.kabu_order_request_builder import actual_broker_submit_count
from small_paper.live_order_safety_sm import KabuBrokerAdapter, OrderLifecycleState, build_engine
from small_paper.stateful_journal_recovery import (
    REQUIRED_SEAL_ARTIFACTS,
    StatefulJournalWriter,
    build_full_session_seal,
    detect_post_seal_mutation,
    restored_order_detail_rows,
    run_seal_mutation_tests,
    run_stateful_restart_matrix,
    soak_w7a_fields,
    w4s_ready_extra_ok,
    write_full_session_seal,
)


def test_stateful_matrix_all_pass(tmp_path: Path):
    rows = run_stateful_restart_matrix(tmp_path)
    by = {r["stop_point"]: r for r in rows}
    assert by["intent_created"]["restored_order_count"] == 1
    assert by["journal_committed"]["restored_order_count"] == 1
    assert by["acknowledged"]["entry_state"] == "ACKNOWLEDGED"
    assert by["partially_filled"]["restored_fill_quantity"] == 30
    assert by["partially_filled"]["restored_remaining_quantity"] == 70
    assert by["entry_filled"]["position_qty"] == 100
    assert by["entry_filled"]["restored_reservation_count"] == 0
    assert by["exit_intent"]["restored_order_count"] == 2
    assert by["partial_exit"]["position_qty"] == 60
    assert by["kill_switch_active"]["kill_switch_match"] is True
    assert by["kill_switch_active"]["recovery_mode"] == "KILL_SWITCH_ACTIVE"
    assert all(r["automatic_resubmit_count"] == 0 for r in rows)
    assert all(r["submit_count"] == 0 and r["cancel_count"] == 0 for r in rows)
    assert all(r["pass"] for r in rows)


def test_partial_fill_object_compare(tmp_path: Path):
    w = StatefulJournalWriter(tmp_path, "pf")
    w.write_partially_filled()
    eng = build_engine(output_dir=tmp_path, session_id="pf")
    r = eng.restore_from_journal()
    o = next(iter(eng.orders.values()))
    assert o.quantity == 100 and o.filled_qty == 30
    assert eng.ledger.open_positions.get("7203") == 30
    open_res = [x for x in eng.ledger.reservations.values() if not x.released][0]
    assert open_res.quantity - open_res.filled_qty == 70
    assert r["resubmit"] is False


def test_kill_switch_no_auto_normal(tmp_path: Path):
    w = StatefulJournalWriter(tmp_path, "ks")
    w.write_kill_switch_active()
    eng = build_engine(output_dir=tmp_path, session_id="ks")
    r = eng.restore_from_journal()
    assert eng.kill_switch and eng.entry_blocked
    assert r["recovery_mode"] == "KILL_SWITCH_ACTIVE"
    assert r["recovery_mode"] != "NORMAL"


def test_no_automatic_resubmit(tmp_path: Path):
    w = StatefulJournalWriter(tmp_path, "ack")
    w.write_acknowledged()
    eng = build_engine(output_dir=tmp_path, session_id="ack")
    r = eng.restore_from_journal()
    o = next(iter(eng.orders.values()))
    assert o.state == OrderLifecycleState.ACKNOWLEDGED
    assert r["automatic_resubmit_count"] == 0
    assert actual_broker_submit_count() == 0


def test_full_seal_and_mutation(tmp_path: Path):
    for name in REQUIRED_SEAL_ARTIFACTS:
        (tmp_path / name).write_text("{}\n" if name.endswith(".json") else '{"a":1}\n', encoding="utf-8")
    seal = build_full_session_seal(tmp_path, session_id="s1")
    assert seal["session_seal_status"] == "SEALED_VALID"
    write_full_session_seal(tmp_path, session_id="s1")
    (tmp_path / "order_intents.jsonl").write_text('{"mutated":true}\n', encoding="utf-8")
    det = detect_post_seal_mutation(tmp_path / "session_seal.json", tmp_path)
    assert det["post_seal_mutation_detected"] is True
    assert det["recovery_mode"] == "MANUAL_REVIEW_REQUIRED"


def test_missing_required_incomplete(tmp_path: Path):
    (tmp_path / "session_manifest.json").write_text("{}\n", encoding="utf-8")
    seal = build_full_session_seal(tmp_path, session_id="x")
    assert seal["session_seal_status"] == "INCOMPLETE"
    assert seal["required_artifact_missing_count"] > 0


def test_seal_mutation_matrix(tmp_path: Path):
    rows = run_seal_mutation_tests(tmp_path)
    assert all(r["pass"] for r in rows)


def test_w4s_fields_and_ready_gate():
    f = soak_w7a_fields(
        journal_restore_status="JOURNAL_OK",
        session_manifest_status="COMPLETE",
        session_seal_status="SEALED_VALID",
        session_seal_entry_count=14,
        session_seal_required_count=14,
        required_artifact_missing_count=0,
        session_seal_verified=True,
        session_seal_generated_at="2026-07-11T00:00:00+09:00",
        session_seal_schema_version="687W7A2.1",
        session_seal_manifest_sha256="a" * 64,
        post_seal_mutation_detected=False,
        seal_propagation_status="SEAL_PROPAGATION_OK",
        recovery_assertion_failure_count=0,
        recovery_unexpected_object_count=0,
        recovery_expected_actual_match=True,
    )
    assert w4s_ready_extra_ok(f) is True
    f2 = dict(f)
    f2["session_seal_status"] = "INCOMPLETE"
    assert w4s_ready_extra_ok(f2) is False
    f3 = dict(f)
    f3["session_seal_entry_count"] = 0
    assert w4s_ready_extra_ok(f3) is False


def test_runtime_hooks_present():
    pilot = (Path(__file__).resolve().parents[1] / "src" / "small_paper" / "pilot_runner.py").read_text(
        encoding="utf-8"
    )
    bridge = (
        Path(__file__).resolve().parents[1] / "src" / "small_paper" / "live_order_runtime_bridge.py"
    ).read_text(encoding="utf-8")
    assert "create_session_manifest" in pilot
    assert "finalize_session_seal_propagation" in pilot
    assert "finalize_session_manifest" in pilot
    assert "restore_from_journal" in bridge
    assert "restart_recovery_test_version" in bridge or "soak_w7a_fields" in bridge


def test_network_isolation():
    assert actual_broker_submit_count() == 0
    try:
        KabuBrokerAdapter().submit_entry_order({"symbol": "X", "quantity": 1})
        assert False
    except RuntimeError as exc:
        assert "HARD_FAIL" in str(exc)


def test_restored_order_details(tmp_path: Path):
    rows = restored_order_detail_rows(tmp_path)
    assert any(r["stop_point"] == "partially_filled" and r["filled_qty"] == 30 for r in rows)
