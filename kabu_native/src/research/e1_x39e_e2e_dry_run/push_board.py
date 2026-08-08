"""Demo PUSH schema → board arrays (same fields as research board loader)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

import numpy as np

JST = ZoneInfo("Asia/Tokyo")


@dataclass
class DemoPush:
    """Kabu-like board snapshot (minimal fields for V1R fill/exit evidence)."""
    symbol: str
    event_time: float  # epoch seconds
    buy1_price: float
    buy1_qty: float
    sell1_price: float
    sell1_qty: float
    fresh_sec: float = 0.5
    special: bool = False
    current_price_time: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "Symbol": self.symbol,
            "SymbolCode": self.symbol,
            "market_event_time": self.event_time,
            "CurrentPriceTime": self.current_price_time or self.event_time,
            "Buy1": {"Price": self.buy1_price, "Qty": self.buy1_qty},
            "Sell1": {"Price": self.sell1_price, "Qty": self.sell1_qty},
            "FreshnessSec": self.fresh_sec,
            "SpecialQuote": self.special,
            "demo_marker": "V1R_E2E_DRY_RUN_TEST",
        }


def demo_day_epoch(day: str, hh: int, mm: int, ss: float = 0.0) -> float:
    y, m, d = int(day[:4]), int(day[4:6]), int(day[6:8])
    whole = int(ss)
    micro = int(round((ss - whole) * 1_000_000))
    return datetime(y, m, d, hh, mm, whole, micro, tzinfo=JST).timestamp()


@dataclass
class SymbolBoardBuilder:
    """Accumulate PUSH events into research-compatible board arrays."""
    symbol: str
    pushes: list[DemoPush] = field(default_factory=list)

    def ingest(self, push: DemoPush) -> None:
        assert push.symbol == self.symbol
        self.pushes.append(push)

    def to_board(self) -> dict[str, np.ndarray]:
        if not self.pushes:
            return {
                "t": np.asarray([], dtype=float),
                "ask": np.asarray([], dtype=float),
                "bid": np.asarray([], dtype=float),
                "ask_qty": np.asarray([], dtype=float),
                "bid_qty": np.asarray([], dtype=float),
                "fresh_sec": np.asarray([], dtype=float),
                "special": np.asarray([], dtype=bool),
            }
        ordered = sorted(self.pushes, key=lambda p: p.event_time)
        return {
            "t": np.asarray([p.event_time for p in ordered], dtype=float),
            "ask": np.asarray([p.sell1_price for p in ordered], dtype=float),
            "bid": np.asarray([p.buy1_price for p in ordered], dtype=float),
            "ask_qty": np.asarray([p.sell1_qty for p in ordered], dtype=float),
            "bid_qty": np.asarray([p.buy1_qty for p in ordered], dtype=float),
            "fresh_sec": np.asarray([p.fresh_sec for p in ordered], dtype=float),
            "special": np.asarray([p.special for p in ordered], dtype=bool),
        }
