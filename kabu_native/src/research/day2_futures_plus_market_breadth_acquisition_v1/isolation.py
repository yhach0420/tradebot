"""Research writes only. No Paper / Runtime / register mutation."""
from __future__ import annotations

from pathlib import Path

from research.am_c0_indicator_exit.isolation import set_research_priority_below_normal, snapshot

NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "day2_futures_plus_market_breadth_acquisition_v1"
PARENT_OUT = NATIVE / "results" / "research" / "market_breadth_leadership_acquisition_v1"

__all__ = ["NATIVE", "OUT", "PARENT_OUT", "set_research_priority_below_normal", "snapshot"]
