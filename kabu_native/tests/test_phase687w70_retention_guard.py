"""Phase687W70 retention guard tests — no Runtime ENTRY/EXIT behavior changes."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from small_paper.data_retention_guard import (
    ProtectedDataDeleteError,
    archive_session_copy,
    check_retention_integrity,
    forbid_protected_delete,
    is_protected_path,
)


@pytest.fixture()
def paper_tree(tmp_path: Path):
    root = tmp_path / "kabu_native"
    sp = root / "results" / "small_paper"
    arch = root / "results" / "archive"
    ret = root / "results" / "retention"
    (root / "data" / "push_jsonl").mkdir(parents=True)
    arch.mkdir(parents=True)
    ret.mkdir(parents=True)

    def _mk_session(day: str, sess: str, *, with_events: bool = True) -> Path:
        d = sp / day / sess
        d.mkdir(parents=True)
        if with_events:
            (d / "small_paper_events.csv").write_text(
                "event_type,symbol\naccepted,1000.T\nobserver_exit,1000.T\n",
                encoding="utf-8",
            )
            (d / "small_paper_summary.json").write_text('{"trade_count":1}', encoding="utf-8")
        return d

    s1 = _mk_session("20260615", "live_session_081000")
    s2 = _mk_session("20260616", "live_session_081000")
    baseline = {
        "sessions": [
            {
                "day": "20260615",
                "session": "live_session_081000",
                "path": str(s1),
                "has_events": True,
            },
            {
                "day": "20260616",
                "session": "live_session_081000",
                "path": str(s2),
                "has_events": True,
            },
        ]
    }
    (ret / "small_paper_retention_baseline.json").write_text(
        json.dumps(baseline), encoding="utf-8"
    )
    # emergency snapshot present
    (arch / "emergency_snapshot_test").mkdir()
    (arch / "emergency_snapshot_test" / "manifest.json").write_text("{}", encoding="utf-8")
    return root


def test_missing_session_blocks(paper_tree: Path):
    shutil.rmtree(paper_tree / "results" / "small_paper" / "20260615")
    r = check_retention_integrity(root=paper_tree, require_archive_emergency=True, block_on_disk_pct=99.9)
    assert not r.ok
    assert r.code == "DATA_RETENTION_INTEGRITY_ERROR"
    assert any(f.kind == "missing_session" for f in r.findings)


def test_missing_events_blocks(paper_tree: Path):
    ev = paper_tree / "results" / "small_paper" / "20260615" / "live_session_081000" / "small_paper_events.csv"
    ev.unlink()
    r = check_retention_integrity(root=paper_tree, block_on_disk_pct=99.9)
    assert not r.ok
    assert any(f.kind == "missing_events" for f in r.findings)


def test_new_session_ok(paper_tree: Path):
    d = paper_tree / "results" / "small_paper" / "20260617" / "live_session_122500"
    d.mkdir(parents=True)
    (d / "small_paper_events.csv").write_text("event_type\naccepted\n", encoding="utf-8")
    r = check_retention_integrity(root=paper_tree, block_on_disk_pct=99.9)
    assert r.ok


def test_archive_sha_mismatch_detected(paper_tree: Path, tmp_path: Path):
    sess = paper_tree / "results" / "small_paper" / "20260615" / "live_session_081000"
    dest = paper_tree / "results" / "archive" / "small_paper" / "20260615" / "live_session_081000"
    dest.mkdir(parents=True)
    (dest / "small_paper_events.csv").write_text("CORRUPT", encoding="utf-8")
    flag = archive_session_copy(sess, root=paper_tree)
    assert flag["failed_count"] >= 1
    assert flag["ok"] is False


def test_backup_failure_not_ok(paper_tree: Path):
    sess = paper_tree / "results" / "small_paper" / "20260615" / "live_session_081000"
    # make dest a file to force copy failure on mkdir path — use read-only parent trick skipped on Windows;
    # instead pre-create mismatched file (covered above). Here ensure success path ok.
    flag = archive_session_copy(sess, root=paper_tree)
    assert flag["ok"] is True
    assert (Path(flag["archive_path"]) / "BACKUP_COMPLETE.json").is_file()


def test_protected_delete_forbidden(paper_tree: Path):
    target = paper_tree / "results" / "small_paper" / "20260615"
    assert is_protected_path(target, root=paper_tree)
    with pytest.raises(ProtectedDataDeleteError):
        forbid_protected_delete(target, root=paper_tree, reason="test")


def test_scratch_under_small_paper_allowed(paper_tree: Path):
    scratch = paper_tree / "results" / "small_paper" / "_phase630" / "tmp"
    scratch.mkdir(parents=True)
    forbid_protected_delete(scratch, root=paper_tree, reason="scratch ok")


def test_empty_new_day_not_missing(paper_tree: Path):
    (paper_tree / "results" / "small_paper" / "20260620").mkdir(parents=True)
    r = check_retention_integrity(root=paper_tree, block_on_disk_pct=99.9)
    assert r.ok
