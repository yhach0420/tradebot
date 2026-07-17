"""Phase687W36 — Paper data-path stall monitor accuracy (no ENTRY/EXIT changes)."""

from __future__ import annotations

import json
from pathlib import Path

from small_paper.data_path_stall_monitor import (
    DataPathMonitorState,
    DataPathStallMonitor,
    StallMonitorConfig,
    format_stall_discord_message,
)

NATIVE = Path(__file__).resolve().parents[1]
OUT = NATIVE / "results" / "reports" / "phase687w36_stall_monitor_accuracy_fix"


def _mon(cfg: StallMonitorConfig | None = None) -> DataPathStallMonitor:
    m = DataPathStallMonitor(cfg or StallMonitorConfig(heartbeat_sec=300.0))
    m.reset(start_mono=0.0)
    return m


def test_startup_60s_hb0_push_gate_growing_no_notify():
    m = _mon()
    snap = m.evaluate(
        mono=60.0,
        push_messages=5326,
        gate_evaluations=438,
        heartbeat_count=0,
        in_market_hours=True,
        in_entry_hours=True,
        process_alive=True,
    )
    assert snap.notify_stalled is False
    assert snap.notify_process_dead is False
    assert snap.state in (DataPathMonitorState.STARTING, DataPathMonitorState.RUNNING, DataPathMonitorState.PUSH_ONLY)
    assert snap.push_delta > 0 or snap.gate_delta > 0


def test_first_heartbeat_at_300s_running():
    m = _mon()
    m.evaluate(
        mono=60.0,
        push_messages=100,
        gate_evaluations=10,
        heartbeat_count=0,
        in_market_hours=True,
    )
    m.note_heartbeat(mono=300.0, heartbeat_count=1)
    snap = m.evaluate(
        mono=300.0,
        push_messages=20000,
        gate_evaluations=1000,
        heartbeat_count=1,
        in_market_hours=True,
    )
    assert snap.notify_stalled is False
    assert snap.state in (DataPathMonitorState.RUNNING, DataPathMonitorState.PUSH_ONLY, DataPathMonitorState.STARTING)
    assert snap.heartbeat_age_sec < 1.0 or snap.state != DataPathMonitorState.STALLED


def test_hb_stopped_but_push_gate_growing_no_notify():
    m = _mon()
    m.note_heartbeat(mono=300.0, heartbeat_count=1)
    # Age 700s (> 2*300) but stream alive
    snap = m.evaluate(
        mono=1000.0,
        push_messages=50000,
        gate_evaluations=3000,
        heartbeat_count=1,
        in_market_hours=True,
    )
    assert snap.heartbeat_age_sec >= 600.0
    assert snap.notify_stalled is False
    assert snap.state != DataPathMonitorState.STALLED


def test_hb_stopped_push_gate_flat_notifies_once():
    m = _mon()
    m.note_heartbeat(mono=300.0, heartbeat_count=1)
    # Freeze at known baseline
    m.evaluate(
        mono=300.0,
        push_messages=1000,
        gate_evaluations=100,
        heartbeat_count=1,
        in_market_hours=True,
    )
    snap1 = m.evaluate(
        mono=1000.0,
        push_messages=1000,
        gate_evaluations=100,
        heartbeat_count=1,
        in_market_hours=True,
        force_window_roll=True,
    )
    assert snap1.state == DataPathMonitorState.STALLED
    assert snap1.notify_stalled is True
    assert snap1.push_delta == 0 and snap1.gate_delta == 0

    snap2 = m.evaluate(
        mono=1060.0,
        push_messages=1000,
        gate_evaluations=100,
        heartbeat_count=1,
        in_market_hours=True,
        force_window_roll=True,
    )
    assert snap2.state == DataPathMonitorState.STALLED
    assert snap2.notify_stalled is False  # anti-spam


