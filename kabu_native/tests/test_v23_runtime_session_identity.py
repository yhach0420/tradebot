"""V23: current-run seal scope, session-clock trading date, stage token, input gate."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from small_paper.certification_input_coverage import (
    CERTIFICATION_INPUT_COVERAGE_FAIL,
    CERTIFICATION_ONLY_INPUT,
    build_full_day_certification_stream,
    evaluate_full_day_input_coverage,
)
from small_paper.day_fixed_am_registration import freeze_same_day_am_universe
from small_paper.ingress_run_identity import ENV_CERTIFICATION_RUN_ID, ENV_STAGE_RUN_ID
from small_paper.kabu_token_authority import (
    STALE_STAGE_TOKEN_REJECTED,
    StaleStageTokenRejected,
    acquire_token_for_readonly,
    issue_station_token,
    load_station_bundle,
    owner_issue_context,
)
from small_paper.paper_trade_checked_runner import (
    PaperTradeCheckedRunner,
    qualify_snapshot_path,
    resolve_session_artifact_paths,
    write_live_forward_session_fixture,
    write_qualified_session_fixture,
)
from small_paper.runtime_clock import ENV_CERT_MODE, ENV_ENABLED, ENV_V0
from small_paper.safety import SafetyCheck, parse_kabu_http_diagnostic, safety_failure_diagnostic_payload
from small_paper.session_runtime_identity import (
    RUNTIME_TRADING_DATE_NOT_PROVEN,
    RuntimeTradingDateNotProven,
    expected_current_run_scope,
    iter_current_run_soak_snapshots,
    resolve_runtime_trading_date,
    stamp_session_identity,
)
from small_paper.v1r_activation_binding import RUNTIME_DEPENDENCY_RELS
from small_paper.v1r_native_entry_live import resolve_day_fixed_am_runtime_universe
from small_paper.w4s_seal_propagation import finalize_session_seal_propagation, resolve_seal_path

JST = ZoneInfo("Asia/Tokyo")
DAY = "20260812"
NATIVE = Path(__file__).resolve().parents[1]
CFG = NATIVE / "configs" / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"


def _ok_runner(w4s_verdict: str = "READONLY_SOAK_IN_PROGRESS"):
    calls: list[str] = []

    def run(cmd, env, cwd):
        s = cmd if isinstance(cmd, str) else " ".join(str(x) for x in cmd)
        calls.append(s)
        if "phase687w4s" in s:
            return 0, json.dumps({"verdict": w4s_verdict, "aggregate": {"session_count": 1}}), ""
        return 0, "{}", ""

    return run, calls


def _am_syms(n: int = 50) -> list[str]:
    return [f"{1000 + i}" for i in range(n)]


def _write_am_csv(root: Path, day: str, symbols: list[str]) -> Path:
    import csv

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


def _push_line(ts: str, symbol: str = "1301") -> str:
    return json.dumps(
        {
            "Symbol": symbol,
            "received_at": ts,
            "CurrentPrice": 1000,
            "CurrentPriceTime": ts,
        },
        ensure_ascii=False,
    )


class _StubClient:
    def __init__(self, counter: Path, token: str) -> None:
        self.base_url = "http://localhost:18080/kabusapi"
        self.counter = counter
        self.token = token

    def post_token_http(self, api_password: str) -> str:
        n = int(self.counter.read_text(encoding="utf-8") or "0") + 1
        self.counter.write_text(str(n), encoding="utf-8")
        return self.token


def test_inventory_contains_v23_modules() -> None:
    assert "src/small_paper/session_runtime_identity.py" in RUNTIME_DEPENDENCY_RELS
    assert "src/small_paper/certification_input_coverage.py" in RUNTIME_DEPENDENCY_RELS


def test_a_historical_seals_excluded_from_current_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADEBOT_RUNTIME_RUN_ID", "rtrun_current_v23")
    monkeypatch.setenv("TRADEBOT_CERTIFICATION_RUN_ID", "cert_cur")
    monkeypatch.setenv("TRADEBOT_CERT_STAGE_RUN_ID", "stage_cur")
    monkeypatch.setenv("TRADEBOT_TRADING_DATE", DAY)
    hist_root = tmp_path / "results" / "small_paper" / DAY
    for i in range(44):
        write_qualified_session_fixture(hist_root / f"hist_{i:02d}", session_id=f"H{i}")
        ident = hist_root / f"hist_{i:02d}" / "session_identity.json"
        body = json.loads(ident.read_text(encoding="utf-8"))
        body["runtime_run_id"] = "rtrun_historical"
        body["certification_run_id"] = "cert_old"
        ident.write_text(json.dumps(body), encoding="utf-8")
        seal = json.loads((hist_root / f"hist_{i:02d}" / "session_seal.json").read_text(encoding="utf-8"))
        seal["runtime_run_id"] = "rtrun_historical"
        (hist_root / f"hist_{i:02d}" / "session_seal.json").write_text(json.dumps(seal), encoding="utf-8")
    cur = write_live_forward_session_fixture(hist_root / "live_current", session_id="CUR")
    qcur = qualify_snapshot_path(cur, paper_exit_code=0)
    assert qcur["seal_qualified"] is True, qcur.get("failures")
    expected = expected_current_run_scope(trading_date=DAY)
    snaps = iter_current_run_soak_snapshots(tmp_path / "results", expected=expected)
    assert len(snaps) == 1
    assert snaps[0] == cur
    run, _ = _ok_runner()
    r = PaperTradeCheckedRunner(native_root=tmp_path, run_command=run, skip_paper=True, skip_w4s=True, config_path=CFG)
    r.paper_exit_code = 0
    post = r.step_post_session(paper_ok=True)
    assert post["historical_seal_included_count"] == 0
    assert post["current_run_snapshot_count"] == 1
    assert len(post.get("am_pm_sessions") or []) == 1
    assert post["am_pm_sessions"][0].get("seal_qualified") is True


def test_b_root_seal_sot_not_safety_incomplete(tmp_path: Path) -> None:
    root = tmp_path / "sess"
    snap = write_live_forward_session_fixture(root, session_id="ROOT")
    safety_seal = root / "live_order_safety" / "session_seal.json"
    safety_seal.write_text(
        json.dumps(
            {
                "session_seal_status": "INCOMPLETE",
                "entry_count": 7,
                "required_count": 14,
                "required_artifact_missing_count": 7,
            }
        ),
        encoding="utf-8",
    )
    paths = resolve_session_artifact_paths(snap)
    assert paths["seal"] == root / "session_seal.json"
    assert resolve_seal_path(root, safety_dir=root / "live_order_safety") == root / "session_seal.json"
    q = qualify_snapshot_path(snap, paper_exit_code=0)
    assert q["fields"]["session_seal_status"] == "SEALED_VALID"
    assert q["fields"]["session_seal_entry_count"] == 14


def test_c_current_root_incomplete_fails(tmp_path: Path) -> None:
    root = tmp_path / "bad"
    snap = write_live_forward_session_fixture(root, session_id="BAD")
    (root / "session_seal.json").write_text(
        json.dumps(
            {
                "session_seal_status": "INCOMPLETE",
                "entry_count": 7,
                "required_count": 14,
                "required_artifact_missing_count": 7,
                "session_seal_verified": False,
            }
        ),
        encoding="utf-8",
    )
    q = qualify_snapshot_path(snap, paper_exit_code=0)
    assert q["seal_qualified"] is False
    assert q["forward_qualified"] is False


def test_d_wall_20260815_session_20260812_resolves_frozen50(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_CERT_MODE, "1")
    monkeypatch.setenv(ENV_ENABLED, "1")
    monkeypatch.setenv(ENV_V0, "2026-08-12T08:50:00+09:00")
    monkeypatch.setenv("TRADEBOT_SESSION_CLOCK_SPEED", "48")
    monkeypatch.setenv("TRADEBOT_TRADING_DATE", "20260815")
    assert resolve_runtime_trading_date() == DAY
    syms = _am_syms(50)
    csv_path = _write_am_csv(tmp_path, DAY, syms)
    frozen = freeze_same_day_am_universe(tmp_path, DAY, symbols=syms, source_path=str(csv_path))
    assert frozen.get("ok") is True
    resolved = resolve_day_fixed_am_runtime_universe(native_root=tmp_path, trading_date=None)
    assert resolved["trading_date"] == DAY
    assert resolved.get("ok") is True
    assert int(resolved.get("symbol_count") or 0) == 50
    assert "EMPTY_UNIVERSE" not in str(resolved.get("reason") or "")


def test_d_fail_closed_when_cert_date_unproven(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_CERT_MODE, "1")
    monkeypatch.delenv("TRADEBOT_TRADING_DATE", raising=False)
    monkeypatch.delenv(ENV_ENABLED, raising=False)
    monkeypatch.delenv(ENV_V0, raising=False)
    with pytest.raises(RuntimeTradingDateNotProven):
        resolve_runtime_trading_date()


def test_e_summary_identity_stamp_exact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADEBOT_CERTIFICATION_RUN_ID", "cert_e")
    monkeypatch.setenv("TRADEBOT_CERT_STAGE_RUN_ID", "stage_e")
    monkeypatch.setenv("TRADEBOT_RUNTIME_RUN_ID", "rtrun_e")
    monkeypatch.setenv("TRADEBOT_DAILY_RUN_ID", "daily_e")
    monkeypatch.setenv("TRADEBOT_INGRESS_RUN_ID", "ing_e")
    monkeypatch.setenv("TRADEBOT_INGRESS_LAUNCH_NONCE", "nonce_e")
    monkeypatch.setenv("TRADEBOT_TRADING_DATE", DAY)
    doc = stamp_session_identity({}, session_id="live_session_e", session_kind="am", trading_date=DAY)
    for key in (
        "certification_run_id",
        "stage_run_id",
        "activation_id",
        "activation_sha",
        "runtime_run_id",
        "daily_run_id",
        "session_id",
        "session_kind",
        "trading_date",
        "ingress_run_id",
        "launch_nonce",
    ):
        assert doc.get(key) not in (None, ""), key
    assert doc["trading_date"] == DAY
    assert doc["session_id"] == "live_session_e"


def test_f_stage_a_token_not_reused_by_b(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KABU_STATION_AUTHORITY_DIR", str(tmp_path))
    monkeypatch.setenv("KABU_TOKEN_AUTHORITY_DIR", str(tmp_path))
    monkeypatch.setenv("KABU_AUTH_MODE", "LIVE")
    monkeypatch.setenv(ENV_CERT_MODE, "1")
    monkeypatch.delenv("KABU_TOKEN_PREFLIGHT", raising=False)
    monkeypatch.setenv(ENV_CERTIFICATION_RUN_ID, "cert_tok")
    monkeypatch.setenv(ENV_STAGE_RUN_ID, "stage_a")
    count = tmp_path / "post_count.txt"
    count.write_text("0", encoding="utf-8")
    client_a = _StubClient(count, "tok-a")
    with owner_issue_context(
        native_root=tmp_path,
        trading_date=DAY,
        pid=os.getpid(),
        session_id="ing_a",
        caller="ingress_connect",
    ):
        tok = issue_station_token(client_a, "pw", caller="ingress_connect")
    assert tok == "tok-a"
    assert count.read_text(encoding="utf-8") == "1"
    bundle_a = load_station_bundle()
    assert bundle_a["stage_run_id"] == "stage_a"
    monkeypatch.setenv(ENV_STAGE_RUN_ID, "stage_b")
    with pytest.raises(StaleStageTokenRejected) as exc:
        acquire_token_for_readonly(native_root=tmp_path, trading_date=DAY, caller="window_b_safety")
    assert STALE_STAGE_TOKEN_REJECTED in str(exc.value)
    owner = tmp_path / "kabu_station_owner.json"
    body = json.loads(owner.read_text(encoding="utf-8"))
    body["pid"] = 2147483646
    owner.write_text(json.dumps(body), encoding="utf-8")
    client_b = _StubClient(count, "tok-b")
    with owner_issue_context(
        native_root=tmp_path,
        trading_date=DAY,
        pid=os.getpid(),
        session_id="ing_b",
        caller="ingress_connect",
    ):
        tok_b = issue_station_token(client_b, "pw", caller="ingress_connect")
    assert tok_b == "tok-b"
    assert count.read_text(encoding="utf-8") == "2"
    bundle_b = load_station_bundle()
    assert bundle_b["stage_run_id"] == "stage_b"
    assert bundle_b["generation"] > bundle_a["generation"]
    got = acquire_token_for_readonly(native_root=tmp_path, trading_date=DAY, caller="window_b_safety")
    assert got["token"] == "tok-b"
    monkeypatch.setenv(ENV_STAGE_RUN_ID, "stage_c")
    with pytest.raises(StaleStageTokenRejected):
        acquire_token_for_readonly(native_root=tmp_path, trading_date=DAY, caller="window_c_safety")
    body = json.loads(owner.read_text(encoding="utf-8"))
    body["pid"] = 2147483645
    owner.write_text(json.dumps(body), encoding="utf-8")
    client_c = _StubClient(count, "tok-c")
    with owner_issue_context(
        native_root=tmp_path,
        trading_date=DAY,
        pid=os.getpid(),
        session_id="ing_c",
        caller="ingress_connect",
    ):
        tok_c = issue_station_token(client_c, "pw", caller="ingress_connect")
    assert tok_c == "tok-c"
    assert count.read_text(encoding="utf-8") == "3"
    got_c = acquire_token_for_readonly(native_root=tmp_path, trading_date=DAY, caller="window_c_safety")
    assert got_c["token"] == "tok-c"


def test_g_partial_capture_full_day_gate_fail(tmp_path: Path) -> None:
    src = tmp_path / "partial.jsonl"
    lines = []
    for m in range(0, 120):
        ts = datetime(2026, 8, 12, 9, m // 60, m % 60, tzinfo=JST).isoformat(timespec="milliseconds")
        lines.append(_push_line(ts))
    src.write_text("\n".join(lines) + "\n", encoding="utf-8")
    dest = tmp_path / "out.jsonl"
    cov = build_full_day_certification_stream([src], dest, trading_date=DAY)
    assert cov["ok"] is False
    assert cov["code"] == CERTIFICATION_INPUT_COVERAGE_FAIL
    assert cov["coverage_pm"] is False
    gate = evaluate_full_day_input_coverage(dest, trading_date=DAY)
    assert gate["ok"] is False


def test_h_complete_certification_stream_coverage_pass(tmp_path: Path) -> None:
    src = tmp_path / "full.jsonl"
    lines: list[str] = []
    t = datetime(2026, 8, 12, 8, 50, tzinfo=JST)
    end = datetime(2026, 8, 12, 15, 35, tzinfo=JST)
    while t <= end:
        lines.append(_push_line(t.isoformat(timespec="milliseconds"), symbol="1301"))
        t = t + timedelta(seconds=20)
    src.write_text("\n".join(lines) + "\n", encoding="utf-8")
    dest = tmp_path / "full_out.jsonl"
    cov = build_full_day_certification_stream([src], dest, trading_date=DAY)
    assert cov["purpose"] == CERTIFICATION_ONLY_INPUT
    assert cov["ok"] is True
    assert cov["coverage_pm"] is True
    assert cov["anchors_16"] is True
    assert cov["session_close"] is True


def test_i_pm_direct_root_seal_generated(tmp_path: Path) -> None:
    root = tmp_path / "live_session_pm_direct"
    write_live_forward_session_fixture(root, session_id="PMD")
    assert (root / "session_seal.json").is_file()
    prop = finalize_session_seal_propagation(root, session_id="PMD", skip_if_locked=True)
    assert (root / "session_seal.json").is_file()
    seal = json.loads((root / "session_seal.json").read_text(encoding="utf-8"))
    assert seal.get("session_seal_status") in {"SEALED_VALID", "INCOMPLETE", "SEALED"}
    assert resolve_seal_path(root) == root / "session_seal.json"
    if not (root / "session_seal.json").is_file():
        raise AssertionError("PM_DIRECT_ROOT_SEAL_MISSING")


def test_j_window_a_failure_diagnostic_payload() -> None:
    chk = SafetyCheck(
        "kabu_station_connection",
        False,
        "board failed HTTP 401 url='http://localhost:18080/kabusapi/board/285A@1' body='{\"Code\":4001007,\"Message\":\"ログイン認証エラー\"}'",
        {"root_cause": "kabu_station_unreachable", "exception_class": "KabuNativeApiError"},
    )
    parsed = parse_kabu_http_diagnostic(chk.message)
    assert parsed["http_status"] == 401
    assert parsed["kabu_code"] == "4001007"
    payload = safety_failure_diagnostic_payload(chk, exception=RuntimeError("boom"))
    assert payload["check_id"] == "kabu_station_connection"
    assert payload["http_status"] == 401
    assert payload["kabu_code"] == "4001007"
    assert payload["kabu_message"]
    assert payload["stderr_exception_class"] == "RuntimeError"
    assert "token" not in json.dumps(payload).lower() or payload.get("token_fingerprint") != "secret"
    assert "password" not in json.dumps(payload).lower()
