"""Research writes only. No Paper / Runtime / capture mutation. No live start."""
from __future__ import annotations

from pathlib import Path

from research.am_c0_indicator_exit.isolation import set_research_priority_below_normal, snapshot

NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "run_20260914_day2_futures_plus_first_live_breadth_v1"
DAY2_OUT = NATIVE / "results" / "research" / "futures_x_stock_state_day2_confirmation_v1"
CONTEXT_ROOT = NATIVE / "data" / "market_context_capture"
BREADTH_RAW = NATIVE / "data" / "market_breadth_capture"

__all__ = [
    "NATIVE",
    "OUT",
    "DAY2_OUT",
    "CONTEXT_ROOT",
    "BREADTH_RAW",
    "set_research_priority_below_normal",
    "snapshot",
]
