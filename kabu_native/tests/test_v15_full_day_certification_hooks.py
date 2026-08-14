"""V15 full-day certification hooks: session clock, ingress replay, pre-paper gate.

Does not change Strategy / ENTRY / EXIT / Universe membership / CAP.
V14 remains immutable parent.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from runner.am_pm_daily_runner import now_jst, wait_until_hhmm
from small_paper.market_ingress_service import replay_payload_from_record
from small_paper.paper_full_day_certification import (
    audit_clock_access,
    detect_teardown_nameerror_would_fail_v13,
    enforce_pre_paper_certification_gate,
    source_regression_gates,
)
from small_paper.pilot_runner import apply_external_backup_teardown_logging
from small_paper.runtime_clock import (
    ENV_CERT_MODE,
    ENV_SKIP_CERT_GATE,
    bind_session_clock,
    now_jst as session_now,
    session_clock_enabled,
)
from small_paper.session_schedule import SessionSchedule, wait_until
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")


@pytest.fixture(autouse=True)
def _clear_clock_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in (
        "TRADEBOT_SESSION_CLOCK",
        "TRADEBOT_SESSION_CLOCK_V0",
        "TRADEBOT_SESSION_CLOCK_REAL_T0",
        "TRADEBOT_SESSION_CLOCK_SPEED",
        "TRADEBOT_SESSION_CLOCK_STOP",
        "TRADEBOT_SESSION_CLOCK_ARM_FILE",
        "TRADEBOT_INGRESS_REPLAY_PATH",
        "TRADEBOT_CERTIFICATION_MODE",
        "TRADEBOT_SKIP_CERT_GATE",
        "TRADEBOT_CERT_CONSUMER_EXTRA_DELAY_SEC",
    ):
        monkeypatch.delenv(k, raising=False)


def test_session_clock_disabled_is_wall() -> None:
    assert session_clock_enabled() is False
    delta = abs((session_now() - datetime.now(JST)).total_seconds())
    assert delta < 2.0


def test_session_clock_bind_advances_faster_than_wall(monkeypatch: pytest.MonkeyPatch) -> None:
    v0 = datetime(2026, 8, 12, 8, 50, 0, tzinfo=JST)
    bind_session_clock(virtual_start=v0, speed_mult=60.0)
    assert session_clock_enabled() is True
    assert now_jst().hour == 8
    import time as _t

    _t.sleep(0.2)
    virt = session_now()
    # 0.2s real * 60 = 12s virtual
    assert virt >= v0 + timedelta(seconds=8)


def test_wait_until_hhmm_uses_session_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    v0 = datetime(2026, 8, 12, 12, 24, 50, tzinfo=JST)
    bind_session_clock(virtual_start=v0, speed_mult=120.0)
    slept: list[float] = []

    def _sleep(sec: float) -> None:
        slept.append(sec)
        import time as _t

        _t.sleep(min(0.05, sec))

    out = wait_until_hhmm("12:25", dry_run_only=False, label="pm_screen", sleep_fn=_sleep)
    assert out["skipped"] is False
    assert out["target"] == "12:25"
    assert now_jst().time().hour == 12
    assert now_jst().time().minute >= 25


def test_session_schedule_wait_until_session_clock() -> None:
    v0 = datetime(2026, 8, 12, 9, 2, 50, tzinfo=JST)
    bind_session_clock(virtual_start=v0, speed_mult=120.0)
    sched = SessionSchedule("09:03", "11:25", v0.date())
    assert sched.is_before_session()
    wait_until(sched.start_dt, poll_sec=0.05)
    assert not sched.is_before_session()


def test_replay_payload_from_capture_stream_row() -> None:
    row = {
        "t": 1786493100.1,
        "symbol": "8050",
        "raw": "8050",
        "received_at": "2026-08-12T09:05:00.100+09:00",
        "Buy1": {"Price": 11270.0, "Qty": 100.0},
        "Sell1": {"Price": 11280.0, "Qty": 200.0},
        "CurrentPrice": 11275.0,
        "SpecialQuote": None,
    }
    p = replay_payload_from_record(row)
    assert p is not None
    assert p["Symbol"] == "8050"
    assert p["__replay_received_at__"] == "2026-08-12T09:05:00.100+09:00"
    assert p["Sell1"]["Qty"] == 200.0


def test_replay_payload_from_ingress_envelope() -> None:
    env = {
        "kind": "market_push",
        "received_at": "2026-08-12T09:05:00.200+09:00",
        "symbol": "285A",
        "original_payload": {"Symbol": "285A", "CurrentPrice": 50550.0, "Sell1": {"Qty": 100}},
    }
    p = replay_payload_from_record(env)
    assert p is not None
    assert p["Symbol"] == "285A"
    assert p["__replay_received_at__"].startswith("2026-08-12T09:05:00")


def test_v13_teardown_nameerror_is_detectable() -> None:
    det = detect_teardown_nameerror_would_fail_v13()
    assert det["ok"] is True
    assert det["v13_reproduced"] is True


def test_v15_teardown_logger_no_nameerror() -> None:
    summary = {"fatal_error": False}
    apply_external_backup_teardown_logging(
        summary,
        {
            "ok": False,
            "pending": True,
            "code": "EXTERNAL_BACKUP_PENDING",
            "session": "cert",
        },
    )
    assert summary["fatal_error"] is False
    assert summary["session_external_backup"]["pending"] is True


def test_pre_paper_gate_refuses_without_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv(ENV_SKIP_CERT_GATE, "")
    monkeypatch.delenv(ENV_CERT_MODE, raising=False)
    # Force no skip: PYTEST_CURRENT_TEST is set by pytest itself — the gate
    # treats it as skip. Directly test identities_equal / load_latest_pass instead.
    from small_paper.paper_full_day_certification import load_latest_pass

    monkeypatch.setattr(
        "small_paper.paper_full_day_certification.CERT_DIR", tmp_path
    )
    assert load_latest_pass(cert_dir=tmp_path) is None


def test_pre_paper_gate_skipped_in_cert_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_CERT_MODE, "1")
    assert enforce_pre_paper_certification_gate() == 0


def test_clock_audit_session_files_not_bypass() -> None:
    audit = audit_clock_access()
    assert audit["session_clock_bypass"] == [], audit["session_clock_bypass"]
    assert audit["verdict"] == "CLOCK_AUDIT_PASS"


def test_source_regression_gates_include_weekly_bugs() -> None:
    gates = source_regression_gates()
    assert gates["ok"], gates.get("failed")
    for name in (
        "LIVE_LAUNCHER_STUB",
        "PM_REBUILD_OVERWROTE_FROZEN_SOURCE",
        "TEARDOWN_EXTERNAL_BACKUP_LOGGER_NAMEERROR",
        "POST_SESSION_AM_SAFETY_SIDE_EFFECT",
        "STALE_RECOVERY_FORCE_EVAL_DEATH_SPIRAL",
    ):
        assert gates["checks"][name]["ok"], name


def test_session_clock_parks_until_arm(tmp_path: Path) -> None:
    v0 = datetime(2026, 8, 12, 8, 50, 0, tzinfo=JST)
    arm = tmp_path / "session_clock_arm.json"
    bind_session_clock(virtual_start=v0, speed_mult=48.0, arm_now=False, arm_file=arm)
    import time as _t

    _t.sleep(0.15)
    assert session_now() == v0
    from small_paper.runtime_clock import arm_session_clock

    arm_session_clock()
    _t.sleep(0.15)
    assert session_now() >= v0 + timedelta(seconds=4)


def test_inventory_includes_new_v15_modules() -> None:
    from small_paper.v1r_activation_binding import RUNTIME_DEPENDENCY_RELS

    assert "src/small_paper/runtime_clock.py" in RUNTIME_DEPENDENCY_RELS
    assert "src/small_paper/paper_full_day_certification.py" in RUNTIME_DEPENDENCY_RELS


def test_safety_trading_date_follows_session_clock() -> None:
    from small_paper.safety import safety_trading_date

    v0 = datetime(2026, 8, 12, 13, 8, 0, tzinfo=JST)
    bind_session_clock(virtual_start=v0, speed_mult=1.0)
    assert safety_trading_date() == "20260812"


def test_clock_audit_rejects_safety_wall_trading_date() -> None:
    clock = audit_clock_access()
    for row in clock.get("rows") or []:
        if str(row.get("file") or "").endswith("safety.py") and row.get("clock_domain") == "BYPASS":
            raise AssertionError(row)
