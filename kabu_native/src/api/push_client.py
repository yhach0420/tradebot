"""
kabu_native PUSH (WebSocket) client for kabuステーション® API.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator, Sequence
from typing import Any
from urllib.parse import urlparse

import requests

from api.rest_client import DEFAULT_BASE_URL, KabuNativeApiError, KabuNativeRestClient, redact_secrets

EXPECTED_PUSH_FIELDS_STOCK = (
    "Symbol",
    "SymbolName",
    "Exchange",
    "CurrentPrice",
    "CurrentPriceTime",
    "TradingVolume",
    "TradingVolumeTime",
    "BidPrice",
    "BidQty",
    "AskPrice",
    "AskQty",
    "VWAP",
    "TradingValue",
    "HighPrice",
    "LowPrice",
    "OpeningPrice",
)


def rest_base_to_websocket_url(rest_base_url: str) -> str:
    """http://localhost:18080/kabusapi -> ws://localhost:18080/kabusapi/websocket"""
    u = urlparse(rest_base_url.rstrip("/"))
    scheme = "wss" if u.scheme == "https" else "ws"
    host = u.netloc
    path = u.path.rstrip("/")
    if not path.endswith("kabusapi"):
        raise ValueError(f"unexpected kabusapi base path: {rest_base_url!r}")
    return f"{scheme}://{host}/kabusapi/websocket"


def push_spec(rest_base_url: str = DEFAULT_BASE_URL) -> dict[str, Any]:
    """
    Off-hours / weekend spec check without opening a WebSocket.
    """
    base = rest_base_url.rstrip("/")
    return {
        "rest_base_url": base,
        "websocket_url": rest_base_to_websocket_url(base),
        "register_endpoint": f"{base}/register",
        "unregister_all_endpoint": f"{base}/unregister/all",
        "notes": [
            "PUSH delivers board-like updates on value changes, not raw tick prints.",
            "During lunch break and after close, PUSH may stop; REST board may still work on session days.",
            "Use --push-spec-only in check_api.py when the market is closed.",
        ],
        "expected_fields_stock": list(EXPECTED_PUSH_FIELDS_STOCK),
    }


class KabuNativePushClient:
    """PUSH registration + WebSocket message stream."""

    def __init__(self, rest_client: KabuNativeRestClient, token: str) -> None:
        self._rest = rest_client
        self._token = token
        self._base_url = rest_client.base_url

    @property
    def websocket_url(self) -> str:
        return rest_base_to_websocket_url(self._base_url)

    def register(self, symbols_spec: Sequence[tuple[str, int]]) -> dict[str, Any]:
        return _register_symbols(
            token=self._token,
            symbols_spec=symbols_spec,
            rest_base_url=self._base_url,
            timeout=self._rest.timeout,
            max_retries=self._rest.max_retries,
            retry_backoff_sec=self._rest.retry_backoff_sec,
        )

    def unregister_all(self) -> dict[str, Any]:
        return _unregister_all(
            token=self._token,
            rest_base_url=self._base_url,
            timeout=self._rest.timeout,
            max_retries=self._rest.max_retries,
            retry_backoff_sec=self._rest.retry_backoff_sec,
        )

    async def iter_messages(
        self,
        *,
        recv_poll_sec: float | None = 30.0,
    ) -> AsyncIterator[dict[str, Any]]:
        async for msg in _iter_push_board_messages(self.websocket_url, recv_poll_sec=recv_poll_sec):
            yield msg

    def iter_messages_sync(
        self,
        *,
        recv_poll_sec: float | None = 30.0,
        max_messages: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Synchronous wrapper for JSONL writers and simple scripts."""

        async def _collect() -> list[dict[str, Any]]:
            out: list[dict[str, Any]] = []
            async for msg in self.iter_messages(recv_poll_sec=recv_poll_sec):
                out.append(msg)
                if max_messages is not None and len(out) >= max_messages:
                    break
            return out

        for item in asyncio.run(_collect()):
            yield item


def _register_symbols(
    *,
    token: str,
    symbols_spec: Sequence[tuple[str, int]],
    rest_base_url: str,
    timeout: float,
    max_retries: int,
    retry_backoff_sec: float,
) -> dict[str, Any]:
    url = f"{rest_base_url.rstrip('/')}/register"
    body = {"Symbols": [{"Symbol": str(s), "Exchange": int(ex)} for s, ex in symbols_spec]}
    response = _put_with_retry(
        url,
        token=token,
        json_body=body,
        timeout=timeout,
        max_retries=max_retries,
        retry_backoff_sec=retry_backoff_sec,
        op="register",
    )
    try:
        return dict(response.json())
    except json.JSONDecodeError as e:
        raise KabuNativeApiError(f"register response not JSON: {e}") from e


def _unregister_all(
    *,
    token: str,
    rest_base_url: str,
    timeout: float,
    max_retries: int,
    retry_backoff_sec: float,
) -> dict[str, Any]:
    url = f"{rest_base_url.rstrip('/')}/unregister/all"
    response = _put_with_retry(
        url,
        token=token,
        json_body=None,
        timeout=timeout,
        max_retries=max_retries,
        retry_backoff_sec=retry_backoff_sec,
        op="unregister/all",
    )
    try:
        return dict(response.json())
    except json.JSONDecodeError as e:
        raise KabuNativeApiError(f"unregister/all response not JSON: {e}") from e


def _put_with_retry(
    url: str,
    *,
    token: str,
    json_body: dict[str, Any] | None,
    timeout: float,
    max_retries: int,
    retry_backoff_sec: float,
    op: str,
) -> requests.Response:
    headers = {"Content-Type": "application/json", "X-API-KEY": token}
    last_error: Exception | None = None
    for attempt in range(max(1, max_retries)):
        try:
            response = requests.put(url, headers=headers, json=json_body, timeout=timeout)
        except requests.RequestException as e:
            last_error = e
            if attempt + 1 < max_retries:
                if retry_backoff_sec > 0:
                    import time

                    time.sleep(retry_backoff_sec * (2**attempt))
                continue
            raise KabuNativeApiError(f"ネットワークエラー ({op}): {e}") from e

        if response.status_code in {502, 503, 504} and attempt + 1 < max_retries:
            last_error = KabuNativeApiError(
                f"{op} HTTP {response.status_code}: {redact_secrets(response.text[:800], token=token)!r}"
            )
            if retry_backoff_sec > 0:
                import time

                time.sleep(retry_backoff_sec * (2**attempt))
            continue

        if not response.ok:
            raise KabuNativeApiError(
                f"{op} HTTP {response.status_code}: {redact_secrets(response.text[:800], token=token)!r}"
            )
        return response

    assert last_error is not None
    if isinstance(last_error, KabuNativeApiError):
        raise last_error
    raise KabuNativeApiError(f"{op} failed after {max_retries} attempts: {last_error}") from last_error


async def _iter_push_board_messages(
    ws_url: str,
    *,
    recv_poll_sec: float | None = 30.0,
) -> AsyncIterator[dict[str, Any]]:
    import websockets

    async with websockets.connect(ws_url, ping_timeout=None, close_timeout=10) as ws:
        while True:
            try:
                if recv_poll_sec is None:
                    raw = await ws.recv()
                else:
                    raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, float(recv_poll_sec)))
            except asyncio.TimeoutError:
                continue
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="replace")
            payload = json.loads(raw)
            if isinstance(payload, dict):
                yield payload
