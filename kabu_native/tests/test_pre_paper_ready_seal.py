"""Cold-start seal: any failed gate keeps paper_call_count at 0."""
from __future__ import annotations

from pathlib import Path

from small_paper.paper_trade_checked_runner import PaperTradeCheckedRunner
from small_paper.pre_paper_ready_seal import (
    CLASS_CERT_ONLY,
    CLASS_PAPER_START,
    FAIL_SEAL_INVALID,
    FAIL_UNIVERSE_UNAVAILABLE,
    authorize_paper_launch,
    production_universe_unavailable,
    seal_conditions_pass,
    write_seal,
)

OWNER = "FixedSupportX1SessionExecutor"


def _pass_body() -> dict:
    return {
        "trading_date": "20260929",
        "startup_run_id": "run-1",
        "activation_id": "FIXED_ENTRY_SUPPORT_PAPER_PRIMARY_ACTIVATION_V6",
        "activation_sha": "abc",
        "candidate_name": "EVENT_TIME_IMPULSE_COMPLETE_STRATEGY_FIXED_ENTRY_SUPPORT_CANDIDATE_V1",
        "entry_sha": "c93c69f0edf84f5ec03b145ee17e25ef5bc2c4ecaa08cfb5e1c6fca7ec052ba9",
        "exit_sha": "8f8ddb3d47190b96df84eb83d9e5f694bfd034ebc650a8a86a85822b5bcb8175",
        "complete_strategy_sha": "3002fdada09206568dd6de5e43f6e2c3317a55e40df2d66cdefef317a7c0fd0a",
        "universe_file": "universe.csv",
        "universe_file_sha": "file",
        "universe_membership_sha": "mem",
        "universe_n": 50,
        "synthetic_universe": False,
        "synthetic_fallback_used": False,
        "ingress_pid": 99,
        "ingress_owner": "MARKET_INGRESS_SERVICE",
        "token_generation": 7,
        "token_issued": True,
        "token_authority_valid": True,
        "desired_n": 50,
        "registered_n": 50,
        "symbol_set_match": True,
        "exact50": True,
        "current_reconciliation": "CURRENT_CLEAN",
        "recovery_ready": True,
        "recovery_readable": True,
        "broker_position_n": 0,
        "active_order_n": 0,
        "broker_only_n": 0,
        "local_only_n": 0,
        "quantity_mismatch_n": 0,
        "session_owner_class": OWNER,
        "admission_owner_class": OWNER,
        "portfolio_owner_class": OWNER,
        "execution_family": "X1_IMMEDIATE_ASK",
        "ledger_path_bound": True,
        "notifier_bound": True,
        "x1_counters_bound": True,
        "same_persistent_executor": True,
        "boot_v1r_native_entry_n": 0,
        "passive_fill_n": 0,
        "v1r_pending_n": 0,
        "real_submit": 0,
        "real_cancel": 0,
        "live_order_calls": 0,
        "classification": CLASS_PAPER_START,
    }


def _current() -> dict:
    body = _pass_body()
    return {
        "trading_date": body["trading_date"],
        "startup_run_id": body["startup_run_id"],
        "activation_id": body["activation_id"],
        "activation_sha": body["activation_sha"],
        "universe_membership_sha": body["universe_membership_sha"],
        "ingress_pid": body["ingress_pid"],
        "token_generation": body["token_generation"],
        "real_submit": 0,
        "real_cancel": 0,
        "live_order_calls": 0,
    }


def _alive(pid: int) -> bool:
    return int(pid) == 99


def test_passing_seal_allows_exactly_one_noop_launch(tmp_path: Path) -> None:
    body = _pass_body()
    assert seal_conditions_pass(body)
    written = write_seal(tmp_path, body)
    assert written["ready"] is True
    ok, reason = authorize_paper_launch(written, _current(), pid_alive=_alive)
    assert ok and reason == ""
    bat = tmp_path / "run_paper_trade.bat"
    bat.write_text("@echo off\r\nexit /b 0\r\n", encoding="utf-8")
    calls: list[int] = []

    def run(_cmd, _env, _cwd):
        calls.append(1)
        return 0, "", ""

    runner = PaperTradeCheckedRunner(
        repo_root=tmp_path,
        native_root=tmp_path,
        paper_bat=bat,
        run_command=run,
        skip_w4s=True,
    )
    runner.trading_date = "20260929"
    runner.runtime_run_id = "run-1"
    runner._authorize_same_startup_paper = lambda: (True, "")  # type: ignore[method-assign]
    assert runner.step_start_paper() == 0
    assert runner.paper_call_count == 1
    assert calls == [1]
    assert runner.step_start_paper() == 2
    assert runner.paper_call_count == 1
    assert calls == [1]


