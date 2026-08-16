"""V26-C/D/E: lifecycle consolidation, recovery matrix, legacy retirement."""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from small_paper.auth_lifecycle import (
    DECISION_FAIL_CLOSED,
    DECISION_PASS,
    PHASE_AM_TO_PM_TRANSITION,
    PHASE_POST_INGRESS_PRE_BOARD,
    PHASE_PRE_INGRESS,
    decide_auth,
    inspect_leftover_auth_state,
    set_auth_phase,
)
from small_paper.capture_child_cleanup import (
    OwnedCaptureProcess,
    cleanup_owned_capture,
)
from small_paper.ingress_run_identity import ENV_CERTIFICATION_RUN_ID, ENV_STAGE_RUN_ID
from small_paper.kabu_token_authority import (
    AUTHORITY_ACTIVE_TOKEN_OWNER,
    AUTHORITY_FAILED_ISSUE,
    acquire_token_for_readonly,
    load_station_bundle,
    load_station_owner,
    reclaim_dead_station_owner,
    station_issue_audit_summary,
    token_fingerprint,
)
from small_paper.ownership_classifier import (
    CONFLICT,
    CURRENT_VALID,
    DEAD_OWNER,
    PID_REUSED,
    STALE_PROVEN_OWNED,
    UNKNOWN,
    classify_owner,
)
from small_paper.paper_runtime_supervisor import PRODUCTION_LIFECYCLE_ACTIVE, _safe_kill
from small_paper.paper_trade_checked_runner import PaperTradeCheckedRunner
from small_paper.runtime_clock import ENV_CERT_MODE, ENV_KABU_AUTH_MODE
from small_paper.runtime_lifecycle import (
    CALLSITE_INVENTORY,
    CALLSITE_OBSOLETE,
    CALLSITE_OWNER,
    KILL_NONE,
    LEGACY_RETIREMENT,
    LIFECYCLE_AUTHORITY,
    LIFECYCLE_AUTHORITY_COUNT,
    STARTUP_SEQUENCE,
    TEARDOWN_SEQUENCE,
    decide_kill,
    evaluate_teardown_residuals,
    finish_teardown,
    is_auth_ready,
    production_lifecycle_path_proof,
    real_kabus_auth_ready,
    reconcile_startup,
)
from small_paper.runtime_ownership import CLASSIFIER_IMPLEMENTATION_COUNT, CLASSIFIER_IMPLEMENTATION_ID

NATIVE = Path(__file__).resolve().parents[1]
SRC = NATIVE / "src"
CFG = NATIVE / "configs" / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
DAY = "20990126"


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
            self._json(200, {"Token": "v26cde-current-stage-token"})
            return
        self._json(200, {})

    def do_PUT(self) -> None:  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        self._json(200, {"RegistList": [], "RegistNum": 0})

    def do_GET(self) -> None:  # noqa: N802
        self._json(
            200,
            {"CurrentPrice": 1000, "CurrentPriceTime": "2026-08-12T09:00:00+09:00", "RegistList": []},
        )

    def log_message(self, fmt: str, *args: Any) -> None:
        return


