"""Resolve NK225mini / TOPIX front-month via OpenAPI. Freeze for the session. Exchange=2 only."""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

from research.new_causal_information_acquisition_v1 import (
    DERIV_MONTH,
    FORBIDDEN_FUTURE_CODES,
    FUTURE_CODES,
    FUTURES_EXCHANGE,
)
from api.rest_client import KabuNativeRestClient

JST = ZoneInfo("Asia/Tokyo")


class FuturesResolveError(RuntimeError):
    pass


def _finite(v: Any) -> bool:
    try:
        if v is None or v == "":
            return False
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def _level_price(payload: Mapping[str, Any], side: str) -> Any:
    lv = payload.get(side)
    if isinstance(lv, Mapping):
        return lv.get("Price")
    return None


def extract_resolved_symbol(resp: Mapping[str, Any]) -> str:
    for key in ("Symbol", "symbol"):
        s = str(resp.get(key) or "").strip()
        if s:
            return s.split("@", 1)[0]
    raise FuturesResolveError(f"symbolname/future missing Symbol: {dict(resp)}")


def resolve_front_month(
    rest: KabuNativeRestClient,
    *,
    token: str,
    future_code: str,
    deriv_month: int = DERIV_MONTH,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    code = str(future_code)
    if code in FORBIDDEN_FUTURE_CODES:
        raise FuturesResolveError(f"future code {code} is out of scope for this acquisition")
    if code not in FUTURE_CODES:
        raise FuturesResolveError(f"unsupported FutureCode={code}")
    if int(deriv_month) != 0:
        raise FuturesResolveError("DerivMonth must be 0 (front month) at session start")
    ts = (now or datetime.now(JST)).isoformat(timespec="milliseconds")
    resp = rest.get_symbolname_future(token=token, future_code=code, deriv_month=int(deriv_month))
    symbol = extract_resolved_symbol(resp if isinstance(resp, Mapping) else {})
    return {
        "FutureCode": code,
        "DerivMonth": 0,
        "resolved_symbol": symbol,
        "resolution_timestamp": ts,
        "api_response": dict(resp) if isinstance(resp, Mapping) else {"raw": resp},
        "source": "GET /kabusapi/symbolname/future",
        "frozen": True,
    }


def board_key(symbol: str, exchange: int = FUTURES_EXCHANGE) -> str:
    s = str(symbol).split("@", 1)[0].strip()
    if int(exchange) != FUTURES_EXCHANGE:
        raise FuturesResolveError("NEW_INFO futures primary registration is Exchange=2 only")
    return f"{s}@{int(exchange)}"


def preflight_board(
    rest: KabuNativeRestClient,
    *,
    token: str,
    resolved_symbol: str,
    future_code: str,
) -> dict[str, Any]:
    key = board_key(resolved_symbol, FUTURES_EXCHANGE)
    try:
        board = rest.get_board(key, token=token)
        status = 200
        err = ""
    except Exception as exc:
        status = int(getattr(exc, "http_status", 0) or 0)
        board = {}
        err = f"{type(exc).__name__}:{exc}"
        raise FuturesResolveError(
            f"board preflight FAIL CLOSED {future_code} {key} http={status} err={err}. "
            "Do not auto-explore Exchange 23/24."
        ) from exc
    if not isinstance(board, Mapping):
        raise FuturesResolveError(f"board preflight non-object {future_code} {key}")
    got = str(board.get("Symbol") or "").split("@", 1)[0].strip()
    want = str(resolved_symbol).split("@", 1)[0].strip()
    if got and got != want:
        raise FuturesResolveError(f"board Symbol mismatch {future_code}: got={got} want={want}")
    if not got:
        raise FuturesResolveError(f"board missing Symbol {future_code} {key}")
    candidates = [
        board.get("CurrentPrice"),
        board.get("CalcPrice"),
        _level_price(board, "Buy1"),
        _level_price(board, "Sell1"),
        board.get("OpeningPrice"),
        board.get("PreviousClose"),
    ]
    present_bad = []
    finite_n = 0
    for v in candidates:
        if v is None or v == "":
            continue
        if _finite(v):
            finite_n += 1
        else:
            present_bad.append(v)
    if present_bad:
        raise FuturesResolveError(f"non-finite market fields {future_code} {key}: {present_bad}")
    return {
        "ok": True,
        "http_status": status,
        "symbol": got,
        "exchange": FUTURES_EXCHANGE,
        "board_key": key,
        "finite_market_field_n": finite_n,
        "auto_explore_23_24": False,
        "future_code": future_code,
    }


def resolve_both(
    rest: KabuNativeRestClient,
    *,
    token: str,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    contracts = []
    preflights = []
    for code in FUTURE_CODES:
        freeze = resolve_front_month(rest, token=token, future_code=code, now=now)
        pre = preflight_board(
            rest,
            token=token,
            resolved_symbol=str(freeze["resolved_symbol"]),
            future_code=code,
        )
        freeze["board_preflight"] = pre
        contracts.append(freeze)
        preflights.append(pre)
    by_code = {str(c["FutureCode"]): c for c in contracts}
    return {
        "ok": True,
        "contracts": contracts,
        "by_code": by_code,
        "nk225mini": by_code.get("NK225mini"),
        "topix": by_code.get("TOPIX"),
        "exchange": FUTURES_EXCHANGE,
        "session_frozen": True,
        "mid_session_roll": False,
        "preflights": preflights,
    }
