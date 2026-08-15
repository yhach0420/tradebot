"""V24: current-stage token identity + deterministic certification delivery stream."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from small_paper.certification_input_coverage import (
    CERTIFICATION_INPUT_COVERAGE_FAIL,
    build_full_day_certification_stream,
    evaluate_full_day_input_coverage,
    inspect_certification_stream,
)
from small_paper.ingress_run_identity import (
    CURRENT_INGRESS_NOT_READY,
    evaluate_current_run_online,
)
from small_paper.kabu_token_authority import (
    BUNDLE_SCHEMA_VERSION,
    CURRENT_STAGE_TOKEN_IDENTITY_NOT_PROVEN,
    STALE_STAGE_TOKEN_REJECTED,
    TOKEN_STAGE_MATCH,
    TOKEN_STAGE_MISMATCH,
    TOKEN_STAGE_MISSING,
    TOKEN_STAGE_NOT_APPLICABLE,
    CurrentStageTokenIdentityNotProven,
    StaleStageTokenRejected,
    acquire_token_for_readonly,
    classify_token_stage,
    evaluate_issue_permission,
    issue_station_token,
    load_station_bundle,
    owner_issue_context,
    token_fingerprint,
)
from small_paper.market_ingress_service import MarketIngressService
from small_paper.runtime_clock import ENV_CERT_MODE, ENV_KABU_AUTH_MODE

JST = ZoneInfo("Asia/Tokyo")
DAY = "20260812"
NATIVE = Path(__file__).resolve().parents[1]


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


def _push_line(ts: str, symbol: str = "1301") -> str:
    return json.dumps({"Symbol": symbol, "received_at": ts, "CurrentPrice": 1000}, ensure_ascii=False)


def _full_day_source(path: Path) -> Path:
    lines: list[str] = []
    t = datetime(2026, 8, 12, 8, 50, tzinfo=JST)
    end = datetime(2026, 8, 12, 15, 35, tzinfo=JST)
    while t <= end:
        lines.append(_push_line(t.isoformat(timespec="milliseconds")))
        t = t + timedelta(seconds=20)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_a_unscoped_leftover_forces_new_generation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    count = _iso_station(tmp_path, monkeypatch)
    monkeypatch.setenv(ENV_CERT_MODE, "1")
    monkeypatch.setenv("TRADEBOT_CERTIFICATION_RUN_ID", "cert_a")
    monkeypatch.setenv("TRADEBOT_CERT_STAGE_RUN_ID", "full_day_aaa")
    leftover_pid = 14372
    _write_unscoped_gen34(tmp_path, pid=leftover_pid)
    monkeypatch.setattr(
        "small_paper.kabu_token_authority._pid_alive",
        lambda pid: int(pid) in {leftover_pid, os.getpid()},
    )
    with pytest.raises(CurrentStageTokenIdentityNotProven) as missing:
        acquire_token_for_readonly(native_root=tmp_path, trading_date=DAY, caller="verify_kabu_connection")
    assert CURRENT_STAGE_TOKEN_IDENTITY_NOT_PROVEN in str(missing.value)
    assert STALE_STAGE_TOKEN_REJECTED not in str(missing.value)
    classified = classify_token_stage(load_station_bundle())
    assert classified["class"] == TOKEN_STAGE_MISSING
    with owner_issue_context(
        native_root=tmp_path,
        trading_date=DAY,
        pid=os.getpid(),
        session_id="ing_current",
        caller="ingress_replay_connect",
    ):
        decision = evaluate_issue_permission(caller="ingress_replay_connect")
        assert decision["allowed"] is True
        assert decision["reason"] == "stale_stage_takeover"
        tok = issue_station_token(_StubClient(count, "tok-v24-a"), "pw")
    assert tok == "tok-v24-a"
    assert count.read_text(encoding="utf-8") == "1"
    bundle = load_station_bundle()
    assert bundle["generation"] == 35
    assert bundle["stage_run_id"] == "full_day_aaa"
    assert bundle["certification_run_id"] == "cert_a"
    assert bundle["bundle_schema_version"] == BUNDLE_SCHEMA_VERSION
    assert "token" in bundle
    got = acquire_token_for_readonly(native_root=tmp_path, trading_date=DAY, caller="verify_kabu_connection")
    assert got["token"] == "tok-v24-a"
    assert got["token_stage_class"] == TOKEN_STAGE_MATCH
    assert got["reused"] is True


def test_b_previous_stage_requires_new_issue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    count = _iso_station(tmp_path, monkeypatch)
    monkeypatch.setenv(ENV_CERT_MODE, "1")
    monkeypatch.setenv("TRADEBOT_CERTIFICATION_RUN_ID", "cert_b")
    monkeypatch.setenv("TRADEBOT_CERT_STAGE_RUN_ID", "stage_a")
    with owner_issue_context(
        native_root=tmp_path,
        trading_date=DAY,
        pid=os.getpid(),
        session_id="ing_a",
        caller="ingress_replay_connect",
    ):
        issue_station_token(_StubClient(count, "tok-a"), "pw")
    monkeypatch.setenv("TRADEBOT_CERT_STAGE_RUN_ID", "stage_b")
    with pytest.raises(StaleStageTokenRejected) as exc:
        acquire_token_for_readonly(native_root=tmp_path, trading_date=DAY, caller="window_b")
    assert STALE_STAGE_TOKEN_REJECTED in str(exc.value)
    assert classify_token_stage(load_station_bundle())["class"] == TOKEN_STAGE_MISMATCH
    owner = json.loads((tmp_path / "kabu_station_owner.json").read_text(encoding="utf-8"))
    owner["pid"] = 2147483646
    (tmp_path / "kabu_station_owner.json").write_text(json.dumps(owner), encoding="utf-8")
    with owner_issue_context(
        native_root=tmp_path,
        trading_date=DAY,
        pid=os.getpid(),
        session_id="ing_b",
        caller="ingress_replay_connect",
    ):
        tok = issue_station_token(_StubClient(count, "tok-b"), "pw")
    assert tok == "tok-b"
    assert count.read_text(encoding="utf-8") == "2"
    assert load_station_bundle()["stage_run_id"] == "stage_b"
    got = acquire_token_for_readonly(native_root=tmp_path, trading_date=DAY, caller="window_b")
    assert got["token"] == "tok-b"


def test_c_current_stage_exact_reuses_without_post(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    count = _iso_station(tmp_path, monkeypatch)
    monkeypatch.setenv(ENV_CERT_MODE, "1")
    monkeypatch.setenv("TRADEBOT_CERTIFICATION_RUN_ID", "cert_c")
    monkeypatch.setenv("TRADEBOT_CERT_STAGE_RUN_ID", "stage_c")
    with owner_issue_context(
        native_root=tmp_path,
        trading_date=DAY,
        pid=os.getpid(),
        session_id="ing_c",
        caller="ingress_replay_connect",
    ):
        issue_station_token(_StubClient(count, "tok-c"), "pw")
        again = issue_station_token(_StubClient(count, "tok-c2"), "pw")
    assert again == "tok-c"
    assert count.read_text(encoding="utf-8") == "1"
    got = acquire_token_for_readonly(native_root=tmp_path, trading_date=DAY, caller="safety")
    assert got["token"] == "tok-c"
    assert got["token_stage_class"] == TOKEN_STAGE_MATCH


def test_d_missing_mismatch_not_applicable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _iso_station(tmp_path, monkeypatch)
    monkeypatch.setenv(ENV_CERT_MODE, "1")
    monkeypatch.setenv("TRADEBOT_CERT_STAGE_RUN_ID", "stage_d")
    _write_unscoped_gen34(tmp_path, pid=os.getpid())
    assert classify_token_stage()["class"] == TOKEN_STAGE_MISSING
    with pytest.raises(CurrentStageTokenIdentityNotProven):
        acquire_token_for_readonly(native_root=tmp_path, trading_date=DAY, caller="board")
    monkeypatch.delenv("TRADEBOT_CERT_STAGE_RUN_ID", raising=False)
    monkeypatch.delenv(ENV_CERT_MODE, raising=False)
    assert classify_token_stage()["class"] == TOKEN_STAGE_NOT_APPLICABLE


def test_e_auth_failure_is_not_waiting_first_push(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_CERT_MODE, "1")
    monkeypatch.setenv(ENV_KABU_AUTH_MODE, "LIVE")
    monkeypatch.setenv("TRADEBOT_CERT_STAGE_RUN_ID", "stage_e")
    monkeypatch.delenv("KABU_TOKEN_PREFLIGHT", raising=False)
    monkeypatch.setattr(
        "small_paper.kabu_token_authority.live_kabu_auth_allowed",
        lambda synthetic=False: (False, "forced"),
    )
    svc = MarketIngressService(native_root=tmp_path, trading_date=DAY, synthetic=False)
    out = svc._replay_try_register()
    assert out.get("ok") is False
    assert out.get("fail_code") == "AUTH_FAILED"
    status = {
        "status_schema_version": "INGRESS_STATUS_CURRENT_RUN_V1",
        "activation_id": "A",
        "activation_sha": "s",
        "ingress_run_id": "ingrun",
        "launch_nonce": "nonce",
        "pid": 1,
        "process_start_identity": "start",
        "trading_date": DAY,
        "role": "MARKET_INGRESS_SERVICE",
        "bus_identity": "bus",
        "state": "WAITING_FIRST_PUSH",
        "status_written_unix": 1.0,
        "registered_symbol_count": 50,
        "token_stage_class": TOKEN_STAGE_MISSING,
        "register_put_ok": False,
    }
    monkeypatch.delenv("KABU_TOKEN_PREFLIGHT", raising=False)
    ev = evaluate_current_run_online(
        status,
        expected={
            "launch_nonce": "nonce",
            "ingress_run_id": "ingrun",
            "activation_id": "A",
            "activation_sha": "s",
            "trading_date": DAY,
            "bus_identity": "bus",
        },
        now_unix=1.0,
        require_registered_count=50,
        query_fn=lambda pid: {"exists": True, "create_time": "start"},
    )
    assert ev["ok"] is False
    assert ev["reject_code"] == "current_stage_token_not_ready"
    assert ev["reason"] == CURRENT_INGRESS_NOT_READY


def test_f_same_source_double_scan_fails(tmp_path: Path) -> None:
    src = _full_day_source(tmp_path / "one.jsonl")
    dest = tmp_path / "dup.jsonl"
    cov = build_full_day_certification_stream([src, src], dest, trading_date=DAY)
    assert cov["duplicate_source_count"] >= 1
    assert cov["unique_source_scan"] is False
    assert cov["ok"] is False
    assert cov["code"] == CERTIFICATION_INPUT_COVERAGE_FAIL
    assert "duplicate_source_scan" in cov["failures"]


def test_g_malformed_file_order_fails(tmp_path: Path) -> None:
    dest = tmp_path / "bad_order.jsonl"
    rows = [
        {"Symbol": "1301", "received_at": "2026-08-12T09:00:00+09:00", "cert_sequence": 1},
        {"Symbol": "1301", "received_at": "2026-08-12T08:50:00+09:00", "cert_sequence": 2},
        {"Symbol": "1301", "received_at": "2026-08-12T09:01:00+09:00", "cert_sequence": 3},
    ]
    dest.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    cov = inspect_certification_stream(dest, trading_date=DAY)
    assert cov["file_time_order_ok"] is False
    assert cov["file_time_backward_count"] >= 1
    assert cov["ok"] is False
    assert "file_time_order_ok" in cov["failures"]


def test_h_chronological_rebuild_pass(tmp_path: Path) -> None:
    src = _full_day_source(tmp_path / "full.jsonl")
    dest = tmp_path / "out.jsonl"
    cov = build_full_day_certification_stream([src], dest, trading_date=DAY)
    assert cov["ok"] is True
    assert cov["file_time_order_ok"] is True
    assert cov["file_time_backward_count"] == 0
    assert cov["cert_sequence_continuity_ok"] is True
    assert cov["cert_sequence_first"] == 1
    assert cov["cert_sequence_last"] == cov["parse_ok"]
    assert cov["cert_sequence_gap"] == 0
    assert cov["duplicate_source_count"] == 0
    assert cov["stream_is_complete_market_tape"] is False


def test_i_cert_sequence_delete_duplicate_backward_fail(tmp_path: Path) -> None:
    src = _full_day_source(tmp_path / "full.jsonl")
    dest = tmp_path / "seq.jsonl"
    build_full_day_certification_stream([src], dest, trading_date=DAY)
    lines = dest.read_text(encoding="utf-8").splitlines()
    objs = [json.loads(x) for x in lines if x.strip()]
    deleted = tmp_path / "deleted.jsonl"
    deleted.write_text("\n".join(json.dumps(x) for x in objs[1:]) + "\n", encoding="utf-8")
    d = inspect_certification_stream(deleted, trading_date=DAY)
    assert d["cert_sequence_continuity_ok"] is False
    swapped = tmp_path / "swap.jsonl"
    objs[1], objs[2] = objs[2], objs[1]
    swapped.write_text("\n".join(json.dumps(x) for x in objs) + "\n", encoding="utf-8")
    s = inspect_certification_stream(swapped, trading_date=DAY)
    assert s["cert_sequence_continuity_ok"] is False or s["file_time_order_ok"] is False
    duped = tmp_path / "dupseq.jsonl"
    objs2 = [json.loads(x) for x in lines if x.strip()]
    objs2[3]["cert_sequence"] = objs2[2]["cert_sequence"]
    duped.write_text("\n".join(json.dumps(x) for x in objs2) + "\n", encoding="utf-8")
    q = inspect_certification_stream(duped, trading_date=DAY)
    assert q["cert_sequence_duplicate"] >= 1
    assert q["ok"] is False


def test_j_time_backward_fail(tmp_path: Path) -> None:
    dest = tmp_path / "tb.jsonl"
    t0 = datetime(2026, 8, 12, 8, 50, tzinfo=JST)
    rows = []
    for i in range(120):
        ts = (t0 + timedelta(seconds=i * 30)).isoformat(timespec="seconds")
        rows.append({"Symbol": "1301", "received_at": ts, "cert_sequence": i + 1})
    rows[80]["received_at"] = (t0 + timedelta(seconds=10)).isoformat(timespec="seconds")
    dest.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    cov = inspect_certification_stream(dest, trading_date=DAY)
    assert cov["file_time_backward_count"] >= 1
    assert cov["ok"] is False


def test_k_paper_mode_unscoped_is_not_applicable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _iso_station(tmp_path, monkeypatch)
    monkeypatch.delenv(ENV_CERT_MODE, raising=False)
    monkeypatch.delenv("TRADEBOT_CERT_STAGE_RUN_ID", raising=False)
    _write_unscoped_gen34(tmp_path, pid=os.getpid())
    assert classify_token_stage()["class"] == TOKEN_STAGE_NOT_APPLICABLE
    got = acquire_token_for_readonly(native_root=tmp_path, trading_date=DAY, caller="paper_safety")
    assert got["token"] == "leftover-gen34"
    assert got["reused"] is True
