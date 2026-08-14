"""Phase687W4T — Kabu Station token + read-only readiness diagnostics.

Never logs passwords, tokens, account numbers, or Authorization headers.
Never calls submit / cancel / flatten.
"""

from __future__ import annotations

import os
import re
import socket
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Optional
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

# Retry policy (explicit; no infinite retry)
TOKEN_MAX_RETRIES = 3
STATION_MAX_RETRIES = 2
TIMEOUT_MAX_RETRIES = 2
RETRY_BACKOFF_BASE_SEC = 0.4
AUTH_FAILED_RETRYABLE = False

EXIT_READONLY_READY = 0
EXIT_STATION_OR_TOKEN_NOT_READY = 2
EXIT_AUTH_OR_CONFIG_ERROR = 3
EXIT_RESPONSE_INVALID = 4
EXIT_SAFETY_INVARIANT_FAILED = 5


class TokenProbeStatus(str, Enum):
    CLIENT_NOT_CONFIGURED = "CLIENT_NOT_CONFIGURED"
    KABU_STATION_NOT_RUNNING = "KABU_STATION_NOT_RUNNING"
    PORT_UNREACHABLE = "PORT_UNREACHABLE"
    API_PASSWORD_MISSING = "API_PASSWORD_MISSING"
    AUTH_FAILED = "AUTH_FAILED"
    TOKEN_ENDPOINT_TIMEOUT = "TOKEN_ENDPOINT_TIMEOUT"
    TOKEN_RESPONSE_INVALID = "TOKEN_RESPONSE_INVALID"
    TOKEN_REQUEST_FAILED = "TOKEN_REQUEST_FAILED"  # last-resort only
    TOKEN_ACQUIRED = "TOKEN_ACQUIRED"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    TOKEN_EMPTY = "TOKEN_EMPTY"
    STATION_REACHABLE_AUTH_DEFERRED = "STATION_REACHABLE_AUTH_DEFERRED"
    READONLY_ENDPOINT_FAILED = "READONLY_ENDPOINT_FAILED"
    READONLY_ONLINE_VALID = "READONLY_ONLINE_VALID"
    READONLY_ONLINE_ZERO_BALANCE = "READONLY_ONLINE_ZERO_BALANCE"
    READONLY_ONLINE_NO_POSITIONS = "READONLY_ONLINE_NO_POSITIONS"
    READONLY_ONLINE_NO_ORDERS = "READONLY_ONLINE_NO_ORDERS"


_SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?password|password)\s*[:=]\s*\S+"),
    re.compile(r"(?i)(token|x-api-key|authorization)\s*[:=]\s*\S+"),
    re.compile(r"(?i)bearer\s+[a-z0-9\-._~+/]+=*"),
    re.compile(r"(?i)\"Token\"\s*:\s*\"[^\"]+\""),
    re.compile(r"(?i)\"APIPassword\"\s*:\s*\"[^\"]+\""),
    re.compile(r"\b\d{10,}\b"),  # likely account-ish long digits
)


def mask_secret_text(text: str, *, known_secrets: Optional[list[str]] = None) -> str:
    """Mask credential-like substrings for safe logs/artifacts."""
    if not text:
        return text
    out = str(text)
    for s in known_secrets or []:
        if s and s in out:
            out = out.replace(s, "<REDACTED>")
    for pat in _SECRET_PATTERNS:
        out = pat.sub("<REDACTED>", out)
    # Also use rest_client helper if available
    try:
        from api.rest_client import redact_secrets

        out = redact_secrets(out)
    except Exception:
        pass
    return out


def error_category(exc: BaseException) -> str:
    name = type(exc).__name__
    msg = mask_secret_text(str(exc)).lower()
    if "timeout" in msg or "timed out" in msg:
        return "timeout"
    if "401" in msg or "unauthorized" in msg or "password" in msg and "incorrect" in msg:
        return "auth"
    if "403" in msg:
        return "auth"
    if "connection" in msg or "refused" in msg or "10061" in msg:
        return "connection"
    if "json" in msg or "not json" in msg or "missing token" in msg:
        return "response_invalid"
    if "password" in msg and ("未設定" in str(exc) or "missing" in msg or "not set" in msg):
        return "password_missing"
    return name


