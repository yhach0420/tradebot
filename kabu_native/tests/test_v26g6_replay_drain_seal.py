"""V26-G6: causal replay drain before session_clock_stop / SEALED_VALID."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from small_paper.runtime_clock import (
    ENV_ARM_FILE,
    ENV_CERT_MODE,
    ENV_ENABLED,
    ENV_REPLAY_PATH,
    ENV_SPEED,
    ENV_STOP,
    ENV_T0,
    ENV_V0,
    _clear_t0_file_cache,
    _parse_iso_dt,
    bind_session_clock,
    load_replay_watermarks,
    now_jst,
    record_replay_progress,
    replay_causal_stop_ready,
    replay_consumer_caught_publish,
    replay_max_publish_lag,
    session_clock_stop_reached,
)
from small_paper.session_validity import (
    INVALID_BOUNDED_STOP,
    SESSION_CLOCK_STOP,
    VALID_SESSION,
    classify_session_validity,
    is_valid_session_clock_stop,
)

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[1]
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


def _bind_replay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, stop: datetime, v0: datetime):
    arm = tmp_path / "arm.json"
    tape = tmp_path / "tape.jsonl"
    tape.write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv(ENV_REPLAY_PATH, str(tape))
    monkeypatch.setenv(ENV_ARM_FILE, str(arm))
    bind_session_clock(
        virtual_start=v0, speed_mult=1.0, stop=stop, arm_now=True, arm_file=arm
    )
    return arm


def test_lag_threshold_default_is_128() -> None:
    assert replay_max_publish_lag() == 128


def test_normal_end_when_lag_zero_and_watermarks_at_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    v0 = datetime(2026, 8, 12, 9, 3, tzinfo=JST)
    stop = datetime(2026, 8, 12, 9, 25, tzinfo=JST)
    _bind_replay(tmp_path, monkeypatch, stop=stop, v0=v0)
    record_replay_progress(
        source_event_time=stop,
        replay_read_watermark=stop,
        ingress_publish_watermark=stop,
        consumer_ack_watermark=stop,
        replay_eof=True,
        force=True,
    )
    assert session_clock_stop_reached() is True
    assert replay_causal_stop_ready() is True


def test_lag_over_128_does_not_stop_until_drain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    v0 = datetime(2026, 8, 12, 9, 3, tzinfo=JST)
    stop = datetime(2026, 8, 12, 9, 25, tzinfo=JST)
    _bind_replay(tmp_path, monkeypatch, stop=stop, v0=v0)
    pub = stop
    cons = stop - timedelta(seconds=8)
    record_replay_progress(
        source_event_time=pub,
        replay_read_watermark=pub,
        ingress_publish_watermark=pub,
        consumer_ack_watermark=cons,
        replay_eof=False,
        force=True,
    )
    assert session_clock_stop_reached() is False
    record_replay_progress(consumer_ack_watermark=pub, replay_eof=True, force=True)
    assert session_clock_stop_reached() is True


def test_debounce_drops_ack_still_behind_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    v0 = datetime(2026, 8, 12, 12, 30, tzinfo=JST)
    stop = datetime(2026, 8, 12, 12, 50, tzinfo=JST)
    _bind_replay(tmp_path, monkeypatch, stop=stop, v0=v0)
    pub = stop
    stale = stop - timedelta(seconds=10)
    behind = stop - timedelta(seconds=9)
    record_replay_progress(
        ingress_publish_watermark=pub,
        consumer_ack_watermark=stale,
        paper_last_processed_event_time=stale,
        force=True,
    )
    record_replay_progress(consumer_ack_watermark=behind, paper_last_processed_event_time=behind)
    wm = load_replay_watermarks()
    assert _parse_iso_dt(wm["consumer_ack_watermark"]) == stale


def test_debounce_does_not_drop_ack_that_catches_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    v0 = datetime(2026, 8, 12, 12, 30, tzinfo=JST)
    stop = datetime(2026, 8, 12, 12, 50, tzinfo=JST)
    _bind_replay(tmp_path, monkeypatch, stop=stop, v0=v0)
    pub = stop - timedelta(seconds=5)
    stale = stop - timedelta(seconds=10)
    record_replay_progress(
        source_event_time=pub,
        replay_read_watermark=pub,
        ingress_publish_watermark=pub,
        consumer_ack_watermark=stale,
        paper_last_processed_event_time=stale,
        force=True,
    )
    record_replay_progress(consumer_ack_watermark=pub, paper_last_processed_event_time=pub)
    wm = load_replay_watermarks()
    assert _parse_iso_dt(wm["consumer_ack_watermark"]) == pub
    assert replay_consumer_caught_publish() is True
    record_replay_progress(replay_eof=True, force=True)
    assert replay_causal_stop_ready() is True
    assert session_clock_stop_reached() is True


def test_eof_with_stale_cons_is_not_causal_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    v0 = datetime(2026, 8, 12, 12, 30, tzinfo=JST)
    stop = datetime(2026, 8, 12, 12, 50, tzinfo=JST)
    _bind_replay(tmp_path, monkeypatch, stop=stop, v0=v0)
    pub = datetime(2026, 8, 12, 12, 49, 55, 1000, tzinfo=JST)
    stale = datetime(2026, 8, 12, 12, 49, 50, 84000, tzinfo=JST)
    record_replay_progress(
        source_event_time=stop,
        replay_read_watermark=stop,
        ingress_publish_watermark=pub,
        consumer_ack_watermark=stale,
        paper_last_processed_event_time=stale,
        replay_eof=True,
        force=True,
    )
    assert replay_consumer_caught_publish() is False
    assert replay_causal_stop_ready() is False
    assert session_clock_stop_reached() is False
    record_replay_progress(consumer_ack_watermark=pub, paper_last_processed_event_time=pub)
    assert replay_causal_stop_ready() is True


def test_now_jst_follows_publish_so_consumer_can_drain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time as _t

    v0 = datetime(2026, 8, 12, 9, 3, tzinfo=JST)
    stop = datetime(2026, 8, 12, 9, 25, tzinfo=JST)
    arm = tmp_path / "arm.json"
    tape = tmp_path / "tape.jsonl"
    tape.write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv(ENV_REPLAY_PATH, str(tape))
    monkeypatch.setenv(ENV_ARM_FILE, str(arm))
    bind_session_clock(
        virtual_start=v0, speed_mult=120.0, stop=stop, arm_now=True, arm_file=arm
    )
    pub = v0 + timedelta(seconds=8)
    cons = v0 + timedelta(seconds=1)
    record_replay_progress(
        ingress_publish_watermark=pub, consumer_ack_watermark=cons, force=True
    )
    _t.sleep(0.12)
    n = now_jst()
    assert n >= pub
    assert n > cons + timedelta(seconds=2)


def test_watermark_before_stop_without_eof_forbids_causal_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    v0 = datetime(2026, 8, 12, 9, 3, tzinfo=JST)
    stop = datetime(2026, 8, 12, 9, 25, tzinfo=JST)
    _bind_replay(tmp_path, monkeypatch, stop=stop, v0=v0)
    early = stop - timedelta(seconds=40)
    record_replay_progress(
        source_event_time=early,
        replay_read_watermark=early,
        ingress_publish_watermark=early,
        consumer_ack_watermark=early,
        replay_eof=False,
        force=True,
    )
    assert session_clock_stop_reached() is False


def test_unprocessed_events_forbid_stop_even_after_eof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    v0 = datetime(2026, 8, 12, 9, 3, tzinfo=JST)
    stop = datetime(2026, 8, 12, 9, 25, tzinfo=JST)
    _bind_replay(tmp_path, monkeypatch, stop=stop, v0=v0)
    pub = stop
    cons = stop - timedelta(seconds=30)
    record_replay_progress(
        source_event_time=pub,
        replay_read_watermark=pub,
        ingress_publish_watermark=pub,
        consumer_ack_watermark=cons,
        replay_eof=True,
        force=True,
    )
    assert session_clock_stop_reached() is False


def test_eof_after_full_ack_allows_stop_even_if_last_event_before_exact_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    v0 = datetime(2026, 8, 12, 9, 3, tzinfo=JST)
    stop = datetime(2026, 8, 12, 9, 25, tzinfo=JST)
    _bind_replay(tmp_path, monkeypatch, stop=stop, v0=v0)
    last = stop - timedelta(milliseconds=304)
    record_replay_progress(
        source_event_time=last,
        replay_read_watermark=stop,
        ingress_publish_watermark=last,
        consumer_ack_watermark=last,
        replay_eof=True,
        force=True,
    )
    assert session_clock_stop_reached() is True


def _scope() -> dict[str, str]:
    return {
        "certification_run_id": "cert_g6_drain",
        "stage_run_id": "g6_fill_am_drain",
        "activation_id": "V1R_EXIT_V2_PAPER_PRIMARY_OPVAL_20260817",
        "activation_sha": "92b11e6cb1d62b74c6bc2ecf2fb20f6a2e679d47b1428803532597fb2b7b97d8",
        "runtime_run_id": "rtrun_g6_drain",
        "trading_date": "20260812",
        "session_id": "live_session_drain",
    }


def _valid_summary(**overrides: object) -> dict:
    scope = _scope()
    last = "2026-08-12T09:25:00.000+09:00"
    evidence = {
        "certification_mode": True,
        "session_clock_enabled": True,
        "replay_path_present": True,
        "configured_stop": last,
        "v0": "2026-08-12T09:03:00.000+09:00",
        "replay_not_before": "09:03",
        "replay_watermark": last,
        "paper_last_processed_event_time": last,
        "consumer_ack_watermark": last,
        "ingress_publish_watermark": last,
        "replay_read_watermark": last,
        "replay_eof": True,
        **scope,
    }
    body = {
        "stop_reason": SESSION_CLOCK_STOP,
        "push_messages": 1000,
        "gate_evaluations": 50,
        "heartbeat_count": 1,
        "runtime_sec": 80.0,
        "market_input_mode": "REPLAY",
        "session_seal_status": "SEALED_VALID",
        "session_clock_evidence": evidence,
        "paper_last_processed_event_time": last,
        **scope,
    }
    body.update(overrides)
    return body


def test_all_ack_required_for_valid_session_clock_stop() -> None:
    summary = _valid_summary()
    clock = is_valid_session_clock_stop(summary, expected_scope=_scope(), environ={})
    assert clock["ok"] is True
    v = classify_session_validity(summary, expected_scope=_scope(), environ={})
    assert v["session_validity"] == VALID_SESSION
    assert v["session_clock_stop_valid"] is True


def test_watermark_before_stop_without_eof_is_not_valid_session() -> None:
    evidence = dict(_valid_summary()["session_clock_evidence"])
    early = "2026-08-12T09:24:00.000+09:00"
    evidence["replay_watermark"] = early
    evidence["paper_last_processed_event_time"] = early
    evidence["consumer_ack_watermark"] = early
    evidence["ingress_publish_watermark"] = early
    evidence["replay_read_watermark"] = early
    evidence["replay_eof"] = False
    summary = _valid_summary(
        session_clock_evidence=evidence,
        paper_last_processed_event_time=early,
    )
    v = classify_session_validity(summary, expected_scope=_scope(), environ={})
    assert v["session_validity"] == INVALID_BOUNDED_STOP
    assert v["session_clock_stop_valid"] is False


def test_replay_loop_drains_at_stop_without_dropping_or_dup_publish() -> None:
    src = (NATIVE / "src/small_paper/market_ingress_service.py").read_text(encoding="utf-8")
    assert "def _replay_drain_published_backlog" in src
    assert "event_dt >= stop_dt" in src
    drain_idx = src.find("self._replay_drain_published_backlog()")
    stop_break = src.find("break", drain_idx)
    chunk = src[drain_idx:stop_break]
    assert "replay_eof=True" in chunk
    assert "self._on_push" not in chunk
    wait = src[src.find("def _replay_wait_consumer_lag") : src.find("def _replay_drain_published_backlog")]
    assert "reanchor_session_clock(event_dt)" not in wait
    drain = src[src.find("def _replay_drain_published_backlog") : src.find("def _mark_replay_resume_after_consumer_gap")]
    assert "replay_consumer_caught_publish" in drain
    assert "while self.bus.max_lag() > 0" in drain


def test_replay_loop_does_not_skip_ack_or_raise_lag_cap() -> None:
    src = (NATIVE / "src/small_paper/market_ingress_service.py").read_text(encoding="utf-8")
    clock = (NATIVE / "src/small_paper/runtime_clock.py").read_text(encoding="utf-8")
    assert "DEFAULT_REPLAY_MAX_PUBLISH_LAG = 128" in clock
    assert "while self.bus.max_lag() > cap" in src
    assert "while self.bus.max_lag() > 0" in src
    assert "return min(pub, cons)" not in clock
