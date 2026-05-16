"""Factory for MARKET_DATA_PROVIDER に応じたクォートソース切替（paper_trade 向け）。"""

from __future__ import annotations

import os

from requests import Session

from .base_provider import MarketDataProvider
from .kabu_provider import KabuProvider
from .yahoo_provider import YahooProvider


def resolve_paper_trade_quote_provider(session: Session) -> MarketDataProvider:
    """環境変数 MARKET_DATA_PROVIDER に従って paper_trade の get_quote を解決します。"""
    name = os.environ.get("MARKET_DATA_PROVIDER", "yahoo").strip().lower()
    if name == "kabu":
        return KabuProvider(session)
    return YahooProvider(session)