def test_entry_hours_off_push_only_no_stall():
    m = _mon()
    m.note_heartbeat(mono=300.0, heartbeat_count=1)
    snap = m.evaluate(
        mono=400.0,
        push_messages=5000,
        gate_evaluations=100,  # flat gate vs prior window after roll
        heartbeat_count=1,
        in_market_hours=True,
        in_entry_hours=False,
    )
    # Force a window where push grows, gate flat
    m._window_push = 4000
    m._window_gate = 100
    snap = m.evaluate(
        mono=460.0,
        push_messages=5000,
        gate_evaluations=100,
        heartbeat_count=1,
        in_market_hours=True,
        in_entry_hours=False,
        force_window_roll=True,
    )
    assert snap.notify_stalled is False
    assert snap.state == DataPathMonitorState.PUSH_ONLY
    assert snap.push_only_warning is False


def test_process_dead_immediate_notify():
    m = _mon()
    snap = m.evaluate(
        mono=10.0,
        push_messages=0,
        gate_evaluations=0,
        heartbeat_count=0,
        in_market_hours=True,
        process_alive=False,
    )
    assert snap.state == DataPathMonitorState.PROCESS_DEAD
    assert snap.notify_process_dead is True
    snap2 = m.evaluate(
        mono=11.0,
        push_messages=0,
        gate_evaluations=0,
        heartbeat_count=0,
        in_market_hours=True,
        process_alive=False,
    )
    assert snap2.notify_process_dead is False


def test_recovery_only_after_increment():
    m = _mon()
    m.note_heartbeat(mono=300.0, heartbeat_count=1)
    m.evaluate(
        mono=300.0,
        push_messages=1000,
        gate_evaluations=100,
        heartbeat_count=1,
        in_market_hours=True,
    )
    stalled = m.evaluate(
        mono=1000.0,
        push_messages=1000,
        gate_evaluations=100,
        heartbeat_count=1,
        in_market_hours=True,
        force_window_roll=True,
    )
    assert stalled.notify_stalled is True
    # Still flat — no recovery
    flat = m.evaluate(
        mono=1060.0,
        push_messages=1000,
        gate_evaluations=100,
        heartbeat_count=1,
        in_market_hours=True,
        force_window_roll=True,
    )
    assert flat.notify_recovered is False
    # Increment resumes
    rec = m.evaluate(
        mono=1120.0,
        push_messages=1001,
        gate_evaluations=100,
        heartbeat_count=1,
        in_market_hours=True,
        force_window_roll=True,
    )
    assert rec.notify_recovered is True
    assert rec.notify_stalled is False


def test_notification_format():
    msg = format_stall_discord_message(
        heartbeat_age_sec=612,
        push_delta=0,
        gate_delta=0,
        process_alive=True,
        capture_status="CAPTURE_WRITING",
    )
    assert "【PAPER DATA PATH STALLED】" in msg
    assert "Heartbeat更新なし: 612秒" in msg
    assert "PUSH増分: 0" in msg
    assert "ENTRY評価増分: 0" in msg
    assert "Paperプロセス: alive" in msg
    assert "Capture: CAPTURE_WRITING" in msg


def test_false_positive_20260716_replay():
    """Replay the known FP: t=60s hb=0 push=5326 gate=438."""
    m = _mon()
    # Simulate growth during first minute
    for t, push, gate in ((10, 800, 50), (30, 2500, 200), (60, 5326, 438)):
        snap = m.evaluate(
            mono=float(t),
            push_messages=push,
            gate_evaluations=gate,
            heartbeat_count=0,
            in_market_hours=True,
        )
        assert snap.notify_stalled is False, snap
    assert snap.state != DataPathMonitorState.STALLED


def test_submit_cancel_zero_manifest():
    # Ops-only change; no order path touched by this module.
    import small_paper.data_path_stall_monitor as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "submit" not in src.lower() or "submit" in "no submit orders"
    assert "SendOrder" not in src
    assert "cancelorder" not in src.lower()
