"""Research writes only. No Paper / Runtime / capture / ranking mutation."""
from __future__ import annotations

from pathlib import Path

from research.am_c0_indicator_exit.isolation import set_research_priority_below_normal, snapshot

NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "futures_x_stock_state_interaction_day1_v1"
CACHE = NATIVE / "results" / "research" / "_work" / "futures_x_stock_state_interaction_day1_v1"
CONTEXT_ROOT = NATIVE / "data" / "market_context_capture"

__all__ = ["NATIVE", "OUT", "CACHE", "CONTEXT_ROOT", "set_research_priority_below_normal", "snapshot"]
