"""V26-G3 targeted regressions: NameError, canonical V1R parity, clock STOP, 48x arm."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from replay.pnl_yen import compute_pnl_yen_100
from small_paper.canonical_summary import (
    collect_v1r_primary_canonical_trades,
    enrich_summary_with_canonical,
    v1r_exit_executed_to_canonical_trade,
)
from small_paper.discord_notifier import notify_discord_session_end
from small_paper.pre_session_warmup import ring_only_warmup_active
from small_paper.runtime_clock import (
    ENV_ARM_FILE,
    ENV_ENABLED,
    ENV_SPEED,
    ENV_STOP,
    ENV_T0,
    ENV_V0,
    bind_session_clock,
    ensure_session_clock_armed,
    now_jst,
    reanchor_session_clock,
    replay_max_publish_lag,
    session_clock_armed,
    session_clock_stop_reached,
    _clear_t0_file_cache,
)
from small_paper.v1r_live_dual_lane import ENV_FLAG, reset_dual_lane_for_tests

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[1]
_CLOCK_KEYS = (ENV_ENABLED, ENV_V0, ENV_T0, ENV_SPEED, ENV_STOP, ENV_ARM_FILE, "TRADEBOT_INGRESS_REPLAY_PATH", "TRADEBOT_CERTIFICATION_MODE")


@pytest.fixture(autouse=True)
def _clean_clock_env(monkeypatch: pytest.MonkeyPatch):
    _clear_t0_file_cache()
    yield
    for k in _CLOCK_KEYS:
        monkeypatch.delenv(k, raising=False)
    reset_dual_lane_for_tests()
    monkeypatch.delenv(ENV_FLAG, raising=False)
    _clear_t0_file_cache()


def test_pilot_runner_log_is_module_logger() -> None:
    from small_paper import pilot_runner as pr

    assert isinstance(pr.log, logging.Logger)
    pr.log.warning("discord session_end notify failed: %s", "timeout")
    pr.log.warning("discord session_end notify timed out; continuing finalize")
    pr.log.warning("post-finalize seal failed: %s", "x")
    pr.log.warning("session archive backup error: %s", "x")


def test_session_end_nameerror_paths_do_not_raise() -> None:
    from small_paper import pilot_runner as pr

    try:
        raise RuntimeError("discord down")
    except Exception as exc:
        pr.log.warning("discord session_end notify failed: %s", exc)
        pr.log.warning("discord session_end notify failed: %s", "nested")


def test_notify_discord_session_end_sink_disabled() -> None:
    notify_discord_session_end(
        None,
        events=[],
        summary={"canonical_summary": {"trade_count": 0, "total_pnl_yen_100": 0.0}},
        native_root=NATIVE,
        output_dir=None,
    )


def test_v1r_exit_executed_maps_to_canonical_and_excludes_shadow() -> None:
    fill, exit_p = 1000.0, 1010.0
    primary = {
        "event": "EXIT_EXECUTED",
        "lane": "primary",
        "symbol": "7203",
        "fill_price": fill,
        "exit_price": exit_p,
        "reason": "EXIT_600",
        "exit_time": 1.0,
        "slot_released": True,
    }
    control = dict(primary, event="CONTROL_EXIT", lane="control")
    cap = {"event": "admit_rejected_non_v1r_source", "lane": "primary", "symbol": "6098"}
    trades = collect_v1r_primary_canonical_trades([primary, control, cap])
    assert len(trades) == 1
    yen = compute_pnl_yen_100(fill, exit_p)
    assert trades[0]["pnl_yen_100"] == round(yen, 2)
    assert v1r_exit_executed_to_canonical_trade(control) is None


def test_canonical_summary_v1r_parity_one_trade(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_FLAG, "1")
    reset_dual_lane_for_tests()
    fill, exit_p = 1500.0, 1485.0
    traces = [
        {
            "event": "EXIT_EXECUTED",
            "lane": "primary",
            "symbol": "6098",
            "fill_price": fill,
            "exit_price": exit_p,
            "reason": "GUARD",
            "exit_time": 2.0,
        },
        {
            "event": "CONTROL_EXIT",
            "lane": "control",
            "symbol": "6098",
            "fill_price": fill,
            "exit_price": 1600.0,
            "reason": "FIXED600",
            "exit_time": 2.0,
        },
    ]
    observer_events = [
        {
            "event_type": "observer_exit",
            "symbol": "9984.T",
            "entry_price": 3000,
            "exit_price": 3300,
            "pnl_pct": 10.0,
            "exit_reason": "trailing_mfe_exit",
        }
    ]
    summary: dict = {}
    enrich_summary_with_canonical(
        summary,
        observer_events,
        max_concurrent_positions=5,
        v1r_traces=traces,
    )
    yen = round(compute_pnl_yen_100(fill, exit_p), 2)
    assert summary["canonical_summary"]["trade_count"] == 1
    assert summary["canonical_summary"]["total_pnl_yen_100"] == yen
    assert summary["canonical_summary_source"] == "v1r_primary_exit_executed"


def test_canonical_summary_observer_path_unchanged_without_v1r() -> None:
    events = [
        {
            "event_type": "observer_exit",
            "symbol": "7203.T",
            "entry_price": 1000,
            "exit_price": 1010,
            "pnl_pct": 1.0,
            "exit_reason": "trailing_mfe_exit",
        }
    ]
    summary: dict = {}
    enrich_summary_with_canonical(summary, events, max_concurrent_positions=3)
    assert summary["canonical_summary"]["trade_count"] == 1
    assert summary["canonical_summary_source"] == "observer_exit"


def test_session_clock_parks_during_prebuild_then_arms() -> None:
    v0 = datetime(2026, 8, 12, 8, 50, 0, tzinfo=JST)
    bind_session_clock(virtual_start=v0, speed_mult=48.0, arm_now=False)
    import time as _t

    _t.sleep(0.2)
    assert now_jst() == v0
    assert session_clock_armed() is False
    ensure_session_clock_armed()
    _t.sleep(0.2)
    assert now_jst() >= v0 + timedelta(seconds=4)


def test_session_clock_stop_reached_does_not_freeze_now() -> None:
    v0 = datetime(2026, 8, 12, 9, 0, 0, tzinfo=JST)
    stop = v0 + timedelta(seconds=2)
    bind_session_clock(virtual_start=v0, speed_mult=20.0, stop=stop, arm_now=True)
    import time as _t

    deadline = _t.time() + 3.0
    while _t.time() < deadline and not session_clock_stop_reached():
        _t.sleep(0.05)
    assert session_clock_stop_reached() is True
    a = now_jst()
    _t.sleep(0.15)
    b = now_jst()
    assert b > a
    assert b > stop


def test_launcher_does_not_arm_session_clock_early() -> None:
    src = (NATIVE / "src/small_paper/v1r_paper_primary_launcher.py").read_text(encoding="utf-8")
    assert "arm_session_clock(environ=env)" not in src
    assert "Do not arm TRADEBOT_SESSION_CLOCK here" in src
    pilot = (NATIVE / "src/small_paper/pilot_runner.py").read_text(encoding="utf-8")
    assert "ensure_session_clock_armed()" in pilot
    assert "session_clock_stop_reached()" in pilot
    assert "now=session_now()" in pilot


def test_warmup_uses_session_clock_not_wall() -> None:
    v0 = datetime(2026, 8, 12, 8, 55, 0, tzinfo=JST)
    bind_session_clock(virtual_start=v0, speed_mult=1.0, arm_now=False)

    class Cfg:
        pre_session_warmup_enabled = True

    class Pol:
        allowed_entry_start = "09:03"
        kind = "am"

        def entry_stop_reached(self, now=None) -> bool:
            return False

        def entry_allowed_now(self, now=None) -> bool:
            return False

    assert ring_only_warmup_active(config=Cfg(), am_pm_policy=Pol(), now=now_jst()) is True


def test_replay_catchup_reanchors_clock_to_event_not_dump_ahead() -> None:
    v0 = datetime(2026, 8, 12, 8, 50, 0, tzinfo=JST)
    bind_session_clock(virtual_start=v0, speed_mult=48.0, arm_now=True)
    import time as _t

    _t.sleep(0.15)
    assert now_jst() >= v0 + timedelta(seconds=4)
    event_dt = v0 + timedelta(seconds=2)
    assert event_dt < now_jst()
    reanchor_session_clock(event_dt)
    delta = abs((now_jst() - event_dt).total_seconds())
    assert delta < 0.25


def test_arm_file_is_t0_source_of_truth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    arm = tmp_path / "arm.json"
    monkeypatch.setenv(ENV_ARM_FILE, str(arm))
    v0 = datetime(2026, 8, 12, 8, 50, 0, tzinfo=JST)
    bind_session_clock(virtual_start=v0, speed_mult=12.0, arm_now=True, arm_file=arm)
    event_dt = v0 + timedelta(seconds=30)
    reanchor_session_clock(event_dt)
    monkeypatch.setenv(ENV_T0, "1.0")
    delta = abs((now_jst() - event_dt).total_seconds())
    assert delta < 1.0


@pytest.mark.parametrize("speed_mult", [1.0, 4.0, 12.0, 48.0])
def test_replay_speed_reanchor_parity(speed_mult: float) -> None:
    v0 = datetime(2026, 8, 12, 9, 5, 0, tzinfo=JST)
    bind_session_clock(virtual_start=v0, speed_mult=speed_mult, arm_now=True)
    import time as _t

    _t.sleep(0.08)
    event_dt = v0 + timedelta(seconds=2)
    reanchor_session_clock(event_dt)
    delta = abs((now_jst() - event_dt).total_seconds())
    assert delta < 0.25, (speed_mult, delta, now_jst(), event_dt)


def test_ingress_replay_paces_and_waits_consumer_lag() -> None:
    src = (NATIVE / "src/small_paper/market_ingress_service.py").read_text(encoding="utf-8")
    assert "def _replay_pace_to_session" in src
    assert "reanchor_session_clock(event_dt)" in src
    assert "def _replay_wait_consumer_lag" in src
    assert "replay_max_publish_lag" in src
    assert replay_max_publish_lag() == 128


def test_consumer_delay_uses_session_now_not_wall() -> None:
    src = (NATIVE / "src/small_paper/pilot_runner.py").read_text(encoding="utf-8")
    assert "wall_now = session_now()" in src
    assert "tracker.note_consumer_delay(event_time=ev_dt, wall_now=wall_now)" in src


def test_arm_file_torn_read_does_not_fall_back_to_stale_env_t0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arm = tmp_path / "arm.json"
    monkeypatch.setenv(ENV_ARM_FILE, str(arm))
    v0 = datetime(2026, 8, 12, 8, 50, 0, tzinfo=JST)
    bind_session_clock(virtual_start=v0, speed_mult=48.0, arm_now=True, arm_file=arm)
    event_dt = v0 + timedelta(seconds=30)
    reanchor_session_clock(event_dt)
    held = now_jst()
    monkeypatch.setenv(ENV_T0, "1.0")
    arm.write_text("{", encoding="utf-8")
    delta = abs((now_jst() - held).total_seconds())
    assert delta < 0.5


def test_capture_finish_labels_live_sidecar_not_paper_push() -> None:
    src = (NATIVE / "src/small_paper/paper_trade_checked_runner.py").read_text(encoding="utf-8")
    assert "live_capture_sidecar_events" in src
    assert "not Paper PUSH" in src


def test_candidate1_not_rewritten() -> None:
    p = (
        NATIVE
        / "results"
        / "research"
        / "v1r_exit_v2_prospective_activation"
        / "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G2_1.json"
    )
    assert p.is_file()
    body = json.loads(p.read_text(encoding="utf-8"))
    assert body["candidate_id"] == "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G2_1"
    assert body["sha256"] == "d7e100dfd62bdb4da7fe055aa23f26c51c379348e8e8c9800052b1c54495cd62"


def test_candidate_v26g3_2_not_rewritten() -> None:
    p = (
        NATIVE
        / "results"
        / "research"
        / "v1r_exit_v2_prospective_activation"
        / "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G3_2.json"
    )
    assert p.is_file()
    body = json.loads(p.read_text(encoding="utf-8"))
    assert body["candidate_id"] == "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G3_2"
    assert body["sha256"] == "7238266f815458ef4a769be0c23c096922b06c77200c47f7adbf703fd45c286f"
