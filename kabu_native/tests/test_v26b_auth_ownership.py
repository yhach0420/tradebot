"""V26-B: AUTH ownership hardening, PID reuse, status fencing, targeted E2E."""
from __future__ import annotations

import json
import os
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
    PHASE_POST_INGRESS_PRE_BOARD,
    PHASE_PRE_INGRESS,
    set_auth_phase,
)
from small_paper.ingress_run_identity import ENV_CERTIFICATION_RUN_ID, ENV_STAGE_RUN_ID
from small_paper.kabu_token_authority import (
    AUTHORITY_ACTIVE_TOKEN_OWNER,
    AUTHORITY_CLAIMED_PENDING_TOKEN,
    OwnerIdentityFailClosed,
    TokenSecondIssuerBlocked,
    acquire_token_for_readonly,
    claim_owner,
    evaluate_issue_permission,
    ingress_owner_active,
    issue_station_token,
    load_station_bundle,
    load_station_owner,
    owner_issue_context,
    reclaim_dead_station_owner,
    station_issue_audit_summary,
    token_fingerprint,
)
from small_paper.ownership_classifier import (
    CURRENT_VALID,
    PID_REUSED,
    STALE_PROVEN_OWNED,
    classify_owner,
)
from small_paper.paper_trade_checked_runner import PaperTradeCheckedRunner
from small_paper.runtime_clock import ENV_CERT_MODE, ENV_KABU_AUTH_MODE

NATIVE = Path(__file__).resolve().parents[1]
CFG = NATIVE / "configs" / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
DAY = "20990116"


class _StubClient:
    def __init__(self, counter: Path, token: str) -> None:
        self.base_url = "http://localhost:18080/kabusapi"
        self.counter = counter
        self.token = token

    def post_token_http(self, api_password: str) -> str:
        n = int(self.counter.read_text(encoding="utf-8") or "0") + 1
        self.counter.write_text(str(n), encoding="utf-8")
        return self.token


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
            self._json(200, {"Token": "v26b-current-stage-token"})
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
    count = tmp_path / "post_count.txt"
    count.write_text("0", encoding="utf-8")
    return count


def _cert_stage(monkeypatch: pytest.MonkeyPatch, stage: str, cert: str = "cert_v26b") -> None:
    monkeypatch.setenv(ENV_CERT_MODE, "1")
    monkeypatch.setenv(ENV_CERTIFICATION_RUN_ID, cert)
    monkeypatch.setenv(ENV_STAGE_RUN_ID, stage)


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
    }
    (tmp / "kabu_station_token_bundle.json").write_text(json.dumps(body), encoding="utf-8")
    owner = {k: v for k, v in body.items() if k != "token"}
    owner["authority_state"] = AUTHORITY_ACTIVE_TOKEN_OWNER
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


def test_claim_only_is_not_auth_ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _iso_station(tmp_path, monkeypatch)
    _cert_stage(monkeypatch, "full_day_claim")
    body = claim_owner(native_root=tmp_path, trading_date=DAY, pid=os.getpid(), session_id="ing_claim")
    assert body.get("authority_state") == AUTHORITY_CLAIMED_PENDING_TOKEN
    assert load_station_owner().get("authority_state") == AUTHORITY_CLAIMED_PENDING_TOKEN
    assert ingress_owner_active(tmp_path, DAY) is False
    set_auth_phase(PHASE_POST_INGRESS_PRE_BOARD)
    with pytest.raises(Exception):
        acquire_token_for_readonly(native_root=tmp_path, trading_date=DAY, caller="verify_kabu_connection")


def test_dead_owner_reclaim_no_kill_no_token_delete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _iso_station(tmp_path, monkeypatch)
    _cert_stage(monkeypatch, "full_day_reclaim")
    leftover = "previous-token-bytes"
    _write_dead_owner(tmp_path, pid=2147483646, token=leftover)
    monkeypatch.setattr("small_paper.kabu_token_authority._pid_alive", lambda pid: False)
    out = reclaim_dead_station_owner(native_root=tmp_path, trading_date=DAY)
    assert out["reclaimed"] is True
    assert out["killed_pid"] is None
    assert out["wrong_process_kill"] == 0
    assert out["bundle_deleted"] is False
    assert load_station_bundle().get("token") == leftover
    assert load_station_owner().get("pid") == 0
    assert load_station_owner().get("previous_owner_history")


