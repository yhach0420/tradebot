"""Market data provider implementations (quote source switching)."""

from __future__ import annotations

from .base_provider import MarketDataProvider
from .factory import resolve_paper_trade_quote_provider

__all__ = [
    "MarketDataProvider",
    "resolve_paper_trade_quote_provider",
]
