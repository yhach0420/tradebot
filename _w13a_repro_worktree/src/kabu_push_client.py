"""
kabuステーション® PUSH（WebSocket）接続と銘柄登録ヘルパ。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any, Mapping, Optional, Sequence
from urllib.parse import urlparse

import requests

from src.kabu_api_client import DEFAULT_BASE_URL, KabuApiError


def rest_base_to_websocket_url(rest_base_url: str) -> str:
    """例: http://localhost:18080/kabusapi -> ws://localhost:18080/kabusapi/websocket"""
    u = urlparse(rest_base_url.rstrip("/"))
    scheme = "wss" if u.scheme == "https" else "ws"
    host = u.netloc
    path = u.path.rstrip("/")
    if not path.endswith("kabusapi"):
        raise ValueError(f"unexpected kabusapi base path: {rest_base_url!r}")
    return f"{scheme}://{host}/kabusapi/websocket"


def register_push_symbols(
    *,
    token: str,
    symbols_spec: Sequence[tuple[str, int]],
    rest_base_url: str = DEFAULT_BASE_URL,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """PUT /register — PUSH 対象銘柄を API 登録リストへ追加。"""
    url = f"{rest_base_url.rstrip('/')}/register"
    body = {"Symbols": [{"Symbol": str(s), "Exchange": int(ex)} for s, ex in symbols_spec]}
    try:
        r = requests.put(
            url,
            headers={"Content-Type": "application/json", "X-API-KEY": token},
            json=body,
            timeout=timeout,
        )
    except requests.RequestException as e:
        raise KabuApiError(f"ネットワークエラー (register): {e}") from e
    if not r.ok:
        raise KabuApiError(f"register HTTP {r.status_code} url={url!r} body={r.text[:800]!r}")
    try:
        return dict(r.json())
    except json.JSONDecodeError as e:
        raise KabuApiError(f"register response not JSON: {e}") from e


def unregister_all_push(*, token: str, rest_base_url: str = DEFAULT_BASE_URL, timeout: float = 30.0) -> dict[str, Any]:
    """PUT /unregister/all"""
    url = f"{rest_base_url.rstrip('/')}/unregister/all"
    try:
        r = requests.put(
            url,
            headers={"Content-Type": "application/json", "X-API-KEY": token},
            timeout=timeout,
        )
    except requests.RequestException as e:
        raise KabuApiError(f"ネットワークエラー (unregister/all): {e}") from e
    if not r.ok:
        raise KabuApiError(f"unregister/all HTTP {r.status_code}: {r.text[:800]!r}")
    try:
        return dict(r.json())
    except json.JSONDecodeError as e:
        raise KabuApiError(f"unregister/all response not JSON: {e}") from e


async def iter_push_board_messages(
    ws_url: str,
    *,
    recv_poll_sec: Optional[float] = 30.0,
) -> AsyncIterator[dict[str, Any]]:
    """
    WebSocket で受け取る PUSH メッセージを JSON dict として列挙。
    recv_poll_sec が None の場合は約定があるまで無限ブロックする。
    ブロッキングを避けるなら recv_poll_sec を秒単位で指定する（定期的にタイムアウトし、その後は再読み）。
    """
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
