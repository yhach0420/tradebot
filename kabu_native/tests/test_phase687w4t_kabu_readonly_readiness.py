"""Phase687W4T — Kabu readonly readiness diagnostics tests."""

from __future__ import annotations

import json

import pytest

from small_paper.kabu_readonly_readiness import (
    EXIT_AUTH_OR_CONFIG_ERROR,
    EXIT_READONLY_READY,
    EXIT_SAFETY_INVARIANT_FAILED,
    EXIT_STATION_OR_TOKEN_NOT_READY,
    TOKEN_MAX_RETRIES,
    TokenDiagnostics,
    TokenProbeStatus,
    acquire_token_with_policy,
    classify_token_exception,
    mask_secret_text,
    parse_host_port,
    readiness_exit_code,
)
from small_paper.live_order_safety_sm import KabuBrokerAdapter


def test_mask_secret_text():
    raw = 'APIPassword=secret123 Token: abcdef Authorization: Bearer xyz "Token":"LIVESECRET"'
    masked = mask_secret_text(raw, known_secrets=["LIVESECRET", "secret123"])
    assert "secret123" not in masked
    assert "LIVESECRET" not in masked
    assert "Bearer xyz" not in masked or "REDACTED" in masked
    assert "REDACTED" in masked


def test_parse_host_port_default_kabusapi():
    host, port = parse_host_port("http://localhost:18080/kabusapi")
    assert host == "localhost"
    assert port == 18080


def test_classify_token_exceptions():
    st, retry, code = classify_token_exception(TimeoutError("timed out"))
    assert st == TokenProbeStatus.TOKEN_ENDPOINT_TIMEOUT
    assert retry is True
    st, retry, code = classify_token_exception(RuntimeError("HTTP 401 unauthorized"))
    assert st == TokenProbeStatus.AUTH_FAILED
    assert retry is False
    st, retry, _ = classify_token_exception(RuntimeError("KABU_API_PASSWORD が未設定です"))
    assert st == TokenProbeStatus.API_PASSWORD_MISSING
    st, retry, _ = classify_token_exception(RuntimeError("connection refused 10061"))
    assert st == TokenProbeStatus.PORT_UNREACHABLE
    st, retry, _ = classify_token_exception(RuntimeError("token response is not JSON"))
    assert st == TokenProbeStatus.TOKEN_RESPONSE_INVALID


def test_acquire_token_auth_no_retry():
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        raise RuntimeError("HTTP 401 unauthorized")

    sleeps: list[float] = []
    token, diag = acquire_token_with_policy(issue_fn=boom, sleep_fn=sleeps.append)
    assert token is None
    assert diag.token_probe_status == TokenProbeStatus.AUTH_FAILED.value
    assert calls["n"] == 1
    assert sleeps == []


def test_acquire_token_timeout_limited_retry():
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        raise TimeoutError("timeout")

    sleeps: list[float] = []
    token, diag = acquire_token_with_policy(
        issue_fn=boom, max_retries=TOKEN_MAX_RETRIES, sleep_fn=sleeps.append
    )
    assert token is None
    assert diag.token_probe_status == TokenProbeStatus.TOKEN_ENDPOINT_TIMEOUT.value
    assert calls["n"] <= TOKEN_MAX_RETRIES
    assert calls["n"] >= 1


def test_acquire_token_success_and_empty():
    token, diag = acquire_token_with_policy(issue_fn=lambda: "tok_ok", sleep_fn=lambda _x: None)
    assert token == "tok_ok"
    assert diag.token_probe_status == TokenProbeStatus.TOKEN_ACQUIRED.value
    token2, diag2 = acquire_token_with_policy(issue_fn=lambda: "  ", sleep_fn=lambda _x: None)
    assert token2 is None
    assert diag2.token_probe_status == TokenProbeStatus.TOKEN_EMPTY.value


def test_empty_positions_not_api_failure_and_hard_fail():
    # Adapter: empty client → CLIENT_NOT_CONFIGURED (not weekend)
    st = KabuBrokerAdapter().refresh_readonly()
    assert st == "CLIENT_NOT_CONFIGURED"
    with pytest.raises(RuntimeError, match="HARD_FAIL"):
        KabuBrokerAdapter().submit_entry_order({"symbol": "X"})
    with pytest.raises(RuntimeError, match="HARD_FAIL"):
        KabuBrokerAdapter().cancel_order("x")
    with pytest.raises(RuntimeError, match="HARD_FAIL"):
        KabuBrokerAdapter().emergency_flatten()


def test_exit_codes():
    d = TokenDiagnostics(
        submit_hard_fail=True,
        cancel_hard_fail=True,
        flatten_hard_fail=True,
        ready_for_soak=True,
        token_probe_status=TokenProbeStatus.READONLY_ONLINE_VALID.value,
    )
    assert readiness_exit_code(d) == EXIT_READONLY_READY
    d2 = TokenDiagnostics(
        submit_hard_fail=False,
        cancel_hard_fail=True,
        flatten_hard_fail=True,
        token_probe_status=TokenProbeStatus.READONLY_ONLINE_VALID.value,
    )
    assert readiness_exit_code(d2) == EXIT_SAFETY_INVARIANT_FAILED
    d3 = TokenDiagnostics(
        submit_hard_fail=True,
        cancel_hard_fail=True,
        flatten_hard_fail=True,
        token_probe_status=TokenProbeStatus.AUTH_FAILED.value,
    )
    assert readiness_exit_code(d3) == EXIT_AUTH_OR_CONFIG_ERROR
    d4 = TokenDiagnostics(
        submit_hard_fail=True,
        cancel_hard_fail=True,
        flatten_hard_fail=True,
        token_probe_status=TokenProbeStatus.PORT_UNREACHABLE.value,
    )
    assert readiness_exit_code(d4) == EXIT_STATION_OR_TOKEN_NOT_READY


def test_diagnostics_dict_has_no_secrets():
    d = TokenDiagnostics(masked_error='password=hunter2 Token: abc', password_configured=True)
    out = d.to_safe_dict()
    blob = json.dumps(out)
    assert "hunter2" not in blob
    assert out["no_secrets"] is True
    assert "password=" not in blob.lower() or "REDACTED" in blob
