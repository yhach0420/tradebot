"""Phase687W11A — Monday P1 blocker fixes tests."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from notify.discord_notification_audit import mask_secrets_text
from small_paper.market_capture_writer import (
    MarketCaptureWriter,
    list_push_part_indexes,
    next_exclusive_part_index,
)
from small_paper.paper_trade_checked_runner import (
    qualify_session_artifacts,
    write_qualified_session_fixture,
)
from small_paper.registration_lifetime import (
    is_live_capture_registration_owner_active,
    safe_paper_unregister,
    should_defer_paper_unregister,
)


def _write_live_capture_day(
    native_root: Path,
    *,
    trading_date: str = "20990101",
    pid: int | None = None,
    synthetic: bool = False,
    fixture: bool = False,
    status: str = "CAPTURE_ONLINE",
    n_symbols: int = 50,
    scheduled_end_at: str = "2099-01-01T15:35:00+09:00",
) -> Path:
    from small_paper.market_capture_sidecar import (
        HEARTBEAT_FILE,
        MANIFEST_FILE,
        PID_FILE_NAME,
        STATUS_FILE,
        capture_day_dir,
    )

    day = capture_day_dir(native_root, trading_date)
    day.mkdir(parents=True, exist_ok=True)
    use_pid = pid if pid is not None else os.getpid()
    (day / PID_FILE_NAME).write_text(str(use_pid), encoding="utf-8")
    symbols = [{"symbol": f"{1000 + i}.T", "exchange": 1} for i in range(n_symbols)]
    man = {
        "capture_session_id": f"cap_{trading_date}",
        "trading_date": trading_date,
        "provenance": "SYNTHETIC_CAPTURE" if synthetic else "LIVE_KABU_PUSH_CAPTURE",
        "fixture": fixture,
        "synthetic": synthetic,
        "test_mode": synthetic or fixture,
        "scheduled_end_at": scheduled_end_at,
        "pid": use_pid,
        "registered_symbols": symbols,
        "registration_generation": "gen-test-1",
    }
    (day / MANIFEST_FILE).write_text(json.dumps(man, indent=2) + "\n", encoding="utf-8")
    (day / STATUS_FILE).write_text(
        json.dumps({"capture_status": status, "pid": use_pid}, indent=2) + "\n", encoding="utf-8"
    )
    (day / HEARTBEAT_FILE).write_text(
        json.dumps({"pid": use_pid, "status": status, "at": "2099-01-01T10:00:00+09:00"}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    reg_dir = native_root / "runtime"
    reg_dir.mkdir(parents=True, exist_ok=True)
    (reg_dir / "market_registration_manifest.json").write_text(
        json.dumps(
            {
                "registered_symbols": symbols,
                "generation_id": "gen-test-1",
                "trading_date": trading_date,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (day / "registration_manifest.json").write_text(
        (reg_dir / "market_registration_manifest.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return day


def test_capture_active_defers_unregister(tmp_path: Path):
    _write_live_capture_day(tmp_path, trading_date="20990101")
    push = MagicMock()
    out = safe_paper_unregister(
        push,
        native_root=tmp_path,
        trading_date="20990101",
        paper_session_id="paper-am",
        am_pm="am",
        path_label="session_finally",
    )
    assert out["deferred"] is True
    assert out["unregister_all_called"] is False
    push.unregister_all.assert_not_called()
    audit = tmp_path / "results" / "notifications" / "20990101" / "paper_unregister_deferred.jsonl"
    assert audit.is_file()
    assert "PAPER_UNREGISTER_DEFERRED_CAPTURE_ACTIVE" in audit.read_text(encoding="utf-8")


def test_capture_inactive_allows_unregister(tmp_path: Path):
    push = MagicMock()
    out = safe_paper_unregister(push, native_root=tmp_path, trading_date="20990101")
    assert out["deferred"] is False
    assert out["unregister_all_called"] is True
    push.unregister_all.assert_called_once()


def test_stale_heartbeat_not_active(tmp_path: Path):
    day = _write_live_capture_day(tmp_path)
    from small_paper.market_capture_sidecar import HEARTBEAT_FILE

    hb = day / HEARTBEAT_FILE
    os.utime(hb, (1_000_000_000, 1_000_000_000))
    d = is_live_capture_registration_owner_active(tmp_path, trading_date="20990101")
    assert d.active is False
    assert d.reason == "stale_heartbeat"


def test_trading_date_mismatch_not_active(tmp_path: Path):
    day = _write_live_capture_day(tmp_path, trading_date="20990101")
    from small_paper.market_capture_sidecar import MANIFEST_FILE

    man = json.loads((day / MANIFEST_FILE).read_text(encoding="utf-8"))
    man["trading_date"] = "20990102"
    (day / MANIFEST_FILE).write_text(json.dumps(man, indent=2) + "\n", encoding="utf-8")
    d = is_live_capture_registration_owner_active(tmp_path, trading_date="20990101")
    assert d.active is False
    assert d.reason == "trading_date_mismatch"


def test_synthetic_capture_not_active(tmp_path: Path):
    _write_live_capture_day(tmp_path, synthetic=True)
    d = should_defer_paper_unregister(tmp_path, trading_date="20990101")
    assert d.active is False
    assert d.reason == "fixture_or_synthetic"


def test_reconnect_path_zero_unregister_when_active(tmp_path: Path):
    _write_live_capture_day(tmp_path)
    push = MagicMock()
    a = safe_paper_unregister(push, native_root=tmp_path, trading_date="20990101", am_pm="am")
    b = safe_paper_unregister(push, native_root=tmp_path, trading_date="20990101", am_pm="pm")
    c = safe_paper_unregister(
        push, native_root=tmp_path, trading_date="20990101", path_label="reconnect_cleanup"
    )
    assert a["unregister_all_called"] is False
    assert b["unregister_all_called"] is False
    assert c["unregister_all_called"] is False
    assert push.unregister_all.call_count == 0


def test_am_pm_capture_continuity_synthetic(tmp_path: Path):
    """Paper AM/PM end must not disturb Capture registration or event stream."""
    _write_live_capture_day(tmp_path, trading_date="20990101")
    out = tmp_path / "cap_parts"
    w = MarketCaptureWriter(output_dir=out, capture_session_id="cap_cont")
    w.start()
    for i in range(5):
        assert w.enqueue({"Symbol": "1000.T", "CurrentPrice": 100 + i}) is True
    time.sleep(0.15)
    before = int(w.stats.written)
    push = MagicMock()
    registered = [(f"{1000 + i}.T", 1) for i in range(50)]

    am = safe_paper_unregister(
        push, native_root=tmp_path, trading_date="20990101", am_pm="am", path_label="am_finally"
    )
    for i in range(5, 10):
        assert w.enqueue({"Symbol": "1000.T", "CurrentPrice": 100 + i}) is True
    pm = safe_paper_unregister(
        push, native_root=tmp_path, trading_date="20990101", am_pm="pm", path_label="pm_finally"
    )
    for i in range(10, 15):
        assert w.enqueue({"Symbol": "1000.T", "CurrentPrice": 100 + i}) is True
    time.sleep(0.2)
    w.stop()
    after = int(w.stats.written)
    assert am["unregister_all_called"] is False
    assert pm["unregister_all_called"] is False
    assert push.unregister_all.call_count == 0
    assert w.stats.dropped == 0
    assert after >= before + 10
    assert after == 15
    assert len(registered) == 50
    assert list_push_part_indexes(out) == [1]


def test_registration_lock_contention_no_unregister_fallback(tmp_path: Path):
    """Lock contention must not fall back to unregister_all while Capture is active."""
    from small_paper.market_capture_registration import (
        FileLock,
        RegistrationLockError,
        lock_path,
        registration_lock,
    )
    from api.kabu_register import register_symbols_cleared

    _write_live_capture_day(tmp_path)
    held = FileLock(lock_path(tmp_path), timeout_sec=0.2)
    held.acquire()
    push = MagicMock()
    push.register.return_value = {"ok": True}
    try:
        out = safe_paper_unregister(
            push,
            native_root=tmp_path,
            trading_date="20990101",
            path_label="lock_contention",
        )
        assert out["unregister_all_called"] is False
        # register path still skips clear even under lock contention elsewhere
        reg = register_symbols_cleared(
            push,
            [("1000.T", 1)],
            clear_first=True,
            native_root=tmp_path,
            trading_date="20990101",
        )
        assert reg.get("clear_first_effective") is False
        push.unregister_all.assert_not_called()
        with pytest.raises(RegistrationLockError):
            with registration_lock(tmp_path, timeout_sec=0.15):
                pass
        push.unregister_all.assert_not_called()
    finally:
        held.release()


def test_register_symbols_cleared_skips_clear_when_capture_active(tmp_path: Path):
    _write_live_capture_day(tmp_path)
    from api.kabu_register import register_symbols_cleared

    push = MagicMock()
    push.register.return_value = {"ok": True}
    specs = [(f"{1000 + i}.T", 1) for i in range(3)]
    out = register_symbols_cleared(
        push, specs, clear_first=True, native_root=tmp_path, trading_date="20990101"
    )
    assert out.get("capture_clear_deferred") is True
    assert out.get("clear_first_effective") is False
    push.unregister_all.assert_not_called()
    push.register.assert_called()


def _matched_seal_snap(*, entry=14, required=14, status="SEALED_VALID", verified=True, missing=0):
    generated = "2099-01-01T12:00:00+09:00"
    seal = {
        "schema_version": "687W7A2.1",
        "session_id": "S1",
        "trading_date": "20990101",
        "generated_at": generated,
        "entry_count": entry,
        "required_count": required,
        "required_artifact_missing_count": missing,
        "missing_required": [],
        "session_seal_status": status,
        "session_seal_manifest_sha256": "a" * 64,
        "session_seal_verified": verified,
        "post_seal_mutation_detected": False,
    }
    snap = {
        "session_id": "S1",
        "trading_date": "20990101",
        "session_seal_status": status,
        "session_seal_entry_count": entry,
        "session_seal_required_count": required,
        "required_artifact_missing_count": missing,
        "session_seal_verified": verified,
        "session_seal_generated_at": generated,
        "session_seal_schema_version": "687W7A2.1",
        "session_seal_manifest_sha256": "a" * 64,
        "post_seal_mutation_detected": False,
        "seal_propagation_status": "SEAL_PROPAGATION_OK",
        "recovery_assertion_failure_count": 0,
        "recovery_unexpected_object_count": 0,
        "session_provenance": "LIVE_PAPER_RUNTIME",
        "runtime_session": True,
        "synthetic": False,
        "fixture": False,
        "test_mode": False,
    }
    return snap, seal


def test_seal_crosscheck_pass_when_match():
    snap, seal = _matched_seal_snap()
    q = qualify_session_artifacts(
        snap=snap,
        seal=seal,
        snapshot_exists=True,
        seal_exists=True,
        manifest_exists=True,
        paper_exit_code=0,
    )
    assert q["seal_qualified"] is True
    assert q["fields"]["snapshot_seal_crosscheck_pass"] is True


def test_seal_crosscheck_fail_entry_mismatch():
    snap, seal = _matched_seal_snap()
    seal["required_count"] = 13
    q = qualify_session_artifacts(
        snap=snap, seal=seal, snapshot_exists=True, seal_exists=True, manifest_exists=True
    )
    assert q["seal_qualified"] is False
    assert q["forward_qualified"] is False
    assert any("SNAPSHOT_SEAL_MISMATCH" in f for f in q["failures"])


def test_seal_crosscheck_fail_status_mismatch():
    snap, seal = _matched_seal_snap()
    seal["session_seal_status"] = "INVALID"
    q = qualify_session_artifacts(
        snap=snap, seal=seal, snapshot_exists=True, seal_exists=True, manifest_exists=True
    )
    assert q["seal_qualified"] is False


def test_seal_crosscheck_fail_verified_mismatch():
    snap, seal = _matched_seal_snap()
    seal["post_seal_mutation_detected"] = True
    q = qualify_session_artifacts(
        snap=snap, seal=seal, snapshot_exists=True, seal_exists=True, manifest_exists=True
    )
    assert q["seal_qualified"] is False


def test_seal_crosscheck_fail_missing_count():
    snap, seal = _matched_seal_snap()
    seal["required_artifact_missing_count"] = 1
    seal["missing_required"] = ["x"]
    q = qualify_session_artifacts(
        snap=snap, seal=seal, snapshot_exists=True, seal_exists=True, manifest_exists=True
    )
    assert q["seal_qualified"] is False


def test_seal_crosscheck_fail_session_id():
    snap, seal = _matched_seal_snap()
    snap["session_id"] = "OTHER"
    q = qualify_session_artifacts(
        snap=snap, seal=seal, snapshot_exists=True, seal_exists=True, manifest_exists=True
    )
    assert q["seal_qualified"] is False
    assert any("session_id" in f for f in q["failures"])


def test_fixture_qualify_still_works(tmp_path: Path):
    snap_path = write_qualified_session_fixture(tmp_path / "sess", session_id="FX")
    from small_paper.paper_trade_checked_runner import qualify_snapshot_path

    q = qualify_snapshot_path(snap_path, paper_exit_code=0)
    assert q["seal_qualified"] is True


def test_restart_part_skips_existing(tmp_path: Path):
    out = tmp_path / "cap"
    out.mkdir()
    (out / "push_part_0001.jsonl").write_text("{}\n", encoding="utf-8")
    (out / "push_part_0002.jsonl").write_text("{}\n", encoding="utf-8")
    h2_before = (out / "push_part_0002.jsonl").read_bytes()
    w = MarketCaptureWriter(output_dir=out, capture_session_id="c1")
    w.stop()
    meta = w.new_part_after_restart()
    assert meta["new_part"] == 3
    assert (out / "push_part_0003.jsonl").is_file()
    assert (out / "push_part_0002.jsonl").read_bytes() == h2_before
    assert list_push_part_indexes(out) == [1, 2, 3]


def test_restart_part_with_gap(tmp_path: Path):
    out = tmp_path / "cap"
    out.mkdir()
    (out / "push_part_0001.jsonl").write_text("a\n", encoding="utf-8")
    (out / "push_part_0002.jsonl").write_text("b\n", encoding="utf-8")
    (out / "push_part_0005.jsonl").write_text("c\n", encoding="utf-8")
    assert next_exclusive_part_index(out) == 6
    w = MarketCaptureWriter(output_dir=out, capture_session_id="c2")
    w.stop()
    meta = w.new_part_after_restart()
    assert meta["new_part"] == 6


def test_secret_masking_webhook_in_exception():
    raw = "failed POST https://discord.com/api/webhooks/123/secret-token-here boom"
    masked = mask_secrets_text(raw)
    assert "secret-token" not in masked
    assert "discord.com/api/webhooks" not in masked.lower()


def test_discord_notifier_exception_message_masked(monkeypatch: pytest.MonkeyPatch):
    from small_paper.discord_notifier import SmallPaperDiscordConfig, SmallPaperDiscordNotifier

    cfg = SmallPaperDiscordConfig(enabled=True, observer_only=True)
    n = SmallPaperDiscordNotifier(cfg, profile="p", entry_profile="e")
    monkeypatch.setattr(
        n,
        "_resolve_trade_webhook",
        lambda: ("https://discord.com/api/webhooks/1/tok", "notify"),
    )

    def boom(*a, **k):
        raise RuntimeError("https://discord.com/api/webhooks/999/super-secret-token failed")

    monkeypatch.setattr("notify.discord_notification_router.get_router", boom)
    res = n._post_with_result(
        event_tag="ENTRY",
        title_line="test",
        fields=[],
        color=1,
        trade_notify=True,
    )
    assert res.exception_type == "RuntimeError"
    assert "super-secret" not in (res.exception_message or "")
    assert "webhooks/999" not in (res.exception_message or "")
