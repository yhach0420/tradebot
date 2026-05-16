from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from requests import Session

from src.kabu_api_client import DEFAULT_BASE_URL, KabuApiClient, KabuApiError, build_symbol_key

from .base_provider import MarketDataProvider


def _pct_change(price: float, previous_close: Optional[float]) -> Optional[float]:
    if previous_close is None:
        return None
    pc = float(previous_close)
    if pc <= 0:
        return None
    return ((float(price) - pc) / pc) * 100.0


def _yahoo_jp_equity_code(symbol: str) -> Optional[str]:
    s = symbol.strip().upper()
    if s.endswith(".T"):
        code = s[:-2].strip()
        if code.isdigit():
            return code
    return None


def _board_market_time_utc(board: Mapping[str, Any]) -> Optional[datetime]:
    raw = board.get("CurrentPriceTime")
    if not raw:
        return None
    if isinstance(raw, datetime):
        dt = raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    if not isinstance(raw, str):
        return None
    t = raw.strip()
    try:
        if t.endswith("Z"):
            dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(t)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def quote_from_board(*, symbol_yahoo: str, board: Mapping[str, Any]) -> Any:
    """BoardSuccess JSON → Quote（yahoo_kabu_watch.Quote と同一形状）。"""
    import yahoo_kabu_watch as yw

    price = board.get("CurrentPrice")
    if price is None and board.get("CalcPrice") is not None:
        price = board.get("CalcPrice")
    if not isinstance(price, (int, float)):
        raise ValueError("board CurrentPrice が数値でありません")

    prev = board.get("PreviousClose")
    pc_f = float(prev) if isinstance(prev, (int, float)) else None
    change = _pct_change(float(price), pc_f)

    day_high = board.get("HighPrice")
    day_low = board.get("LowPrice")
    vol = board.get("TradingVolume")

    return yw.Quote(
        symbol=symbol_yahoo,
        price=float(price),
        currency="JPY",
        previous_close=pc_f,
        change_percent=float(change) if isinstance(change, (int, float)) else None,
        day_high=float(day_high) if isinstance(day_high, (int, float)) else None,
        day_low=float(day_low) if isinstance(day_low, (int, float)) else None,
        volume=float(vol) if isinstance(vol, (int, float)) else None,
        market_time_utc=_board_market_time_utc(board),
        market_cap=None,
    )


class KabuProvider(MarketDataProvider):
    """
    kabuステーション /board で現値系を構築。API が使えない場合は Yahoo の fetch_quote に戻す。
    """

    def __init__(self, session: Session, *, client: KabuApiClient | None = None) -> None:
        self._session = session
        raw_base = os.environ.get("KABU_API_BASE", "").strip()
        self._client = client or KabuApiClient(base_url=raw_base or DEFAULT_BASE_URL)
        self._token: Optional[str] = None

    def _yahoo_fallback_log(self, symbol: str, detail: str) -> None:
        import yahoo_kabu_watch as yw

        ts = yw.now_str()
        tail = detail[:260] + ("…" if len(detail) > 260 else "")
        print(f"[{ts}] [PAPER] kabu_quote_fallback symbol={symbol} {tail}")

    def _yahoo_quote(self, symbol: str) -> Any:
        import yahoo_kabu_watch as yw

        return yw.fetch_quote(self._session, symbol)

    def get_quote(self, symbol: str) -> Any:
        code_opt = _yahoo_jp_equity_code(symbol)
        if code_opt is None:
            return self._yahoo_quote(symbol)

        pwd = os.environ.get("KABU_API_PASSWORD", "").strip()
        if not pwd:
            self._yahoo_fallback_log(symbol, "KABU_API_PASSWORD unset → Yahoo quote")
            return self._yahoo_quote(symbol)

        ex = os.environ.get("KABU_EXCHANGE", "1").strip() or "1"
        kabu_symbol = build_symbol_key(code_opt, ex)

        def _attempt() -> Any:
            if not self._token:
                self._token = self._client.issue_token(pwd)
            board = self._client.get_board(kabu_symbol, token=self._token)
            return quote_from_board(symbol_yahoo=symbol, board=board)

        try:
            return _attempt()
        except KabuApiError:
            self._token = None
            try:
                return _attempt()
            except KabuApiError as e2:
                self._yahoo_fallback_log(symbol, repr(e2))
                return self._yahoo_quote(symbol)
            except ValueError as ve2:
                self._yahoo_fallback_log(symbol, repr(ve2))
                return self._yahoo_quote(symbol)
        except ValueError as ve:
            self._yahoo_fallback_log(symbol, repr(ve))
            return self._yahoo_quote(symbol)