def test_failure_cases_do_not_authorize_paper(tmp_path: Path) -> None:
    base = _pass_body()
    current = _current()
    cases = {
        "no_token": {**base, "token_issued": False, "token_authority_valid": False},
        "freeze_missing_membership": {**base, "universe_membership_sha": ""},
        "synthetic": {**base, "synthetic_universe": True},
        "registered_49": {**base, "registered_n": 49, "symbol_set_match": False, "exact50": False},
        "wrong_symbol_set": {**base, "symbol_set_match": False, "exact50": False},
        "unreadable": {
            **base,
            "current_reconciliation": "CURRENT_UNREADABLE",
            "recovery_ready": False,
            "recovery_readable": False,
            "broker_position_n": None,
            "active_order_n": None,
            "broker_only_n": None,
            "local_only_n": None,
            "quantity_mismatch_n": None,
        },
        "recovery_not_ready": {**base, "recovery_ready": False, "current_reconciliation": "CURRENT_UNRESOLVED"},
        "wrong_activation": {**base, "activation_id": "OTHER"},
        "v1r_boot": {**base, "boot_v1r_native_entry_n": 1},
        "ledger_unbound": {**base, "ledger_path_bound": False},
        "notifier_unbound": {**base, "notifier_bound": False},
        "stale_classification": {**base, "classification": CLASS_CERT_ONLY},
    }
    for name, body in cases.items():
        written = write_seal(tmp_path, body)
        ok, reason = authorize_paper_launch(written, current, pid_alive=_alive)
        assert ok is False, name
        assert reason == FAIL_SEAL_INVALID, name
        if name == "wrong_activation":
            assert written["ready"] is True, name
        else:
            assert written["ready"] is False, name


def test_cert_only_seal_is_invalid_for_paper_start(tmp_path: Path) -> None:
    body = {**_pass_body(), "classification": CLASS_CERT_ONLY}
    written = write_seal(tmp_path, body)
    assert written["classification"] == CLASS_CERT_ONLY
    assert written["invalid_for_paper_start"] is True
    assert written["ready"] is False
    ok, reason = authorize_paper_launch(written, _current(), pid_alive=_alive)
    assert ok is False and reason == FAIL_SEAL_INVALID


def test_stale_startup_does_not_call_paper(tmp_path: Path) -> None:
    written = write_seal(tmp_path, _pass_body())
    current = {**_current(), "startup_run_id": "tomorrow"}
    ok, reason = authorize_paper_launch(written, current, pid_alive=_alive)
    assert ok is False and reason == FAIL_SEAL_INVALID
    bat = tmp_path / "run_paper_trade.bat"
    bat.write_text("@echo off\r\nexit /b 0\r\n", encoding="utf-8")
    calls: list[int] = []
    runner = PaperTradeCheckedRunner(
        repo_root=tmp_path,
        native_root=tmp_path,
        paper_bat=bat,
        run_command=lambda *_a: calls.append(1) or (0, "", ""),
        skip_w4s=True,
    )
    runner._authorize_same_startup_paper = lambda: (False, FAIL_SEAL_INVALID)  # type: ignore[method-assign]
    assert runner.step_start_paper() == 2
    assert runner.paper_call_count == 0
    assert calls == []


def test_synthetic_universe_is_unavailable() -> None:
    fixture = [str(7200 + i) for i in range(50)]
    assert production_universe_unavailable(fixture, reason="synthetic_universe") == FAIL_UNIVERSE_UNAVAILABLE
    assert production_universe_unavailable(fixture, reason="") == FAIL_UNIVERSE_UNAVAILABLE
    assert production_universe_unavailable(["7200"], reason="") == FAIL_UNIVERSE_UNAVAILABLE
    assert production_universe_unavailable(["6758"], reason="") == ""
    normal = [f"{code}.T" for code in range(1000, 1049)] + ["7220.T"]
    assert len(normal) == 50
    assert production_universe_unavailable(normal, reason="") == ""


def test_premarket_flag_does_not_enable_synthetic() -> None:
    runner = PaperTradeCheckedRunner(premarket_cert_only=True, skip_w4s=True)
    assert runner.premarket_cert_only is True
    assert runner.capture_synthetic is False
    assert runner.skip_paper is False
    assert runner.paper_call_count == 0
