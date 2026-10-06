"""Research writes only. No Paper / Runtime / register mutation."""
from __future__ import annotations

from pathlib import Path

from research.am_c0_indicator_exit.isolation import set_research_priority_below_normal, snapshot

NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "market_breadth_leadership_acquisition_v1"
RAW_ROOT = NATIVE / "data" / "market_breadth_capture"

__all__ = ["NATIVE", "OUT", "RAW_ROOT", "set_research_priority_below_normal", "snapshot"]