def _iso_station(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("KABU_STATION_AUTHORITY_DIR", str(tmp_path))
    monkeypatch.setenv("KABU_TOKEN_AUTHORITY_DIR", str(tmp_path))
    monkeypatch.setenv("KABU_AUTH_MODE", "LIVE")
    monkeypatch.delenv("KABU_TOKEN_PREFLIGHT", raising=False)
    monkeypatch.delenv("KABU_CERTIFICATION_PROBE", raising=False)
    return tmp_path


def _cert_stage(monkeypatch: pytest.MonkeyPatch, stage: str, cert: str = "cert_v26cde") -> None:
    monkeypatch.setenv(ENV_CERT_MODE, "1")
    monkeypatch.setenv(ENV_CERTIFICATION_RUN_ID, cert)
    monkeypatch.setenv(ENV_STAGE_RUN_ID, stage)


def _write_failed_issue_dead(
    tmp: Path,
    *,
    pid: int,
    token: str,
    generation: int = 34,
    start: str = "failed-issue-start",
) -> None:
    """Generalized FAILED_ISSUE + dead process. No hardcoded production pid."""
    body = {
        "caller": "ingress_replay_connect",
        "fingerprint": token_fingerprint(token),
        "generation": generation,
        "owner": "MARKET_INGRESS_SERVICE",
        "owner_role": "MARKET_INGRESS_SERVICE",
        "component_role": "MARKET_INGRESS_SERVICE",
        "pid": pid,
        "owner_pid": pid,
        "owner_process_start_identity": start,
        "session_id": "ing_failed_issue",
        "token": token,
        "token_generation": generation,
        "trading_date": DAY,
        "kabu_token_authority": "MARKET_INGRESS_SERVICE",
        "authority_state": AUTHORITY_FAILED_ISSUE,
    }
    (tmp / "kabu_station_token_bundle.json").write_text(json.dumps(body), encoding="utf-8")
    owner = {k: v for k, v in body.items() if k != "token"}
    owner["authority_state"] = AUTHORITY_FAILED_ISSUE
    (tmp / "kabu_station_owner.json").write_text(json.dumps(owner), encoding="utf-8")


def _write_dead_owner(tmp: Path, *, pid: int, token: str, generation: int = 34, start: str = "dead-start") -> None:
    body = {
        "caller": "ingress_replay_connect",
        "fingerprint": token_fingerprint(token),
        "generation": generation,
        "owner": "MARKET_INGRESS_SERVICE",
        "owner_role": "MARKET_INGRESS_SERVICE",
        "component_role": "MARKET_INGRESS_SERVICE",
        "pid": pid,
        "owner_pid": pid,
        "owner_process_start_identity": start,
        "session_id": "ing_dead_owner",
        "token": token,
        "token_generation": generation,
        "trading_date": DAY,
        "kabu_token_authority": "MARKET_INGRESS_SERVICE",
        "authority_state": AUTHORITY_ACTIVE_TOKEN_OWNER,
    }
    (tmp / "kabu_station_token_bundle.json").write_text(json.dumps(body), encoding="utf-8")
    owner = {k: v for k, v in body.items() if k != "token"}
    (tmp / "kabu_station_owner.json").write_text(json.dumps(owner), encoding="utf-8")


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


def _replay_file(path: Path) -> None:
    lines = []
    for i in range(80):
        ts = (
            f"2026-08-12T09:00:{i:02d}.000+09:00"
            if i < 60
            else f"2026-08-12T09:01:{i - 60:02d}.000+09:00"
        )
        lines.append(
            json.dumps(
                {"Symbol": "1301", "received_at": ts, "CurrentPrice": 1000 + i, "cert_sequence": i + 1}
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_single_classifier_and_authority() -> None:
    assert CLASSIFIER_IMPLEMENTATION_COUNT == 1
    assert CLASSIFIER_IMPLEMENTATION_ID == "small_paper.ownership_classifier.classify_owner"
    assert LIFECYCLE_AUTHORITY == "paper_trade_checked_runner"
    assert LIFECYCLE_AUTHORITY_COUNT == 1
    owners = [c for c in CALLSITE_INVENTORY if c["class"] == CALLSITE_OWNER]
    assert any(c["module"] == "paper_trade_checked_runner.py" for c in owners)
    assert PRODUCTION_LIFECYCLE_ACTIVE is False
    proof = production_lifecycle_path_proof()
    assert "PM_DIRECT harness" in proof["shared_by"]
    defs = []
    for path in (SRC / "small_paper").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if re.search(r"^def classify_owner\(", text, re.M):
            defs.append(path.name)
    assert defs == ["ownership_classifier.py"]


def test_startup_auth_ready_not_claimed_or_env_blocked() -> None:
    assert is_auth_ready(status={"state": "CLAIMED_PENDING_TOKEN"})[0] is False
    assert is_auth_ready(status={"authority_state": "FAILED_ISSUE", "state": "RUNNING"})[0] is False
    blocked, reason = is_auth_ready(
        status={"state": "AUTH_FAILED", "auth_failure_code": "ENVIRONMENT_AUTH_BLOCKED|4001007|http=401"}
    )
    assert blocked is False
    assert "ENVIRONMENT_AUTH_BLOCKED" in reason
    gate = real_kabus_auth_ready(
        status={"state": "AUTH_FAILED", "auth_failure_code": "ENVIRONMENT_AUTH_BLOCKED|4001007|http=401"}
    )
    assert gate["REAL_KABUS_AUTH_READY"] is False
    assert gate["ENVIRONMENT_AUTH_BLOCKED"] is True
    assert gate["kabu_code"] == "4001007"
    ok, why = is_auth_ready(status={"state": "RUNNING"})
    assert ok is True
    assert why == "AUTH_READY"
    assert STARTUP_SEQUENCE[-2] == "BOARD_NATIVE"
    src = (SRC / "small_paper" / "paper_trade_checked_runner.py").read_text(encoding="utf-8")
    board_idx = src.find("PHASE_BOARD_ACTIVE")
    ready_idx = src.find("is_auth_ready")
    assert ready_idx > 0 and board_idx > ready_idx


def test_decide_kill_fail_closed_classes() -> None:
    for ocls in (UNKNOWN, PID_REUSED, CONFLICT):
        out = decide_kill({"class": ocls, "reason": ocls}, identity_proven=True)
        assert out["action"] == KILL_NONE
        assert out["kill_allowed"] is False
        assert out["wrong_process_kill"] == 0
    stale = decide_kill({"class": STALE_PROVEN_OWNED}, identity_proven=True, stale_graceful_done=False)
    assert stale["kill_allowed"] is True
    assert stale["action"] == "GRACEFUL"
    force = decide_kill({"class": STALE_PROVEN_OWNED}, identity_proven=True, stale_graceful_done=True)
    assert force["action"] == "FORCE"


def test_pid_reused_and_unknown_cleanup_does_not_kill() -> None:
    owned = OwnedCaptureProcess(
        pid=os.getpid(),
        cmd=["python", "-m", "small_paper.market_ingress_service"],
        native_root=str(NATIVE),
        trading_date=DAY,
        cmdline_fingerprint="small_paper.market_ingress_service --native-root x",
        process_start_identity="other-process-start",
        component_role="MARKET_INGRESS_SERVICE",
        create_time="not-this-process",
    )
    result = cleanup_owned_capture(owned, reason="test_teardown", skip_capture_wait=True, graceful_timeout_sec=0.2)
    assert result.kill_used is False
    assert result.wrong_process_kill == 0
    assert result.skipped is True
    unknown = OwnedCaptureProcess(
        pid=os.getpid(),
        cmd=["notepad.exe"],
        native_root=str(NATIVE),
        trading_date=DAY,
        process_start_identity="",
        component_role="FOREIGN",
        cmdline_fingerprint="notepad.exe",
    )
    live = {"exists": True, "cmdline": "notepad.exe", "create_time": "x"}
    with patch("small_paper.capture_child_cleanup.query_process", return_value=live), patch(
        "small_paper.capture_child_cleanup.verify_ownership",
        return_value={"owned": True, "reason": "owned"},
    ), patch("small_paper.capture_child_cleanup._pid_alive", return_value=True), patch(
        "small_paper.capture_child_cleanup._kill_pid"
    ) as killed, patch("small_paper.capture_child_cleanup._terminate_pid") as term:
        out = cleanup_owned_capture(unknown, reason="force", skip_capture_wait=True, graceful_timeout_sec=0.2)
        killed.assert_not_called()
        term.assert_not_called()
    assert out.skipped is True
    assert out.wrong_process_kill == 0


def test_supervisor_pid_only_kill_retired() -> None:
    assert PRODUCTION_LIFECYCLE_ACTIVE is False
    assert _safe_kill(os.getpid()) is False
    assert _safe_kill(os.getpid(), process_start_identity="") is False


def test_failed_issue_dead_reclaim_and_restart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _iso_station(tmp_path, monkeypatch)
    _cert_stage(monkeypatch, "full_day_failed_issue")
    leftover = "failed-issue-token-bytes"
    _write_failed_issue_dead(tmp_path, pid=2147483001, token=leftover)
    monkeypatch.setattr("small_paper.kabu_token_authority._pid_alive", lambda pid: False)
    classified = classify_owner(
        owner=load_station_owner(),
        bundle=load_station_bundle(),
        current={"pid": os.getpid(), "stage_run_id": "full_day_failed_issue"},
        pid_alive_fn=lambda pid: False,
    )
    assert classified["class"] == DEAD_OWNER
    rec = reconcile_startup(native_root=tmp_path, trading_date=DAY)
    assert rec["ok"] is True
    assert rec["wrong_process_kill"] == 0
    owner = load_station_owner()
    assert owner.get("pid") == 0
    assert owner.get("previous_owner_history")
    assert load_station_bundle().get("token") == leftover
    assert reclaim_dead_station_owner(native_root=tmp_path, trading_date=DAY)["reclaimed"] is False


def test_abnormal_recovery_matrix_unit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _iso_station(tmp_path, monkeypatch)
    _cert_stage(monkeypatch, "full_day_matrix")
    native = tmp_path / "native"
    day_dir = native / "data" / "market_capture" / DAY
    day_dir.mkdir(parents=True)
    monkeypatch.setattr("small_paper.kabu_token_authority._pid_alive", lambda pid: False)

    # A/J clean + dead owner
    _write_dead_owner(tmp_path, pid=2147483002, token="dead-tok")
    rec = reconcile_startup(native_root=native, trading_date=DAY)
    assert rec["ok"] is True
    torn = finish_teardown(native_root=native, trading_date=DAY, owned_pid=2147483002)
    assert torn["history_deleted"] is False
    assert torn["wrong_process_kill"] == 0
    assert torn["residuals"]["ok"] is True

    # C ENVIRONMENT_AUTH_BLOCKED
    status = {
        "state": "AUTH_FAILED",
        "auth_failure_code": "ENVIRONMENT_AUTH_BLOCKED|4001007|http=401",
        "auth_failure_http_status": 401,
    }
    gate = real_kabus_auth_ready(status=status)
    assert gate["REAL_KABUS_AUTH_READY"] is False
    rec2 = reconcile_startup(native_root=native, trading_date=DAY)
    assert rec2["wrong_process_kill"] == 0

    # D claim-only dead
    owner = {
        "pid": 2147483003,
        "owner_pid": 2147483003,
        "component_role": "MARKET_INGRESS_SERVICE",
        "caller": "ingress_replay_connect",
        "authority_state": "CLAIMED_PENDING_TOKEN",
        "owner_process_start_identity": "claim-start",
    }
    (tmp_path / "kabu_station_owner.json").write_text(json.dumps(owner), encoding="utf-8")
    (tmp_path / "kabu_station_token_bundle.json").write_text(json.dumps(owner), encoding="utf-8")
    rec3 = reconcile_startup(native_root=native, trading_date=DAY)
    assert rec3["ok"] is True
    assert load_station_owner().get("previous_owner_history")

    # K PID reuse
    live = os.getpid()
    _write_dead_owner(tmp_path, pid=live, token="reuse", start="other-start")
    classified = classify_owner(
        owner=load_station_owner(),
        bundle=load_station_bundle(),
        current={"pid": live, "process_start_identity": "this-start"},
        pid_alive_fn=lambda pid: int(pid) == live,
        live_process_start_fn=lambda pid: "this-start",
    )
    assert classified["class"] == PID_REUSED
    kill = decide_kill(classified, identity_proven=True)
    assert kill["kill_allowed"] is False

    # L unknown
    unk = classify_owner(
        owner={"pid": live, "process_start_identity": "x", "component_role": "FOREIGN"},
        bundle={"pid": live, "process_start_identity": "x"},
        current={"pid": live + 1},
        pid_alive_fn=lambda pid: int(pid) == live,
        live_process_start_fn=lambda pid: "x",
    )
    assert unk["class"] in {UNKNOWN, STALE_PROVEN_OWNED, CURRENT_VALID}
    if unk["class"] == UNKNOWN:
        assert decide_kill(unk, identity_proven=False)["kill_allowed"] is False


def test_wait_env_auth_blocked_early(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from small_paper.market_ingress_spawn import wait_ingress_online

    day = tmp_path / "data" / "market_capture" / DAY
    day.mkdir(parents=True)
    payload = {
        "state": "AUTH_FAILED",
        "launch_nonce": "nonce_env",
        "auth_failure_code": "ENVIRONMENT_AUTH_BLOCKED|4001007|http=401",
        "auth_failure_http_status": 401,
        "auth_failure_message_sanitized": "Code 4001007",
    }
    (day / "ingress_status.json").write_text(json.dumps(payload), encoding="utf-8")
    t0 = time.monotonic()
    wait = wait_ingress_online(
        tmp_path,
        DAY,
        timeout_sec=8.0,
        expected_launch_nonce="nonce_env",
        expected_ingress_run_id="ing",
        expected_activation_id="a",
        expected_activation_sha="b",
    )
    assert time.monotonic() - t0 < 5.0
    assert wait.get("ok") is False
    assert wait.get("reason") == "ENVIRONMENT_AUTH_BLOCKED"
    assert wait.get("REAL_KABUS_AUTH_READY") is False
    assert wait.get("kabu_code") == "4001007"


def test_am_pm_does_not_force_new_generation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _iso_station(tmp_path, monkeypatch)
    _cert_stage(monkeypatch, "am_stage")
    from small_paper.ingress_run_identity import capture_process_start_identity
    from small_paper.kabu_token_authority import current_stage_token_identity

    pid = os.getpid()
    start = str(capture_process_start_identity(pid) or "am-start")
    ident = current_stage_token_identity()
    body = {
        "pid": pid,
        "owner_pid": pid,
        "token": "am-token",
        "generation": 7,
        "token_generation": 7,
        "stage_run_id": ident.get("stage_run_id") or "am_stage",
        "certification_run_id": ident.get("certification_run_id"),
        "activation_sha": ident.get("activation_sha"),
        "activation_id": ident.get("activation_id"),
        "component_role": "MARKET_INGRESS_SERVICE",
        "caller": "ingress_replay_connect",
        "owner_process_start_identity": start,
        "authority_state": AUTHORITY_ACTIVE_TOKEN_OWNER,
    }
    (tmp_path / "kabu_station_token_bundle.json").write_text(json.dumps(body), encoding="utf-8")
    (tmp_path / "kabu_station_owner.json").write_text(json.dumps({k: v for k, v in body.items() if k != "token"}), encoding="utf-8")
    monkeypatch.setattr("small_paper.kabu_token_authority._pid_alive", lambda p: int(p) == pid)
    residue = inspect_leftover_auth_state(native_root=tmp_path, trading_date=DAY)
    d = decide_auth(phase=PHASE_AM_TO_PM_TRANSITION, residue=residue)
    assert d["decision"] == DECISION_PASS
    assert int(residue.get("generation") or 0) == 7
    monkeypatch.setenv(ENV_STAGE_RUN_ID, "pm_stage")
    residue_pm = inspect_leftover_auth_state(native_root=tmp_path, trading_date=DAY)
    d_pm = decide_auth(phase=PHASE_POST_INGRESS_PRE_BOARD, residue=residue_pm)
    assert d_pm["decision"] == DECISION_FAIL_CLOSED


def test_legacy_retirement_and_callsite_classes() -> None:
    assert all(item["production_active"] is False for item in LEGACY_RETIREMENT)
    obsolete = [c["module"] for c in CALLSITE_INVENTORY if c["class"] == CALLSITE_OBSOLETE]
    assert "paper_runtime_supervisor.py" in obsolete
    readonly = (SRC / "small_paper" / "kabu_readonly_readiness.py").read_text(encoding="utf-8")
    assert "acquire_token_for_readonly" in readonly
    assert "issue_station_token(" not in readonly
    cert_script = (NATIVE / "scripts" / "run_paper_full_day_certification.py").read_text(encoding="utf-8")
    assert "issue_token_from_env" not in cert_script
    runner = (SRC / "small_paper" / "paper_trade_checked_runner.py").read_text(encoding="utf-8")
    spawn = (SRC / "small_paper" / "market_ingress_spawn.py").read_text(encoding="utf-8")
    assert "spawn_ingress_process" in runner
    assert "wait_ingress_online" in runner
    assert "official_cert_child_env" in spawn


def test_taskkill_sites_are_identity_gated() -> None:
    hits = []
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "taskkill" not in text:
            continue
        hits.append(path.name)
        assert "decide_kill" in text or "process_start_identity" in text or "verify_ownership" in text
    assert "capture_child_cleanup.py" in hits
    assert "paper_runtime_supervisor.py" in hits
    assert "bounded_side_task.py" in hits


def test_submit_cancel_live_zero() -> None:
    from small_paper.live_order_safety_sm import KabuBrokerAdapter

    kabu = KabuBrokerAdapter()
    with pytest.raises(RuntimeError) as sub:
        kabu.submit_entry_order({"symbol": "X", "quantity": 1})
    assert "HARD_FAIL" in str(sub.value)
    with pytest.raises(RuntimeError) as can:
        kabu.cancel_order("x")
    assert "HARD_FAIL" in str(can.value)


def _spawn_stub(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    stage: str,
    leftover_failed_issue: bool = False,
    leftover_dead: bool = False,
    trading_date: str = DAY,
) -> dict[str, Any]:
    from small_paper.kabu_token_authority import TOKEN_STAGE_MATCH
    from small_paper.market_ingress_spawn import spawn_ingress_process, wait_ingress_online

    station = tmp_path / "station"
    station.mkdir(exist_ok=True)
    native = tmp_path / "native"
    native.mkdir(exist_ok=True)
    (native / "src" / "small_paper").mkdir(parents=True, exist_ok=True)
    replay = tmp_path / "replay.jsonl"
    if not replay.is_file():
        _replay_file(replay)
    port = _free_port()
    bus_port = _free_port()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), _KabuStub)
    httpd.post_token_count = 0  # type: ignore[attr-defined]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("KABU_STATION_AUTHORITY_DIR", str(station))
    monkeypatch.setenv("KABU_TOKEN_AUTHORITY_DIR", str(station))
    monkeypatch.setenv("KABU_API_BASE", f"http://127.0.0.1:{port}/kabusapi")
    monkeypatch.setenv("KABU_API_PASSWORD", "e2e-password")
    monkeypatch.setenv(ENV_CERT_MODE, "1")
    monkeypatch.setenv(ENV_KABU_AUTH_MODE, "LIVE")
    monkeypatch.setenv(ENV_CERTIFICATION_RUN_ID, "cert_e2e_v26cde")
    monkeypatch.setenv(ENV_STAGE_RUN_ID, stage)
    monkeypatch.setenv("TRADEBOT_INGRESS_REPLAY_PATH", str(replay))
    monkeypatch.setenv("TRADEBOT_INGRESS_REPLAY_MAX_EPS", "8")
    monkeypatch.setenv("TRADEBOT_MARKET_BUS_PORT", str(bus_port))
    monkeypatch.setenv("TRADEBOT_TRADING_DATE", trading_date)
    monkeypatch.delenv("TRADEBOT_SESSION_CLOCK", raising=False)
    monkeypatch.delenv("KABU_TOKEN_PREFLIGHT", raising=False)
    if leftover_failed_issue:
        _write_failed_issue_dead(station, pid=2147483010, token="leftover-failed-issue")
    if leftover_dead:
        _write_dead_owner(station, pid=2147483011, token="leftover-dead")
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
    r.trading_date = trading_date
    rec = reconcile_startup(native_root=native, trading_date=trading_date)
    assert rec["ok"] is True, rec
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
        ready, why = is_auth_ready(status=wait.get("snapshot") or {})
        assert ready is True, why
        bundle = load_station_bundle()
        assert bundle.get("stage_run_id") == stage
        assert bundle.get("token") == "v26cde-current-stage-token"
        got = acquire_token_for_readonly(
            native_root=native, trading_date=trading_date, caller="verify_kabu_connection"
        )
        assert got["token_stage_class"] == TOKEN_STAGE_MATCH
        session_err = Path(str(spawn.get("ingress_stderr_log") or ""))
        assert session_err.is_file()
        return {
            "pid": pid,
            "wait": wait,
            "spawn": spawn,
            "native": native,
            "station": station,
            "httpd": httpd,
            "http_posts": int(httpd.post_token_count),  # type: ignore[attr-defined]
            "generation": int(bundle.get("generation") or 0),
            "audit": station_issue_audit_summary(),
            "session_stderr": str(session_err),
            "ingress_run_id": spawn.get("ingress_run_id"),
        }
    except Exception:
        _stop_pid(pid)
        httpd.shutdown()
        raise


def test_case_a_clean_shutdown_and_restart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from small_paper.market_ingress_spawn import spawn_ingress_process, wait_ingress_online

    first = _spawn_stub(tmp_path, monkeypatch, stage="full_day_v26cde_a")
    pid = int(first["pid"])
    native = first["native"]
    httpd = first["httpd"]
    try:
        _stop_pid(pid)
        time.sleep(0.6)
        torn = finish_teardown(native_root=native, trading_date=DAY, owned_pid=pid)
        assert torn["wrong_process_kill"] == 0
        rec = reconcile_startup(native_root=native, trading_date=DAY)
        assert rec["ok"] is True
        with patch("small_paper.market_ingress_spawn._live_ingress_pids", return_value=[]):
            spawn2 = spawn_ingress_process(
                native_root=native,
                trading_date=DAY,
                python_exe=sys.executable,
                synthetic=False,
                bus_port=_free_port(),
                code_root=NATIVE,
                allow_duplicate=True,
            )
        pid2 = int(spawn2.get("pid") or 0)
        assert pid2 > 0
        wait2 = wait_ingress_online(
            native,
            DAY,
            timeout_sec=45.0,
            expected_launch_nonce=str(spawn2.get("launch_nonce") or ""),
            expected_ingress_run_id=str(spawn2.get("ingress_run_id") or ""),
            expected_activation_id=str(spawn2.get("activation_id") or ""),
            expected_activation_sha=str(spawn2.get("activation_sha") or ""),
            expected_pid=pid2,
            expected_process_start_identity=str(spawn2.get("process_start_identity") or ""),
            expected_bus_identity=str(spawn2.get("bus_identity") or ""),
        )
        assert wait2.get("ok") is True, wait2
        _stop_pid(pid2)
        finish_teardown(native_root=native, trading_date=DAY, owned_pid=pid2)
    finally:
        _stop_pid(pid)
        httpd.shutdown()
        set_auth_phase("TEARDOWN")
        time.sleep(0.3)


def test_stderr_session_not_truncated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from small_paper.market_ingress_spawn import spawn_ingress_process

    first = _spawn_stub(tmp_path, monkeypatch, stage="window_stderr_1")
    native = first["native"]
    first_err = Path(first["session_stderr"])
    marker = "STAGE1_STDERR_MUST_SURVIVE\n"
    first_err.write_text(first_err.read_text(encoding="utf-8") + marker, encoding="utf-8")
    pid1 = int(first["pid"])
    try:
        with patch("small_paper.market_ingress_spawn._live_ingress_pids", return_value=[]):
            spawn2 = spawn_ingress_process(
                native_root=native,
                trading_date=DAY,
                python_exe=sys.executable,
                synthetic=False,
                bus_port=_free_port(),
                code_root=NATIVE,
                allow_duplicate=True,
            )
        pid2 = int(spawn2.get("pid") or 0)
        assert pid2 > 0
        assert first_err.is_file()
        assert marker in first_err.read_text(encoding="utf-8")
        day_log = native / "data" / "market_capture" / DAY / "ingress_stderr.log"
        assert day_log.is_file()
        assert "index" in day_log.read_text(encoding="utf-8")
        assert spawn2.get("ingress_stderr_log") != first["session_stderr"]
        _stop_pid(pid2)
    finally:
        _stop_pid(pid1)
        first["httpd"].shutdown()
        set_auth_phase("TEARDOWN")
        time.sleep(0.3)


def test_failed_issue_then_stub_startup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = _spawn_stub(tmp_path, monkeypatch, stage="full_day_v26cde_failed", leftover_failed_issue=True)
    try:
        assert out["http_posts"] >= 1
        assert out["generation"] != 34
        assert load_station_bundle().get("token") != "leftover-failed-issue"
    finally:
        _stop_pid(int(out["pid"]))
        out["httpd"].shutdown()
        finish_teardown(native_root=out["native"], trading_date=DAY, owned_pid=int(out["pid"]))
        set_auth_phase("TEARDOWN")
        time.sleep(0.3)


@pytest.mark.parametrize(
    "stage",
    ["pm_direct_v26cde", "window_A_v26cde", "window_B_v26cde", "window_C_v26cde"],
)
def test_pm_and_windows_same_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str) -> None:
    out = _spawn_stub(tmp_path, monkeypatch, stage=stage)
    try:
        assert out["http_posts"] >= 1
        assert is_auth_ready(status=out["wait"].get("snapshot") or {})[0] is True
        proof = production_lifecycle_path_proof()
        assert proof["lifecycle_authority"] == "paper_trade_checked_runner"
    finally:
        _stop_pid(int(out["pid"]))
        out["httpd"].shutdown()
        finish_teardown(native_root=out["native"], trading_date=DAY, owned_pid=int(out["pid"]))
        set_auth_phase("TEARDOWN")
        time.sleep(0.3)


def test_teardown_sequence_and_parent_crash_sim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _iso_station(tmp_path, monkeypatch)
    native = tmp_path / "native"
    (native / "data" / "market_capture" / DAY).mkdir(parents=True)
    monkeypatch.setattr("small_paper.kabu_token_authority._pid_alive", lambda pid: False)
    _write_dead_owner(tmp_path, pid=2147483099, token="crash-sim")
    out = finish_teardown(native_root=native, trading_date=DAY, owned_pid=2147483099)
    assert list(TEARDOWN_SEQUENCE)
    assert out["history_deleted"] is False
    assert evaluate_teardown_residuals(native_root=native, trading_date=DAY, owned_pid=2147483099)["ok"] is True
    runner = PaperTradeCheckedRunner(
        native_root=native,
        repo_root=tmp_path,
        skip_paper=True,
        capture_synthetic=True,
        skip_w4s=True,
        config_path=CFG,
        skip_capture_wait=True,
    )
    runner.trading_date = DAY
    runner._shutdown_reason = "exception"
    runner.cleanup_owned_capture(reason="exception")
    assert runner._cleanup_result is not None