def test_case_c_pid_reuse_fail_closed_no_kill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _iso_station(tmp_path, monkeypatch)
    _cert_stage(monkeypatch, "full_day_reuse")
    live_pid = os.getpid()
    _write_dead_owner(tmp_path, pid=live_pid, token="reused-pid-token", start="other-process-start")
    classified = classify_owner(
        owner=load_station_owner(),
        bundle=load_station_bundle(),
        current={"pid": live_pid, "process_start_identity": "this-process-start"},
        pid_alive_fn=lambda pid: int(pid) == live_pid,
        live_process_start_fn=lambda pid: "this-process-start",
    )
    assert classified["class"] == PID_REUSED
    assert classified["kill_allowed"] is False
    assert classified["wrong_process_kill"] == 0
    with pytest.raises(OwnerIdentityFailClosed):
        claim_owner(native_root=tmp_path, trading_date=DAY, pid=live_pid + 1, session_id="ing_reuse")
    assert load_station_bundle().get("token") == "reused-pid-token"


def test_case_d_live_valid_owner_not_reclaimed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    count = _iso_station(tmp_path, monkeypatch)
    _cert_stage(monkeypatch, "full_day_live")
    with owner_issue_context(
        native_root=tmp_path,
        trading_date=DAY,
        pid=os.getpid(),
        session_id="ing_live",
        caller="ingress_replay_connect",
    ):
        issue_station_token(_StubClient(count, "live-tok"), "pw")
    monkeypatch.setattr("small_paper.kabu_token_authority._pid_alive", lambda pid: int(pid) == os.getpid())
    out = reclaim_dead_station_owner(native_root=tmp_path, trading_date=DAY)
    assert out["reclaimed"] is False
    assert out["wrong_process_kill"] == 0
    assert load_station_bundle().get("token") == "live-tok"
    assert load_station_owner().get("pid") == os.getpid()
    classified = classify_owner(
        owner=load_station_owner(),
        bundle=load_station_bundle(),
        current={"pid": os.getpid(), "stage_run_id": "full_day_live", "process_start_identity": ""},
        pid_alive_fn=lambda pid: int(pid) == os.getpid(),
    )
    assert classified["class"] in {CURRENT_VALID, STALE_PROVEN_OWNED}


def test_case_e_duplicate_issuer_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    assert load_station_bundle().get("token") == "tok-1"


