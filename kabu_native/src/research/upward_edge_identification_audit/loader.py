"""UEIA loader — reuses IOAR canonical PUSH semantics (stride=1)."""
from __future__ import annotations

from research.integrated_order_flow_absorption_reversal.loader import (  # noqa: F401
    Tick,
    bid_at,
    classify_trade_side,
    discover_days,
    exec_entry_ok,
    first_valid_ask,
    iter_day_ticks,
    load_streams,
    parse_ts,
)

# Override CAPTURE_ROOT usage: IOAR discover_days uses its CAPTURE_ROOT which is same path pattern.
# Re-export discover that uses UEIA root if needed — both point to data/market_capture.
