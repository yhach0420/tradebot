"""Phase687W16 — Automatic owned Capture child-process cleanup."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from small_paper.capture_child_cleanup import (
    OwnedCaptureProcess,
    cleanup_owned_capture,
    query_process,
    should_stop_on_shutdown,
    verify_ownership,
)
from small_paper.market_capture_registration import coordinate_registration
from small_paper.market_capture_sidecar import (
    capture_day_dir,
    spawn_sidecar_process,
    subprocess_creationflags,
    wait_capture_online,
)
from small_paper.paper_trade_checked_runner import PaperTradeCheckedRunner

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
CFG = NATIVE / "configs" / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"


@pytest.fixture
def owned_sidecar_tracker():
    """Ensure synthetic sidecars spawned in a test leave residual PID count 0."""
    owned: list[OwnedCaptureProcess] = []
    yield owned
    for oc in owned:
        # Mark synthetic on the owned record (cleanup reads owned.synthetic, not kwargs).
        oc.synthetic = True
        cleanup_owned_capture(
            oc,
            reason="test_teardown",
            skip_capture_wait=True,
            graceful_timeout_sec=8.0,
            terminate_timeout_sec=3.0,
        )
        live = query_process(oc.pid)
        assert not live.get("exists"), f"orphan sidecar pid={oc.pid} remains after teardown"


def _ok_runner(cmds: dict[str, int] | None = None, paper_code: int = 0):
    table = {
        "disk_guard_report": 0,
        "prebuild_vol_liq": 0,
        "check_kabu_readonly": 0,
        "check_live_pipeline_preflight": 0,
        "run_production_startup_smoke": 0,
        "check_live_order_recovery": 0,
        "check_live_order_design_consistency": 0,
        "check_production_enablement": 0,
        "run_paper_trade.bat": paper_code,
        "phase687w4s": 0,
    }
    if cmds:
        table.update(cmds)

    def run(cmd, env, cwd):
        s = cmd if isinstance(cmd, str) else " ".join(cmd)
        if "disk_guard_report" in s:
            return 0, '{"disk_state":"OK","disk_usage_pct":40.0}', ""
        if "phase687w4s" in s:
            return 0, '{"verdict":"FORWARD_SOAK_IN_PROGRESS","aggregate":{"session_count":0,"readonly_success_sessions":0,"mapping_loss_total":0,"duplicate_intent_total":0,"reservation_leak_total":0,"submit_total":0,"cancel_total":0}}', ""
        for key, code in table.items():
            if key in s:
                return code, "{}\n", ""
        return 0, "", ""

    return run


def _design_pass(native: Path) -> None:
    design = (
        native
        / "results"
        / "reports"
        / "phase687w3_e2e_readonly_reconciliation"
        / "phase687w3_design_consistency.json"
    )
    design.parent.mkdir(parents=True, exist_ok=True)
    design.write_text('{"pass":true,"mismatch_count":0}', encoding="utf-8")


def _harness(**kwargs):
    kw = {
        "capture_synthetic": True,
        "skip_capture_wait": True,
        "skip_w4s": True,
        "config_path": CFG,
        "repo_root": REPO,
    }
    kw.update(kwargs)
    return kw


def test_should_stop_policy_distinguishes_paper_block_continue(monkeypatch):
    monkeypatch.delenv("TRADEBOT_CERTIFICATION_MODE", raising=False)
    stop, why = should_stop_on_shutdown(
        reason="normal_exit",
        paper_blocked_capture_continues=True,
        synthetic=False,
        skip_capture_wait=False,
    )
    assert stop is False
    assert why == "paper_blocked_capture_continues"

    stop2, why2 = should_stop_on_shutdown(
        reason="normal_exit",
        paper_blocked_capture_continues=True,
        synthetic=True,
        skip_capture_wait=True,
    )
    assert stop2 is True
    assert "synthetic" in why2 or "test" in why2

    stop3, _ = should_stop_on_shutdown(reason="keyboard_interrupt", paper_blocked_capture_continues=True)
    assert stop3 is True

    stop4, why4 = should_stop_on_shutdown(
        reason="normal_exit",
        continuing_until_scheduled_end=True,
        synthetic=False,
        skip_capture_wait=False,
    )
    assert stop4 is False
    assert why4 == "capture_continuing_until_scheduled_end"


def test_windows_subprocess_flags():
    flags = subprocess_creationflags()
    assert flags & 0x00000200  # CREATE_NEW_PROCESS_GROUP
    if sys.platform == "win32":
        # spawn path uses at least CREATE_NEW_PROCESS_GROUP
        assert 0x00000200 == 0x00000200


def test_ctrl_c_stops_sidecar(tmp_path: Path, owned_sidecar_tracker):
    run = _ok_runner()
    _design_pass(tmp_path)
    r = PaperTradeCheckedRunner(native_root=tmp_path, run_command=run, **_harness())

    # Inject KeyboardInterrupt after capture starts.
    orig_cache = r.step_cache_prebuild

    def boom():
        raise KeyboardInterrupt()

    r.step_cache_prebuild = boom  # type: ignore[method-assign]
    code = r.run()
    assert code == 130
    assert r._owned_capture is not None
    owned_sidecar_tracker.append(r._owned_capture)
    live = query_process(r._owned_capture.pid)
    assert not live.get("exists")
    cleanup = r._cleanup_result or {}
    assert cleanup.get("shutdown_reason") == "keyboard_interrupt"
    assert cleanup.get("capture_pid") == r._owned_capture.pid
    assert cleanup.get("remaining_processes") in ([], None) or cleanup.get("already_dead") or cleanup.get(
        "graceful_stop_ok"
    )
    _ = orig_cache


def test_universe_block_no_sidecar(tmp_path: Path):
    """Universe BLOCK before capture start → no owned sidecar to clean."""
    run = _ok_runner()
    r = PaperTradeCheckedRunner(native_root=tmp_path, run_command=run, **_harness())

    def fail_prebuild():
        r._block("universe_prebuild", 1, "universe_generation_failed", "test")
        return False

    r.step_universe_prebuild = fail_prebuild  # type: ignore[method-assign]
    code = r.run()
    assert code != 0
    assert r._owned_capture is None
    assert r.capture.get("started") is False
    cleanup = r._cleanup_result or {}
    assert cleanup.get("skipped") is True
    assert cleanup.get("skip_reason") == "no_owned_pid"


def test_paper_block_continue_policy_live_skips_stop():
    """Live Paper BLOCK → cleanup skips (Capture continues by design)."""
    owned = OwnedCaptureProcess(
        pid=999999,
        cmd=["python", "-m", "small_paper.market_capture_sidecar", "--native-root", "X"],
        output_dir="",
        native_root="X",
        synthetic=False,
        cmdline_fingerprint="python -m small_paper.market_capture_sidecar --native-root X",
    )
    result = cleanup_owned_capture(
        owned,
        reason="normal_exit",
        paper_blocked_capture_continues=True,
        skip_capture_wait=False,
    )
    assert result.skipped is True
    assert result.skip_reason == "paper_blocked_capture_continues"
    assert result.terminate_used is False
    assert result.kill_used is False


def test_paper_block_synthetic_stops(tmp_path: Path, owned_sidecar_tracker):
    run = _ok_runner({"prebuild_vol_liq": 1})
    _design_pass(tmp_path)
    r = PaperTradeCheckedRunner(native_root=tmp_path, run_command=run, **_harness())
    r.run()
    assert r.paper_blocked_capture_continues is True
    assert r._owned_capture is not None
    owned_sidecar_tracker.append(r._owned_capture)
    live = query_process(r._owned_capture.pid)
    assert not live.get("exists"), "synthetic paper-block must still stop sidecar"
    cleanup = r._cleanup_result or {}
    assert cleanup.get("skipped") is not True or cleanup.get("already_dead")


def test_normal_exit_stops_synthetic(tmp_path: Path, owned_sidecar_tracker):
    run = _ok_runner()
    _design_pass(tmp_path)
    r = PaperTradeCheckedRunner(native_root=tmp_path, run_command=run, **_harness())
    r.run()
    assert r._owned_capture is not None
    owned_sidecar_tracker.append(r._owned_capture)
    live = query_process(r._owned_capture.pid)
    assert not live.get("exists")
    cleanup = r._cleanup_result or {}
    assert cleanup.get("shutdown_reason") == "normal_exit"
    assert "graceful_stop_requested" in cleanup
    assert "cleanup_duration_sec" in cleanup


def test_exception_exit_stops_sidecar(tmp_path: Path, owned_sidecar_tracker):
    run = _ok_runner()
    _design_pass(tmp_path)
    r = PaperTradeCheckedRunner(native_root=tmp_path, run_command=run, **_harness())

    def boom():
        raise RuntimeError("injected_failure")

    r.step_cache_prebuild = boom  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="injected_failure"):
        r.run()
    assert r._owned_capture is not None
    owned_sidecar_tracker.append(r._owned_capture)
    live = query_process(r._owned_capture.pid)
    assert not live.get("exists")
    assert (r._cleanup_result or {}).get("shutdown_reason") == "exception"


def test_synthetic_spawn_residual_zero(tmp_path: Path, owned_sidecar_tracker):
    day = "20990303"
    coordinate_registration(
        tmp_path,
        day,
        expected_symbols=[str(7200 + i) for i in range(50)],
        apply_register=False,
        test_mode=True,
    )
    from small_paper.capture_child_cleanup import record_owned_from_spawn

    spawn = spawn_sidecar_process(
        native_root=tmp_path,
        trading_date=day,
        synthetic=True,
        synthetic_events=30,
    )
    owned = record_owned_from_spawn(spawn, native_root=tmp_path)
    owned_sidecar_tracker.append(owned)
    wait = wait_capture_online(tmp_path, day, timeout_sec=20)
    assert wait["ok"] is True
    result = cleanup_owned_capture(owned, reason="test_teardown", skip_capture_wait=True)
    assert not query_process(owned.pid).get("exists")
    assert result.remaining_processes == []


def test_foreign_process_not_killed():
    """Ownership mismatch must refuse terminate/kill (PID reuse safety)."""
    foreign_pid = os.getpid()
    owned = OwnedCaptureProcess(
        pid=foreign_pid,
        cmd=["python", "-m", "small_paper.market_capture_sidecar", "--native-root", "C:\\fake"],
        output_dir="",
        native_root="C:\\fake_native_root_that_will_not_match",
        synthetic=True,
        cmdline_fingerprint="python -m small_paper.market_capture_sidecar --native-root C:\\fake",
        create_time="NOT_A_REAL_CREATE_TIME",
    )
    live = query_process(foreign_pid)
    assert live.get("exists")
    ownership = verify_ownership(owned, live)
    # Current process cmdline won't match capture markers + native root + create_time
    assert ownership.get("owned") is False or ownership.get("marker_match") is False or ownership.get(
        "native_root_match"
    ) is False or ownership.get("create_time_ok") is False

    result = cleanup_owned_capture(owned, reason="force", skip_capture_wait=True, graceful_timeout_sec=0.5)
    assert result.skipped is True
    assert result.skip_reason == "ownership_mismatch"
    assert result.terminate_used is False
    assert result.kill_used is False
    # Our pytest process must still be alive
    assert query_process(foreign_pid).get("exists")


def test_cleanup_idempotent_double(tmp_path: Path, owned_sidecar_tracker):
    run = _ok_runner()
    _design_pass(tmp_path)
    r = PaperTradeCheckedRunner(native_root=tmp_path, run_command=run, **_harness())
    r.run()
    assert r._owned_capture is not None
    owned_sidecar_tracker.append(r._owned_capture)
    first = dict(r._cleanup_result or {})
    second = r.cleanup_owned_capture(reason="duplicate_cleanup")
    assert second.get("duplicate_cleanup") is True
    assert not query_process(r._owned_capture.pid).get("exists")
    # No exception on double stop
    assert first.get("capture_pid") == second.get("capture_pid")


def test_orphan_pid_cleanup_detection():
    """If kill cannot remove process, remaining_processes is recorded."""
    owned = OwnedCaptureProcess(
        pid=424242,
        cmd=["python", "-m", "small_paper.market_capture_sidecar"],
        output_dir="",
        native_root="",
        synthetic=True,
        cmdline_fingerprint="python -m small_paper.market_capture_sidecar",
    )
    fake_live = {
        "exists": True,
        "cmdline": "python -m small_paper.market_capture_sidecar",
        "create_time": "",
        "parent_pid": 1,
        "name": "python.exe",
    }

    with patch("small_paper.capture_child_cleanup.query_process", return_value=fake_live), patch(
        "small_paper.capture_child_cleanup.verify_ownership",
        return_value={"owned": True, "exists": True, "reason": "owned"},
    ), patch("small_paper.capture_child_cleanup.request_graceful_stop", return_value=False), patch(
        "small_paper.capture_child_cleanup._terminate_pid", return_value=False
    ), patch("small_paper.capture_child_cleanup._kill_pid", return_value=False), patch(
        "small_paper.capture_child_cleanup._pid_alive", return_value=True
    ), patch("small_paper.capture_child_cleanup.time.sleep", return_value=None):
        result = cleanup_owned_capture(
            owned,
            reason="force",
            skip_capture_wait=True,
            graceful_timeout_sec=0.01,
            terminate_timeout_sec=0.01,
        )
    assert result.terminate_used is True
    assert result.kill_used is True
    assert result.remaining_processes == [424242]
    assert result.error == "orphan_remains_after_kill"


def test_artifact_fields_present(tmp_path: Path, owned_sidecar_tracker):
    run = _ok_runner()
    _design_pass(tmp_path)
    r = PaperTradeCheckedRunner(native_root=tmp_path, run_command=run, **_harness())
    r.run()
    if r._owned_capture is not None:
        owned_sidecar_tracker.append(r._owned_capture)
    c = r._cleanup_result or {}
    for key in (
        "shutdown_reason",
        "capture_pid",
        "graceful_stop_requested",
        "graceful_stop_ok",
        "terminate_used",
        "kill_used",
        "remaining_processes",
        "cleanup_duration_sec",
    ):
        assert key in c, f"missing artifact field {key}"
