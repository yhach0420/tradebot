"""20260824 OPVAL startup repair: TRANSPORT_FAILURE probe retry + owned Ingress cleanup.

Does not change Strategy / ENTRY / EXIT / Universe selection.
submit/cancel/live remains 0/0/0.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from api.rest_client import KabuNativeApiError
from small_paper.capture_child_cleanup import CleanupResult, OwnedCaptureProcess
from small_paper.market_ingress_spawn import spawn_ingress_process
from small_paper.pre_freeze_kabu_validation import (
    AUTH_NOT_READY,
    INVALID_SYMBOL,
    RATE_LIMIT,
    TRANSPORT_FAILURE,
    VALID_SYMBOL,
    live_board_probe,
    select_valid50_from_ranked,
)
from small_paper.v1r_pbv2_duplicate_runtime import VERDICT

NATIVE = Path(__file__).resolve().parents[1]
LAUNCHER = NATIVE / "scripts" / "run_paper_trade_opval.py"


def _load_opval():
    spec = importlib.util.spec_from_file_location("run_paper_trade_opval", LAUNCHER)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def opval():
    return _load_opval()


def _ranked(n: int = 60) -> list[str]:
    return [f"{1000 + i:04d}" for i in range(n)]


def _owned(
    *,
    pid: int = 4242,
    start: str = "start-abc",
    nonce: str = "nonce-x",
    run_id: str = "ingrun_x",
) -> OwnedCaptureProcess:
    return OwnedCaptureProcess(
        pid=pid,
        cmd=["python", "-m", "small_paper.market_ingress_service"],
        native_root=str(NATIVE),
        cmdline_fingerprint="python -m small_paper.market_ingress_service",
        process_start_identity=start,
        ingress_run_id=run_id,
        launch_nonce=nonce,
        output_dir=str(NATIVE / "data" / "market_capture" / "20260824"),
        trading_date="20260824",
    )


def test_a_first_transport_failure_second_success_continues(monkeypatch: pytest.MonkeyPatch) -> None:
    import small_paper.pre_freeze_kabu_validation as mod

    monkeypatch.setattr(mod, "_throttle_live_probe", lambda: None)
    sleeps: list[float] = []
    monkeypatch.setattr(mod.time, "sleep", lambda s: sleeps.append(float(s)))
    calls = {"n": 0}

    class _Client:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def get_board(self, key: str, token: str = "") -> dict[str, Any]:
            calls["n"] += 1
            if calls["n"] == 1:
                raise KabuNativeApiError("connection reset by peer", failure_class="TRANSPORT")
            return {"Symbol": key}

    monkeypatch.setattr("api.rest_client.KabuNativeRestClient", _Client)
    monkeypatch.setattr("api.rest_client.default_base_url", lambda: "http://127.0.0.1:18080")
    got = live_board_probe("285A", token="tok")
    assert got["ok"] is True
    assert got["verdict"] == VALID_SYMBOL
    assert got["attempt"] == 2
    assert got["max_attempts"] == 3
    assert calls["n"] == 2
    assert sleeps == [1.0]


def test_b_three_transport_failures_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    import small_paper.pre_freeze_kabu_validation as mod

    monkeypatch.setattr(mod, "_throttle_live_probe", lambda: None)
    sleeps: list[float] = []
    monkeypatch.setattr(mod.time, "sleep", lambda s: sleeps.append(float(s)))

    class _Client:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def get_board(self, key: str, token: str = "") -> dict[str, Any]:
            raise KabuNativeApiError("timed out", failure_class="TRANSPORT")

    monkeypatch.setattr("api.rest_client.KabuNativeRestClient", _Client)
    monkeypatch.setattr("api.rest_client.default_base_url", lambda: "http://127.0.0.1:18080")
    secret = "super-secret-token"
    got = live_board_probe("285A", token=secret)
    assert got["ok"] is False
    assert got["verdict"] == TRANSPORT_FAILURE
    assert got["attempt"] == 3
    assert got["max_attempts"] == 3
    assert got["error_type"] == "KabuNativeApiError"
    assert "timed out" in str(got.get("error_message") or "")
    assert secret not in str(got.get("error_message") or "")
    assert sleeps == [1.0, 2.0]
    ranked = _ranked(60)

    def probe(sym: str) -> dict[str, Any]:
        if str(sym) == ranked[0]:
            return dict(got)
        return {"ok": True, "verdict": VALID_SYMBOL}

    closed = select_valid50_from_ranked(ranked, probe_fn=probe)
    assert closed["ok"] is False
    assert closed["fail_closed"] is True
    assert closed["reason"] == TRANSPORT_FAILURE
    assert closed["error_type"] == "KabuNativeApiError"
    assert closed["attempt"] == 3


def test_c_invalid_symbol_does_not_retry_keeps_refill(monkeypatch: pytest.MonkeyPatch) -> None:
    import small_paper.pre_freeze_kabu_validation as mod

    monkeypatch.setattr(mod, "_throttle_live_probe", lambda: None)
    sleeps: list[float] = []
    monkeypatch.setattr(mod.time, "sleep", lambda s: sleeps.append(float(s)))
    calls = {"n": 0}

    class _Client:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def get_board(self, key: str, token: str = "") -> dict[str, Any]:
            calls["n"] += 1
            raise KabuNativeApiError("symbol not found", kabu_code="4002001", http_status=400)

    monkeypatch.setattr("api.rest_client.KabuNativeRestClient", _Client)
    monkeypatch.setattr("api.rest_client.default_base_url", lambda: "http://127.0.0.1:18080")
    got = live_board_probe("9999", token="tok")
    assert got["verdict"] == INVALID_SYMBOL
    assert got["ok"] is False
    assert got["attempt"] == 1
    assert calls["n"] == 1
    assert sleeps == []
    ranked = _ranked(60)
    invalid = {ranked[0]}

    def probe(sym: str) -> dict[str, Any]:
        if str(sym) in invalid:
            return {"ok": False, "verdict": INVALID_SYMBOL, "kabu_code": "4002001"}
        return {"ok": True, "verdict": VALID_SYMBOL}

    selected = select_valid50_from_ranked(ranked, probe_fn=probe)
    assert selected["ok"] is True
    assert ranked[0] not in selected["valid_symbols"]
    assert selected["valid_symbols"] == [s for s in ranked if s not in invalid][:50]
    assert selected["refill_attempt_count"] == 1
    assert selected["refill_success_count"] == 1


def test_d_auth_not_ready_fail_closed_no_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    import small_paper.pre_freeze_kabu_validation as mod

    monkeypatch.setattr(mod, "_throttle_live_probe", lambda: None)
    sleeps: list[float] = []
    monkeypatch.setattr(mod.time, "sleep", lambda s: sleeps.append(float(s)))
    calls = {"n": 0}

    class _Client:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def get_board(self, key: str, token: str = "") -> dict[str, Any]:
            calls["n"] += 1
            raise KabuNativeApiError("unauthorized", http_status=401, kabu_code="4001007")

    monkeypatch.setattr("api.rest_client.KabuNativeRestClient", _Client)
    monkeypatch.setattr("api.rest_client.default_base_url", lambda: "http://127.0.0.1:18080")
    got = live_board_probe("7203", token="tok")
    assert got["verdict"] == AUTH_NOT_READY
    assert got["attempt"] == 1
    assert calls["n"] == 1
    assert sleeps == []
    ranked = _ranked(60)

    def probe(sym: str) -> dict[str, Any]:
        if str(sym) == ranked[0]:
            return {"ok": False, "verdict": AUTH_NOT_READY}
        return {"ok": True, "verdict": VALID_SYMBOL}

    auth = select_valid50_from_ranked(ranked, probe_fn=probe)
    assert auth["ok"] is False
    assert auth["fail_closed"] is True
    assert auth["reason"] == AUTH_NOT_READY
    assert ranked[1] not in auth["valid_symbols"]


def test_rate_limit_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    import small_paper.pre_freeze_kabu_validation as mod

    monkeypatch.setattr(mod, "_throttle_live_probe", lambda: None)
    sleeps: list[float] = []
    monkeypatch.setattr(mod.time, "sleep", lambda s: sleeps.append(float(s)))

    class _Client:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def get_board(self, key: str, token: str = "") -> dict[str, Any]:
            raise KabuNativeApiError("rate", http_status=429, kabu_code="4001006")

    monkeypatch.setattr("api.rest_client.KabuNativeRestClient", _Client)
    monkeypatch.setattr("api.rest_client.default_base_url", lambda: "http://127.0.0.1:18080")
    got = live_board_probe("7203", token="tok")
    assert got["verdict"] == RATE_LIMIT
    assert got["attempt"] == 1
    assert sleeps == []


def test_e_later_fail_cleans_only_owned_ingress(opval, monkeypatch: pytest.MonkeyPatch) -> None:
    owned = _owned()
    cleaned: list[int] = []

    def fake_query(pid: int) -> dict[str, Any]:
        if pid in cleaned:
            return {"pid": pid, "exists": False, "cmdline": ""}
        return {
            "pid": pid,
            "exists": True,
            "cmdline": "python -m small_paper.market_ingress_service",
            "create_time": "",
        }

    def fake_cleanup(rec: Any, *, reason: str, **kwargs: Any) -> CleanupResult:
        cleaned.append(int(rec.pid))
        return CleanupResult(shutdown_reason=reason, capture_pid=int(rec.pid), ownership_verified=True)

    monkeypatch.setattr("small_paper.capture_child_cleanup.query_process", fake_query)
    monkeypatch.setattr(
        "small_paper.capture_child_cleanup.verify_ownership",
        lambda rec, live=None: {"owned": True, "reason": "owned"},
    )
    monkeypatch.setattr("small_paper.capture_child_cleanup.cleanup_owned_capture", fake_cleanup)
    monkeypatch.setattr("small_paper.capture_child_cleanup.write_cleanup_artifact", lambda *a, **k: Path("x"))
    monkeypatch.setattr(
        "small_paper.ingress_run_identity.capture_process_start_identity",
        lambda pid, **k: "start-abc",
    )

    cl = opval.cleanup_owned_opval_ingress(
        owned,
        reason="opval_failure",
        paper_handoff=False,
        native_root=NATIVE,
        trading_date="20260824",
    )
    assert cleaned == [4242]
    assert cl["orphan_count"] == 0
    assert cl["killed_foreign"] is False
    assert cl["capture_left_running"] is False
    assert cl["startup_liveness_fix"] is True
    assert cl["failure_cleanup_fix"] is True

    monkeypatch.setattr(
        "small_paper.capture_child_cleanup.verify_ownership",
        lambda rec, live=None: {"owned": False, "reason": "ownership_mismatch"},
    )
    skipped = opval.cleanup_owned_opval_ingress(
        _owned(pid=7777, start="other-start", nonce="other-nonce", run_id="other-run"),
        reason="opval_failure",
        paper_handoff=False,
        native_root=NATIVE,
        trading_date="20260824",
    )
    assert skipped["skipped"] is True
    assert skipped["skip_reason"] == "ownership_mismatch"
    assert skipped["killed_foreign"] is False
    assert 7777 not in cleaned

    handed = opval.cleanup_owned_opval_ingress(owned, reason="opval_failure", paper_handoff=True)
    assert handed["skipped"] is True
    assert handed["skip_reason"] == "paper_ownership_handoff"


def test_f_existing_live_ingress_duplicate_guard_still_fails(tmp_path: Path, opval, monkeypatch: pytest.MonkeyPatch) -> None:
    live = [{"pid": 111, "kind": "ingress", "cmdline": "market_ingress_service --trading-date 20260824"}]
    with patch("small_paper.v1r_pbv2_duplicate_runtime.list_live_ingress", return_value=live):
        meta = spawn_ingress_process(native_root=tmp_path, trading_date="20260824", synthetic=False)
    assert meta.get("rejected") is True
    assert meta.get("reason") == VERDICT
    assert meta.get("pid") == 0

    monkeypatch.setattr(opval, "day_already_sealed", lambda *a, **k: {"sealed": False})
    monkeypatch.setattr(opval, "capture_day_dir", lambda *a, **k: tmp_path)
    monkeypatch.setattr(
        "small_paper.capture_child_cleanup.prepare_day_dir_operator_stop_for_spawn",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "small_paper.market_ingress_spawn.spawn_ingress_process",
        lambda **kwargs: {"rejected": True, "reason": VERDICT, "pid": 0},
    )
    out = opval.spawn_live_capture(native_root=tmp_path, trading_date="20260824", python_exe="python")
    assert out["ok"] is False
    assert VERDICT in str(out.get("reason") or "")
    assert not out.get("owned")
    assert int(out.get("pid") or 0) == 0
