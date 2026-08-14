"""V11: single Kabu token authority, PM direct-start, 401/429 storm prevention."""
from __future__ import annotations

import ast
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from api.rest_client import KabuNativeApiError, KabuNativeRestClient
from runner.am_pm_daily_runner import (
    SKIPPED_AFTER_SESSION_END,
    DailyRunnerOptions,
    _run_daily_runner_body,
    make_state,
    should_skip_am_live_after_session_end,
)
from small_paper.ingress_control_channel import write_desired_universe
from small_paper.kabu_token_authority import (
    AUTH_INVALID,
    OWNER_INGRESS,
    RATE_LIMIT,
    ChildTokenIssueBlocked,
    acquire_token_for_readonly,
    audit_snapshot,
    claim_owner,
    classify_kabu_api_error,
    gate_token_issue,
    owner_issue_context,
    publish_owned_token,
)
from small_paper.market_ingress_service import MarketIngressService
from small_paper.pilot_runner import verify_kabu_connection

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[1]
NOW_1350 = datetime(2026, 8, 13, 13, 50, tzinfo=JST)


@pytest.fixture(autouse=True)
def _isolate_token_auth_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("KABU_AUTH_MODE", "LIVE")
    monkeypatch.setenv("KABU_STATION_AUTHORITY_DIR", str(tmp_path / "station_auth"))
    monkeypatch.setenv("KABU_TOKEN_AUTHORITY_DIR", str(tmp_path / "day_auth"))
    monkeypatch.delenv("KABU_TOKEN_PREFLIGHT", raising=False)
    monkeypatch.delenv("KABU_CERTIFICATION_PROBE", raising=False)
    monkeypatch.delenv("MARKET_INPUT_MODE", raising=False)


class _FakePush:
    def __init__(self) -> None:
        self.calls: list[list[tuple[str, int]]] = []
        self.regist: list[tuple[str, int]] = []
        self._token = "owned-token"
        self.fail_mode = ""
        self.fetch_fail_mode = ""

    def register(self, symbols_spec: list[tuple[str, int]]) -> dict[str, Any]:
        self.calls.append(list(symbols_spec))
        if self.fail_mode == "401":
            raise KabuNativeApiError(
                "register HTTP 401: '{\"Code\":4001009,\"Message\":\"APIキー不一致\"}'"
            )
        if self.fail_mode == "429":
            raise KabuNativeApiError(
                "register HTTP 429: '{\"Code\":4001006,\"Message\":\"API実行回数エラー\"}'"
            )
        have = {s for s, _ in self.regist}
        for s, ex in symbols_spec:
            if s not in have:
                self.regist.append((s, int(ex)))
                have.add(s)
        return {
            "RegistNum": len(symbols_spec),
            "Symbols": [{"Symbol": s, "Exchange": int(ex)} for s, ex in symbols_spec],
            "RegistList": [{"Symbol": s, "Exchange": int(ex)} for s, ex in symbols_spec],
        }

    def unregister_all(self) -> dict[str, Any]:
        self.regist = []
        return {"RegistNum": 0, "Symbols": [], "RegistList": []}

    def fetch_regist_list(self) -> dict[str, Any]:
        if self.fetch_fail_mode == "401":
            return {
                "ok": False,
                "readonly": True,
                "reason": "GET_HTTP_401",
                "http_status": 401,
                "symbols": [],
            }
        if self.fetch_fail_mode == "429":
            return {
                "ok": False,
                "readonly": True,
                "reason": "GET_HTTP_429",
                "http_status": 429,
                "symbols": [],
            }
        return {
            "ok": True,
            "readonly": True,
            "reason": "push_fetch_regist_list",
            "symbols": [s for s, _ in self.regist],
            "http_status": 200,
        }


def _svc(tmp_path: Path) -> MarketIngressService:
    return MarketIngressService(
        native_root=tmp_path,
        trading_date="20260813",
        synthetic=False,
        enable_tcp_bus=False,
    )


def _symbols(n: int = 50) -> list[str]:
    return [f"{7200 + i}" for i in range(n)]


def test_classify_401_429() -> None:
    assert classify_kabu_api_error("HTTP 401", 401) == AUTH_INVALID
    assert "4001009" and classify_kabu_api_error("register HTTP 401: 4001009 APIキー不一致") == AUTH_INVALID
    assert classify_kabu_api_error("HTTP 429", 429) == RATE_LIMIT
    assert classify_kabu_api_error("register HTTP 429: 4001006 API実行回数エラー") == RATE_LIMIT


def test_case_d_child_token_issue_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KABU_TOKEN_AUTHORITY_DIR", str(tmp_path))
    monkeypatch.setenv("KABU_STATION_AUTHORITY_DIR", str(tmp_path))
    claim_owner(
        native_root=tmp_path,
        trading_date="20260813",
        pid=os.getpid(),
        session_id="ing_test",
    )
    with pytest.raises(ChildTokenIssueBlocked):
        gate_token_issue(caller="daily_safety")
    with pytest.raises(ChildTokenIssueBlocked):
        KabuNativeRestClient().issue_token("dummy")
    snap = audit_snapshot(tmp_path, "20260813")
    assert snap["blocked_child_issue_count"] >= 1
    assert snap["unexpected_token_issue_count"] == 0


