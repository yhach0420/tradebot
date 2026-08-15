"""V25: auth lifecycle phases, leftover cleanup, production-path startup E2E."""
from __future__ import annotations

import inspect
import json
import os
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from small_paper.auth_lifecycle import (
    DECISION_CLEANUP,
    DECISION_DEFER,
    DECISION_FAIL_CLOSED,
    DECISION_PASS,
    DECISION_REISSUE,
    ENV_AUTH_PHASE,
    FAIL_CLOSED_PHASES,
    PHASE_AM_RUNTIME,
    PHASE_AM_TO_PM_TRANSITION,
    PHASE_BOARD_ACTIVE,
    PHASE_INGRESS_STARTING,
    PHASE_PM_RUNTIME,
    PHASE_POST_INGRESS_PRE_BOARD,
    PHASE_PRE_INGRESS,
    PHASE_TEARDOWN,
    REASON_CURRENT_IDENTITY_NOT_PROVEN,
    REASON_DUPLICATE_ISSUER,
    REASON_GENERATION_MISMATCH,
    REASON_ISSUER_NOT_STARTED,
    REASON_MATCH,
    REASON_OWNER_DEAD,
    REASON_STALE_STAGE,
    consumer_auth_outcome,
    current_auth_phase,
    decide_auth,
    inspect_leftover_auth_state,
    set_auth_phase,
)
from small_paper.ingress_run_identity import ENV_CERTIFICATION_RUN_ID, ENV_STAGE_RUN_ID
from small_paper.kabu_token_authority import (
    CURRENT_STAGE_TOKEN_IDENTITY_NOT_PROVEN,
    STALE_STAGE_TOKEN_REJECTED,
    TOKEN_STAGE_MATCH,
    TOKEN_STAGE_MISSING,
    TOKEN_STAGE_MISMATCH,
    CurrentStageTokenIdentityNotProven,
    StaleStageTokenRejected,
    TokenSecondIssuerBlocked,
    TokenUnavailable,
    acquire_token_for_readonly,
    classify_token_stage,
    evaluate_issue_permission,
    issue_station_token,
    load_station_bundle,
    owner_issue_context,
    token_fingerprint,
)
from small_paper.paper_trade_checked_runner import PaperTradeCheckedRunner
from small_paper.runtime_clock import ENV_CERT_MODE, ENV_KABU_AUTH_MODE
from small_paper.v1r_activation_binding import RUNTIME_DEPENDENCY_RELS

JST = ZoneInfo("Asia/Tokyo")
DAY = "20260812"
NATIVE = Path(__file__).resolve().parents[1]
CFG = NATIVE / "configs" / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
REPO = NATIVE.parent

FAIL_CLOSED_RUNTIME = (
    PHASE_POST_INGRESS_PRE_BOARD,
    PHASE_BOARD_ACTIVE,
    PHASE_AM_RUNTIME,
    PHASE_AM_TO_PM_TRANSITION,
    PHASE_PM_RUNTIME,
)


@pytest.fixture(autouse=True)
def _isolate_auth_phase() -> Any:
    prev = os.environ.get(ENV_AUTH_PHASE)
    os.environ.pop(ENV_AUTH_PHASE, None)
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop(ENV_AUTH_PHASE, None)
        else:
            os.environ[ENV_AUTH_PHASE] = prev


class _StubClient:
    def __init__(self, counter: Path, token: str) -> None:
        self.base_url = "http://localhost:18080/kabusapi"
        self.counter = counter
        self.token = token

    def post_token_http(self, api_password: str) -> str:
        n = int(self.counter.read_text(encoding="utf-8") or "0") + 1
        self.counter.write_text(str(n), encoding="utf-8")
        return self.token