def test_case_f_status_writes_serialized(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from small_paper.ingress_run_identity import ENV_INGRESS_RUN_ID, ENV_LAUNCH_NONCE
    from small_paper.market_ingress_service import MarketIngressService

    monkeypatch.setenv(ENV_LAUNCH_NONCE, "nonce_f_v26b")
    monkeypatch.setenv(ENV_INGRESS_RUN_ID, "ingrun_f_v26b")
    native = tmp_path / "native"
    day = "20990117"
    svc = MarketIngressService(native_root=native, trading_date=day, enable_tcp_bus=False, synthetic=True)
    spawn = {
        "pid": os.getpid(),
        "launch_nonce": svc.launch_nonce,
        "ingress_run_id": svc.ingress_run_id,
    }
    (svc.day_root / "ingress_spawn.json").write_text(json.dumps(spawn), encoding="utf-8")
    errors: list[str] = []

    def _writer() -> None:
        try:
            for _ in range(40):
                svc._write_status()
        except Exception as exc:
            errors.append(type(exc).__name__)

    t1 = threading.Thread(target=_writer)
    t2 = threading.Thread(target=_writer)
    t1.start()
    t2.start()
    t1.join(timeout=15)
    t2.join(timeout=15)
    assert errors == []
    raw = (svc.day_root / "ingress_status.json").read_text(encoding="utf-8")
    json.loads(raw)
    assert svc._stale_status_writer_fenced_count == 0


def test_case_g_stale_status_writer_fenced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from small_paper.market_ingress_service import MarketIngressService

    native = tmp_path / "native"
    day = "20990118"
    svc = MarketIngressService(native_root=native, trading_date=day, enable_tcp_bus=False, synthetic=True)
    canonical = svc.day_root / "ingress_status.json"
    canonical.write_text(json.dumps({"state": "ORIGINAL", "pid": 1}) + "\n", encoding="utf-8")
    (svc.day_root / "ingress_spawn.json").write_text(
        json.dumps({"pid": 1, "launch_nonce": "other", "ingress_run_id": "other_run"}),
        encoding="utf-8",
    )
    before = canonical.read_text(encoding="utf-8")
    svc._write_status()
    after = canonical.read_text(encoding="utf-8")
    assert after == before
    assert json.loads(after)["state"] == "ORIGINAL"
    assert svc._stale_status_writer_fenced_count >= 1
    assert list(svc.session_path.glob("stale_status_writer_fenced.jsonl"))


def test_environment_auth_blocked_class() -> None:
    from api.rest_client import ENVIRONMENT_AUTH_BLOCKED, _http_failure_class

    assert _http_failure_class(op="token issue", status=401, kabu_code="4001007") == ENVIRONMENT_AUTH_BLOCKED
    assert _http_failure_class(op="token issue", status=401, kabu_code="") == ENVIRONMENT_AUTH_BLOCKED


def test_submit_cancel_live_zero() -> None:
    from small_paper.live_order_safety_sm import KabuBrokerAdapter

    kabu = KabuBrokerAdapter()
    with pytest.raises(RuntimeError) as sub:
        kabu.submit_entry_order({"symbol": "X", "quantity": 1})
    assert "HARD_FAIL" in str(sub.value)
    with pytest.raises(RuntimeError) as can:
        kabu.cancel_order("x")
    assert "HARD_FAIL" in str(can.value)


def _spawn_e2e(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    leftover: bool,
    stage: str,
) -> dict[str, Any]:
    from small_paper.kabu_token_authority import TOKEN_STAGE_MATCH

    from small_paper.market_ingress_spawn import spawn_ingress_process, wait_ingress_online

    station = tmp_path / "station"
    station.mkdir()
    native = tmp_path / "native"
    (native / "src" / "small_paper").mkdir(parents=True)
    replay = tmp_path / "replay.jsonl"
    _replay_file(replay)
    port = _free_port()
    bus_port = _free_port()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), _KabuStub)
    httpd.post_token_count = 0  # type: ignore[attr-defined]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    trading_date = DAY
    monkeypatch.setenv("KABU_STATION_AUTHORITY_DIR", str(station))
    monkeypatch.setenv("KABU_TOKEN_AUTHORITY_DIR", str(station))
    monkeypatch.setenv("KABU_API_BASE", f"http://127.0.0.1:{port}/kabusapi")
    monkeypatch.setenv("KABU_API_PASSWORD", "e2e-password")
    monkeypatch.setenv(ENV_CERT_MODE, "1")
    monkeypatch.setenv(ENV_KABU_AUTH_MODE, "LIVE")
    monkeypatch.setenv(ENV_CERTIFICATION_RUN_ID, "cert_e2e_v26b")
    monkeypatch.setenv(ENV_STAGE_RUN_ID, stage)
    monkeypatch.setenv("TRADEBOT_INGRESS_REPLAY_PATH", str(replay))
    monkeypatch.setenv("TRADEBOT_INGRESS_REPLAY_MAX_EPS", "8")
    monkeypatch.setenv("TRADEBOT_MARKET_BUS_PORT", str(bus_port))
    monkeypatch.setenv("TRADEBOT_TRADING_DATE", trading_date)
    monkeypatch.delenv("TRADEBOT_SESSION_CLOCK", raising=False)
    monkeypatch.delenv("KABU_TOKEN_PREFLIGHT", raising=False)
    if leftover:
        _write_dead_owner(station, pid=2147483646, token="leftover-previous-token")
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
        assert bundle.get("token") == "v26b-current-stage-token"
        if leftover:
            assert bundle.get("token") != "leftover-previous-token"
        got = acquire_token_for_readonly(
            native_root=native, trading_date=trading_date, caller="verify_kabu_connection"
        )
        assert got["token_stage_class"] == TOKEN_STAGE_MATCH
        assert got["token"] == "v26b-current-stage-token"
        audit = station_issue_audit_summary()
        return {
            "pid": pid,
            "wait": wait,
            "audit": audit,
            "http_posts": int(httpd.post_token_count),  # type: ignore[attr-defined]
            "generation": int(bundle.get("generation") or 0),
        }
    finally:
        _stop_pid(pid)
        httpd.shutdown()
        set_auth_phase("TEARDOWN")
        time.sleep(0.4)


def test_case_a_clean_production_startup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = _spawn_e2e(tmp_path, monkeypatch, leftover=False, stage="full_day_v26b_a")
    assert out["http_posts"] >= 1
    assert int(out["audit"].get("post_token_http_attempt_count") or 0) >= 1
    assert int(out["audit"].get("post_token_success_count") or 0) >= 1
    assert int(out["audit"].get("post_token_call_attempt_count") or 0) >= 1


def test_case_b_dead_owner_production_startup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = _spawn_e2e(tmp_path, monkeypatch, leftover=True, stage="full_day_v26b_b")
    assert out["http_posts"] >= 1
    assert out["generation"] != 34
    assert int(out["audit"].get("post_token_success_count") or 0) >= 1