def test_case_a_b_safety_reuses_token_generation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KABU_TOKEN_AUTHORITY_DIR", str(tmp_path))
    monkeypatch.setenv("KABU_STATION_AUTHORITY_DIR", str(tmp_path))
    claim_owner(
        native_root=tmp_path,
        trading_date="20260813",
        pid=os.getpid(),
        session_id="ing_test",
    )
    body = publish_owned_token(
        "shared-token-v1",
        native_root=tmp_path,
        trading_date="20260813",
        caller="ingress_connect",
    )
    gen = int(body["token_generation"])
    got = acquire_token_for_readonly(
        native_root=tmp_path,
        trading_date="20260813",
        caller="daily_safety",
    )
    assert got["reused"] is True
    assert got["issued"] is False
    assert got["token"] == "shared-token-v1"
    assert int(got["token_generation"]) == gen
    got2 = acquire_token_for_readonly(
        native_root=tmp_path,
        trading_date="20260813",
        caller="pilot_safety",
    )
    assert got2["reused"] is True
    assert audit_snapshot(tmp_path, "20260813")["token_generation"] == gen


def test_verify_kabu_connection_reuses_when_owner_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KABU_TOKEN_AUTHORITY_DIR", str(tmp_path))
    monkeypatch.setenv("KABU_STATION_AUTHORITY_DIR", str(tmp_path))
    claim_owner(
        native_root=tmp_path,
        trading_date="20260813",
        pid=os.getpid(),
        session_id="ing_test",
    )
    publish_owned_token(
        "shared-token-v1",
        native_root=tmp_path,
        trading_date="20260813",
        caller="ingress_connect",
    )
    issued = {"n": 0}

    class _Rest:
        def get_board(self, symbol_key: str, *, token: str) -> dict[str, Any]:
            assert token == "shared-token-v1"
            return {"CurrentPrice": 100.0, "CurrentPriceTime": "2026-08-13T13:50:00+09:00"}

        def issue_token_from_env(self) -> str:
            issued["n"] += 1
            return "NEW"

    monkeypatch.setattr("api.rest_client.KabuNativeRestClient", lambda *_a, **_k: _Rest())
    conn = verify_kabu_connection(
        tmp_path,
        symbol_key="285A@1",
        native_root=tmp_path,
        trading_date="20260813",
    )
    assert conn["ok"] is True
    assert conn["token_reused"] is True
    assert issued["n"] == 0


def test_case_c_am_skip_after_session_end() -> None:
    state = make_state(
        NATIVE.parent,
        NATIVE,
        DailyRunnerOptions(day_stamp="20260813"),
    )
    assert should_skip_am_live_after_session_end(state, now=NOW_1350) is True
    morning = datetime(2026, 8, 13, 10, 0, tzinfo=JST)
    assert should_skip_am_live_after_session_end(state, now=morning) is False


def test_pm_direct_start_does_not_run_am_pilot(tmp_path: Path) -> None:
    repo = tmp_path
    native = tmp_path / "kabu_native"
    native.mkdir()
    state = make_state(
        repo,
        native,
        DailyRunnerOptions(
            day_stamp="20260813",
            skip_safety=True,
            skip_kabu=True,
            dry_run_only=False,
        ),
    )
    sessions: list[str] = []

    def _pilot(_state: Any, *, session: str) -> dict[str, Any]:
        sessions.append(session)
        return {
            "session": session,
            "exit_code": 0,
            "ok": True,
            "pilot_ok": True,
            "pilot_verdict": "success",
        }

    with patch("runner.am_pm_daily_runner.now_jst", return_value=NOW_1350):
        with patch("runner.am_pm_daily_runner.preflight", return_value=True):
            with patch(
                "small_paper.day_fixed_am_registration.reuse_frozen_am_universe",
                return_value={
                    "ok": True,
                    "attempted": True,
                    "am_csv": "kabu_native/results/reports/universe.csv",
                    "am_rows": [],
                },
            ):
                with patch(
                    "runner.am_pm_daily_runner.notify_screening_universe_discord",
                    return_value={"skipped": True},
                ):
                    with patch("runner.am_pm_daily_runner.run_pilot_session", side_effect=_pilot):
                        with patch(
                            "runner.am_pm_daily_runner.wait_until_hhmm",
                            return_value={"skipped": True},
                        ):
                            with patch(
                                "runner.am_pm_daily_runner.build_pm_universe",
                                return_value={"ok": True, "pm_csv": "kabu_native/results/reports/pm.csv"},
                            ):
                                with patch("runner.am_pm_daily_runner.write_outputs"):
                                    with patch(
                                        "runner.am_pm_daily_runner.kabu_clear_stale_registrations",
                                        return_value={"skipped": True},
                                    ):
                                        rc = _run_daily_runner_body(state)
    assert rc == 0
    assert sessions == ["pm"]
    assert state.am_live.get("am_runtime_skipped_after_session_end") is True
    assert state.am_live.get("reason") == SKIPPED_AFTER_SESSION_END
    assert state.am_live.get("am_kabu_full_safety") == "NOT_RUN"
    assert int(state.am_live.get("am_token_mutation") or 0) == 0
    assert state.am_live.get("counted_as_success_session") is False


