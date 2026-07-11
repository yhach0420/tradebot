"""Phase687W7A2 — W4S session seal propagation integrity tests."""

from __future__ import annotations

from pathlib import Path

from small_paper.kabu_order_request_builder import actual_broker_submit_count
from small_paper.live_order_safety_sm import KabuBrokerAdapter
from small_paper.stateful_journal_recovery import (
    REQUIRED_SEAL_ARTIFACTS,
    soak_w7a_fields,
    w4s_ready_extra_ok,
)
from small_paper.w4s_seal_propagation import (
    SEAL_PROPAGATION_OK,
    build_synthetic_full_seal_session,
    compare_seal_snapshot,
    finalize_session_seal_propagation,
    run_negative_seal_mismatch_tests,
    w4s_seal_success_ok,
)


def test_full_seal_14_propagates_to_snapshot(tmp_path: Path):
    built = build_synthetic_full_seal_session(tmp_path / "sess")
    assert built["pass"] is True
    assert built["entry_count"] == 14
    assert built["required_count"] == 14
    assert built["snapshot_entry_count"] == 14
    snap = built["snapshot"]
    seal = built["seal"]
    assert snap["session_seal_entry_count"] == 14
    assert snap["session_seal_required_count"] == 14
    assert snap["required_artifact_missing_count"] == 0
    assert snap["session_seal_status"] == "SEALED_VALID"
    assert snap["session_seal_verified"] is True
    assert snap["post_seal_mutation_detected"] is False
    assert snap["seal_propagation_status"] == SEAL_PROPAGATION_OK
    cmp = compare_seal_snapshot(snap, seal, verified=True, post_mutation=False)
    assert cmp["pass"] is True
    assert cmp["mismatch_count"] == 0
    assert w4s_seal_success_ok(snap, seal) is True


def test_negative_mismatch_all_fail(tmp_path: Path):
    built = build_synthetic_full_seal_session(tmp_path / "neg")
    neg = run_negative_seal_mismatch_tests(good_snap=built["snapshot"], good_seal=built["seal"])
    assert neg["pass"] is True
    assert all(c["detected_fail"] for c in neg["cases"])
    assert all(c["w4s_seal_success_ok"] is False for c in neg["cases"])


def test_seal14_snapshot0_rejected(tmp_path: Path):
    built = build_synthetic_full_seal_session(tmp_path / "z")
    snap = dict(built["snapshot"])
    snap["session_seal_entry_count"] = 0
    snap["seal_propagation_status"] = "SEAL_SNAPSHOT_MISMATCH"
    assert w4s_seal_success_ok(snap, built["seal"]) is False
    assert w4s_ready_extra_ok(snap) is False


def test_seal14_snapshot13_rejected(tmp_path: Path):
    built = build_synthetic_full_seal_session(tmp_path / "t")
    snap = dict(built["snapshot"])
    snap["session_seal_entry_count"] = 13
    assert w4s_seal_success_ok(snap, built["seal"]) is False


def test_duplicate_finalize_same_result(tmp_path: Path):
    root = tmp_path / "dup"
    built = build_synthetic_full_seal_session(root)
    first = built["finalize"]
    second = finalize_session_seal_propagation(
        root, safety_dir=root / "live_order_safety", session_id="W7A2"
    )
    assert second.get("duplicate_finalize") is True
    assert second.get("pass") is True
    assert second["snapshot"]["session_seal_entry_count"] == first["snapshot"]["session_seal_entry_count"]


def test_w4s_ready_rejects_mismatch():
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
        session_seal_manifest_sha256="b" * 64,
        post_seal_mutation_detected=False,
        seal_propagation_status=SEAL_PROPAGATION_OK,
        recovery_assertion_failure_count=0,
        recovery_unexpected_object_count=0,
        recovery_expected_actual_match=True,
    )
    assert w4s_ready_extra_ok(ok) is True
    bad = dict(ok)
    bad["session_seal_entry_count"] = 13
    assert w4s_ready_extra_ok(bad) is False
    bad2 = dict(ok)
    bad2["seal_propagation_status"] = "SEAL_SNAPSHOT_MISMATCH"
    assert w4s_ready_extra_ok(bad2) is False


def test_submit_cancel_resubmit_zero():
    assert actual_broker_submit_count() == 0
    try:
        KabuBrokerAdapter().submit_entry_order({"symbol": "X", "quantity": 1})
        assert False
    except RuntimeError as exc:
        assert "HARD_FAIL" in str(exc)


def test_required_artifacts_unchanged():
    assert len(REQUIRED_SEAL_ARTIFACTS) == 14
    assert "soak_session_snapshot.json" in REQUIRED_SEAL_ARTIFACTS
    assert "np_feature_summary.json" in REQUIRED_SEAL_ARTIFACTS


def test_pilot_uses_propagation_finalize():
    pilot = (Path(__file__).resolve().parents[1] / "src" / "small_paper" / "pilot_runner.py").read_text(
        encoding="utf-8"
    )
    assert "finalize_session_seal_propagation" in pilot
    assert "write_soak_session_snapshot" in pilot
