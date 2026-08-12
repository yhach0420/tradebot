"""V7: registration-safe Kabu probe, pre-warmup residency, exit-code propagation, stale READY."""
from __future__ import annotations

import csv
import inspect
import os
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from runner.am_pm_daily_runner import DailyRunnerOptions, DailyRunnerState, pilot_command_argv
from small_paper.kabu_registration_authority import (
    EXPECTED_PRE_WARMUP,
    LEGACY_BOARD_PROBE_SYMBOL,
    LIVE_RUNTIME_FLOW_PASS,
    OWNER_MARKET_INGRESS,
    POST_INGRESS_COMMIT_UNREGISTER_ALL,
    PREMATURE_PRE_WARMUP_EXIT,
    PREMATURE_PRE_WARMUP_EXIT_CODE,
    PRE_WARMUP_CONNECTIVITY_FAIL,
    PRE_WARMUP_CONNECTIVITY_PASS,
    classify_pre_warmup_process_exit,
    evaluate_live_runtime_flow,
    evaluate_native_runtime_ready,
    evaluate_pre_warmup_connectivity,
    forbid_post_ingress_unregister_all,
    is_pre_warmup,
    post_ingress_unregister_executed_count,
    select_registration_safe_probe_symbol,
    write_actual_regist_snapshot,
    write_registration_owner,
)
from small_paper.paper_trade_checked_runner import (
    PaperTradeCheckedRunner,
    run_paper_bat_preserving_exitcode,
)
from small_paper.safety import check_kabu_station_connection

JST = ZoneInfo("Asia/Tokyo")
PRE_OPEN = datetime(2026, 8, 13, 8, 17, tzinfo=JST)
AFTER_WARMUP = datetime(2026, 8, 13, 8, 51, tzinfo=JST)


def _am_syms(n: int = 50, *, start: int = 1000) -> list[str]:
    return [f"{start + i}" for i in range(n)]


def _write_am_csv(root: Path, day: str, symbols: list[str]) -> Path:
    path = root / "results" / "reports" / f"universe_core10_dynamic40_price_risk_am_{day}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for i, bare in enumerate(symbols):
        slot = "core" if i < 10 else "dynamic"
        rows.append(
            {
                "symbol": f"{bare}.T",
                "symbol_key": f"{bare}@1",
                "exchange": "1",
                "passed": "True",
                "source_bucket": "core10_discord" if slot == "core" else "vol_liq_dynamic40",
                "selected_reason": slot,
                "universe_slot": slot,
                "rank": str(i + 1),
                "am_pm_session": "am",
            }
        )
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return path


def _owned_exact50(tmp_path: Path, day: str, symbols: list[str]) -> None:
    _write_am_csv(tmp_path, day, symbols)
    write_registration_owner(
        tmp_path,
        trading_date=day,
        pid=os.getpid(),
        ingress_session_id="ing_v7_test",
        committed=True,
    )
    write_actual_regist_snapshot(
        tmp_path,
        trading_date=day,
        symbols=symbols,
        source="kabu_put_response",
    )


