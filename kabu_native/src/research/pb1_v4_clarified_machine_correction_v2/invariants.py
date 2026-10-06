"""Strict state invariants. Count from walked days."""
from __future__ import annotations

from typing import Any


def count_invariants(*, funnel: list[dict[str, Any]], walked: dict[str, Any]) -> dict[str, Any]:
    counts = dict(walked.get("counts") or {})
    active_without_seed = 0
    loc_without_active = 0
    thesis_without_loc = 0
    thesis_without_active = 0
    e0_without_thesis = 0
    e1_without_thesis = 0
    exec_without_thesis = 0
    for r in funnel:
        seed = r.get("OPENING_DRIVE_SEED")
        active = bool(r.get("OPENING_DRIVE_ACTIVE"))
        loc = bool(r.get("LOCATION_IDENTIFIED"))
        thesis = bool(r.get("THESIS_READY"))
        e0 = bool(r.get("E0"))
        e1 = bool(r.get("E1"))
        if active and seed in (None, "NO_VALID_DRIVE_SEED"):
            active_without_seed += 1
        if loc and not active:
            loc_without_active += 1
        if thesis and not loc:
            thesis_without_loc += 1
        if thesis and not active:
            thesis_without_active += 1
        if e0 and not thesis:
            e0_without_thesis += 1
        if e1 and not thesis:
            e1_without_thesis += 1
        if (e0 or e1) and not thesis:
            exec_without_thesis += 1
    out = {
        "ACTIVE_without_SEED": active_without_seed,
        "LOCATION_without_ACTIVE": loc_without_active,
        "THESIS_READY_without_LOCATION": thesis_without_loc,
        "THESIS_READY_without_ACTIVE": thesis_without_active,
        "E0_without_THESIS_READY": e0_without_thesis,
        "E1_without_THESIS_READY": e1_without_thesis,
        "EXECUTION_READY_without_THESIS_READY": exec_without_thesis,
        "1M_created_location_n": int(counts.get("1M_created_location_n") or 0),
        "1M_changed_direction_n": int(counts.get("1M_changed_direction_n") or 0),
        "1M_changed_opening_drive_n": int(counts.get("1M_changed_opening_drive_n") or 0),
        "THESIS_REVIVED_BY_EXECUTION_n": int(counts.get("THESIS_REVIVED_BY_EXECUTION_n") or 0),
        "identity_mismatch_n": int(counts.get("identity_mismatch_n") or 0),
        "SAME_BAR_ENTRY": int(walked.get("same_bar_entry_n") or 0),
        "HIDDEN_1M_THESIS_PARITY": bool(walked.get("HIDDEN_1M_THESIS_PARITY")),
    }
    out["violations"] = sum(int(v) for k, v in out.items() if k != "HIDDEN_1M_THESIS_PARITY" and isinstance(v, int))
    if not out["HIDDEN_1M_THESIS_PARITY"]:
        out["violations"] += int(counts.get("HIDDEN_1M_THESIS_PARITY_FAIL") or 1)
    return out
