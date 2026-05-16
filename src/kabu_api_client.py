"""
Minimal HTTP client for kabuステーション® REST API (local kabusapi).
"""

from __future__ import annotations

import json
from typing import Any, Mapping

import requests


DEFAULT_BASE_URL = "http://localhost:18080/kabusapi"


class KabuApiError(RuntimeError):
    """Unexpected HTTP status or malformed response from kabusapi."""


class KabuApiClient:
    def __init__(self, base_url: str = DEFAULT_BASE_URL, *, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def issue_token(self, api_password: str) -> str:
        url = f"{self.base_url}/token"
        try:
            response = requests.post(
                url,
                json={"APIPassword": api_password},
                headers={"Content-Type": "application/json"},
                timeout=self.timeout,
            )
        except requests.RequestException as e:
            raise KabuApiError(f"ネットワークエラー ({url}): {e}") from e
        if not response.ok:
            raise KabuApiError(_format_http_error("token issue", url, response))
        try:
            payload = response.json()
        except json.JSONDecodeError as e:
            raise KabuApiError(f"token response is not JSON: {e}") from e
        token = payload.get("Token")
        if not token:
            raise KabuApiError(f"token response missing Token field: {payload!r}")
        return str(token)

    def get_board(self, symbol_key: str, *, token: str) -> dict[str, Any]:
        url = f"{self.base_url}/board/{symbol_key}"
        try:
            response = requests.get(
                url,
                headers={
                    "Content-Type": "application/json",
                    "X-API-KEY": token,
                },
                timeout=self.timeout,
            )
        except requests.RequestException as e:
            raise KabuApiError(f"ネットワークエラー ({url}): {e}") from e
        if not response.ok:
            raise KabuApiError(_format_http_error("board", url, response))
        try:
            return dict(response.json())
        except json.JSONDecodeError as e:
            raise KabuApiError(f"board response is not JSON: {e}") from e


def build_symbol_key(code: str, exchange: str) -> str:
    return f"{code.strip()}@{exchange.strip()}"


def summarize_board(board: Mapping[str, Any], *, quote_depth: int = 5) -> dict[str, Any]:
    """
    Split board payload into 「現値」summary and 「板」の一部（先頭だけ）.
    """
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


def _format_http_error(op: str, url: str, response: requests.Response) -> str:
    body_preview = ""
    try:
        body_preview = response.text[:2000]
    except Exception:
        body_preview = "<unreadable body>"
    return f"{op} failed HTTP {response.status_code} url={url!r} body={body_preview!r}"
