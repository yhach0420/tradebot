"""Phase687W71 external backup tests — no Runtime ENTRY/EXIT changes."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from small_paper.external_backup import (
    add_pending_session,
    check_external_sync_status,
    copy_session_to_external,
    drive_probe,
    load_pending,
    remove_pending_session,
    sync_pending_external,
)


@pytest.fixture()
def roots(tmp_path: Path):
    native = tmp_path / "kabu_native"
    external = tmp_path / "kabudata"
    sp = native / "results" / "small_paper" / "20260701" / "live_session_090000"
    sp.mkdir(parents=True)
    (sp / "small_paper_events.csv").write_text("event_type\naccepted\n", encoding="utf-8")
    (sp / "small_paper_summary.json").write_text('{"trade_count":1}', encoding="utf-8")
    arch = native / "results" / "archive" / "small_paper" / "20260701" / "live_session_090000"
    arch.mkdir(parents=True)
    for name in ("small_paper_events.csv", "small_paper_summary.json"):
        (arch / name).write_text((sp / name).read_text(encoding="utf-8"), encoding="utf-8")
    (native / "results" / "retention").mkdir(parents=True)
    return native, external, sp, arch


def test_copy_session_verified(roots):
    native, external, sp, arch = roots
    flag = copy_session_to_external(sp, native=native, external_root=external)
    assert flag["ok"] is True
    dest = external / "small_paper_archive" / "20260701" / "live_session_090000"
    assert (dest / "BACKUP_COMPLETE.json").is_file()
    assert (dest / "small_paper_events.csv").is_file()
    # C source intact
    assert (arch / "small_paper_events.csv").is_file()
    assert (sp / "small_paper_events.csv").is_file()


def test_no_overwrite_preserves_dest(roots):
    native, external, sp, _arch = roots
    dest = external / "small_paper_archive" / "20260701" / "live_session_090000"
    dest.mkdir(parents=True)
    (dest / "extra_keep.txt").write_text("keep-me", encoding="utf-8")
    (dest / "small_paper_events.csv").write_text("event_type\naccepted\n", encoding="utf-8")
    flag = copy_session_to_external(sp, native=native, external_root=external)
    assert flag["ok"] is True
    assert (dest / "extra_keep.txt").read_text(encoding="utf-8") == "keep-me"


def test_mismatch_no_backup_complete(roots):
    native, external, sp, _arch = roots
    dest = external / "small_paper_archive" / "20260701" / "live_session_090000"
    dest.mkdir(parents=True)
    (dest / "small_paper_events.csv").write_text("CORRUPT", encoding="utf-8")
    flag = copy_session_to_external(sp, native=native, external_root=external)
    assert flag["ok"] is False
    assert not (dest / "BACKUP_COMPLETE.json").is_file()
    assert (dest / "BACKUP_FAILED.json").is_file()


def test_d_missing_does_not_block(roots, monkeypatch):
    native, external, sp, _arch = roots
    monkeypatch.setattr(
        "small_paper.external_backup.drive_probe",
        lambda root=None: {
            "drive_exists": False,
            "writable": False,
            "free_space_bytes": 0,
            "total_space_bytes": 0,
            "connected": False,
            "root": str(external),
        },
    )
    flag = copy_session_to_external(sp, native=native, external_root=external)
    assert flag.get("pending") is True
    assert flag.get("code") == "EXTERNAL_BACKUP_PENDING"
    status = check_external_sync_status(
        native=native, external_root=external, sync_if_connected=True
    )
    assert status.blocks_start is False
    pending = load_pending(native)
    assert any(p.get("session") == "live_session_090000" for p in pending.get("pending", []))


def test_pending_sync_when_connected(roots):
    native, external, sp, _arch = roots
    add_pending_session("20260701", "live_session_090000", root=native, reason="test")
    result = sync_pending_external(native=native, external_root=external)
    assert "20260701/live_session_090000" in result["synced"]
    assert (external / "small_paper_archive" / "20260701" / "live_session_090000" / "BACKUP_COMPLETE.json").is_file()
    assert not load_pending(native).get("pending")


def test_drive_probe_real_d_optional():
    # Smoke: function returns dict; may or may not be connected in CI
    p = drive_probe(Path("D:/kabudata"))
    assert "drive_exists" in p
    assert "connected" in p