@dataclass
class TokenDiagnostics:
    # Process-name detection is advisory only — never sole availability gate.
    station_process_detected: Optional[bool] = None
    station_running: Optional[bool] = None  # alias of station_process_detected (compat)
    api_port_reachable: bool = False
    port_reachable: bool = False  # alias of api_port_reachable (compat)
    token_endpoint_reachable: bool = False
    readonly_endpoint_reachable: bool = False
    operational_api_available: bool = False
    process_detection_warning: bool = False
    host: str = ""
    port: int = 0
    base_url: str = ""
    client_configured: bool = False
    password_configured: bool = False
    token_acquired: bool = False
    token_present: bool = False  # never store token value
    token_probe_status: str = TokenProbeStatus.CLIENT_NOT_CONFIGURED.value
    http_status: Optional[int] = None
    endpoint: str = ""
    exception_class: str = ""
    error_category: str = ""
    masked_error: str = ""
    latency_ms: Optional[float] = None
    retryable: bool = False
    retry_attempts: int = 0
    token_refresh_count: int = 0
    wallet_readable: bool = False
    positions_readable: bool = False
    orders_readable: bool = False
    executions_readable: bool = False
    account_status: str = ""
    buying_power_present: bool = False
    position_count: int = 0
    open_order_count: int = 0
    executions_count: int = 0
    readonly_successful_endpoint_count: int = 0
    submit_hard_fail: bool = False
    cancel_hard_fail: bool = False
    flatten_hard_fail: bool = False
    ready_for_soak: bool = False
    failure_reason: str = ""
    probed_at: str = ""
    no_secrets: bool = True

    def to_safe_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # belt-and-suspenders
        for k in list(d.keys()):
            if any(x in k.lower() for x in ("password", "token_value", "secret", "authorization")):
                if k not in (
                    "token_acquired",
                    "token_present",
                    "token_probe_status",
                    "token_refresh_count",
                    "password_configured",
                ):
                    d[k] = "<REDACTED>"
        d["masked_error"] = mask_secret_text(str(d.get("masked_error") or ""))
        d["no_secrets"] = True
        return d


def parse_host_port(base_url: str) -> tuple[str, int]:
    u = urlparse(base_url if "://" in base_url else f"http://{base_url}")
    host = u.hostname or "localhost"
    if u.port is not None:
        return host, int(u.port)
    # kabusapi local default
    if "18080" in base_url or (host in ("localhost", "127.0.0.1") and "kabusapi" in (u.path or base_url)):
        return host, 18080
    return host, 443 if u.scheme == "https" else 80


def check_port_reachable(host: str, port: int, *, timeout_sec: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_sec):
            return True
    except OSError:
        return False


