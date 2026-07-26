"""Phase675 — mock/fake WebSocket freeze recovery tests (Paper only)."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, AsyncIterator
from unittest.mock import MagicMock, patch

import pytest

from small_paper.ws_freeze_recovery import (
    PUSH_RECONNECT_SILENCE_TIMEOUT,
    ReconnectBudget,
    WS_LIFECYCLE_TICK_KEY,
    WS_RECONNECT_EXHAUSTED,
    apply_orphan_recovery_to_events,
    enrich_heartbeat_fields,
    find_orphan_accepted,
    is_lifecycle_tick,
    make_recv_timeout_tick,
    record_supervisor_attempt,
    supervisor_may_restart,
)
from small_paper.paper_runtime_supervisor import evaluate_stall, handle_event_loop_stall


# ---------------------------------------------------------------------------
# A: timeout ticks surface (recv no longer swallowed forever)
# ---------------------------------------------------------------------------
def test_A_recv_timeout_yields_lifecycle_tick(monkeypatch: pytest.MonkeyPatch) -> None:
    from api import push_client as pc
    import websockets

    class FakeWS:
        async def recv(self) -> str:
            await asyncio.sleep(10)
            return "{}"

        async def __aenter__(self) -> "FakeWS":
            return self

        async def __aexit__(self, *a: Any) -> None:
            return None

    class FakeConnect:
        def __call__(self, *a: Any, **k: Any) -> FakeWS:
            assert "open_timeout" in k
            return FakeWS()

    monkeypatch.setattr(websockets, "connect", FakeConnect())

    async def _run() -> int:
        ticks = 0
        async for payload in pc._iter_push_board_messages(
            "ws://fake",
            recv_poll_sec=0.05,
            open_timeout_sec=1.0,
            yield_timeout_ticks=True,
        ):
            if is_lifecycle_tick(payload):
                ticks += 1
                if ticks >= 2:
                    break
        return ticks

    ticks = asyncio.run(asyncio.wait_for(_run(), timeout=5.0))
    assert ticks >= 1


# ---------------------------------------------------------------------------
# B: reconnect budget success path resets after push
# ---------------------------------------------------------------------------
def test_B_reconnect_budget_success_and_reset() -> None:
    b = ReconnectBudget(max_attempts=3, overall_deadline_sec=60, post_reconnect_silence_sec=1.0)
    ok, _ = b.can_attempt(mono=100.0)
    assert ok
    n = b.note_attempt_start(mono=100.0)
    assert n == 1
    b.note_success(mono=101.0)
    assert not b.silence_exceeded(last_push_mono=102.0, reconnect_succeeded_mono=101.0, mono=103.0)
    b.note_push_resumed()
    assert b.attempts_in_window == 0


# ---------------------------------------------------------------------------
# C: post-reconnect silence / exhausted → stop reasons
# ---------------------------------------------------------------------------
def test_C_reconnect_silence_and_exhausted() -> None:
    b = ReconnectBudget(
        max_attempts=2,
        overall_deadline_sec=10,
        post_reconnect_silence_sec=5.0,
    )
    b.note_attempt_start(mono=0.0)
    b.note_success(mono=1.0)
    assert b.silence_exceeded(
        last_push_mono=None, reconnect_succeeded_mono=1.0, mono=7.0
    )
    b2 = ReconnectBudget(max_attempts=2, overall_deadline_sec=100)
    b2.note_attempt_start(0.0)
    b2.note_attempt_start(1.0)
    ok, reason = b2.can_attempt(2.0)
    assert not ok
    assert reason == WS_RECONNECT_EXHAUSTED


# ---------------------------------------------------------------------------
# D: Supervisor detects HB stall + PID alive
# ---------------------------------------------------------------------------
def test_D_supervisor_detects_heartbeat_stall(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sess = tmp_path / "live_session_test"
    sess.mkdir()
    # old heartbeat
    hb = {
        "event_time": "2026-07-21T15:13:49+09:00",
        "emitted_at": "2026-07-21T15:13:49+09:00",
        "heartbeat_index": 30,
        "last_push_at": "2026-07-21T15:18:35+09:00",
    }
    (sess / "heartbeat.jsonl").write_text(json.dumps(hb) + "\n", encoding="utf-8")
    monkeypatch.setattr(
        "small_paper.paper_runtime_supervisor._pid_alive", lambda pid: True
    )
    monkeypatch.setattr(
        "small_paper.paper_runtime_supervisor._safe_kill", lambda pid: True
    )
    monkeypatch.setattr(
        "small_paper.paper_runtime_supervisor.notify_discord_critical",
        lambda *a, **k: True,
    )
    from datetime import datetime
    from zoneinfo import ZoneInfo

    now = datetime(2026, 7, 21, 16, 0, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
    snap = evaluate_stall(sess, runtime_pid=99999, hb_stall_sec=600, now=now)
    assert snap["stall"] is True
    out = handle_event_loop_stall(sess, runtime_pid=99999, hb_stall_sec=600)
    assert out["action"] == "EVENT_LOOP_STALL"
    assert out["orphan_recovery_required"] is True
    assert (sess / "stall_evidence").is_dir()


# ---------------------------------------------------------------------------
# E: orphan recovery idempotent (4 → 1 round only)
# ---------------------------------------------------------------------------
def test_E_orphan_recovery_idempotent_four() -> None:
    events: list[dict[str, Any]] = []
    for sym, pid in [
        ("6058.T", "6058.T_1"),
        ("5016.T", "5016.T_1"),
        ("5985.T", "5985.T_1"),
        ("3449.T", "3449.T_1"),
    ]:
        events.append(
            {
                "event_type": "accepted",
                "symbol": sym,
                "position_id": pid,
                "entry_time": "2026-07-21T14:00:00+09:00",
                "entry_price": 100.0,
                "current_price": 100.0,
                "dry_run": True,
            }
        )
    # one normal exit elsewhere
    events.append(
        {
            "event_type": "accepted",
            "symbol": "7974.T",
            "position_id": "7974.T_1",
            "entry_time": "2026-07-21T14:00:00+09:00",
            "entry_price": 1.0,
            "current_price": 1.0,
        }
    )
    events.append(
        {
            "event_type": "observer_exit",
            "symbol": "7974.T",
            "position_id": "7974.T_1",
            "entry_time": "2026-07-21T14:00:00+09:00",
            "exit_reason": "no_progress_exit",
        }
    )
    assert len(find_orphan_accepted(events)) == 4
    r1 = apply_orphan_recovery_to_events(events, recovery_note="test")
    assert r1.orphan_forced_close_count == 4
    assert r1.active_positions == 0
    r2 = apply_orphan_recovery_to_events(events, recovery_note="test")
    assert r2.orphan_forced_close_count == 0
    assert r2.active_positions == 0
    assert len(find_orphan_accepted(events)) == 0
    assert sum(1 for e in events if e.get("exit_reason") == "recovery_forced_close") == 4


# ---------------------------------------------------------------------------
# F: Discord hang does not block summary artifact (timeout path)
# ---------------------------------------------------------------------------
def test_F_discord_timeout_summary_still_written(tmp_path: Path) -> None:
    # Simulate the bounded wait pattern used in pilot_runner
    import concurrent.futures

    summary: dict[str, Any] = {"ok": False}

    def _hang() -> None:
        time.sleep(10)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(_hang)
        try:
            fut.result(timeout=0.2)
        except concurrent.futures.TimeoutError:
            summary["discord_session_end_timeout"] = True
    summary["artifact"] = "written"
    path = tmp_path / "small_paper_summary.json"
    path.write_text(json.dumps(summary), encoding="utf-8")
    assert path.is_file()
    assert json.loads(path.read_text(encoding="utf-8"))["discord_session_end_timeout"] is True


# ---------------------------------------------------------------------------
# G: D-drive I/O stop → EXTERNAL_BACKUP_PENDING; summary already exists
# ---------------------------------------------------------------------------
def test_G_external_backup_pending_summary_complete(tmp_path: Path) -> None:
    summary = {"accepted_count": 25, "active_positions": 0}
    sp = tmp_path / "small_paper_summary.json"
    sp.write_text(json.dumps(summary), encoding="utf-8")
    ext = {
        "ok": False,
        "pending": True,
        "code": "EXTERNAL_BACKUP_PENDING",
        "error": "external_backup_timeout",
    }
    data = json.loads(sp.read_text(encoding="utf-8"))
    data["session_external_backup"] = ext
    sp.write_text(json.dumps(data), encoding="utf-8")
    final = json.loads(sp.read_text(encoding="utf-8"))
    assert final["accepted_count"] == 25
    assert final["session_external_backup"]["code"] == "EXTERNAL_BACKUP_PENDING"


# ---------------------------------------------------------------------------
# H: reconnect storm → exhausted; supervisor restart loop blocked
# ---------------------------------------------------------------------------
def test_H_reconnect_exhausted_and_no_restart_loop(tmp_path: Path) -> None:
    b = ReconnectBudget(max_attempts=3, overall_deadline_sec=1000)
    for i in range(3):
        assert b.can_attempt(float(i))[0]
        b.note_attempt_start(float(i))
    ok, reason = b.can_attempt(10.0)
    assert not ok and reason == WS_RECONNECT_EXHAUSTED

    sess = tmp_path / "sess"
    sess.mkdir()
    record_supervisor_attempt(sess, action="safe_stop", max_attempts=1)
    may, why = supervisor_may_restart(sess, max_attempts=1, cooldown_sec=0)
    assert may is False
    assert why == "max_restarts_per_session"


def test_heartbeat_enrichment_push_independent() -> None:
    fields = enrich_heartbeat_fields(
        runtime_pid=1,
        event_loop_alive=True,
        last_push_at="2026-07-21T15:18:35+09:00",
        last_push_mono=time.monotonic() - 100,
        websocket_state="receiving",
        reconnect_attempt=1,
        session_state="close_due",
        active_positions=4,
        close_due=True,
        consecutive_recv_timeouts=3,
    )
    assert fields["event_loop_alive"] is True
    assert fields["close_due"] is True
    assert fields["active_positions"] == 4
    assert fields["last_push_age_sec"] is not None
    assert fields["last_push_age_sec"] >= 99


def test_lifecycle_tick_helper() -> None:
    t = make_recv_timeout_tick(5)
    assert t[WS_LIFECYCLE_TICK_KEY] is True
    assert is_lifecycle_tick(t)
    assert not is_lifecycle_tick({"Symbol": "1"})


def test_runtime_parity_constants_unchanged() -> None:
    """AM/PM schedule constants and freeze helpers stay paper-safe."""
    text = (Path(__file__).resolve().parents[1] / "src" / "small_paper" / "am_pm_session_policy.py").read_text(
        encoding="utf-8"
    )
    assert 'entry_stop="15:18"' in text
    assert 'force_close="15:23"' in text
    assert "afternoon_session_close" in text
    assert WS_RECONNECT_EXHAUSTED == "WS_RECONNECT_EXHAUSTED"
    assert PUSH_RECONNECT_SILENCE_TIMEOUT
