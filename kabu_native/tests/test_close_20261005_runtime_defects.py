"""Runtime-defect regressions for 20261005 close. Paper only. No orders."""
from __future__ import annotations

import inspect
from pathlib import Path

from small_paper.day_fixed_am_registration import frozen_universe_path
from small_paper.paper_trade_checked_runner import (
    PaperTradeCheckedRunner,
    certification_owns_capture_runtime,
)
from small_paper.pilot_runner import live_summary_stop_reason
from small_paper.pre_freeze_kabu_validation import (
    AUTH_NOT_READY,
    INVALID_SYMBOL,
    VALID_SYMBOL,
    freeze_valid50_after_kabu_validation,
    select_valid50_from_ranked,
    wait_and_freeze_valid50,
)
from small_paper.v1r_activation_binding import (
    collect_runtime_inventory,
    inventory_digest,
    load_activation_manifest,
    load_active_selector,
    verify_runtime_inventory,
)
from small_paper.x1_pm_session_identity import attach_pm_summary_identity
from tests.test_v13_frozen_universe_sot import _am_syms, _freeze


def _runner(tmp_path: Path) -> PaperTradeCheckedRunner:
    runner = PaperTradeCheckedRunner(
        repo_root=tmp_path,
        native_root=tmp_path,
        paper_bat=tmp_path / "run_paper_trade.bat",
        skip_w4s=True,
    )
    runner.trading_date = "20261006"
    return runner


def test_auth_not_ready_does_not_freeze_unvalidated(monkeypatch, tmp_path: Path) -> None:
    symbols = [f"{1000 + i}" for i in range(50)]
    monkeypatch.setattr(
        "small_paper.day_fixed_am_registration.load_am_canonical_50",
        lambda *_a, **_k: {
            "ok": True,
            "symbols": symbols,
            "symbol_count": 50,
            "reason": "",
            "universe_path": "",
            "universe_sha256": "",
        },
    )
    monkeypatch.setattr(
        "small_paper.pre_freeze_kabu_validation.freeze_valid50_after_kabu_validation",
        lambda *_a, **_k: {"ok": False, "reason": AUTH_NOT_READY, "freeze_created": False},
    )
    frozen_called = {"n": 0}

    def _forbid_freeze(*_a, **_k):
        frozen_called["n"] += 1
        raise AssertionError("unvalidated freeze forbidden")

    monkeypatch.setattr(
        "small_paper.day_fixed_am_registration.freeze_same_day_am_universe",
        _forbid_freeze,
    )
    runner = _runner(tmp_path)
    assert runner.step_universe_resolve() is True
    assert runner.capture.get("freeze_deferred") is True
    assert runner.capture["universe"]["pre_freeze_validation"] == "DEFERRED_AUTH_NOT_READY"
    assert frozen_called["n"] == 0
    assert not frozen_universe_path(tmp_path, "20261006").is_file()