def check_station_process() -> Optional[bool]:
    """Best-effort Windows/process check. None = unknown/unavailable."""
    try:
        import subprocess

        # Windows tasklist
        proc = subprocess.run(
            ["tasklist"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        text = (proc.stdout or "").lower()
        markers = ("kabustation", "kabuステーション", "kabusapi", "kabu station")
        if any(m in text for m in markers):
            return True
        # If tasklist worked but no marker — likely not running (or different name)
        if proc.returncode == 0 and text:
            return False
        return None
    except Exception:
        return None


def password_configured() -> bool:
    return bool(os.environ.get("KABU_API_PASSWORD", "").strip())


def classify_token_exception(exc: BaseException) -> tuple[TokenProbeStatus, bool, Optional[int]]:
    """Return (status, retryable, http_status_guess)."""
    msg = str(exc)
    low = msg.lower()
    http_status = None
    m = re.search(r"\b([45]\d\d)\b", msg)
    if m:
        http_status = int(m.group(1))
    cat = error_category(exc)
    if cat == "password_missing" or "KABU_API_PASSWORD" in msg:
        return TokenProbeStatus.API_PASSWORD_MISSING, False, http_status
    if cat == "timeout" or isinstance(exc, TimeoutError):
        return TokenProbeStatus.TOKEN_ENDPOINT_TIMEOUT, True, http_status
    if http_status == 401 or http_status == 403 or cat == "auth":
        return TokenProbeStatus.AUTH_FAILED, False, http_status
    if cat == "connection":
        return TokenProbeStatus.PORT_UNREACHABLE, True, http_status
    if cat == "response_invalid" or "missing token" in low or "not json" in low:
        return TokenProbeStatus.TOKEN_RESPONSE_INVALID, False, http_status
    return TokenProbeStatus.TOKEN_REQUEST_FAILED, True, http_status


def _readonly_or_owned_issue(rest: Any) -> Callable[[], str]:
    """Reuse Ingress shared token only. Never POST /token."""

    def _issue() -> str:
        from small_paper.kabu_token_authority import acquire_token_for_readonly
        from small_paper.runtime_clock import now_jst as session_now

        native = Path(__file__).resolve().parents[2]
        day = session_now().strftime("%Y%m%d")
        got = acquire_token_for_readonly(
            native_root=native,
            trading_date=day,
            caller="kabu_readonly_readiness",
            rest=rest,
        )
        return str(got.get("token") or "")

    return _issue


def acquire_token_with_policy(
    *,
    issue_fn: Callable[[], str],
    max_retries: int = TOKEN_MAX_RETRIES,
    backoff_sec: float = RETRY_BACKOFF_BASE_SEC,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> tuple[Optional[str], TokenDiagnostics]:
    """Acquire token with bounded retries. Never returns password; token only in memory briefly."""
    diag = TokenDiagnostics(probed_at=datetime.now(JST).isoformat(timespec="seconds"))
    last_exc: Optional[BaseException] = None
    for attempt in range(max(1, max_retries)):
        diag.retry_attempts = attempt + 1
        t0 = time.perf_counter()
        try:
            token = issue_fn()
            diag.latency_ms = round((time.perf_counter() - t0) * 1000.0, 3)
            if not token or not str(token).strip():
                diag.token_probe_status = TokenProbeStatus.TOKEN_EMPTY.value
                diag.failure_reason = "token_empty"
                diag.retryable = False
                return None, diag
            diag.token_acquired = True
            diag.token_present = True
            diag.token_probe_status = TokenProbeStatus.TOKEN_ACQUIRED.value
            diag.token_refresh_count = 1
            return str(token), diag
        except Exception as exc:
            last_exc = exc
            diag.latency_ms = round((time.perf_counter() - t0) * 1000.0, 3)
            status, retryable, http_status = classify_token_exception(exc)
            diag.token_probe_status = status.value
            diag.exception_class = type(exc).__name__
            diag.error_category = error_category(exc)
            diag.masked_error = mask_secret_text(str(exc))
            diag.http_status = http_status
            diag.retryable = bool(retryable)
            if status == TokenProbeStatus.AUTH_FAILED:
                diag.retryable = False
                diag.failure_reason = "auth_failed"
                break
            if status == TokenProbeStatus.API_PASSWORD_MISSING:
                diag.retryable = False
                diag.failure_reason = "api_password_missing"
                break
            if not diag.retryable or attempt + 1 >= max_retries:
                diag.failure_reason = status.value.lower()
                break
            if status == TokenProbeStatus.TOKEN_ENDPOINT_TIMEOUT and attempt + 1 >= TIMEOUT_MAX_RETRIES:
                diag.failure_reason = status.value.lower()
                break
            if status in (TokenProbeStatus.PORT_UNREACHABLE, TokenProbeStatus.KABU_STATION_NOT_RUNNING):
                if attempt + 1 >= STATION_MAX_RETRIES:
                    diag.failure_reason = status.value.lower()
                    break
            sleep_fn(backoff_sec * (2**attempt))
    if last_exc and not diag.failure_reason:
        diag.failure_reason = diag.token_probe_status.lower()
    return None, diag


def run_readonly_readiness_probe(
    *,
    load_env: bool = True,
    allow_live: bool = True,
) -> TokenDiagnostics:
    """Full readiness probe. Never submits orders. Never logs secrets."""
    from api.order_read_client import KabuOrderReadClient
    from api.rest_client import KabuNativeRestClient, default_base_url, load_kabu_env
    from small_paper.live_order_safety_sm import KabuBrokerAdapter

    if load_env:
        try:
            load_kabu_env(repo_root=Path(__file__).resolve().parents[3])
        except Exception:
            pass

    base = default_base_url()
    host, port = parse_host_port(base)
    diag = TokenDiagnostics(
        probed_at=datetime.now(JST).isoformat(timespec="seconds"),
        base_url=base,
        host=host,
        port=port,
        endpoint="/token",
    )
    diag.password_configured = password_configured()
    diag.station_process_detected = check_station_process()
    diag.station_running = diag.station_process_detected  # compat alias
    diag.api_port_reachable = check_port_reachable(host, port)
    diag.port_reachable = diag.api_port_reachable  # compat alias

    if not diag.api_port_reachable:
        # Process-name false alone must NOT classify as KABU_STATION_NOT_RUNNING
        # when we cannot distinguish "wrong process name" from "API down".
        # Only when port is unreachable AND process is explicitly false do we
        # use KABU_STATION_NOT_RUNNING; otherwise PORT_UNREACHABLE.
        if diag.station_process_detected is False:
            diag.token_probe_status = TokenProbeStatus.KABU_STATION_NOT_RUNNING.value
        else:
            diag.token_probe_status = TokenProbeStatus.PORT_UNREACHABLE.value
        diag.failure_reason = diag.token_probe_status.lower()
        diag.operational_api_available = False
        diag.ready_for_soak = False
        _assert_hard_fails(diag)
        return diag

    if not diag.password_configured:
        diag.token_probe_status = TokenProbeStatus.API_PASSWORD_MISSING.value
        diag.failure_reason = "api_password_missing"
        diag.client_configured = True
        _assert_hard_fails(diag)
        return diag

    try:
        rest = KabuNativeRestClient(base)
        diag.client_configured = True
    except Exception as exc:
        diag.client_configured = False
        diag.token_probe_status = TokenProbeStatus.CLIENT_NOT_CONFIGURED.value
        diag.exception_class = type(exc).__name__
        diag.masked_error = mask_secret_text(str(exc))
        diag.failure_reason = "client_not_configured"
        _assert_hard_fails(diag)
        return diag

    from small_paper.kabu_token_authority import TokenUnavailable, acquire_token_for_readonly
    from small_paper.runtime_clock import now_jst as session_now

    native = Path(__file__).resolve().parents[2]
    day = session_now().strftime("%Y%m%d")
    token = ""
    try:
        got = acquire_token_for_readonly(
            native_root=native,
            trading_date=day,
            caller="kabu_readonly_readiness",
            rest=rest,
        )
        token = str(got.get("token") or "")
        diag.token_acquired = bool(token)
        diag.token_present = bool(token)
        diag.token_probe_status = (
            TokenProbeStatus.TOKEN_ACQUIRED.value if token else TokenProbeStatus.STATION_REACHABLE_AUTH_DEFERRED.value
        )
    except TokenUnavailable:
        token = ""
        diag.token_acquired = False
        diag.token_present = False
        diag.token_probe_status = TokenProbeStatus.STATION_REACHABLE_AUTH_DEFERRED.value
        diag.failure_reason = "auth_deferred_until_ingress"
        diag.token_endpoint_reachable = True
        _assert_hard_fails(diag)
        return diag

    if not token:
        diag.token_probe_status = TokenProbeStatus.STATION_REACHABLE_AUTH_DEFERRED.value
        diag.failure_reason = "auth_deferred_until_ingress"
        diag.token_endpoint_reachable = True
        _assert_hard_fails(diag)
        return diag

    diag.token_endpoint_reachable = True

    read_client = KabuOrderReadClient(base)
    kabu = KabuBrokerAdapter(client=read_client, token=token)
    status = kabu.refresh_readonly()
    diag.account_status = status
    st = kabu.get_account_status()
    ok_endpoints = 0
    try:
        kabu.get_buying_power()
        diag.wallet_readable = True
        diag.buying_power_present = True
        ok_endpoints += 1
    except Exception as exc:
        diag.wallet_readable = False
        diag.masked_error = mask_secret_text(str(exc), known_secrets=[token])
        diag.exception_class = type(exc).__name__
    try:
        pos = kabu.get_positions()
        diag.positions_readable = True
        diag.position_count = len(pos)
        ok_endpoints += 1
    except Exception:
        diag.positions_readable = False
    try:
        orders = kabu.get_open_orders()
        diag.orders_readable = True
        diag.open_order_count = len(orders)
        ok_endpoints += 1
    except Exception:
        diag.orders_readable = False
    try:
        ex = kabu.get_recent_executions()
        diag.executions_readable = True
        diag.executions_count = len(ex)
        ok_endpoints += 1
    except Exception:
        diag.executions_readable = False
    diag.readonly_successful_endpoint_count = ok_endpoints
    diag.readonly_endpoint_reachable = ok_endpoints >= 1
    # Prefer token + read-only reachability over process-name detection.
    diag.operational_api_available = bool(
        diag.token_acquired and diag.readonly_endpoint_reachable
    )
    diag.process_detection_warning = bool(
        diag.station_process_detected is False and diag.operational_api_available
    )

    if ok_endpoints == 0:
        diag.token_probe_status = TokenProbeStatus.READONLY_ENDPOINT_FAILED.value
        diag.failure_reason = "readonly_endpoint_failed"
    elif status == "ONLINE_ZERO_BALANCE":
        diag.token_probe_status = TokenProbeStatus.READONLY_ONLINE_ZERO_BALANCE.value
    elif status in ("ONLINE_NO_POSITIONS", "MARKET_CLOSED_READ_AVAILABLE") and diag.position_count == 0:
        diag.token_probe_status = TokenProbeStatus.READONLY_ONLINE_NO_POSITIONS.value
    elif diag.open_order_count == 0 and diag.position_count > 0 and diag.wallet_readable:
        diag.token_probe_status = TokenProbeStatus.READONLY_ONLINE_NO_ORDERS.value
    elif st.get("online") and diag.wallet_readable:
        diag.token_probe_status = TokenProbeStatus.READONLY_ONLINE_VALID.value
    else:
        diag.token_probe_status = TokenProbeStatus.READONLY_ENDPOINT_FAILED.value
        diag.failure_reason = status or "readonly_not_online"

    _assert_hard_fails(diag)
    diag.ready_for_soak = bool(
        diag.operational_api_available
        and diag.submit_hard_fail
        and diag.cancel_hard_fail
        and diag.flatten_hard_fail
        and str(diag.token_probe_status).startswith("READONLY_ONLINE")
    )
    if diag.ready_for_soak:
        diag.failure_reason = ""
    return diag


def _assert_hard_fails(diag: TokenDiagnostics) -> None:
    from small_paper.live_order_safety_sm import KabuBrokerAdapter

    kabu = KabuBrokerAdapter()
    try:
        kabu.submit_entry_order({"symbol": "X", "quantity": 100})
        diag.submit_hard_fail = False
    except RuntimeError as exc:
        diag.submit_hard_fail = "HARD_FAIL" in str(exc)
    try:
        kabu.cancel_order("x")
        diag.cancel_hard_fail = False
    except RuntimeError as exc:
        diag.cancel_hard_fail = "HARD_FAIL" in str(exc)
    try:
        kabu.emergency_flatten()
        diag.flatten_hard_fail = False
    except RuntimeError as exc:
        diag.flatten_hard_fail = "HARD_FAIL" in str(exc)
    if not (diag.submit_hard_fail and diag.cancel_hard_fail and diag.flatten_hard_fail):
        diag.failure_reason = "safety_invariant_failed"


def readiness_exit_code(diag: TokenDiagnostics) -> int:
    if not (diag.submit_hard_fail and diag.cancel_hard_fail and diag.flatten_hard_fail):
        return EXIT_SAFETY_INVARIANT_FAILED
    st = diag.token_probe_status
    if diag.ready_for_soak or st.startswith("READONLY_ONLINE"):
        return EXIT_READONLY_READY
    if st == TokenProbeStatus.STATION_REACHABLE_AUTH_DEFERRED.value:
        return EXIT_READONLY_READY
    if st in (
        TokenProbeStatus.API_PASSWORD_MISSING.value,
        TokenProbeStatus.AUTH_FAILED.value,
        TokenProbeStatus.CLIENT_NOT_CONFIGURED.value,
    ):
        return EXIT_AUTH_OR_CONFIG_ERROR
    if st in (
        TokenProbeStatus.TOKEN_RESPONSE_INVALID.value,
        TokenProbeStatus.TOKEN_EMPTY.value,
    ):
        return EXIT_RESPONSE_INVALID
    return EXIT_STATION_OR_TOKEN_NOT_READY


def probe_summary_for_cli(diag: TokenDiagnostics) -> dict[str, Any]:
    return {
        "station_process_detected": diag.station_process_detected,
        "station_running": diag.station_running,  # compat alias
        "api_port_reachable": diag.api_port_reachable,
        "port_reachable": diag.port_reachable,  # compat alias
        "token_endpoint_reachable": diag.token_endpoint_reachable,
        "token_acquired": diag.token_acquired,
        "readonly_endpoint_reachable": diag.readonly_endpoint_reachable,
        "operational_api_available": diag.operational_api_available,
        "process_detection_warning": diag.process_detection_warning,
        "client_configured": diag.client_configured,
        "password_configured": diag.password_configured,
        "account_status": diag.account_status or diag.token_probe_status,
        "token_probe_status": diag.token_probe_status,
        "positions_readable": diag.positions_readable,
        "orders_readable": diag.orders_readable,
        "wallet_readable": diag.wallet_readable,
        "executions_readable": diag.executions_readable,
        "submit_hard_fail": diag.submit_hard_fail,
        "ready_for_soak": diag.ready_for_soak,
        "failure_reason": diag.failure_reason,
        "host": diag.host,
        "port": diag.port,
        "retry_attempts": diag.retry_attempts,
        "latency_ms": diag.latency_ms,
        "no_secrets": True,
    }
