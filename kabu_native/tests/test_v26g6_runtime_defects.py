"""V26-G6: stage clock reset, window topology collector, cert-owned ingress stop."""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from small_paper.capture_child_cleanup import should_stop_on_shutdown
from small_paper.paper_trade_checked_runner import (
    PaperTradeCheckedRunner,
    write_live_forward_session_fixture,
)
from small_paper.runtime_clock import (
    ENV_ARM_FILE,
    ENV_CERT_MODE,
    ENV_ENABLED,
    ENV_REPLAY_PATH,
    ENV_SPEED,
    ENV_STOP,
    ENV_T0,
    ENV_V0,
    bind_session_clock,
    load_replay_watermarks,
    record_replay_progress,
    _clear_t0_file_cache,
    _t0_value,
)
from small_paper.session_runtime_identity import (
    expected_session_kinds,
    write_session_identity_file,
)

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[1]
CFG = NATIVE / "configs" / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
_CLOCK_KEYS = (
    ENV_ENABLED,
    ENV_V0,
    ENV_T0,
    ENV_SPEED,
    ENV_STOP,
    ENV_ARM_FILE,
    ENV_REPLAY_PATH,
    ENV_CERT_MODE,
)


@pytest.fixture(autouse=True)
def _clean_clock(monkeypatch: pytest.MonkeyPatch):
    _clear_t0_file_cache()
    for k in _CLOCK_KEYS:
        monkeypatch.delenv(k, raising=False)
    yield
    for k in _CLOCK_KEYS:
        monkeypatch.delenv(k, raising=False)
    _clear_t0_file_cache()


def test_expected_session_kinds_from_window_not_stage_name() -> None:
    assert expected_session_kinds(
        datetime(2026, 8, 12, 8, 50, tzinfo=JST),
        datetime(2026, 8, 12, 9, 20, tzinfo=JST),
    ) == frozenset({"am"})
    assert expected_session_kinds(
        datetime(2026, 8, 12, 12, 30, tzinfo=JST),
        datetime(2026, 8, 12, 15, 35, tzinfo=JST),
    ) == frozenset({"pm"})
    assert expected_session_kinds(
        datetime(2026, 8, 12, 11, 20, tzinfo=JST),
        datetime(2026, 8, 12, 12, 45, tzinfo=JST),
    ) == frozenset({"am", "pm"})
    assert expected_session_kinds(
        datetime(2026, 8, 12, 15, 10, tzinfo=JST),
        datetime(2026, 8, 12, 15, 35, tzinfo=JST),
    ) == frozenset({"pm"})
    assert expected_session_kinds(
        datetime(2026, 8, 12, 8, 50, tzinfo=JST),
        datetime(2026, 8, 12, 15, 35, tzinfo=JST),
    ) == frozenset({"am", "pm"})
    assert expected_session_kinds(None, None) == frozenset()


def test_bind_resets_previous_stage_watermarks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arm = tmp_path / "arm.json"
    replay = tmp_path / "tape.jsonl"
    replay.write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv(ENV_ARM_FILE, str(arm))
    monkeypatch.setenv(ENV_REPLAY_PATH, str(replay))
    env: dict[str, str] = os.environ.copy()
    v0 = datetime(2026, 8, 12, 8, 50, tzinfo=JST)
    stop = datetime(2026, 8, 12, 15, 35, tzinfo=JST)
    bind_session_clock(
        virtual_start=v0, speed_mult=48.0, stop=stop, arm_now=True, arm_file=arm, environ=env
    )
    record_replay_progress(
        source_event_time=stop,
        replay_read_watermark=stop,
        ingress_publish_watermark=stop,
        consumer_ack_watermark=stop,
        replay_eof=True,
        force=True,
        environ=env,
    )
    assert bool(load_replay_watermarks(environ=env).get("replay_eof")) is True
    assert _t0_value(environ=env) is not None

    v1 = datetime(2026, 8, 12, 12, 30, tzinfo=JST)
    bind_session_clock(
        virtual_start=v1, speed_mult=48.0, stop=stop, arm_now=False, arm_file=arm, environ=env
    )
    wm = load_replay_watermarks(environ=env)
    assert bool(wm.get("replay_eof")) is False
    assert wm.get("replay_read_watermark") in (None, "")
    assert _t0_value(environ=env) is None


def test_stale_arm_t0_ignored_when_v0_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arm = tmp_path / "arm.json"
    monkeypatch.setenv(ENV_ARM_FILE, str(arm))
    env: dict[str, str] = os.environ.copy()
    bind_session_clock(
        virtual_start=datetime(2026, 8, 12, 8, 50, tzinfo=JST),
        speed_mult=48.0,
        stop=datetime(2026, 8, 12, 15, 35, tzinfo=JST),
        arm_now=True,
        arm_file=arm,
        environ=env,
    )
    stale_t0 = _t0_value(environ=env)
    bind_session_clock(
        virtual_start=datetime(2026, 8, 12, 12, 30, tzinfo=JST),
        speed_mult=48.0,
        stop=datetime(2026, 8, 12, 15, 35, tzinfo=JST),
        arm_now=False,
        arm_file=arm,
        environ=env,
    )
    assert _t0_value(environ=env) is None
    # leftover t0 must not win after spec change
    arm.write_text(
        json.dumps({"t0": str(stale_t0), "v0": "2026-08-12T08:50:00.000+09:00", "replay_eof": True}),
        encoding="utf-8",
    )
    _clear_t0_file_cache()
    assert _t0_value(environ=env) is None


