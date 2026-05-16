from __future__ import annotations

from abc import ABC, abstractmethod


class MarketDataProvider(ABC):
    """
    Minimal interface for realtime quote sources used by callers such as paper_trade.

    Implementations wrap Yahoo Finance, kabusapi, etc.; callers rely on `.get_quote(symbol)` only.
    """

    @abstractmethod
    def get_quote(self, symbol: str):
        """Return a `yahoo_kabu_watch.Quote`-compatible snapshot for Yahoo-style symbols (例: 7203.T)。"""

