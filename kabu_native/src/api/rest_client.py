"""
kabu_native REST client for kabuステーション® API (local kabusapi).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Mapping

import requests

DEFAULT_BASE_URL = "http://localhost:18080/kabusapi"
_ENV_PASSWORD = "KABU_API_PASSWORD"
_ENV_BASE = "KABU_API_BASE"
_RETRYABLE_HTTP = frozenset({502, 503, 504})


class KabuNativeApiError(RuntimeError):
    """HTTP / network / malformed response from kabusapi."""


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

    def issue_token(self, api_password: str) -> str:
        _gate_live_token_issue()
        url = f"{self.base_url}/token"
        response = self._request(
            "POST",
            url,
            json_body={"APIPassword": api_password},
            op="token issue",
        )
        try:
            payload = response.json()
        except json.JSONDecodeError as e:
            raise KabuNativeApiError(f"token response is not JSON: {e}") from e
        token = payload.get("Token")
        if not token:
            raise KabuNativeApiError(f"token response missing Token field: {_safe_payload_repr(payload)}")
        return str(token)

    def issue_token_from_env(self) -> str:
        password = require_kabu_password()
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
                raise KabuNativeApiError(f"ネットワークエラー ({op} {url}): {e}") from e

            if response.status_code in _RETRYABLE_HTTP and attempt + 1 < self.max_retries:
                last_error = KabuNativeApiError(_format_http_error(op, url, response))
                self._sleep_backoff(attempt)
                continue

            if not response.ok:
                raise KabuNativeApiError(_format_http_error(op, url, response))
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
    """Block child POST /token while MARKET_INGRESS_SERVICE owns the live session."""
    try:
        from small_paper.kabu_token_authority import gate_token_issue
    except Exception:
        return
    gate_token_issue(caller="rest_client.issue_token")


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


def _format_http_error(op: str, url: str, response: requests.Response) -> str:
    body_preview = ""
    try:
        body_preview = redact_secrets(response.text[:2000])
    except Exception:
        body_preview = "<unreadable body>"
    return f"{op} failed HTTP {response.status_code} url={url!r} body={body_preview!r}"


def _safe_payload_repr(payload: Any) -> str:
    if not isinstance(payload, Mapping):
        return repr(payload)
    safe = {k: ("<REDACTED>" if str(k).lower() == "token" else v) for k, v in payload.items()}
    return repr(safe)
