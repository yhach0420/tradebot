"""V26-G4: AM→PM Ingress lifetime, replay watermark clock, collector binding."""
from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from small_paper.canonical_summary import collect_v1r_primary_canonical_trades
from small_paper.paper_trade_checked_runner import (
    PaperTradeCheckedRunner,
    qualify_snapshot_path,
    write_live_forward_session_fixture,
    write_qualified_session_fixture,
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
    now_jst,
    projected_session_now,
    reanchor_session_clock,
    record_replay_progress,
    session_clock_stop_reached,
    _clear_t0_file_cache,
)
from small_paper.session_runtime_identity import (
    expected_current_run_scope,
    iter_current_run_soak_snapshots,
    stamp_session_identity,
    write_session_identity_file,
)
from small_paper.session_validity import (
    INVALID_ABNORMAL_STOP,
    INVALID_BOUNDED_STOP,
    INVALID_NO_GATE,
    SESSION_CLOCK_STOP,
    VALID_SESSION,
    _NORMAL_STOP,
    classify_session_validity,
    is_valid_session_clock_stop,
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
    "TRADEBOT_CERTIFICATION_RUN_ID",
    "TRADEBOT_CERT_STAGE_RUN_ID",
    "TRADEBOT_RUNTIME_RUN_ID",
    "TRADEBOT_SESSION_ID",
    "TRADEBOT_DAILY_RUN_ID",
    "TRADEBOT_TRADING_DATE",
)


@pytest.fixture(autouse=True)
def _clean_clock_env(monkeypatch: pytest.MonkeyPatch):
    _clear_t0_file_cache()
    for k in _CLOCK_KEYS:
        monkeypatch.delenv(k, raising=False)
    yield
    for k in _CLOCK_KEYS:
        monkeypatch.delenv(k, raising=False)
    _clear_t0_file_cache()


def test_ingress_holds_replay_after_paper_disconnect() -> None:
    src = (NATIVE / "src/small_paper/market_ingress_service.py").read_text(encoding="utf-8")
    assert "self._paper_consumer_seen" in src
    assert "dump the remaining Full-Day tape" in src
    assert "except (PermissionError, OSError)" in src
    assert "projected_session_now()" in src


