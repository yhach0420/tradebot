"""Normalize first-pass human labels from V3 67 + unseen 21. Mapping documented."""
from __future__ import annotations

from typing import Any


def _finite_bool(x: Any) -> bool:
    return bool(x)


def map_v3_opening(row: dict[str, Any]) -> str:
    if _finite_bool(row.get("A_opening_continuation")) and _finite_bool(row.get("B_impulse_directional")):
        return "CLEAR_DIRECTIONAL_AUCTION"
    if _finite_bool(row.get("I_already_extended_stale")):
        return "AMBIGUOUS_OPEN"
    return "AMBIGUOUS_OPEN"


def map_v3_location(row: dict[str, Any]) -> str:
    loc = str(row.get("location") or "")
    if loc == "CLEAR_ROUTE":
        return "CLEAR_DEFENDED_LOCATION"
    if loc == "STRUCTURALLY_BLOCKED":
        return "STRUCTURALLY_BAD"
    return "QUESTIONABLE_LOCATION"


def map_v3_retest(row: dict[str, Any]) -> str:
    r = str(row.get("retest_lab") or row.get("retest") or "")
    if r == "FRESH_RETEST":
        return "VALID_FIRST_RETEST"
    if r == "EXHAUSTED_RETEST":
        return "STALE_OR_EXHAUSTED"
    return "QUESTIONABLE_RETEST"


def map_v3_trigger(row: dict[str, Any]) -> str:
    t = str(row.get("trigger_lab") or row.get("trigger") or "")
    if t == "VALID_RECLAIM":
        return "VALID_REACCELERATION"
    if t == "INVALID_TRIGGER":
        return "INVALID_TRIGGER"
    return "WEAK_REACCELERATION"


def normalize_first_pass(row: dict[str, Any], *, cohort: str) -> dict[str, Any]:
    pattern = str(row.get("pattern") or "")
    if cohort == "v3_semantic_dev":
        opening = map_v3_opening(row)
        location = map_v3_location(row)
        retest = map_v3_retest(row)
        trigger = map_v3_trigger(row)
        late = bool(row.get("I_already_extended_stale"))
        opening_mapped = True
    else:
        opening = str(row.get("opening") or "")
        location = str(row.get("location") or "")
        retest = str(row.get("retest") or "")
        trigger = str(row.get("trigger") or "")
        late = bool(row.get("late_stalled_leak"))
        opening_mapped = False
    return {
        "fp_pattern": pattern,
        "fp_opening": opening,
        "fp_location": location,
        "fp_retest": retest,
        "fp_trigger": trigger,
        "fp_late": late,
        "fp_note": str(row.get("note") or ""),
        "fp_opening_mapped_from_v3_flags": opening_mapped,
        "fp_future_used": False,
    }
