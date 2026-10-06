"""Research writes only. Does not mutate Day2 primary freeze, Paper, Runtime, or capture."""
from __future__ import annotations

from pathlib import Path

from research.am_c0_indicator_exit.isolation import set_research_priority_below_normal, snapshot

NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "futures_reversal_x_cash_participation_day1_v1"
CACHE = NATIVE / "results" / "research" / "_work" / "futures_reversal_x_cash_participation_day1_v1"
PARENT_OUT = NATIVE / "results" / "research" / "futures_reversal_confirmed_entry_day1_v1"
DAY2_PRIMARY_OUT = NATIVE / "results" / "research" / "futures_x_stock_state_day1_candidate_mechanism_audit_v1"

__all__ = [
    "NATIVE",
    "OUT",
    "CACHE",
    "PARENT_OUT",
    "DAY2_PRIMARY_OUT",
    "set_research_priority_below_normal",
    "snapshot",
]
