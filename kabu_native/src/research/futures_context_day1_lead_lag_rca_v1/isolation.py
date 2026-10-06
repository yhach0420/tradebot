"""Research writes only. No Paper / Runtime / capture mutation. Day2 freeze untouched."""
from __future__ import annotations

from pathlib import Path

from research.am_c0_indicator_exit.isolation import set_research_priority_below_normal, snapshot

NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "futures_context_day1_lead_lag_rca_v1"
CACHE = NATIVE / "results" / "research" / "_work" / "futures_context_day1_lead_lag_rca_v1"
CONTEXT_ROOT = NATIVE / "data" / "market_context_capture"

__all__ = ["NATIVE", "OUT", "CACHE", "CONTEXT_ROOT", "set_research_priority_below_normal", "snapshot"]
