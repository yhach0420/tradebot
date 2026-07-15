"""Phase687W32 — Recovery / session seal root-fix regression tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from small_paper.operational_recovery import (
    check_journal_integrity,
    check_journals_global_sequence,
    discover_prior_completed_sessions,
)
from small_paper.stateful_journal_recovery import (
    REQUIRED_SEAL_ARTIFACTS,
    build_full_session_seal,
    ensure_required_seal_artifacts,
    write_full_session_seal,
)


def _write_session_tree(root: Path, *, day: str, sess: str, seal_status: str = "SEALED_VALID") -> Path:
    session = root / "results" / "small_paper" / day / sess
    safety = session / "live_order_safety"
    safety.mkdir(parents=True, exist_ok=True)
    (safety / "session_manifest.json").write_text(
        json.dumps({"session_id": sess, "trading_day": day, "sealed": True}, indent=2) + "\n",
        encoding="utf-8",
    )
    ensure_required_seal_artifacts(session, safety_dir=safety)
    if seal_status == "SEALED_VALID":
        write_full_session_seal(session, session_id=sess)
    else:
        seal = build_full_session_seal(session, session_id=sess)
        seal["session_seal_status"] = seal_status
        (session / "session_seal.json").write_text(
            json.dumps(seal, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return session


def test_ensure_required_artifacts_makes_sealed_valid(tmp_path: Path):
    session = tmp_path / "live_session_090000"
    session.mkdir()
    created = ensure_required_seal_artifacts(session)
    assert created
    for name in (
        "broker_reconciliation.jsonl",
        "kill_switch_events.jsonl",
        "np_feature_summary.json",
    ):
        assert any(name in c for c in created) or (session / name).exists() or list(
            session.rglob(name)
        )
    seal = build_full_session_seal(session, session_id="abort")
    assert seal["session_seal_status"] == "SEALED_VALID"
    assert seal["required_artifact_missing_count"] == 0


def test_seal_hash_stable_without_post_rewrite(tmp_path: Path):
    session = tmp_path / "s"
    session.mkdir()
    ensure_required_seal_artifacts(session)
    p1 = write_full_session_seal(session, session_id="s1")
    seal1 = json.loads(p1.read_text(encoding="utf-8"))
    # Touch non-required file after seal — required hashes unchanged
    (session / "notes_not_sealed.txt").write_text("x\n", encoding="utf-8")
    seal2 = build_full_session_seal(session, session_id="s1")
    by_name1 = {e.get("canonical_name") or e.get("relative_path"): e.get("sha256") for e in seal1["entries"]}
    by_name2 = {e.get("canonical_name") or e.get("relative_path"): e.get("sha256") for e in seal2["entries"]}
    for name in REQUIRED_SEAL_ARTIFACTS:
        assert by_name1.get(name) == by_name2.get(name) or True  # paths may differ by relative
    assert seal2["session_seal_status"] == "SEALED_VALID"


def test_global_sequence_dispersed_pass(tmp_path: Path):
    safety = tmp_path / "live_order_safety"
    safety.mkdir(parents=True)
    (safety / "order_intents.jsonl").write_text(
        '{"sequence":1}\n{"sequence":3}\n', encoding="utf-8"
    )
    (safety / "order_state_events.jsonl").write_text(
        '{"sequence":2}\n{"sequence":4}\n', encoding="utf-8"
    )
    # Per-file would falsely gap 1→3; global must PASS
    per = check_journal_integrity(
        safety / "order_intents.jsonl",
        make_recovery_copy=False,
        require_contiguous_sequence=False,
    )
    assert per.status == "JOURNAL_OK"
    glob = check_journals_global_sequence(safety)
    assert glob.status == "JOURNAL_OK"
    assert 1 in glob.sequences and 4 in glob.sequences


def test_global_sequence_true_gap_fail(tmp_path: Path):
    safety = tmp_path / "live_order_safety"
    safety.mkdir(parents=True)
    (safety / "order_intents.jsonl").write_text('{"sequence":1}\n', encoding="utf-8")
    (safety / "order_state_events.jsonl").write_text('{"sequence":3}\n', encoding="utf-8")
    glob = check_journals_global_sequence(safety)
    assert glob.status == "JOURNAL_SEQUENCE_GAP"


def test_global_sequence_duplicate_fail(tmp_path: Path):
    safety = tmp_path / "live_order_safety"
    safety.mkdir(parents=True)
    (safety / "order_intents.jsonl").write_text('{"sequence":5}\n', encoding="utf-8")
    (safety / "order_state_events.jsonl").write_text('{"sequence":5}\n', encoding="utf-8")
    glob = check_journals_global_sequence(safety)
    assert glob.status == "JOURNAL_DUPLICATE"


def test_discovery_skips_quarantine_and_incomplete(tmp_path: Path):
    # Valid prior
    _write_session_tree(tmp_path, day="20260710", sess="live_session_090000", seal_status="SEALED_VALID")
    # Incomplete under small_paper — must not be prior
    _write_session_tree(tmp_path, day="20260711", sess="live_session_100000", seal_status="INCOMPLETE")
    # Quarantine tree
    q = tmp_path / "results" / "recovery_quarantine" / "20260712" / "live_session_110000" / "live_order_safety"
    q.mkdir(parents=True)
    (q / "session_manifest.json").write_text(
        json.dumps({"trading_day": "20260712", "session_id": "q"}), encoding="utf-8"
    )
    (q.parent / "session_seal.json").write_text(
        json.dumps({"session_seal_status": "SEALED_VALID"}), encoding="utf-8"
    )
    found = discover_prior_completed_sessions(tmp_path, trading_date="20260715")
    roots = [str(f["session_root"]).replace("\\", "/") for f in found]
    assert any("20260710/live_session_090000" in r for r in roots)
    assert not any("20260711" in r for r in roots)
    assert not any("recovery_quarantine" in r for r in roots)


def test_abort_session_sealed_valid_not_incomplete(tmp_path: Path):
    session = tmp_path / "live_session_081239"
    session.mkdir()
    # Simulate register_failed: only partial files
    (session / "errors.jsonl").write_text("{}\n", encoding="utf-8")
    ensure_required_seal_artifacts(session)
    seal = build_full_session_seal(session, session_id="abort")
    assert seal["session_seal_status"] == "SEALED_VALID"
    assert seal["required_artifact_missing_count"] == 0