def test_case_e_401_does_not_tight_put_loop(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    push = _FakePush()
    svc._push_client = push
    svc._token_refresh_fn = lambda: "refreshed"
    write_desired_universe(tmp_path, symbols=_symbols(50), generation=1001, trading_date="20260813")
    push.fail_mode = "401"
    svc._poll_desired_universe()
    first = len(push.calls)
    assert first == 1
    for _ in range(40):
        svc._poll_desired_universe()
        svc._maybe_register_desired_live(reason="desired_poll")
    assert len(push.calls) == 1
    assert svc._auth_failure_count >= 1
    assert svc._circuit_open_count >= 1
    svc.writer.close()
    svc.bus.stop()


def test_case_f_429_bounded_backoff(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    push = _FakePush()
    svc._push_client = push
    write_desired_universe(tmp_path, symbols=_symbols(50), generation=2002, trading_date="20260813")
    push.fail_mode = "429"
    svc._poll_desired_universe()
    assert len(push.calls) == 1
    for _ in range(50):
        svc._poll_desired_universe()
        svc._maybe_register_desired_live(reason="desired_poll")
    assert len(push.calls) == 1
    assert svc._rate_limit_count >= 1
    assert svc._backoff_count >= 1
    assert svc._circuit_open_count >= 1
    svc.writer.close()
    svc.bus.stop()


def test_case_g_exact50_no_unnecessary_put(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    push = _FakePush()
    svc._push_client = push
    write_desired_universe(tmp_path, symbols=_symbols(50), generation=3003, trading_date="20260813")
    svc._poll_desired_universe()
    assert len(push.calls) == 1
    svc._poll_desired_universe()
    svc._poll_desired_universe()
    out = svc._maybe_register_desired_live(reason="repeat")
    assert out.get("skipped") is True
    assert len(push.calls) == 1
    svc.writer.close()
    svc.bus.stop()


def test_auth_is_not_treated_as_registration_drift(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    push = _FakePush()
    svc._push_client = push
    write_desired_universe(tmp_path, symbols=_symbols(50), generation=4004, trading_date="20260813")
    svc._poll_desired_universe()
    assert svc._had_verified_exact50 is True
    push.fetch_fail_mode = "401"
    svc._token_refresh_fn = lambda: "refreshed"
    before = len(push.calls)
    svc._maybe_register_desired_live(reason="desired_poll")
    assert len(push.calls) == before
    svc.writer.close()
    svc.bus.stop()


def test_v10_native_ingest_marker_unchanged() -> None:
    src = (NATIVE / "src" / "small_paper" / "pilot_runner.py").read_text(encoding="utf-8")
    assert "# V1R native ingest+fill EVERY PUSH before PBv2 should_evaluate." in src


def test_live_issue_token_callsites_go_through_authority() -> None:
    """Live startup files must not call issue_token_from_env (Ingress excepted)."""
    live_files = [
        NATIVE / "src" / "small_paper" / "pilot_runner.py",
        NATIVE / "src" / "small_paper" / "safety.py",
        NATIVE / "src" / "runner" / "am_pm_daily_runner.py",
        NATIVE / "src" / "small_paper" / "kabu_readonly_readiness.py",
        NATIVE / "src" / "api" / "kabu_register.py",
        NATIVE / "src" / "small_paper" / "market_capture_sidecar.py",
        NATIVE / "src" / "small_paper" / "live_order_api_wiring.py",
    ]
    for path in live_files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = ""
            if isinstance(func, ast.Attribute):
                name = func.attr
            elif isinstance(func, ast.Name):
                name = func.id
            assert name not in {"issue_token_from_env", "post_token_http"}, (
                f"unexpected {name} in {path}"
            )


def test_owner_context_allows_issue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KABU_TOKEN_AUTHORITY_DIR", str(tmp_path))
    monkeypatch.setenv("KABU_STATION_AUTHORITY_DIR", str(tmp_path))
    called = {"n": 0}

    def _ok() -> None:
        called["n"] += 1

    monkeypatch.setattr("api.rest_client._gate_live_token_issue", _ok)
    with owner_issue_context(
        native_root=tmp_path,
        trading_date="20260813",
        pid=os.getpid(),
        session_id="ing_test",
        caller="ingress_connect",
    ):
        gate_token_issue(caller="ingress_connect")
    assert called["n"] == 0  # owner context skips block; _ok not used
    # After context, owner file is active so child is blocked.
    with pytest.raises(ChildTokenIssueBlocked):
        gate_token_issue(caller="child")