def test_replay_clock_frozen_until_watermark(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arm = tmp_path / "arm.json"
    replay = tmp_path / "tape.jsonl"
    replay.write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv(ENV_ARM_FILE, str(arm))
    monkeypatch.setenv(ENV_REPLAY_PATH, str(replay))
    v0 = datetime(2026, 8, 12, 8, 50, 0, tzinfo=JST)
    bind_session_clock(virtual_start=v0, speed_mult=48.0, arm_now=True, arm_file=arm)
    import time as _t

    _t.sleep(0.12)
    assert projected_session_now() >= v0 + timedelta(seconds=3)
    assert abs((now_jst() - v0).total_seconds()) < 0.5


def test_session_stop_requires_replay_watermark(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arm = tmp_path / "arm.json"
    replay = tmp_path / "tape.jsonl"
    replay.write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv(ENV_ARM_FILE, str(arm))
    monkeypatch.setenv(ENV_REPLAY_PATH, str(replay))
    v0 = datetime(2026, 8, 12, 8, 50, 0, tzinfo=JST)
    stop = v0 + timedelta(seconds=2)
    bind_session_clock(
        virtual_start=v0, speed_mult=48.0, stop=stop, arm_now=True, arm_file=arm
    )
    import time as _t

    deadline = _t.time() + 3.0
    while _t.time() < deadline and projected_session_now() < stop:
        _t.sleep(0.05)
    assert projected_session_now() >= stop
    assert session_clock_stop_reached() is False
    record_replay_progress(
        source_event_time=stop,
        replay_read_watermark=stop,
        ingress_publish_watermark=stop,
        consumer_ack_watermark=stop,
        force=True,
    )
    assert session_clock_stop_reached() is True


def test_now_jst_does_not_pass_watermark(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arm = tmp_path / "arm.json"
    replay = tmp_path / "tape.jsonl"
    replay.write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv(ENV_ARM_FILE, str(arm))
    monkeypatch.setenv(ENV_REPLAY_PATH, str(replay))
    v0 = datetime(2026, 8, 12, 8, 50, 0, tzinfo=JST)
    wm = v0 + timedelta(minutes=14)
    bind_session_clock(virtual_start=v0, speed_mult=48.0, arm_now=True, arm_file=arm)
    record_replay_progress(ingress_publish_watermark=wm, consumer_ack_watermark=wm, force=True)
    import time as _t

    _t.sleep(0.12)
    assert projected_session_now() >= v0 + timedelta(seconds=3)
    assert now_jst() <= wm + timedelta(seconds=3)


def test_reanchor_permission_error_does_not_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arm = tmp_path / "arm.json"
    monkeypatch.setenv(ENV_ARM_FILE, str(arm))
    v0 = datetime(2026, 8, 12, 8, 50, 0, tzinfo=JST)
    bind_session_clock(virtual_start=v0, speed_mult=12.0, arm_now=True, arm_file=arm)

    def _boom(*_a, **_k):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(os, "replace", _boom)
    reanchor_session_clock(v0 + timedelta(seconds=30))


def test_am_pm_wait_uses_projected_clock() -> None:
    src = (NATIVE / "src/runner/am_pm_daily_runner.py").read_text(encoding="utf-8")
    assert "projected_session_now() if session_clock_enabled()" in src
    assert "PM skipped: TRADEBOT_SESSION_CLOCK_STOP reached before PM screen" in src


def test_warmup_barrier_source() -> None:
    src = (NATIVE / "src/small_paper/pilot_runner.py").read_text(encoding="utf-8")
    assert "ensure_session_clock_armed()" in src
    assert "record_replay_progress" in src
    clock = (NATIVE / "src/small_paper/runtime_clock.py").read_text(encoding="utf-8")
    assert "replay_clock_bind_enabled" in clock
    assert "causally capped to replay watermarks" in clock


def test_sealed_valid_collected_when_paper_exit_nonzero(tmp_path: Path) -> None:
    root = tmp_path / "live_session_cur"
    snap = write_live_forward_session_fixture(root, session_id="CUR")
    q = qualify_snapshot_path(snap, paper_exit_code=2)
    assert q["seal_qualified"] is True
    assert "paper_exit_code!=0" not in (q.get("failures") or [])
    assert q["fields"].get("paper_exit_failure") is True


def test_collector_excludes_previous_stage_and_wrong_activation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRADEBOT_CERTIFICATION_RUN_ID", "cert_g4")
    monkeypatch.setenv("TRADEBOT_STAGE_RUN_ID", "window_A_cur")
    monkeypatch.setenv("TRADEBOT_RUNTIME_RUN_ID", "rtrun_g4")
    results = tmp_path / "results"

    cur = results / "small_paper" / "20260812" / "live_session_cur"
    write_live_forward_session_fixture(cur, session_id="CUR")
    write_session_identity_file(cur, session_id="CUR")
    stamp = json.loads((cur / "session_identity.json").read_text(encoding="utf-8"))
    stamp.update(
        {
            "certification_run_id": "cert_g4",
            "stage_run_id": "window_A_cur",
            "activation_sha": "aaa",
            "runtime_run_id": "rtrun_g4",
            "trading_date": "20260812",
        }
    )
    (cur / "session_identity.json").write_text(json.dumps(stamp, indent=2) + "\n", encoding="utf-8")

    prev = results / "small_paper" / "20260812" / "live_session_prev"
    write_live_forward_session_fixture(prev, session_id="PREV")
    write_session_identity_file(prev, session_id="PREV")
    pstamp = dict(stamp)
    pstamp["stage_run_id"] = "window_A_prev"
    pstamp["session_id"] = "PREV"
    (prev / "session_identity.json").write_text(json.dumps(pstamp, indent=2) + "\n", encoding="utf-8")

    wrong = results / "small_paper" / "20260812" / "live_session_wrong_act"
    write_live_forward_session_fixture(wrong, session_id="WRONG")
    write_session_identity_file(wrong, session_id="WRONG")
    wstamp = dict(stamp)
    wstamp["activation_sha"] = "bbb"
    wstamp["session_id"] = "WRONG"
    (wrong / "session_identity.json").write_text(json.dumps(wstamp, indent=2) + "\n", encoding="utf-8")

    expected = {
        "certification_run_id": "cert_g4",
        "stage_run_id": "window_A_cur",
        "activation_sha": "aaa",
        "runtime_run_id": "rtrun_g4",
    }
    snaps = iter_current_run_soak_snapshots(results, expected=expected)
    assert len(snaps) == 1
    assert "live_session_cur" in str(snaps[0])


def test_missing_and_invalid_seal_fail(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    missing.mkdir()
    safety = missing / "live_order_safety"
    safety.mkdir()
    snap = safety / "soak_session_snapshot.json"
    snap.write_text(json.dumps({"session_id": "M", "session_seal_status": "MISSING"}), encoding="utf-8")
    q = qualify_snapshot_path(snap, paper_exit_code=0)
    assert q["seal_qualified"] is False

    root = tmp_path / "invalid"
    write_qualified_session_fixture(root, session_id="INV")
    seal_path = root / "session_seal.json"
    body = json.loads(seal_path.read_text(encoding="utf-8"))
    body["session_seal_status"] = "SEAL_INVALID"
    seal_path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    q2 = qualify_snapshot_path(root / "live_order_safety" / "soak_session_snapshot.json", paper_exit_code=0)
    assert q2["seal_qualified"] is False


def test_canonical_summary_still_exclusive_primary() -> None:
    src = (NATIVE / "src/small_paper/canonical_summary.py").read_text(encoding="utf-8")
    assert "collect_v1r_primary_canonical_trades" in src
    assert "v1r_exit_executed_to_canonical_trade" in src


def test_disconnect_wait_does_not_reanchor() -> None:
    src = (NATIVE / "src/small_paper/market_ingress_service.py").read_text(encoding="utf-8")
    assert "Do not reanchor during that wait" in src
    assert "_mark_replay_resume_after_consumer_gap" in src
    assert "FULL_DAY_PM_RESUME" in src


def test_expected_scope_does_not_mint_runtime_run_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TRADEBOT_RUNTIME_RUN_ID", raising=False)
    monkeypatch.setenv("TRADEBOT_CERTIFICATION_RUN_ID", "cert_nomint")
    monkeypatch.setenv("TRADEBOT_CERT_STAGE_RUN_ID", "window_A_nomint")
    before = os.environ.get("TRADEBOT_RUNTIME_RUN_ID")
    scope = expected_current_run_scope(trading_date="20260812")
    assert "runtime_run_id" not in scope
    assert os.environ.get("TRADEBOT_RUNTIME_RUN_ID") == before
    assert scope.get("certification_run_id") == "cert_nomint"


def test_collector_stale_cert_excluded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADEBOT_CERTIFICATION_RUN_ID", "cert_g4")
    monkeypatch.setenv("TRADEBOT_CERT_STAGE_RUN_ID", "window_A_cur")
    monkeypatch.setenv("TRADEBOT_RUNTIME_RUN_ID", "rtrun_g4")
    results = tmp_path / "results"
    stale = results / "small_paper" / "20260812" / "live_session_stale"
    write_live_forward_session_fixture(stale, session_id="STALE")
    write_session_identity_file(stale, session_id="STALE")
    stamp = json.loads((stale / "session_identity.json").read_text(encoding="utf-8"))
    stamp.update(
        {
            "certification_run_id": "cert_old",
            "stage_run_id": "window_A_cur",
            "activation_sha": "aaa",
            "runtime_run_id": "rtrun_old",
            "trading_date": "20260812",
        }
    )
    (stale / "session_identity.json").write_text(json.dumps(stamp, indent=2) + "\n", encoding="utf-8")
    expected = {
        "certification_run_id": "cert_g4",
        "stage_run_id": "window_A_cur",
        "activation_sha": "aaa",
        "runtime_run_id": "rtrun_g4",
    }
    snaps = iter_current_run_soak_snapshots(results, expected=expected)
    assert snaps == []


def _ok_w4s_runner():
    def run(cmd, env, cwd):
        s = cmd if isinstance(cmd, str) else " ".join(str(x) for x in cmd)
        if "phase687w4s" in s:
            return 0, json.dumps({"verdict": "READONLY_SOAK_IN_PROGRESS", "aggregate": {"session_count": 1}}), ""
        return 0, "{}", ""

    return run


def _stamp_current(root: Path, session_id: str) -> None:
    write_live_forward_session_fixture(root, session_id=session_id)
    write_session_identity_file(root, session_id=session_id)


def test_current_sealed_valid_collected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADEBOT_CERTIFICATION_RUN_ID", "cert_g4_collect")
    monkeypatch.setenv("TRADEBOT_CERT_STAGE_RUN_ID", "window_A_g4c")
    monkeypatch.setenv("TRADEBOT_RUNTIME_RUN_ID", "rtrun_g4c")
    monkeypatch.setenv("TRADEBOT_TRADING_DATE", "20260812")
    live_root = NATIVE / "results" / "paper_sessions" / "g4_iso_cur"
    if live_root.exists():
        shutil.rmtree(live_root, ignore_errors=True)
    try:
        _stamp_current(live_root, "G4CUR")
        r = PaperTradeCheckedRunner(
            native_root=NATIVE,
            run_command=_ok_w4s_runner(),
            skip_paper=True,
            skip_w4s=False,
            config_path=CFG,
        )
        r.paper_exit_code = 0
        post = r.step_post_session(paper_ok=True)
        assert post["sessions_collected"] >= 1
        assert post["result"] != "SESSION_ARTIFACT_INCOMPLETE"
    finally:
        shutil.rmtree(live_root, ignore_errors=True)


def test_multiple_current_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADEBOT_CERTIFICATION_RUN_ID", "cert_g4_multi")
    monkeypatch.setenv("TRADEBOT_CERT_STAGE_RUN_ID", "window_A_g4m")
    monkeypatch.setenv("TRADEBOT_RUNTIME_RUN_ID", "rtrun_g4m")
    monkeypatch.setenv("TRADEBOT_TRADING_DATE", "20260812")
    a = NATIVE / "results" / "paper_sessions" / "g4_iso_multi_a"
    b = NATIVE / "results" / "paper_sessions" / "g4_iso_multi_b"
    for p in (a, b):
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)
    try:
        _stamp_current(a, "G4A")
        _stamp_current(b, "G4B")
        r = PaperTradeCheckedRunner(
            native_root=NATIVE,
            run_command=_ok_w4s_runner(),
            skip_paper=True,
            skip_w4s=False,
            config_path=CFG,
        )
        r.paper_exit_code = 0
        post = r.step_post_session(paper_ok=True)
        assert post["sessions_collected"] > 1
        assert post["result"] == "FAIL_CLOSED_MULTIPLE_CURRENT"
    finally:
        shutil.rmtree(a, ignore_errors=True)
        shutil.rmtree(b, ignore_errors=True)


def _scope() -> dict[str, str]:
    return {
        "certification_run_id": "cert_g4_stop",
        "stage_run_id": "g4_window_a_stop",
        "activation_id": "V1R_EXIT_V2_PAPER_PRIMARY_WORKING_V26G4",
        "activation_sha": "276d71580c8a093b8bd1ce08537c720b4b982a26ae2ac08015599da0158898d7",
        "runtime_run_id": "rtrun_g4_stop",
        "trading_date": "20260812",
        "session_id": "live_session_071158",
    }


def _window_a_like_summary(**overrides: object) -> dict:
    scope = _scope()
    evidence = {
        "certification_mode": True,
        "session_clock_enabled": True,
        "replay_path_present": True,
        "configured_stop": "2026-08-12T09:20:00.000+09:00",
        "v0": "2026-08-12T08:50:00.000+09:00",
        "replay_not_before": "08:50",
        "replay_watermark": "2026-08-12T09:20:05.032+09:00",
        "paper_last_processed_event_time": "2026-08-12T09:20:00.092+09:00",
        "consumer_ack_watermark": "2026-08-12T09:20:00.092+09:00",
        "ingress_publish_watermark": "2026-08-12T09:20:05.032+09:00",
        "replay_read_watermark": "2026-08-12T09:20:05.032+09:00",
        "replay_eof": True,
        **scope,
    }
    body = {
        "stop_reason": SESSION_CLOCK_STOP,
        "push_messages": 149656,
        "gate_evaluations": 8583,
        "heartbeat_count": 1,
        "runtime_sec": 1300.0,
        "market_input_mode": "REPLAY",
        "session_start": "09:03",
        "am_pm_session": {"allowed_entry_start": "09:03", "session_start": "09:03"},
        "paper_last_processed_event_time": "2026-08-12T09:20:00.092+09:00",
        "session_clock_evidence": evidence,
        **scope,
    }
    body.update(overrides)
    if "session_clock_evidence" in overrides and overrides["session_clock_evidence"] is not None:
        body["session_clock_evidence"] = overrides["session_clock_evidence"]
    return body


def test_session_clock_stop_not_in_normal_stop() -> None:
    assert SESSION_CLOCK_STOP not in _NORMAL_STOP


def test_window_a_session_clock_stop_is_valid() -> None:
    summary = _window_a_like_summary()
    clock = is_valid_session_clock_stop(summary, expected_scope=_scope(), environ={})
    assert clock["ok"] is True
    v = classify_session_validity(summary, expected_scope=_scope(), environ={})
    assert v["session_validity"] == VALID_SESSION
    assert v["session_clock_stop_valid"] is True
    assert v["session_validity"] != INVALID_NO_GATE


def test_identity_ignores_daily_run_id_and_accepts_composite_session_id() -> None:
    summary = _window_a_like_summary()
    summary["session_id"] = "20260816_am_live_session_071158"
    summary["session_clock_evidence"] = dict(
        summary["session_clock_evidence"], session_id="20260816_am_live_session_071158"
    )
    expected = dict(
        _scope(),
        daily_run_id="daily_cert_extra",
        session_id="live_session_071158",
    )
    v = classify_session_validity(summary, expected_scope=expected, environ={})
    assert v["session_validity"] == VALID_SESSION
    assert v["session_clock_stop_valid"] is True


def test_live_session_clock_stop_without_bounded_cert_is_invalid() -> None:
    summary = {
        "stop_reason": SESSION_CLOCK_STOP,
        "push_messages": 100,
        "gate_evaluations": 10,
        "runtime_sec": 60.0,
        "session_id": "live_session_live",
        "market_input_mode": "LIVE",
    }
    v = classify_session_validity(summary, expected_scope=_scope(), environ={})
    assert v["session_validity"] != VALID_SESSION
    assert v["session_clock_stop_valid"] is False


def test_watermark_before_stop_without_eof_is_invalid() -> None:
    evidence = dict(_window_a_like_summary()["session_clock_evidence"])
    evidence["replay_watermark"] = "2026-08-12T09:10:00.000+09:00"
    evidence["paper_last_processed_event_time"] = "2026-08-12T09:10:00.000+09:00"
    evidence["consumer_ack_watermark"] = "2026-08-12T09:10:00.000+09:00"
    evidence["ingress_publish_watermark"] = "2026-08-12T09:10:00.000+09:00"
    evidence["replay_read_watermark"] = "2026-08-12T09:10:00.000+09:00"
    evidence["replay_eof"] = False
    summary = _window_a_like_summary(
        session_clock_evidence=evidence,
        paper_last_processed_event_time="2026-08-12T09:10:00.000+09:00",
    )
    v = classify_session_validity(summary, expected_scope=_scope(), environ={})
    assert v["session_validity"] == INVALID_BOUNDED_STOP
    assert v["session_clock_stop_valid"] is False
    assert v["session_validity"] != INVALID_NO_GATE


def test_waiting_market_session_clock_stop_is_invalid() -> None:
    summary = _window_a_like_summary(gate_evaluations=0, push_messages=24433)
    v = classify_session_validity(summary, expected_scope=_scope(), environ={})
    assert v["session_validity"] == INVALID_NO_GATE
    assert v["session_clock_stop_valid"] is False


def test_eof_during_warmup_is_invalid() -> None:
    evidence = dict(_window_a_like_summary()["session_clock_evidence"])
    evidence["replay_watermark"] = "2026-08-12T08:55:00.000+09:00"
    evidence["paper_last_processed_event_time"] = "2026-08-12T08:55:00.000+09:00"
    evidence["consumer_ack_watermark"] = "2026-08-12T08:55:00.000+09:00"
    evidence["ingress_publish_watermark"] = "2026-08-12T08:55:00.000+09:00"
    evidence["replay_read_watermark"] = "2026-08-12T08:55:00.000+09:00"
    evidence["replay_eof"] = True
    summary = _window_a_like_summary(
        session_clock_evidence=evidence,
        paper_last_processed_event_time="2026-08-12T08:55:00.000+09:00",
        gate_evaluations=1,
    )
    v = classify_session_validity(summary, expected_scope=_scope(), environ={})
    assert v["session_validity"] == INVALID_BOUNDED_STOP
    assert v["session_clock_stop_reason"] == "stop_preempted_replay_warmup"


def test_wrong_cert_stage_identity_is_invalid() -> None:
    summary = _window_a_like_summary()
    wrong = dict(_scope(), certification_run_id="cert_other", stage_run_id="g4_other")
    v = classify_session_validity(summary, expected_scope=wrong, environ={})
    assert v["session_validity"] == INVALID_BOUNDED_STOP
    assert v["session_clock_stop_reason"] == "current_run_identity_mismatch"


def test_stale_previous_run_stop_evidence_is_invalid() -> None:
    summary = _window_a_like_summary()
    evidence = dict(summary["session_clock_evidence"])
    evidence["certification_run_id"] = "cert_old"
    evidence["stage_run_id"] = "g4_window_a_old"
    summary["session_clock_evidence"] = evidence
    v = classify_session_validity(summary, expected_scope=_scope(), environ={})
    assert v["session_validity"] == INVALID_BOUNDED_STOP
    assert v["session_clock_stop_reason"] == "stale_previous_run_stop_evidence"


def test_spoofed_stop_reason_without_clock_evidence_is_invalid() -> None:
    summary = {
        "stop_reason": SESSION_CLOCK_STOP,
        "push_messages": 149656,
        "gate_evaluations": 8583,
        "runtime_sec": 1300.0,
        "session_id": "live_session_071158",
        "certification_run_id": "cert_g4_stop",
        "stage_run_id": "g4_window_a_stop",
        "trading_date": "20260812",
    }
    v = classify_session_validity(summary, expected_scope=_scope(), environ={})
    assert v["session_validity"] != VALID_SESSION
    assert v["session_clock_stop_valid"] is False
    assert v["session_validity"] != INVALID_NO_GATE


def test_gate_evaluations_never_classify_as_invalid_no_gate() -> None:
    v = classify_session_validity(
        {
            "stop_reason": "unexpected_abort",
            "push_messages": 10,
            "gate_evaluations": 8583,
            "runtime_sec": 120.0,
        }
    )
    assert v["gate_evaluations"] > 0
    assert v["session_validity"] != INVALID_NO_GATE
    assert v["session_validity"] == INVALID_ABNORMAL_STOP


def test_collector_accepts_valid_bounded_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    scope = _scope()
    monkeypatch.setenv("TRADEBOT_CERTIFICATION_RUN_ID", scope["certification_run_id"])
    monkeypatch.setenv("TRADEBOT_CERT_STAGE_RUN_ID", scope["stage_run_id"])
    monkeypatch.setenv("TRADEBOT_RUNTIME_RUN_ID", scope["runtime_run_id"])
    monkeypatch.setenv("TRADEBOT_TRADING_DATE", scope["trading_date"])
    root = NATIVE / "results" / "paper_sessions" / "g4_iso_bounded_stop"
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    try:
        snap = write_live_forward_session_fixture(root, session_id=scope["session_id"])
        ident = json.loads((root / "session_identity.json").read_text(encoding="utf-8"))
        ident.update(scope)
        (root / "session_identity.json").write_text(json.dumps(ident, indent=2) + "\n", encoding="utf-8")
        (root / "small_paper_summary.json").write_text(
            json.dumps(_window_a_like_summary(), indent=2) + "\n", encoding="utf-8"
        )
        q = qualify_snapshot_path(snap, paper_exit_code=0, expected_scope=scope)
        assert q["seal_qualified"] is True
        assert q["forward_qualified"] is True
        assert "INVALID_SESSION" not in " ".join(q.get("failures") or [])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_collector_rejects_invalid_bounded_stop_even_if_sealed(tmp_path: Path) -> None:
    scope = _scope()
    root = tmp_path / "live_session_bad_stop"
    snap = write_live_forward_session_fixture(root, session_id=scope["session_id"])
    summary = _window_a_like_summary()
    evidence = dict(summary["session_clock_evidence"])
    early = "2026-08-12T09:00:00.000+09:00"
    evidence["replay_eof"] = False
    evidence["replay_watermark"] = early
    evidence["paper_last_processed_event_time"] = early
    evidence["consumer_ack_watermark"] = early
    evidence["ingress_publish_watermark"] = early
    evidence["replay_read_watermark"] = early
    summary["session_clock_evidence"] = evidence
    summary["paper_last_processed_event_time"] = early
    (root / "small_paper_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    q = qualify_snapshot_path(snap, paper_exit_code=0, expected_scope=scope)
    assert q["seal_qualified"] is False
    assert any(str(x).startswith("INVALID_SESSION:") for x in (q.get("failures") or []))


def test_session_close_canonical_is_not_strategy_exit_parity() -> None:
    close = {
        "event": "EXIT_EXECUTED",
        "lane": "primary",
        "symbol": "3103",
        "fill_price": 1000.0,
        "exit_price": 1007.0,
        "reason": "SESSION_CLOSE",
        "exit_time": "09:20:00",
    }
    guard = {
        "event": "EXIT_EXECUTED",
        "lane": "primary",
        "symbol": "7203",
        "fill_price": 1500.0,
        "exit_price": 1485.0,
        "reason": "GUARD",
        "exit_time": "10:00:00",
        "slot_released": True,
    }
    close_trades = collect_v1r_primary_canonical_trades([close])
    strat_trades = collect_v1r_primary_canonical_trades([guard])
    assert close_trades[0]["exit_reason"] == "SESSION_CLOSE"
    assert strat_trades[0]["exit_reason"] == "GUARD"
    mixed = collect_v1r_primary_canonical_trades([close, guard])
    assert len(mixed) == 2
    assert {t["exit_reason"] for t in mixed} == {"SESSION_CLOSE", "GUARD"}


def test_heartbeat_session_close_uses_session_event_epoch() -> None:
    src = (NATIVE / "src/small_paper/pilot_runner.py").read_text(encoding="utf-8")
    assert "session_event_epoch()" in src
    assert "dual.maybe_session_close(event_t=now_t)" in src
    hb = src.split("def _emit_heartbeat", 1)[1]
    assert "now_t = time.time()" not in hb.split("def ", 1)[0]


def test_offday_wall_clock_must_not_session_close_open_am(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from small_paper.v1r_live_dual_lane import (
        V1RLiveDualLane,
        session_end_for_position,
        session_event_epoch,
    )

    os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"
    arm = tmp_path / "arm.json"
    monkeypatch.setenv(ENV_ARM_FILE, str(arm))
    v0 = datetime(2026, 8, 12, 9, 5, 0, tzinfo=JST)
    bind_session_clock(virtual_start=v0, speed_mult=48.0, arm_now=True, arm_file=arm)
    fill_t = v0.timestamp()
    se = session_end_for_position(date="20260812", session="AM", fill_time=fill_t)
    assert session_event_epoch() < se
    assert time.time() > se
    dual = V1RLiveDualLane(trace_dir=tmp_path)
    dual.try_admit_fill(
        symbol="3103",
        fill_price=1410.0,
        fill_time=fill_t,
        payload={
            "Buy1": {"Price": 1400.0, "Qty": 200.0},
            "Sell1": {"Price": 1410.0, "Qty": 200.0},
            "CurrentPrice": 1405.0,
            "board_age_sec": 0.0,
            "fresh_sec": 0.0,
            "SpecialQuote": False,
        },
        session="AM",
        date="20260812",
        source="v1r_native",
    )
    assert dual.open_n("primary") == 1
    wall_exits = dual.maybe_session_close(event_t=time.time())
    assert wall_exits, "control: off-day wall must look like session end"
    dual.try_admit_fill(
        symbol="8050",
        fill_price=11270.0,
        fill_time=fill_t,
        payload={
            "Buy1": {"Price": 11200.0, "Qty": 200.0},
            "Sell1": {"Price": 11270.0, "Qty": 200.0},
            "CurrentPrice": 11235.0,
            "board_age_sec": 0.0,
            "fresh_sec": 0.0,
            "SpecialQuote": False,
        },
        session="AM",
        date="20260812",
        source="v1r_native",
    )
    assert dual.open_n("primary") == 1
    sess_exits = dual.maybe_session_close(event_t=session_event_epoch())
    assert sess_exits == []
    assert dual.open_n("primary") == 1


