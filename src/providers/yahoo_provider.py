from __future__ import annotations

from requests import Session

from .base_provider import MarketDataProvider


class YahooProvider(MarketDataProvider):
    """Yahoo Finance (既存 fetch_quote と同一)。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_quote(self, symbol: str):
        import yahoo_kabu_watch as yw

        return yw.fetch_quote(self._session, symbol)
