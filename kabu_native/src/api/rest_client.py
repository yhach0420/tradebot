"""
kabu_native REST client for kabuステーション® API (local kabusapi).
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Mapping

import requests

DEFAULT_BASE_URL = "http://localhost:18080/kabusapi"
_ENV_PASSWORD = "KABU_API_PASSWORD"
_ENV_BASE = "KABU_API_BASE"
_RETRYABLE_HTTP = frozenset({502, 503, 504})
ENVIRONMENT_AUTH_BLOCKED = "ENVIRONMENT_AUTH_BLOCKED"
_KABU_CODE_RE = re.compile(r'"Code"\s*:\s*(\d+)')


class KabuNativeApiError(RuntimeError):
    """HTTP / network / malformed response from kabusapi."""

    def __init__(
        self,
        message: str,
        *args: Any,
        http_status: int | None = None,
        failure_class: str = "OTHER",
        kabu_code: str | None = None,
    ) -> None:
        super().__init__(message, *args) if args else super().__init__(message)
        self.http_status = http_status
        self.failure_class = str(failure_class or "OTHER")
        self.kabu_code = str(kabu_code or "") or None


class KabuNativeRestClient:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        timeout: float = 30.0,
        max_retries: int = 3,
        retry_backoff_sec: float = 0.5,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max(1, int(max_retries))
        self.retry_backoff_sec = max(0.0, float(retry_backoff_sec))

    def post_token_http(self, api_password: str) -> str:
        """HTTP POST /token. TokenAuthority only — consumers must not call this."""
        url = f"{self.base_url}/token"
        from small_paper.auth_issue_trace import (
            HTTP_ATTEMPT,
            HTTP_RESULT,
            POST_TOKEN_HTTP_BEGIN,
            POST_TOKEN_HTTP_RESULT,
            record_auth_issue_event,
        )

        record_auth_issue_event(POST_TOKEN_HTTP_BEGIN, result="begin", extra={"url_host": self.base_url})
        record_auth_issue_event(HTTP_ATTEMPT, result="begin", audit=True, extra={"url_host": self.base_url})
        try:
            response = self._request(
                "POST",
                url,
                json_body={"APIPassword": api_password},
                op="token issue",
            )
        except Exception as exc:
            status = getattr(exc, "http_status", None)
            klass = str(getattr(exc, "failure_class", "") or "")
            record_auth_issue_event(
                POST_TOKEN_HTTP_RESULT,
                result=klass or "error",
                exception=exc,
                http_status=status,
            )
            record_auth_issue_event(
                HTTP_RESULT,
                result=klass or "error",
                exception=exc,
                http_status=status,
                audit=True,
                allowed=False,
            )
            raise
        try:
            payload = response.json()
        except json.JSONDecodeError as e:
            err = KabuNativeApiError(
                f"token response is not JSON: {e}",
                http_status=int(response.status_code),
                failure_class="PARSE",
            )
            record_auth_issue_event(
                POST_TOKEN_HTTP_RESULT, result="parse_error", exception=err, http_status=response.status_code
            )
            record_auth_issue_event(
                HTTP_RESULT, result="parse_error", exception=err, http_status=response.status_code, audit=True, allowed=False
            )
            raise err from e
        token = payload.get("Token")
        if not token:
            err = KabuNativeApiError(
                f"token response missing Token field: {_safe_payload_repr(payload)}",
                http_status=int(response.status_code),
                failure_class="PARSE",
            )
            record_auth_issue_event(
                POST_TOKEN_HTTP_RESULT, result="missing_token", exception=err, http_status=response.status_code
            )
            record_auth_issue_event(
                HTTP_RESULT,
                result="missing_token",
                exception=err,
                http_status=response.status_code,
                audit=True,
                allowed=False,
            )
            raise err
        record_auth_issue_event(
            POST_TOKEN_HTTP_RESULT, result="ok", http_status=int(response.status_code)
        )
        record_auth_issue_event(
            HTTP_RESULT, result="ok", http_status=int(response.status_code), audit=True, allowed=True
        )
        return str(token)

    def issue_token(self, api_password: str) -> str:
        from small_paper.kabu_token_authority import issue_station_token

        return issue_station_token(self, api_password, caller="rest_client.issue_token")

    def issue_token_from_env(self) -> str:
        from small_paper.auth_issue_trace import (
            API_PASSWORD_RESOLVE_BEGIN,
            API_PASSWORD_RESOLVE_RESULT,
            password_present,
            record_auth_issue_event,
        )

        record_auth_issue_event(API_PASSWORD_RESOLVE_BEGIN, result="begin")
        present = password_present()
        try:
            password = require_kabu_password()
        except Exception as exc:
            record_auth_issue_event(
                API_PASSWORD_RESOLVE_RESULT,
                result="missing" if not present else "error",
                exception=exc,
                extra={"password_present": present},
            )
            raise
        record_auth_issue_event(
            API_PASSWORD_RESOLVE_RESULT,
            result="ok",
            extra={"password_present": True},
        )
        return self.issue_token(password)

    def get_board(self, symbol_key: str, *, token: str) -> dict[str, Any]:
        url = f"{self.base_url}/board/{symbol_key}"
        response = self._request(
            "GET",
            url,
            token=token,
            op="board",
        )
        try:
            return dict(response.json())
        except json.JSONDecodeError as e:
            raise KabuNativeApiError(f"board response is not JSON: {e}") from e

    def _request(
        self,
        method: str,
        url: str,
        *,
        token: str | None = None,
        json_body: Mapping[str, Any] | None = None,
        op: str,
    ) -> requests.Response:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if token:
            headers["X-API-KEY"] = token

        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                response = requests.request(
                    method,
                    url,
                    headers=headers,
                    json=dict(json_body) if json_body is not None else None,
                    timeout=self.timeout,
                )
            except requests.RequestException as e:
                last_error = e
                if attempt + 1 < self.max_retries:
                    self._sleep_backoff(attempt)
                    continue
                raise KabuNativeApiError(
                    f"ネットワークエラー ({op} {url}): {e}",
                    failure_class="TRANSPORT",
                ) from e

            if response.status_code in _RETRYABLE_HTTP and attempt + 1 < self.max_retries:
                last_error = KabuNativeApiError(
                    _format_http_error(op, url, response),
                    http_status=int(response.status_code),
                    failure_class="TRANSPORT",
                )
                self._sleep_backoff(attempt)
                continue

            if not response.ok:
                preview = _response_body_preview(response)
                kabu_code = _kabu_code_from_text(preview)
                status = int(response.status_code)
                klass = _http_failure_class(op=op, status=status, kabu_code=kabu_code)
                raise KabuNativeApiError(
                    _format_http_error(op, url, response, body_preview=preview),
                    http_status=status,
                    failure_class=klass,
                    kabu_code=kabu_code,
                )
            return response

        assert last_error is not None
        if isinstance(last_error, KabuNativeApiError):
            raise last_error
        raise KabuNativeApiError(f"{op} failed after {self.max_retries} attempts: {last_error}") from last_error

    def _sleep_backoff(self, attempt: int) -> None:
        if self.retry_backoff_sec <= 0:
            return
        time.sleep(self.retry_backoff_sec * (2**attempt))


def _gate_live_token_issue() -> None:
    """No-op shim. issue_station_token holds the Station lock; nested gate would deadlock."""
    return


def load_kabu_env(*, repo_root: Path | None = None) -> Path:
    """Load `.env` from repository root. Returns the root path used."""
    root = repo_root or _default_repo_root()
    try:
        from dotenv import load_dotenv

        load_dotenv(dotenv_path=root / ".env", override=False)
    except ImportError:
        pass
    return root


def require_kabu_password() -> str:
    password = os.environ.get(_ENV_PASSWORD, "").strip()
    if not password:
        raise KabuNativeApiError(
            f"{_ENV_PASSWORD} が未設定です。リポジトリ直下の .env に API パスワードを設定してください。"
        )
    return password


def default_base_url() -> str:
    return os.environ.get(_ENV_BASE, DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL


def build_symbol_key(code: str, exchange: str | int) -> str:
    return f"{code.strip()}@{exchange}"


def summarize_board(board: Mapping[str, Any], *, quote_depth: int = 5) -> dict[str, Any]:
    """Split board payload into current quote summary and shallow book excerpt."""
    quote_depth = max(1, min(quote_depth, 10))
    quote_keys_current = (
        "Symbol",
        "SymbolName",
        "Exchange",
        "ExchangeName",
        "CurrentPrice",
        "CurrentPriceTime",
        "CurrentPriceStatus",
        "CurrentPriceChangeStatus",
        "CalcPrice",
        "PreviousClose",
        "PreviousCloseTime",
        "ChangePreviousClose",
        "ChangePreviousClosePer",
        "OpeningPrice",
        "HighPrice",
        "LowPrice",
        "TradingVolume",
        "TradingValue",
        "VWAP",
        "BidQty",
        "BidPrice",
        "BidTime",
        "BidSign",
        "AskQty",
        "AskPrice",
        "AskTime",
        "AskSign",
    )
    current: dict[str, Any] = {}
    for key in quote_keys_current:
        if key in board and board[key] is not None:
            current[key] = board[key]

    board_excerpt: dict[str, Any] = {}
    for prefix in ("Sell", "Buy"):
        for i in range(1, quote_depth + 1):
            k = f"{prefix}{i}"
            if k in board and board[k] is not None:
                board_excerpt[k] = board[k]

    return {"current_quote": current, "board_excerpt": board_excerpt}


def redact_secrets(text: str, *, token: str | None = None) -> str:
    """Remove token-like substrings before logging or writing diagnostics."""
    if not text:
        return text
    out = text
    if token:
        out = out.replace(token, "<REDACTED_TOKEN>")
    for marker in ("Token", "X-API-KEY", "APIPassword"):
        if marker in out:
            out = out.replace(marker, f"{marker}<REDACTED>")
    return out


def _default_repo_root() -> Path:
    # kabu_native/src/api/rest_client.py -> repo root
    return Path(__file__).resolve().parents[3]


def _kabu_code_from_text(text: str) -> str:
    m = _KABU_CODE_RE.search(str(text or ""))
    return str(m.group(1)) if m else ""


def _http_failure_class(*, op: str, status: int, kabu_code: str) -> str:
    token_op = str(op or "").lower().startswith("token")
    if kabu_code == "4001007" or (token_op and status in {401, 403}):
        return ENVIRONMENT_AUTH_BLOCKED
    if status in {400, 401, 403}:
        return "AUTH_REJECTION"
    return "HTTP_ERROR"


def _response_body_preview(response: requests.Response) -> str:
    raw = b""
    try:
        raw = bytes(response.content or b"")[:2000]
    except Exception:
        raw = b""
    text = ""
    for enc in ("utf-8", "cp932", "shift_jis"):
        if not raw:
            break
        try:
            cand = raw.decode(enc)
        except Exception:
            continue
        text = cand
        if "ログイン" in cand or "認証" in cand:
            break
    if not text:
        try:
            text = str(response.text or "")[:2000]
        except Exception:
            text = "<unreadable body>"
    return redact_secrets(text)


def _format_http_error(
    op: str,
    url: str,
    response: requests.Response,
    *,
    body_preview: str | None = None,
) -> str:
    preview = body_preview if body_preview is not None else _response_body_preview(response)
    return f"{op} failed HTTP {response.status_code} url={url!r} body={preview!r}"


def _safe_payload_repr(payload: Any) -> str:
    if not isinstance(payload, Mapping):
        return repr(payload)
    safe = {k: ("<REDACTED>" if str(k).lower() == "token" else v) for k, v in payload.items()}
    return repr(safe)
