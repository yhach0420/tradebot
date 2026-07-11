"""Phase687W7A1 — Recovery assertion integrity tests."""

from __future__ import annotations

import tempfile
from pathlib import Path

from small_paper.live_order_safety_sm import build_engine
from small_paper.recovery_assertion_oracle import (
    CAPITAL_RESERVED_SEMANTICS,
    KILL_SWITCH_RESERVATION_POLICY,
    TEST_ORACLE_VERSION,
    evaluate_assertions,
    expected_for_stop_point,
    run_negative_oracle_tests,
)
from small_paper.stateful_journal_recovery import (
    REQUIRED_SEAL_ARTIFACTS,
    StatefulJournalWriter,
    build_full_session_seal,
    run_stateful_restart_matrix,
    soak_w7a_fields,
    w4s_ready_extra_ok,
)


def test_matrix_assertion_failure_count_zero(tmp_path: Path):
    rows = run_stateful_restart_matrix(tmp_path)
    assert all(r["pass"] for r in rows)
    assert sum(r["assertion_failure_count"] for r in rows) == 0
    by = {r["stop_point"]: r for r in rows}
    assert by["capital_reserved"]["expected_intent_count"] == 0
    assert by["capital_reserved"]["expected_order_aggregate_count"] == 1
    assert by["capital_reserved"]["restored_active_reservation_count"] == 1
    assert by["partially_filled"]["restored_position_quantity"] == 30
    assert by["partially_filled"]["restored_reserved_quantity"] == 70
    assert by["entry_filled"]["restored_active_reservation_count"] == 0
    assert by["exit_intent"]["expected_position_count"] == 1
    assert by["exit_intent"]["restored_position_quantity"] == 100
    assert by["partial_exit"]["expected_position_count"] == 1
    assert by["partial_exit"]["restored_position_quantity"] == 60
    assert by["kill_switch_active"]["restored_active_reservation_count"] == 1


def test_capital_reserved_semantics_match_design():
    assert CAPITAL_RESERVED_SEMANTICS["expected_intent_count"] == 0
    assert CAPITAL_RESERVED_SEMANTICS["expected_order_aggregate_count"] == 1
    assert CAPITAL_RESERVED_SEMANTICS["expected_active_reservation_count"] == 1


def test_kill_switch_policy_a_hold():
    assert KILL_SWITCH_RESERVATION_POLICY["policy_letter"] == "A"
    assert KILL_SWITCH_RESERVATION_POLICY["release_on_kill"] is False
    assert KILL_SWITCH_RESERVATION_POLICY["expected_active_reservation_count"] == 1


def test_negative_oracle_all_fail():
    def factory(stop: str):
        d = Path(tempfile.mkdtemp()) / stop
        w = StatefulJournalWriter(d, stop)
        getattr(w, f"write_{stop}")()
        w.written["_seq_before"] = w.seq - 1
        eng = build_engine(output_dir=d, session_id=stop)
        restore = eng.restore_from_journal()
        return d, w.written, eng, restore

    neg = run_negative_oracle_tests(None, factory)
    assert neg["pass"] is True
    assert all(c["detected_fail"] for c in neg["cases"])
    assert all(c["pass"] is False for c in neg["cases"])


def test_manual_pass_true_impossible_without_match(tmp_path: Path):
    w = StatefulJournalWriter(tmp_path, "x")
    w.write_intent_created()
    w.written["_seq_before"] = w.seq - 1
    eng = build_engine(output_dir=tmp_path, session_id="x")
    restore = eng.restore_from_journal()
    exp = expected_for_stop_point("intent_created", w.written)
    exp.order_aggregate_count = 99
    row = evaluate_assertions("intent_created", w.written, eng, restore, expected_override=exp)
    assert row["pass"] is False
    assert row["assertion_failure_count"] > 0


def test_w4s_gate_requires_assertion_match():
    ok = soak_w7a_fields(
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
    assert w4s_ready_extra_ok(ok) is True
    bad = dict(ok)
    bad["recovery_assertion_failure_count"] = 1
    assert w4s_ready_extra_ok(bad) is False
    bad2 = dict(ok)
    bad2["recovery_expected_actual_match"] = False
    assert w4s_ready_extra_ok(bad2) is False


def test_full_seal_details_fourteen_files(tmp_path: Path):
    for name in REQUIRED_SEAL_ARTIFACTS:
        (tmp_path / name).write_text("{}\n" if name.endswith(".json") else '{"a":1}\n', encoding="utf-8")
    seal = build_full_session_seal(tmp_path, session_id="s")
    assert seal["session_seal_status"] == "SEALED_VALID"
    assert len(REQUIRED_SEAL_ARTIFACTS) == 14
    details = []
    for ent in seal["entries"]:
        if ent.get("exists"):
            details.append(
                {
                    "relative_path": ent.get("relative_path") or ent.get("canonical_name"),
                    "required": ent.get("required"),
                    "exists": ent.get("exists"),
                    "size": ent.get("size"),
                    "sha256": ent.get("sha256"),
                    "row_count": ent.get("row_count"),
                    "schema_version": ent.get("schema_version"),
                    "verification_result": "OK",
                }
            )
    assert len(details) == 14
    assert all(d["sha256"] for d in details)


def test_oracle_version():
    assert TEST_ORACLE_VERSION.startswith("687W7A1")