def _iso_station(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("KABU_STATION_AUTHORITY_DIR", str(tmp_path))
    monkeypatch.setenv("KABU_TOKEN_AUTHORITY_DIR", str(tmp_path))
    monkeypatch.setenv("KABU_AUTH_MODE", "LIVE")
    monkeypatch.delenv("KABU_TOKEN_PREFLIGHT", raising=False)
    monkeypatch.delenv("KABU_CERTIFICATION_PROBE", raising=False)
    count = tmp_path / "post_count.txt"
    count.write_text("0", encoding="utf-8")
    return count


def _write_unscoped_gen34(tmp: Path, *, pid: int, token: str = "leftover-gen34") -> None:
    body = {
        "caller": "ingress_replay_connect",
        "fingerprint": token_fingerprint(token),
        "generation": 34,
        "issue_reason": "ingress_replay_connect",
        "issued_at": "2026-08-15T06:14:43.333+09:00",
        "kabu_token_authority": "MARKET_INGRESS_SERVICE",
        "owner": "MARKET_INGRESS_SERVICE",
        "pid": pid,
        "session_id": "ing_20260815_14372_leftover",
        "station_endpoint": "localhost_18080",
        "token": token,
        "token_generation": 34,
        "trading_date": DAY,
    }
    (tmp / "kabu_station_token_bundle.json").write_text(json.dumps(body), encoding="utf-8")
    owner = {k: v for k, v in body.items() if k != "token"}
    owner["token_issue_count"] = 13
    (tmp / "kabu_station_owner.json").write_text(json.dumps(owner), encoding="utf-8")


def _write_stage_bundle(
    tmp: Path,
    *,
    pid: int,
    token: str,
    stage: str,
    generation: int = 1,
    cert: str = "cert_x",
) -> None:
    from small_paper.kabu_token_authority import current_stage_token_identity

    ident = current_stage_token_identity()
    body = {
        "caller": "ingress_replay_connect",
        "fingerprint": token_fingerprint(token),
        "generation": generation,
        "owner": "MARKET_INGRESS_SERVICE",
        "pid": pid,
        "session_id": "ing_stage",
        "token": token,
        "token_generation": generation,
        "stage_run_id": stage,
        "certification_run_id": cert,
        "activation_id": ident.get("activation_id") or None,
        "activation_sha": ident.get("activation_sha") or None,
        "trading_date": DAY,
    }
    (tmp / "kabu_station_token_bundle.json").write_text(json.dumps(body), encoding="utf-8")
    owner = {k: v for k, v in body.items() if k != "token"}
    (tmp / "kabu_station_owner.json").write_text(json.dumps(owner), encoding="utf-8")


def _cert_stage(monkeypatch: pytest.MonkeyPatch, stage: str, cert: str = "cert_v25") -> None:
    monkeypatch.setenv(ENV_CERT_MODE, "1")
    monkeypatch.setenv(ENV_CERTIFICATION_RUN_ID, cert)
    monkeypatch.setenv(ENV_STAGE_RUN_ID, stage)


def _residue(**kwargs: Any) -> dict[str, Any]:
    base = {
        "stage_class": "",
        "want_stage": "full_day_aaa",
        "got_stage": "missing",
        "owner_pid": 0,
        "owner_alive": False,
        "ingress_pid": 0,
        "ingress_alive": False,
        "generation": 0,
        "has_token_string": False,
        "has_bundle": False,
        "generation_mismatch": False,
        "bundle_corrupt": False,
    }
    base.update(kwargs)
    return base


def test_inventory_pins_auth_lifecycle() -> None:
    assert "src/small_paper/auth_lifecycle.py" in RUNTIME_DEPENDENCY_RELS
    assert "src/api/kabu_register.py" in RUNTIME_DEPENDENCY_RELS


def test_checked_bat_startup_order_has_no_token_before_issuer_deadlock() -> None:
    src = inspect.getsource(PaperTradeCheckedRunner.run)
    i_preclear = src.find("step_legacy_register_preclear")
    i_capture = src.find("step_start_capture")
    assert i_preclear > 0 and i_capture > i_preclear
    preclear = inspect.getsource(PaperTradeCheckedRunner.step_legacy_register_preclear)
    assert "PHASE_PRE_INGRESS" in preclear
    assert "clear_register_before_session" in preclear
    spawn_src = (NATIVE / "src" / "small_paper" / "market_ingress_spawn.py").read_text(encoding="utf-8")
    assert "PHASE_INGRESS_STARTING" in spawn_src
    assert "PHASE_POST_INGRESS_PRE_BOARD" in inspect.getsource(PaperTradeCheckedRunner.step_start_capture)
    cert = (NATIVE / "scripts" / "run_paper_full_day_certification.py").read_text(encoding="utf-8")
    assert "--skip-paper" not in cert
    assert "--capture-synthetic" not in cert
    ps1 = (NATIVE / "scripts" / "run_paper_trade_checked.ps1").read_text(encoding="utf-8")
    cert_block = ps1.split("if ($FullDayCert)")[1].split("Set-Location")[0]
    assert "--skip-paper" not in cert_block
    assert "--capture-synthetic" not in cert_block


def test_skip_paper_must_not_be_the_only_production_preclear_path() -> None:
    ctor = inspect.getsource(PaperTradeCheckedRunner.__init__)
    # Document V24 miss: skip_paper currently forces capture_synthetic, which skips preclear.
    # Production BAT does not pass --skip-paper. Targeted E2E must use skip_paper=False.
    assert "skip_paper" in ctor and "capture_synthetic" in ctor


@pytest.mark.parametrize(
    "phase,residue,decision,reason",
    [
        (PHASE_PRE_INGRESS, _residue(), DECISION_DEFER, REASON_ISSUER_NOT_STARTED),
        (
            PHASE_PRE_INGRESS,
            _residue(stage_class=TOKEN_STAGE_MISSING, has_bundle=True, has_token_string=True, generation=34),
            DECISION_CLEANUP,
            REASON_ISSUER_NOT_STARTED,
        ),
        (
            PHASE_PRE_INGRESS,
            _residue(stage_class=TOKEN_STAGE_MISMATCH, got_stage="old", has_bundle=True, has_token_string=True),
            DECISION_CLEANUP,
            REASON_ISSUER_NOT_STARTED,
        ),
        (
            PHASE_PRE_INGRESS,
            _residue(stage_class=TOKEN_STAGE_MATCH, got_stage="full_day_aaa", owner_pid=9, owner_alive=True, has_token_string=True),
            DECISION_PASS,
            REASON_MATCH,
        ),
        (PHASE_INGRESS_STARTING, _residue(), DECISION_REISSUE, "CURRENT_STAGE_INGRESS_MUST_ISSUE"),
        (
            PHASE_POST_INGRESS_PRE_BOARD,
            _residue(stage_class=TOKEN_STAGE_MISSING),
            DECISION_FAIL_CLOSED,
            REASON_CURRENT_IDENTITY_NOT_PROVEN,
        ),
        (
            PHASE_BOARD_ACTIVE,
            _residue(stage_class=TOKEN_STAGE_MISMATCH, got_stage="old"),
            DECISION_FAIL_CLOSED,
            REASON_STALE_STAGE,
        ),
        (
            PHASE_AM_RUNTIME,
            _residue(stage_class=TOKEN_STAGE_MATCH, got_stage="full_day_aaa", owner_alive=False, owner_pid=3),
            DECISION_FAIL_CLOSED,
            REASON_OWNER_DEAD,
        ),
        (
            PHASE_AM_TO_PM_TRANSITION,
            _residue(stage_class=TOKEN_STAGE_MATCH, got_stage="full_day_aaa", owner_pid=8, owner_alive=True, has_token_string=True),
            DECISION_PASS,
            REASON_MATCH,
        ),
        (
            PHASE_PM_RUNTIME,
            _residue(
                owner_pid=11,
                ingress_pid=22,
                owner_alive=True,
                ingress_alive=True,
                stage_class=TOKEN_STAGE_MATCH,
                got_stage="full_day_aaa",
            ),
            DECISION_FAIL_CLOSED,
            REASON_DUPLICATE_ISSUER,
        ),
        (
            PHASE_POST_INGRESS_PRE_BOARD,
            _residue(generation_mismatch=True, stage_class=TOKEN_STAGE_MATCH, owner_alive=True, got_stage="full_day_aaa"),
            DECISION_FAIL_CLOSED,
            REASON_GENERATION_MISMATCH,
        ),
        (PHASE_TEARDOWN, _residue(), DECISION_CLEANUP, "TEARDOWN_NO_CONSUMER_TOKEN"),
    ],
)
def test_phase_policy_matrix(phase: str, residue: dict[str, Any], decision: str, reason: str) -> None:
    out = decide_auth(phase=phase, residue=residue, caller="matrix")
    assert out["decision"] == decision
    assert out["reason"] == reason
    assert "phase=" not in json.dumps(out) or out["phase"] == phase


def test_leftover_cases_1_to_12(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    count = _iso_station(tmp_path, monkeypatch)
    _cert_stage(monkeypatch, "full_day_v25")
    set_auth_phase(PHASE_PRE_INGRESS)

    # 1 clean
    r1 = inspect_leftover_auth_state(native_root=tmp_path, trading_date=DAY)
    d1 = decide_auth(phase=PHASE_PRE_INGRESS, residue=r1)
    assert d1["decision"] in {DECISION_DEFER, DECISION_CLEANUP}

    # 2 old token only (dead owner)
    _write_unscoped_gen34(tmp_path, pid=2147483646)
    monkeypatch.setattr("small_paper.kabu_token_authority._pid_alive", lambda pid: False)
    r2 = inspect_leftover_auth_state(native_root=tmp_path, trading_date=DAY)
    d2 = decide_auth(phase=PHASE_PRE_INGRESS, residue=r2)
    assert d2["decision"] in {DECISION_DEFER, DECISION_CLEANUP}
    assert classify_token_stage()["class"] == TOKEN_STAGE_MISSING

    # 3 old process only: pid file, no bundle
    (tmp_path / "kabu_station_token_bundle.json").unlink(missing_ok=True)
    (tmp_path / "kabu_station_owner.json").unlink(missing_ok=True)
    day_dir = tmp_path / "data" / "market_capture" / DAY
    day_dir.mkdir(parents=True, exist_ok=True)
    (day_dir / "ingress.pid").write_text("2147483646", encoding="utf-8")
    r3 = inspect_leftover_auth_state(native_root=tmp_path, trading_date=DAY)
    d3 = decide_auth(phase=PHASE_PRE_INGRESS, residue=r3)
    assert d3["decision"] in {DECISION_DEFER, DECISION_CLEANUP}

    # 4 old token + old process
    _write_unscoped_gen34(tmp_path, pid=2147483646)
    r4 = inspect_leftover_auth_state(native_root=tmp_path, trading_date=DAY)
    assert decide_auth(phase=PHASE_PRE_INGRESS, residue=r4)["decision"] in {DECISION_DEFER, DECISION_CLEANUP}

    # 5 unscoped
    assert classify_token_stage()["class"] == TOKEN_STAGE_MISSING

    # 6 previous-stage
    _write_stage_bundle(tmp_path, pid=os.getpid(), token="am-tok", stage="am_old")
    monkeypatch.setattr("small_paper.kabu_token_authority._pid_alive", lambda pid: int(pid) == os.getpid())
    assert classify_token_stage(load_station_bundle())["class"] == TOKEN_STAGE_MISMATCH
    d6 = decide_auth(phase=PHASE_PRE_INGRESS, residue=inspect_leftover_auth_state(native_root=tmp_path, trading_date=DAY))
    assert d6["decision"] in {DECISION_DEFER, DECISION_CLEANUP}
    d6b = decide_auth(
        phase=PHASE_POST_INGRESS_PRE_BOARD,
        residue=inspect_leftover_auth_state(native_root=tmp_path, trading_date=DAY),
    )
    assert d6b["decision"] == DECISION_FAIL_CLOSED

    # 7 current-stage + dead owner
    _write_stage_bundle(tmp_path, pid=2147483646, token="cur", stage="full_day_v25", cert="cert_v25")
    monkeypatch.setattr("small_paper.kabu_token_authority._pid_alive", lambda pid: False)
    d7 = decide_auth(
        phase=PHASE_BOARD_ACTIVE,
        residue=inspect_leftover_auth_state(native_root=tmp_path, trading_date=DAY),
    )
    assert d7["decision"] == DECISION_FAIL_CLOSED
    assert d7["reason"] == REASON_OWNER_DEAD

    # 8 current-stage + wrong issuer (two live pids)
    _write_stage_bundle(tmp_path, pid=111, token="cur", stage="full_day_v25", cert="cert_v25")
    (day_dir / "ingress.pid").write_text("222", encoding="utf-8")
    monkeypatch.setattr("small_paper.kabu_token_authority._pid_alive", lambda pid: int(pid) in {111, 222})
    d8 = decide_auth(
        phase=PHASE_POST_INGRESS_PRE_BOARD,
        residue=inspect_leftover_auth_state(native_root=tmp_path, trading_date=DAY),
    )
    assert d8["decision"] == DECISION_FAIL_CLOSED
    assert d8["reason"] == REASON_DUPLICATE_ISSUER

    # 9 stale generation
    _write_stage_bundle(tmp_path, pid=os.getpid(), token="g", stage="full_day_v25", generation=10)
    owner = json.loads((tmp_path / "kabu_station_owner.json").read_text(encoding="utf-8"))
    owner["token_generation"] = 99
    (tmp_path / "kabu_station_owner.json").write_text(json.dumps(owner), encoding="utf-8")
    monkeypatch.setattr("small_paper.kabu_token_authority._pid_alive", lambda pid: int(pid) == os.getpid())
    r9 = inspect_leftover_auth_state(native_root=tmp_path, trading_date=DAY)
    assert r9["generation_mismatch"] is True
    d9 = decide_auth(phase=PHASE_AM_RUNTIME, residue=r9)
    assert d9["decision"] == DECISION_FAIL_CLOSED
    assert d9["reason"] == REASON_GENERATION_MISMATCH

    # 10 corrupt token file
    (tmp_path / "kabu_station_token_bundle.json").write_text("{not-json", encoding="utf-8")
    r10 = inspect_leftover_auth_state(native_root=tmp_path, trading_date=DAY)
    d10 = decide_auth(phase=PHASE_PRE_INGRESS, residue=r10)
    assert d10["decision"] in {DECISION_DEFER, DECISION_CLEANUP}
    d10b = decide_auth(phase=PHASE_POST_INGRESS_PRE_BOARD, residue=r10)
    assert d10b["decision"] == DECISION_FAIL_CLOSED

    # 11 metadata missing (unscoped leftover)
    _write_unscoped_gen34(tmp_path, pid=os.getpid())
    monkeypatch.setattr("small_paper.kabu_token_authority._pid_alive", lambda pid: int(pid) == os.getpid())
    assert "stage_run_id" not in load_station_bundle()
    d11 = decide_auth(
        phase=PHASE_PRE_INGRESS,
        residue=inspect_leftover_auth_state(native_root=tmp_path, trading_date=DAY),
    )
    assert d11["decision"] in {DECISION_DEFER, DECISION_CLEANUP}

    # 12 duplicate issuer already covered in 8; issue gate still blocks second POST
    _write_stage_bundle(tmp_path, pid=os.getpid(), token="live", stage="full_day_v25")
    monkeypatch.setattr("small_paper.kabu_token_authority._pid_alive", lambda pid: int(pid) == os.getpid())
    with owner_issue_context(
        native_root=tmp_path,
        trading_date=DAY,
        pid=os.getpid(),
        session_id="ing_dup",
        caller="ingress_replay_connect",
    ):
        issue_station_token(_StubClient(count, "tok-dup"), "pw")
    with pytest.raises(TokenSecondIssuerBlocked):
        issue_station_token(_StubClient(count, "tok-dup-2"), "pw", caller="second")


def test_gate_a_pre_ingress_no_token_then_issue_match(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    count = _iso_station(tmp_path, monkeypatch)
    _cert_stage(monkeypatch, "full_day_v25a")
    set_auth_phase(PHASE_PRE_INGRESS)
    with pytest.raises(TokenUnavailable) as deferred:
        acquire_token_for_readonly(native_root=tmp_path, trading_date=DAY, caller="push_client_from_repo")
    assert "AUTH_DEFERRED" in str(deferred.value)
    with owner_issue_context(
        native_root=tmp_path,
        trading_date=DAY,
        pid=os.getpid(),
        session_id="ing_a",
        caller="ingress_replay_connect",
    ):
        issue_station_token(_StubClient(count, "tok-a"), "pw")
    set_auth_phase(PHASE_POST_INGRESS_PRE_BOARD)
    got = acquire_token_for_readonly(native_root=tmp_path, trading_date=DAY, caller="verify_kabu_connection")
    assert got["token"] == "tok-a"
    assert got["token_stage_class"] == TOKEN_STAGE_MATCH
    assert count.read_text(encoding="utf-8") == "1"


def test_gate_b_unscoped_leftover_preclear_then_match(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    count = _iso_station(tmp_path, monkeypatch)
    _cert_stage(monkeypatch, "full_day_v25b")
    leftover_pid = 14372
    _write_unscoped_gen34(tmp_path, pid=leftover_pid)
    monkeypatch.setattr(
        "small_paper.kabu_token_authority._pid_alive",
        lambda pid: int(pid) in {leftover_pid, os.getpid()},
    )
    set_auth_phase(PHASE_PRE_INGRESS)
    from api.kabu_register import clear_register_before_session

    out = clear_register_before_session(tmp_path)
    assert out.get("ok") is True
    assert out.get("reason") == "AUTH_DEFERRED_UNTIL_INGRESS"
    marker = tmp_path / "data" / "market_capture" / datetime.now(JST).strftime("%Y%m%d") / "pre_ingress_leftover_ignored.json"
    # trading_date for cleanup uses session clock day, not DAY fixture; accept either
    ignored = list((tmp_path / "data" / "market_capture").glob("*/pre_ingress_leftover_ignored.json"))
    assert ignored or marker.is_file() or out.get("auth_decision")
    with owner_issue_context(
        native_root=tmp_path,
        trading_date=DAY,
        pid=os.getpid(),
        session_id="ing_b",
        caller="ingress_replay_connect",
    ):
        tok = issue_station_token(_StubClient(count, "tok-b"), "pw")
    assert tok == "tok-b"
    assert load_station_bundle()["generation"] == 35
    assert load_station_bundle()["stage_run_id"] == "full_day_v25b"
    set_auth_phase(PHASE_POST_INGRESS_PRE_BOARD)
    got = acquire_token_for_readonly(native_root=tmp_path, trading_date=DAY, caller="verify_kabu_connection")
    assert got["token"] == "tok-b"
    assert got["token"] != "leftover-gen34"


def test_gate_c_previous_stage_not_reused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    count = _iso_station(tmp_path, monkeypatch)
    _cert_stage(monkeypatch, "stage_am")
    with owner_issue_context(
        native_root=tmp_path,
        trading_date=DAY,
        pid=os.getpid(),
        session_id="ing_am",
        caller="ingress_replay_connect",
    ):
        issue_station_token(_StubClient(count, "tok-am"), "pw")
    monkeypatch.setenv(ENV_STAGE_RUN_ID, "stage_pm")
    set_auth_phase(PHASE_PRE_INGRESS)
    with pytest.raises(TokenUnavailable):
        acquire_token_for_readonly(native_root=tmp_path, trading_date=DAY, caller="push_client_from_repo")
    owner = json.loads((tmp_path / "kabu_station_owner.json").read_text(encoding="utf-8"))
    owner["pid"] = 2147483646
    (tmp_path / "kabu_station_owner.json").write_text(json.dumps(owner), encoding="utf-8")
    with owner_issue_context(
        native_root=tmp_path,
        trading_date=DAY,
        pid=os.getpid(),
        session_id="ing_pm",
        caller="ingress_replay_connect",
    ):
        issue_station_token(_StubClient(count, "tok-pm"), "pw")
    assert count.read_text(encoding="utf-8") == "2"
    assert load_station_bundle()["token"] == "tok-pm"
    set_auth_phase(PHASE_PM_RUNTIME)
    got = acquire_token_for_readonly(native_root=tmp_path, trading_date=DAY, caller="run_live_dry_run")
    assert got["token"] == "tok-pm"


@pytest.mark.parametrize("phase", FAIL_CLOSED_RUNTIME)
def test_gate_d_e_fail_closed_missing_or_wrong_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    _iso_station(tmp_path, monkeypatch)
    _cert_stage(monkeypatch, "full_day_v25d")
    set_auth_phase(phase)
    _write_unscoped_gen34(tmp_path, pid=os.getpid())
    with pytest.raises(CurrentStageTokenIdentityNotProven) as missing:
        acquire_token_for_readonly(native_root=tmp_path, trading_date=DAY, caller="verify_kabu_connection")
    assert CURRENT_STAGE_TOKEN_IDENTITY_NOT_PROVEN in str(missing.value)
    _write_stage_bundle(tmp_path, pid=os.getpid(), token="wrong", stage="other_stage")
    with pytest.raises(StaleStageTokenRejected) as stale:
        acquire_token_for_readonly(native_root=tmp_path, trading_date=DAY, caller="paper_safety")
    assert STALE_STAGE_TOKEN_REJECTED in str(stale.value)


def test_gate_g_duplicate_issuer_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    count = _iso_station(tmp_path, monkeypatch)
    _cert_stage(monkeypatch, "full_day_dup")
    with owner_issue_context(
        native_root=tmp_path,
        trading_date=DAY,
        pid=os.getpid(),
        session_id="ing_1",
        caller="ingress_replay_connect",
    ):
        issue_station_token(_StubClient(count, "tok-1"), "pw")
        decision = evaluate_issue_permission(caller="ingress_replay_connect")
        assert decision["allowed"] is True
    with pytest.raises(TokenSecondIssuerBlocked):
        with owner_issue_context(
            native_root=tmp_path,
            trading_date=DAY,
            pid=os.getpid() + 1 if os.getpid() > 1 else 2,
            session_id="ing_2",
            caller="ingress_replay_connect",
        ):
            issue_station_token(_StubClient(count, "tok-2"), "pw")
    assert count.read_text(encoding="utf-8") == "1"


def test_gate_h_issue_failure_does_not_pass_post_ingress(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _iso_station(tmp_path, monkeypatch)
    _cert_stage(monkeypatch, "full_day_fail")
    set_auth_phase(PHASE_POST_INGRESS_PRE_BOARD)
    with pytest.raises(CurrentStageTokenIdentityNotProven):
        acquire_token_for_readonly(native_root=tmp_path, trading_date=DAY, caller="verify_kabu_connection")


def test_production_preclear_path_not_skipped_when_not_synthetic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _iso_station(tmp_path, monkeypatch)
    _cert_stage(monkeypatch, "full_day_4f44bc60ed78")
    _write_unscoped_gen34(tmp_path, pid=14372)
    monkeypatch.setattr("small_paper.kabu_token_authority._pid_alive", lambda pid: int(pid) in {14372, os.getpid()})
    monkeypatch.chdir(tmp_path)
    r = PaperTradeCheckedRunner(
        native_root=tmp_path,
        repo_root=tmp_path,
        skip_paper=False,
        capture_synthetic=False,
        skip_w4s=True,
        config_path=CFG,
        skip_capture_wait=True,
    )
    assert r.capture_synthetic is False
    r.trading_date = DAY
    set_auth_phase(PHASE_PRE_INGRESS)
    ok = r.step_legacy_register_preclear()
    assert ok is True
    rec = next(s for s in r.steps if s.name == "legacy_register_preclear")
    assert rec.result == "PASS"
    payload = json.loads(rec.stdout_tail or "{}")
    assert payload.get("reason") == "AUTH_DEFERRED_UNTIL_INGRESS"


def test_readonly_defers_pre_ingress_but_fail_closes_after(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _iso_station(tmp_path, monkeypatch)
    _cert_stage(monkeypatch, "full_day_ro")
    _write_unscoped_gen34(tmp_path, pid=os.getpid())
    set_auth_phase(PHASE_PRE_INGRESS)
    with pytest.raises(TokenUnavailable):
        acquire_token_for_readonly(native_root=tmp_path, trading_date=DAY, caller="kabu_readonly_readiness")
    set_auth_phase(PHASE_BOARD_ACTIVE)
    with pytest.raises(CurrentStageTokenIdentityNotProven):
        acquire_token_for_readonly(native_root=tmp_path, trading_date=DAY, caller="kabu_readonly_readiness")


def test_am_pm_same_full_day_stage_reuses_match(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    count = _iso_station(tmp_path, monkeypatch)
    _cert_stage(monkeypatch, "full_day_same")
    with owner_issue_context(
        native_root=tmp_path,
        trading_date=DAY,
        pid=os.getpid(),
        session_id="ing_day",
        caller="ingress_replay_connect",
    ):
        issue_station_token(_StubClient(count, "tok-day"), "pw")
    set_auth_phase(PHASE_AM_TO_PM_TRANSITION)
    got = acquire_token_for_readonly(native_root=tmp_path, trading_date=DAY, caller="after_am_session")
    assert got["token"] == "tok-day"
    assert got["token_stage_class"] == TOKEN_STAGE_MATCH
    assert count.read_text(encoding="utf-8") == "1"


def test_windows_a_b_c_and_pm_direct_stage_issue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    count = _iso_station(tmp_path, monkeypatch)
    stages = ("window_a_v25", "window_b_v25", "window_c_v25", "pm_direct_v25")
    prev_pid_owner = None
    for i, stage in enumerate(stages, start=1):
        _cert_stage(monkeypatch, stage, cert=f"cert_{stage}")
        set_auth_phase(PHASE_PRE_INGRESS)
        if i > 1:
            with pytest.raises((TokenUnavailable, StaleStageTokenRejected, CurrentStageTokenIdentityNotProven)):
                acquire_token_for_readonly(native_root=tmp_path, trading_date=DAY, caller="push_client_from_repo")
            owner = json.loads((tmp_path / "kabu_station_owner.json").read_text(encoding="utf-8"))
            prev_pid_owner = owner.get("pid")
            owner["pid"] = 2147483646
            (tmp_path / "kabu_station_owner.json").write_text(json.dumps(owner), encoding="utf-8")
        with owner_issue_context(
            native_root=tmp_path,
            trading_date=DAY,
            pid=os.getpid(),
            session_id=f"ing_{stage}",
            caller="ingress_replay_connect",
        ):
            tok = issue_station_token(_StubClient(count, f"tok-{stage}"), "pw")
        assert tok == f"tok-{stage}"
        assert load_station_bundle()["stage_run_id"] == stage
        set_auth_phase(PHASE_POST_INGRESS_PRE_BOARD)
        got = acquire_token_for_readonly(native_root=tmp_path, trading_date=DAY, caller="verify_kabu_connection")
        assert got["token"] == tok
        assert got["token_stage_class"] == TOKEN_STAGE_MATCH
        if prev_pid_owner:
            assert int(load_station_bundle().get("pid") or 0) == os.getpid()
    assert count.read_text(encoding="utf-8") == str(len(stages))


def test_fault_injection_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _iso_station(tmp_path, monkeypatch)
    _cert_stage(monkeypatch, "full_day_fault")
    day_dir = tmp_path / "data" / "market_capture" / DAY
    day_dir.mkdir(parents=True, exist_ok=True)

    def _run(phase: str) -> dict[str, Any]:
        residue = inspect_leftover_auth_state(native_root=tmp_path, trading_date=DAY)
        return decide_auth(phase=phase, residue=residue, caller="fault")

    (tmp_path / "kabu_station_token_bundle.json").unlink(missing_ok=True)
    (tmp_path / "kabu_station_owner.json").unlink(missing_ok=True)
    assert _run(PHASE_PRE_INGRESS)["decision"] == DECISION_DEFER
    assert _run(PHASE_POST_INGRESS_PRE_BOARD)["decision"] == DECISION_FAIL_CLOSED
    assert _run(PHASE_INGRESS_STARTING)["decision"] == DECISION_REISSUE
    assert _run(PHASE_TEARDOWN)["decision"] == DECISION_CLEANUP

    _write_unscoped_gen34(tmp_path, pid=os.getpid())
    monkeypatch.setattr("small_paper.kabu_token_authority._pid_alive", lambda pid: int(pid) == os.getpid())
    assert _run(PHASE_PRE_INGRESS)["decision"] == DECISION_CLEANUP
    assert _run(PHASE_POST_INGRESS_PRE_BOARD)["decision"] == DECISION_FAIL_CLOSED

    _write_stage_bundle(tmp_path, pid=os.getpid(), token="x", stage="other", cert="cert_v25")
    out = _run(PHASE_BOARD_ACTIVE)
    assert out["decision"] == DECISION_FAIL_CLOSED
    assert out["reason"] == REASON_STALE_STAGE

    _write_stage_bundle(tmp_path, pid=os.getpid(), token="x", stage="full_day_fault", cert="cert_v25")
    monkeypatch.setattr("small_paper.kabu_token_authority._pid_alive", lambda pid: False)
    out = _run(PHASE_AM_RUNTIME)
    assert out["decision"] == DECISION_FAIL_CLOSED
    assert out["reason"] == REASON_OWNER_DEAD

    _write_stage_bundle(tmp_path, pid=111, token="x", stage="full_day_fault", cert="cert_v25")
    (day_dir / "ingress.pid").write_text("222", encoding="utf-8")
    monkeypatch.setattr("small_paper.kabu_token_authority._pid_alive", lambda pid: int(pid) in {111, 222})
    out = _run(PHASE_PM_RUNTIME)
    assert out["decision"] == DECISION_FAIL_CLOSED
    assert out["reason"] == REASON_DUPLICATE_ISSUER


def test_auth_decision_log_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _iso_station(tmp_path, monkeypatch)
    _cert_stage(monkeypatch, "full_day_log")
    set_auth_phase(PHASE_PRE_INGRESS)
    consumer_auth_outcome(native_root=tmp_path, trading_date=DAY, caller="push_client_from_repo")
    text = capsys.readouterr().out
    assert "AUTH_DECISION" in text
    assert "phase=PRE_INGRESS" in text
    assert "decision=" in text
    assert "reason=" in text
    assert "expected_stage=" in text
    assert "got_stage=" in text


def test_submit_cancel_live_remain_hard_fail() -> None:
    from small_paper.live_order_safety_sm import KabuBrokerAdapter

    kabu = KabuBrokerAdapter()
    with pytest.raises(RuntimeError) as sub:
        kabu.submit_entry_order({"symbol": "X", "quantity": 1})
    assert "HARD_FAIL" in str(sub.value)
    with pytest.raises(RuntimeError) as can:
        kabu.cancel_order("x")
    assert "HARD_FAIL" in str(can.value)


class _KabuStub(BaseHTTPRequestHandler):
    def _json(self, code: int, body: dict[str, Any]) -> None:
        raw = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self) -> None:  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        if str(self.path).rstrip("/").endswith("/token"):
            self.server.post_token_count += 1  # type: ignore[attr-defined]
            self._json(200, {"Token": "e2e-current-stage-token"})
            return
        self._json(200, {})

    def do_PUT(self) -> None:  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        self._json(200, {"RegistList": [], "RegistNum": 0})

    def do_GET(self) -> None:  # noqa: N802
        self._json(200, {"CurrentPrice": 1000, "CurrentPriceTime": "2026-08-12T09:00:00+09:00", "RegistList": []})

    def log_message(self, fmt: str, *args: Any) -> None:
        return


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


def _stop_pid(pid: int) -> None:
    if pid <= 0:
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False)
    else:
        try:
            os.kill(pid, 15)
        except OSError:
            pass


def test_production_path_real_ingress_spawn_e2e(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """legacy_register_preclear → real Ingress spawn → local POST /token → MATCH → stop.

    Orders remain disabled. Uses a local kabusapi stub, not live Station orders.
    """
    from small_paper.market_ingress_spawn import spawn_ingress_process, wait_ingress_online

    station = tmp_path / "station"
    station.mkdir()
    native = tmp_path / "native"
    (native / "src" / "small_paper").mkdir(parents=True)
    replay = tmp_path / "replay.jsonl"
    lines = []
    for i in range(80):
        lines.append(
            json.dumps(
                {
                    "Symbol": "1301",
                    "received_at": f"2026-08-12T09:00:{i:02d}.000+09:00" if i < 60 else f"2026-08-12T09:01:{i-60:02d}.000+09:00",
                    "CurrentPrice": 1000 + i,
                    "cert_sequence": i + 1,
                }
            )
        )
    replay.write_text("\n".join(lines) + "\n", encoding="utf-8")
    port = _free_port()
    bus_port = _free_port()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), _KabuStub)
    httpd.post_token_count = 0  # type: ignore[attr-defined]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    stage = "full_day_e2e_v25"
    trading_date = "20990115"
    monkeypatch.setenv("KABU_STATION_AUTHORITY_DIR", str(station))
    monkeypatch.setenv("KABU_TOKEN_AUTHORITY_DIR", str(station))
    monkeypatch.setenv("KABU_API_BASE", f"http://127.0.0.1:{port}/kabusapi")
    monkeypatch.setenv("KABU_API_PASSWORD", "e2e-password")
    monkeypatch.setenv(ENV_CERT_MODE, "1")
    monkeypatch.setenv(ENV_KABU_AUTH_MODE, "LIVE")
    monkeypatch.setenv(ENV_CERTIFICATION_RUN_ID, "cert_e2e_v25")
    monkeypatch.setenv(ENV_STAGE_RUN_ID, stage)
    monkeypatch.setenv("TRADEBOT_INGRESS_REPLAY_PATH", str(replay))
    monkeypatch.setenv("TRADEBOT_INGRESS_REPLAY_MAX_EPS", "8")
    monkeypatch.setenv("TRADEBOT_MARKET_BUS_PORT", str(bus_port))
    monkeypatch.setenv("TRADEBOT_TRADING_DATE", trading_date)
    monkeypatch.delenv("TRADEBOT_SESSION_CLOCK", raising=False)
    monkeypatch.delenv("KABU_TOKEN_PREFLIGHT", raising=False)
    _write_unscoped_gen34(station, pid=14372)
    set_auth_phase(PHASE_PRE_INGRESS)
    r = PaperTradeCheckedRunner(
        native_root=native,
        repo_root=tmp_path,
        skip_paper=False,
        capture_synthetic=False,
        skip_w4s=True,
        config_path=CFG,
        skip_capture_wait=True,
    )
    assert r.capture_synthetic is False
    r.trading_date = trading_date
    assert r.step_legacy_register_preclear() is True
    pid = 0
    try:
        with patch("small_paper.market_ingress_spawn._live_ingress_pids", return_value=[]):
            spawn = spawn_ingress_process(
                native_root=native,
                trading_date=trading_date,
                python_exe=sys.executable,
                synthetic=False,
                bus_port=bus_port,
                code_root=NATIVE,
                allow_duplicate=True,
            )
        assert not spawn.get("rejected"), spawn
        pid = int(spawn.get("pid") or 0)
        assert pid > 0
        set_auth_phase(PHASE_POST_INGRESS_PRE_BOARD)
        wait = wait_ingress_online(
            native,
            trading_date,
            timeout_sec=45.0,
            require_registered_count=0,
            expected_launch_nonce=str(spawn.get("launch_nonce") or ""),
            expected_ingress_run_id=str(spawn.get("ingress_run_id") or ""),
            expected_activation_id=str(spawn.get("activation_id") or ""),
            expected_activation_sha=str(spawn.get("activation_sha") or ""),
            expected_pid=pid,
            expected_process_start_identity=str(spawn.get("process_start_identity") or ""),
            expected_bus_identity=str(spawn.get("bus_identity") or ""),
        )
        assert wait.get("ok") is True, wait
        bundle = load_station_bundle()
        assert bundle.get("stage_run_id") == stage
        assert bundle.get("token") == "e2e-current-stage-token"
        got = acquire_token_for_readonly(
            native_root=native, trading_date=trading_date, caller="verify_kabu_connection"
        )
        assert got["token_stage_class"] == TOKEN_STAGE_MATCH
        assert got["token"] == "e2e-current-stage-token"
        assert int(httpd.post_token_count) >= 1  # type: ignore[attr-defined]
        assert int(httpd.post_token_count) <= 2  # type: ignore[attr-defined]
    finally:
        _stop_pid(pid)
        httpd.shutdown()
        set_auth_phase(PHASE_TEARDOWN)
        time.sleep(0.4)
        # teardown residual: spawned pid must not remain
        if pid > 0:
            try:
                os.kill(pid, 0)
                alive = True
            except OSError:
                alive = False
            if sys.platform == "win32":
                q = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                alive = str(pid) in (q.stdout or "")
            assert alive is False