def test_case_1_registered50_probe_not_9984(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARKET_INGRESS_V2", "1")
    monkeypatch.setenv("KABU_API_PASSWORD", "x")
    day = datetime.now(JST).strftime("%Y%m%d")
    am = _am_syms()
    assert "9984" not in am
    _owned_exact50(tmp_path, day, am)
    mutations: list[str] = []

    def _forbid_mut(*_a, **_k):
        mutations.append("register_or_unregister")
        raise AssertionError("registration mutation forbidden during safety probe")

    monkeypatch.setattr("api.kabu_register.unregister_all_until_zero", _forbid_mut)
    monkeypatch.setattr("api.kabu_register.register_symbols", _forbid_mut, raising=False)

    probed: list[str] = []

    def _board(repo_root, *, symbol_key="9984@1"):
        probed.append(symbol_key)
        return {"ok": True, "symbol_key": symbol_key, "current_price": 1, "current_price_time": None}

    monkeypatch.setattr("small_paper.pilot_runner.verify_kabu_connection", _board)
    monkeypatch.setattr(
        "api.kabu_register.resolve_native_root_for_register_state",
        lambda root: tmp_path,
    )

    sel = select_registration_safe_probe_symbol(tmp_path, day, actual_symbols=am)
    assert sel["ok"] is True
    assert sel["kabu_probe_symbol_registered"] is True
    assert sel["kabu_probe_symbol"] != LEGACY_BOARD_PROBE_SYMBOL
    assert sel["kabu_probe_symbol"].split("@", 1)[0] in am
    assert int(sel["registration_mutation"] or 0) == 0

    chk = check_kabu_station_connection(tmp_path)
    assert chk.passed is True
    assert chk.details.get("kabu_probe_symbol_registered") is True
    assert chk.details.get("kabu_probe_symbol") != LEGACY_BOARD_PROBE_SYMBOL
    assert chk.details.get("kabu_probe_symbol") in probed
    assert probed and probed[0] != LEGACY_BOARD_PROBE_SYMBOL
    assert mutations == []
    audit = tmp_path / "data" / "market_capture" / day / "registration_authority_audit.jsonl"
    text = audit.read_text(encoding="utf-8")
    assert "kabu_probe_symbol" in text
    assert "kabu_probe_symbol_registered" in text


def test_case_2_probe_outside_actual_set_forbidden(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARKET_INGRESS_V2", "1")
    day = "20260813"
    am = _am_syms()
    _owned_exact50(tmp_path, day, am)
    out = select_registration_safe_probe_symbol(
        tmp_path, day, actual_symbols=am, proposed_symbol="9984@1"
    )
    assert out["ok"] is False
    assert out["reason"] == "probe_symbol_not_in_actual_registered_set"
    assert out["kabu_probe_symbol_registered"] is False
    assert int(out["registration_mutation"] or 0) == 0


def test_case_3_preflight_pass_reaches_wait_until_session() -> None:
    src = inspect.getsource(__import__("runner.am_pm_daily_runner", fromlist=["_run_daily_runner_body"])._run_daily_runner_body)
    assert "if not preflight(state):" in src
    assert "return 2" in src
    assert "run_pilot_session(state, session=\"am\")" in src or "run_pilot_session(state, session='am')" in src
    state = DailyRunnerState(
        options=DailyRunnerOptions(day_stamp="20260813", poll_interval_sec=5.0),
        repo_root=Path("."),
        native_root=Path("."),
        reports_dir=Path("."),
        push_root=Path("."),
        trade_date=date(2026, 8, 13),
    )
    argv = pilot_command_argv(state, session="am", universe_rel="results/reports/u.csv")
    assert "--wait-until-session" in argv
    assert is_pre_warmup(now=PRE_OPEN) is True
    still_resident = classify_pre_warmup_process_exit(2, now=PRE_OPEN)
    assert still_resident["fail"] is True
    assert still_resident["reason"] == "PRE_WARMUP_STARTUP_FAIL"


def test_case_4_daily_exit_2_propagates_to_checked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bat = tmp_path / "run_paper_trade.bat"
    bat.write_text("@echo off\r\nexit /b 2\r\n", encoding="utf-8")
    monkeypatch.setattr(
        "small_paper.kabu_registration_authority.is_pre_warmup",
        lambda **_k: True,
    )

    def _run(cmd, env, cwd):
        return 2, '{"verdict": "preflight_blocked", "exit_code": 2}\n', ""

    r = PaperTradeCheckedRunner(
        repo_root=tmp_path,
        native_root=tmp_path,
        paper_bat=bat,
        run_command=_run,
        skip_w4s=True,
        skip_capture_wait=True,
        capture_synthetic=True,
    )
    code = r.step_start_paper()
    assert code == 2
    assert r.paper_exit_code == 2
    paper_ok = code == 0
    assert paper_ok is False
    step = r.steps[-1]
    assert step.name == "paper_trade"
    assert step.result == "FAIL"
    ev = evaluate_pre_warmup_connectivity(
        kabu_token_ok=True,
        same_day_am50=True,
        actual_kabu_exact50=True,
        ingress_resident=True,
        receiver_resident=True,
        registration_drift=False,
        post_registration_unregister=0,
        wait_until_session=True,
        paper_exit_code=2,
        paper_ok=False,
    )
    assert ev["ok"] is False
    assert ev["verdict"] == PRE_WARMUP_CONNECTIVITY_FAIL
    assert "paper_exit_nonzero" in ev["blockers"]
    assert ev["verdict"] != PRE_WARMUP_CONNECTIVITY_PASS


def test_case_5_daily_exit_0_before_warmup_is_premature() -> None:
    out = classify_pre_warmup_process_exit(0, now=PRE_OPEN)
    assert out["fail"] is True
    assert out["reason"] == PREMATURE_PRE_WARMUP_EXIT
    assert out["exit_code"] == PREMATURE_PRE_WARMUP_EXIT_CODE
    after = classify_pre_warmup_process_exit(0, now=AFTER_WARMUP)
    assert after["fail"] is False
    assert after["exit_code"] == 0


def test_case_5_checked_runner_maps_exit0_pre_warmup_to_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bat = tmp_path / "run_paper_trade.bat"
    bat.write_text("@echo off\r\nexit /b 0\r\n", encoding="utf-8")
    monkeypatch.setattr(
        "small_paper.kabu_registration_authority.is_pre_warmup",
        lambda **_k: True,
    )

    def _run(cmd, env, cwd):
        return 0, "finished with exit code 0\n", ""

    r = PaperTradeCheckedRunner(
        repo_root=tmp_path,
        native_root=tmp_path,
        paper_bat=bat,
        run_command=_run,
        skip_w4s=True,
        skip_capture_wait=True,
        capture_synthetic=True,
    )
    code = r.step_start_paper()
    assert code == PREMATURE_PRE_WARMUP_EXIT_CODE
    assert r.paper_exit_code == PREMATURE_PRE_WARMUP_EXIT_CODE
    assert r.steps[-1].result == "FAIL"
    assert r.steps[-1].blocked_reason == PREMATURE_PRE_WARMUP_EXIT


def test_case_6_stale_native_boot_is_not_runtime_ready() -> None:
    dead = evaluate_native_runtime_ready(
        native_boot_ready=True,
        primary_resident=False,
        heartbeat_fresh=False,
        heartbeat_age_sec=3600.0,
    )
    assert dead["ready"] is False
    assert dead["ok"] is False
    assert dead["stale_boot_rejected"] is True
    assert "primary_not_resident" in dead["blockers"]
    live = evaluate_native_runtime_ready(
        native_boot_ready=True,
        primary_resident=True,
        heartbeat_fresh=True,
        heartbeat_age_sec=1.0,
    )
    assert live["ready"] is True
    flow = evaluate_live_runtime_flow(
        consumer_connected=True,
        consumer_ready=True,
        transport="TCP",
        raw_forward=True,
        publisher_forward=True,
        ack_forward_or_catchup=True,
        heartbeat_continuous=True,
        native_ready=bool(dead["ready"]),
        primary_resident=False,
    )
    assert flow["ok"] is False
    assert "native_ready" in flow["blockers"]


def test_case_7_post_ingress_unregister_still_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARKET_INGRESS_V2", "1")
    day = "20260813"
    am = _am_syms()
    _owned_exact50(tmp_path, day, am)
    gate = forbid_post_ingress_unregister_all(tmp_path, day, caller="v7.case7")
    assert gate.get("blocked") is True
    assert gate.get("allow") is False
    assert post_ingress_unregister_executed_count(tmp_path, day) == 0
    assert POST_INGRESS_COMMIT_UNREGISTER_ALL
    ev = evaluate_pre_warmup_connectivity(
        kabu_token_ok=True,
        same_day_am50=True,
        actual_kabu_exact50=True,
        ingress_resident=True,
        receiver_resident=True,
        registration_drift=False,
        post_registration_unregister=0,
        wait_until_session=True,
        consumer_connected=False,
        paper_consumer_last_ack=0,
    )
    assert ev["verdict"] == PRE_WARMUP_CONNECTIVITY_PASS
    assert ev["consumer_status"] == EXPECTED_PRE_WARMUP


@pytest.mark.skipif(os.name != "nt", reason="cmd.exe ERRORLEVEL preservation is Windows-specific")
@pytest.mark.parametrize("child_code", [0, 2, 4])
def test_paper_bat_actual_exitcode_preserved(tmp_path: Path, child_code: int) -> None:
    bat = tmp_path / f"child_exit_{child_code}.bat"
    bat.write_text(f"@echo off\r\nexit /b {child_code}\r\n", encoding="utf-8")
    code, _out, _err = run_paper_bat_preserving_exitcode(bat, os.environ, tmp_path)
    assert code == child_code
