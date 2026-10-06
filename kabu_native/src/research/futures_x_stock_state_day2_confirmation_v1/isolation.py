"""Research writes only. No Paper / Runtime / capture / ranking mutation."""
from __future__ import annotations

from pathlib import Path

from research.am_c0_indicator_exit.isolation import set_research_priority_below_normal, snapshot

NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "futures_x_stock_state_day2_confirmation_v1"
FREEZE_PARENT = NATIVE / "results" / "research" / "futures_x_stock_state_day1_candidate_mechanism_audit_v1"

__all__ = ["NATIVE", "OUT", "FREEZE_PARENT", "set_research_priority_below_normal", "snapshot"]
