"""Research writes only the close summary. Does not mutate Day2 freeze, Paper, or Runtime."""
from __future__ import annotations

from pathlib import Path

from research.am_c0_indicator_exit.isolation import set_research_priority_below_normal, snapshot

NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "current_day1_information_close_v1"
DAY2_PRIMARY_OUT = NATIVE / "results" / "research" / "futures_x_stock_state_day1_candidate_mechanism_audit_v1"

__all__ = [
    "NATIVE",
    "OUT",
    "DAY2_PRIMARY_OUT",
    "set_research_priority_below_normal",
    "snapshot",
]
