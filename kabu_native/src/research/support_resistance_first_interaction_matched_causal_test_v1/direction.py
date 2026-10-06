"""Direction normalization. Resistance rejection favorable = DOWN. Support rejection favorable = UP."""
from __future__ import annotations

from typing import Any

# Question A: first touch → rejection
#   RESISTANCE: favorable = DOWN  (sign -1)
#   SUPPORT:    favorable = UP    (sign +1)
# Question B: break → continuation through the zone
#   RESISTANCE: favorable = UP
#   SUPPORT:    favorable = DOWN
# Question C: break → retest → hold (continuation of the break)
#   same signs as B
# Question D: failed break (reversal back through the zone)
#   RESISTANCE: favorable = DOWN
#   SUPPORT:    favorable = UP


def is_primary(slot: str) -> bool:
    return str(slot or "") in {
        "NEAREST_ACTIVE_RESISTANCE_ABOVE",
        "NEAREST_ACTIVE_SUPPORT_BELOW",
    }


def is_secondary(slot: str) -> bool:
    return str(slot or "") in {
        "NEAREST_BROKEN_RESISTANCE_BELOW",
        "NEAREST_BROKEN_SUPPORT_ABOVE",
    }


def as_resistance_primary(ep: dict[str, Any]) -> bool:
    return str(ep.get("selection_label") or "") == "ACTIVE_RESISTANCE" or (
        str(ep.get("role") or "") == "RESISTANCE" and str(ep.get("selection_slot") or "") == "NEAREST_ACTIVE_RESISTANCE_ABOVE"
    )


def sign_for(question: str, *, resistance: bool) -> int:
    q = str(question or "")
    if q in {"A", "D"}:
        return -1 if resistance else 1
    if q in {"B", "C"}:
        return 1 if resistance else -1
    return 1


def direction_table() -> dict[str, Any]:
    return {
        "A_first_touch_rejection": {
            "resistance_favorable": "DOWN",
            "support_favorable": "UP",
        },
        "B_break_continuation": {
            "resistance_favorable": "UP",
            "support_favorable": "DOWN",
        },
        "C_retest_hold": {
            "resistance_favorable": "UP",
            "support_favorable": "DOWN",
        },
        "D_failed_break": {
            "resistance_favorable": "DOWN",
            "support_favorable": "UP",
        },
        "pooled_analysis_uses_signed_bps": True,
        "five_pp_continuation_not_sole_gate": True,
    }