def test_registration_skips_bind_while_freeze_deferred(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    runner.capture["freeze_deferred"] = True
    assert runner.step_registration_coordination() is True
    assert runner.capture["registration"]["desired_bound"] is False
    assert runner.capture["registration"]["expected_count"] == 0


def test_post_ingress_freeze_refills_invalid(tmp_path: Path) -> None:
    ranked = [f"{1000 + i}" for i in range(60)]
    invalid = {ranked[0]}

    def probe(sym: str) -> dict:
        if str(sym) in invalid:
            return {"ok": False, "verdict": INVALID_SYMBOL, "kabu_code": "4002001"}
        return {"ok": True, "verdict": VALID_SYMBOL}

    frozen = freeze_valid50_after_kabu_validation(
        tmp_path,
        "20261006",
        ranked=ranked,
        probe_fn=probe,
        skip_if_frozen=False,
    )
    assert frozen["ok"] is True
    assert ranked[0] not in frozen["valid_symbols"]
    assert ranked[50] in frozen["valid_symbols"]
    assert frozen["final_valid_count"] == 50
    assert frozen.get("kabu_board_validated") is True


def test_multiple_invalid_refill_and_insufficient_fail_closed() -> None:
    ranked = [f"{1000 + i}" for i in range(55)]
    invalid = set(ranked[:3])

    def probe(sym: str) -> dict:
        if str(sym) in invalid:
            return {"ok": False, "verdict": INVALID_SYMBOL, "kabu_code": "4002001"}
        return {"ok": True, "verdict": VALID_SYMBOL}

    selected = select_valid50_from_ranked(ranked, probe_fn=probe)
    assert selected["ok"] is True
    assert selected["final_valid_count"] == 50
    for bad in invalid:
        assert bad not in selected["valid_symbols"]

    def too_many_invalid(sym: str) -> dict:
        return {"ok": False, "verdict": INVALID_SYMBOL, "kabu_code": "4002001"}

    closed = select_valid50_from_ranked(ranked, probe_fn=too_many_invalid)
    assert closed["ok"] is False
    assert closed.get("fail_closed") is True
    assert closed["final_valid_count"] < 50


def test_wait_and_freeze_retries_auth_then_validates(monkeypatch, tmp_path: Path) -> None:
    ranked = [f"{1000 + i}" for i in range(50)]
    calls = {"n": 0}

    def freeze(*_a, **_k):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"ok": False, "reason": AUTH_NOT_READY, "freeze_created": False}
        return {
            "ok": True,
            "reason": "frozen",
            "valid_symbols": ranked,
            "kabu_board_validated": True,
        }

    sleeps: list[float] = []
    monkeypatch.setattr(
        "small_paper.pre_freeze_kabu_validation.freeze_valid50_after_kabu_validation",
        freeze,
    )
    got = wait_and_freeze_valid50(
        tmp_path,
        "20261006",
        ranked=ranked,
        timeout_sec=5,
        poll_sec=0.01,
        sleep_fn=sleeps.append,
        monotonic_fn=lambda: 0.0 if calls["n"] < 2 else 1.0,
    )
    assert got["ok"] is True
    assert calls["n"] == 2
    assert sleeps == [0.05]


def test_wait_and_freeze_auth_timeout_fail_closed_does_not_freeze(monkeypatch, tmp_path: Path) -> None:
    clocks = iter([0.0, 0.02, 0.2])

    def freeze(*_a, **_k):
        return {"ok": False, "reason": AUTH_NOT_READY, "freeze_created": False}

    monkeypatch.setattr(
        "small_paper.pre_freeze_kabu_validation.freeze_valid50_after_kabu_validation",
        freeze,
    )
    got = wait_and_freeze_valid50(
        tmp_path,
        "20261006",
        ranked=[f"{1000 + i}" for i in range(50)],
        timeout_sec=0.1,
        poll_sec=0.01,
        sleep_fn=lambda _s: None,
        monotonic_fn=lambda: next(clocks),
    )
    assert got["ok"] is False
    assert got.get("wait_exhausted") is True
    assert got.get("fail_closed") is True
    assert got.get("freeze_created") is False
    assert not frozen_universe_path(tmp_path, "20261006").is_file()


def test_heartbeat_partial_stop_reason_is_running_not_completed() -> None:
    assert live_summary_stop_reason(stop_reason="", summary_kind="heartbeat_partial") == "running"
    assert live_summary_stop_reason(stop_reason="", summary_kind="final") == "completed"
    assert live_summary_stop_reason(stop_reason="afternoon_session_close", summary_kind="heartbeat_partial") == (
        "afternoon_session_close"
    )


def test_daily_runner_does_not_capture_pilot_stdout() -> None:
    from runner.am_pm_daily_runner import run_pilot_session

    src = inspect.getsource(run_pilot_session)
    assert "capture_output=True" not in src
    assert "subprocess.Popen" in src
    assert "pilot_stdout.log" in src


def test_launcher_always_writes_daily_runner_result() -> None:
    from small_paper.v1r_paper_primary_launcher import _run_daily_live

    src = inspect.getsource(_run_daily_live)
    assert "daily_runner_result.json" in src
    assert "finally:" in src
    assert "state\": \"STOPPED\"" in src or 'state": "STOPPED"' in src or "STOPPED" in src


