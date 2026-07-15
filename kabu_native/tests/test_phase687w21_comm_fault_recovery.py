"""Phase687W21 — Kabu communication fault injection & recovery tests."""

from __future__ import annotations

import inspect
import os

import pytest

from small_paper.comm_fault_runtime_path import (
    ENV_FLAG,
    FakePushClient,
    comm_fault_e2e_enabled,
    require_comm_fault_mode,
    run_reconnect_attempt_scenario,
    run_token_scenarios,
)


def test_fault_mode_disabled_refuses(monkeypatch):
    monkeypatch.delenv(ENV_FLAG, raising=False)
    assert comm_fault_e2e_enabled() is False
    with pytest.raises(RuntimeError, match="COMM_FAULT_REFUSED"):
        require_comm_fault_mode()


def test_fault_mode_enabled_via_env(monkeypatch):
    monkeypatch.setenv(ENV_FLAG, "1")
    assert comm_fault_e2e_enabled() is True
    require_comm_fault_mode()


def test_reconnect_fail_then_success(monkeypatch):
    monkeypatch.setenv(ENV_FLAG, "1")
    r = run_reconnect_attempt_scenario(scenario_id="C05", fail_first_n=1)
    assert r.reconnect_success is True
    assert r.reconnect_attempt_count == 2


def test_reconnect_fail_continues_blocked(monkeypatch):
    monkeypatch.setenv(ENV_FLAG, "1")
    r = run_reconnect_attempt_scenario(scenario_id="C06", fail_first_n=99, max_attempts=3)
    assert r.reconnect_success is False
    assert r.final_status == "BLOCKED_COMMUNICATION"


def test_token_scenarios(monkeypatch):
    monkeypatch.setenv(ENV_FLAG, "1")
    rows = run_token_scenarios()
    by = {r.scenario_id: r for r in rows}
    assert by["C10"].final_status == "RECOVERED"
    assert by["C11"].final_status == "BLOCKED_COMMUNICATION"


def test_fake_push_limit_error_message():
    push = FakePushClient(fail_times=1)
    from api.rest_client import KabuNativeApiError

    with pytest.raises(KabuNativeApiError) as ei:
        push.register([("7203", 1)])
    assert "4002006" in str(ei.value)


def test_checked_runner_flag_wiring():
    from small_paper import paper_trade_checked_runner as m

    src = inspect.getsource(m.main)
    assert "--comm-fault-e2e" in src
    src2 = inspect.getsource(m.PaperTradeCheckedRunner.step_start_comm_fault_e2e)
    assert "comm_fault_runtime_path" in src2


def test_capture_live_loop_has_reconnect():
    from small_paper.market_capture_sidecar import MarketCaptureSidecar

    src = inspect.getsource(MarketCaptureSidecar.run_live_loop)
    assert "RECONNECTING" in src
    assert "reconnect_count" in src
    assert "backoff" in src