def _ok_w4s_runner():
    def run(cmd, env, cwd):  # noqa: ANN001
        if "phase687w4s" in " ".join(str(x) for x in cmd):
            return 0, '{"verdict":"OK","aggregate":{"session_count":1,"readonly_success_sessions":1}}', ""
        return 0, "{}", ""

    return run


def test_window_b_topology_collects_am_and_pm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADEBOT_CERTIFICATION_RUN_ID", "cert_g6_winb")
    monkeypatch.setenv("TRADEBOT_CERT_STAGE_RUN_ID", "window_B_g6")
    monkeypatch.setenv("TRADEBOT_RUNTIME_RUN_ID", "rtrun_g6b")
    monkeypatch.setenv("TRADEBOT_TRADING_DATE", "20260812")
    monkeypatch.setenv(ENV_ENABLED, "1")
    monkeypatch.setenv(ENV_V0, "2026-08-12T11:20:00.000+09:00")
    monkeypatch.setenv(ENV_STOP, "2026-08-12T12:45:00.000+09:00")
    am = NATIVE / "results" / "paper_sessions" / "g6_iso_winb_am"
    pm = NATIVE / "results" / "paper_sessions" / "g6_iso_winb_pm"
    for p in (am, pm):
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)
    try:
        write_live_forward_session_fixture(am, session_id="G6AM")
        write_live_forward_session_fixture(pm, session_id="G6PM")
        write_session_identity_file(am, session_id="G6AM", session_kind="am")
        write_session_identity_file(pm, session_id="G6PM", session_kind="pm")
        r = PaperTradeCheckedRunner(
            native_root=NATIVE,
            run_command=_ok_w4s_runner(),
            skip_paper=True,
            skip_w4s=False,
            config_path=CFG,
        )
        r.paper_exit_code = 0
        post = r.step_post_session(paper_ok=True)
        assert post["sessions_collected"] == 2
        assert post["result"] == "OK"
        assert post["result"] != "FAIL_CLOSED_MULTIPLE_CURRENT"
        assert post["session_topology"]["expected_session_kinds"] == ["am", "pm"]
    finally:
        for p in (am, pm):
            shutil.rmtree(p, ignore_errors=True)


def test_two_am_still_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADEBOT_CERTIFICATION_RUN_ID", "cert_g6_twoam")
    monkeypatch.setenv("TRADEBOT_CERT_STAGE_RUN_ID", "window_B_g6_bad")
    monkeypatch.setenv("TRADEBOT_TRADING_DATE", "20260812")
    monkeypatch.setenv(ENV_ENABLED, "1")
    monkeypatch.setenv(ENV_V0, "2026-08-12T11:20:00.000+09:00")
    monkeypatch.setenv(ENV_STOP, "2026-08-12T12:45:00.000+09:00")
    a = NATIVE / "results" / "paper_sessions" / "g6_iso_twoam_a"
    b = NATIVE / "results" / "paper_sessions" / "g6_iso_twoam_b"
    for p in (a, b):
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)
    try:
        write_live_forward_session_fixture(a, session_id="G6A1")
        write_live_forward_session_fixture(b, session_id="G6A2")
        write_session_identity_file(a, session_id="G6A1", session_kind="am")
        write_session_identity_file(b, session_id="G6A2", session_kind="am")
        r = PaperTradeCheckedRunner(
            native_root=NATIVE,
            run_command=_ok_w4s_runner(),
            skip_paper=True,
            skip_w4s=False,
            config_path=CFG,
        )
        r.paper_exit_code = 0
        post = r.step_post_session(paper_ok=True)
        assert post["result"] == "FAIL_CLOSED_MULTIPLE_CURRENT"
    finally:
        for p in (a, b):
            shutil.rmtree(p, ignore_errors=True)


def test_live_continuing_until_still_skips_stop() -> None:
    stop, why = should_stop_on_shutdown(
        reason="normal_exit",
        continuing_until_scheduled_end=True,
        synthetic=False,
        skip_capture_wait=False,
    )
    assert stop is False
    assert why == "capture_continuing_until_scheduled_end"


def test_certification_owned_ingress_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_CERT_MODE, "1")
    stop, why = should_stop_on_shutdown(
        reason="normal_exit",
        continuing_until_scheduled_end=True,
        synthetic=False,
        skip_capture_wait=False,
    )
    assert stop is True
    assert why == "certification_stage_owned_stop"


def test_wait_until_hhmm_skips_pm_when_stop_before_pm_without_replay_eof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AM-only cert STOP must skip PM wait even if replay lag blocks watermark EOF."""
    import time as _t

    from runner.am_pm_daily_runner import wait_until_hhmm

    tape = tmp_path / "tape.jsonl"
    tape.write_text("{}\n", encoding="utf-8")
    arm = tmp_path / "arm.json"
    v0 = datetime(2026, 8, 12, 9, 24, 50, tzinfo=JST)
    stop = datetime(2026, 8, 12, 9, 25, 0, tzinfo=JST)
    bind_session_clock(virtual_start=v0, speed_mult=120.0, stop=stop, arm_file=arm, arm_now=True)
    monkeypatch.setenv(ENV_REPLAY_PATH, str(tape))
    os.environ[ENV_REPLAY_PATH] = str(tape)

    def _sleep(sec: float) -> None:
        _t.sleep(min(0.05, sec))

    out = wait_until_hhmm("12:25", dry_run_only=False, label="pm_screening_start", sleep_fn=_sleep)
    assert out.get("reason") == "session_clock_stop"
    assert out.get("target") == "12:25"
    arm_doc = json.loads(arm.read_text(encoding="utf-8")) if arm.is_file() else {}
    assert arm_doc.get("replay_eof") in (None, False, "")