def test_heartbeat_partial_source_path() -> None:
    text = (
        Path(__file__).resolve().parents[1].joinpath("src/small_paper/pilot_runner.py").read_text(encoding="utf-8")
    )
    assert 'summary_kind="heartbeat_partial"' in text
    assert "live_summary_stop_reason" in text


def test_normal_paper_does_not_own_certification_teardown(monkeypatch) -> None:
    monkeypatch.setenv("TRADEBOT_CERTIFICATION_MODE", "1")
    assert certification_owns_capture_runtime() is False
    assert certification_owns_capture_runtime(capture_synthetic=True) is True
    assert certification_owns_capture_runtime(skip_capture_wait=True) is True
    src = inspect.getsource(PaperTradeCheckedRunner.step_capture_finalize_verify)
    assert "certification_owns_capture_runtime" in src
    assert "certification_mode()" not in src


def test_full_pm_single_segment_valid(tmp_path: Path) -> None:
    session = tmp_path / "20261007" / "live_session_123000"
    session.mkdir(parents=True)
    session.joinpath("small_paper_summary.json").write_text(
        '{"session_id":"live_session_123000","am_pm_session":{"kind":"pm"},'
        '"stop_reason":"afternoon_session_close","x1_executor":{"x1_entry_n":2}}',
        encoding="utf-8",
    )
    summary = {
        "session_id": "live_session_123000",
        "am_pm_session": {"kind": "pm"},
        "stop_reason": "afternoon_session_close",
        "x1_executor": {"x1_entry_n": 2, "x1_exit_n": 2, "x1_open_n": 0},
    }
    identity = attach_pm_summary_identity(summary, session)
    assert identity["full_pm_summary"] is True
    assert identity["SUMMARY_SCOPE"] == "FULL_PM"
    assert identity["FULL_PM_VALID"] is True
    assert identity["OPERATIONALLY_INVALID"] is False
    assert summary["x1_executor"]["x1_entry_n"] == 2


def test_pm_csv_mismatch_does_not_fail_closed_membership(tmp_path: Path) -> None:
    from small_paper.fixed_support_x1_session import FixedSupportX1SessionExecutor
    from small_paper.paper_session_executor import bind_outer_registration

    am = _am_syms()
    am[-1] = "4166"
    _freeze(tmp_path, "20261007", am)
    pm = list(am)
    pm[-1] = "9223"
    exe = FixedSupportX1SessionExecutor()
    admit = bind_outer_registration(exe, native_root=tmp_path, trading_date="20261007", universe=pm)
    assert exe.screening_session_diff is True
    assert admit("4166", 0.0) == (True, "PASS")
    assert admit("9223", 0.0)[0] is False
    assert "9223" not in exe.admission_membership
    assert len(exe.admission_membership) == 50


def test_runtime_inventory_matches_working_tree() -> None:
    selector = load_active_selector()
    manifest = load_activation_manifest(selector=selector)
    check = verify_runtime_inventory(manifest)
    inv = collect_runtime_inventory()
    assert inventory_digest(inv) == str(manifest.get("runtime_inventory_digest") or "")
    assert check.get("ok") is True
    assert check.get("mismatches") in (None, [], ())
    assert manifest.get("entry_sha") == "c93c69f0edf84f5ec03b145ee17e25ef5bc2c4ecaa08cfb5e1c6fca7ec052ba9"
    assert manifest.get("exit_sha") == "8f8ddb3d47190b96df84eb83d9e5f694bfd034ebc650a8a86a85822b5bcb8175"
    assert manifest.get("complete_strategy_sha") == (
        "3002fdada09206568dd6de5e43f6e2c3317a55e40df2d66cdefef317a7c0fd0a"
    )
    assert str(manifest.get("submit_cancel_live") or "") == "0/0/0"


def test_submit_cancel_live_contract() -> None:
    from small_paper.paper_primary_activation import assert_selected_paper_primary

    result = assert_selected_paper_primary()
    assert result.ok, result.reason
    assert str(result.identity.get("submit_cancel_live") or "0/0/0") == "0/0/0"
