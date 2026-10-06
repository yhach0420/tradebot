"""Strict state invariants. Live vs reached. Count from walked days."""
from __future__ import annotations

from typing import Any


def count_invariants(*, funnel: list[dict[str, Any]], walked: dict[str, Any]) -> dict[str, Any]:
    counts = dict(walked.get("counts") or {})
    active_without_seed = 0
    loc_without_active = 0
    thesis_without_loc = 0
    thesis_live_without_active_live = 0
    e0_without_thesis_live = 0
    e1_without_thesis_live = 0
    exec_without_thesis_live = 0
    thesis_ready_is_id = 0
    for r in funnel:
        seed = r.get("OPENING_DRIVE_SEED")
        active_live = bool(r.get("OPENING_DRIVE_LIVE", r.get("OPENING_DRIVE_ACTIVE")))
        active_reached = bool(r.get("OPENING_DRIVE_REACHED") or r.get("opening_drive_id"))
        loc = bool(r.get("LOCATION_IDENTIFIED"))
        thesis_live = bool(r.get("THESIS_LIVE", r.get("THESIS_READY")))
        thesis_reached = bool(r.get("THESIS_REACHED") or r.get("thesis_id"))
        e0 = bool(r.get("E0"))
        e1 = bool(r.get("E1"))
        if (active_live or active_reached) and seed in (None, "NO_VALID_DRIVE_SEED"):
            active_without_seed += 1
        if loc and not (active_live or active_reached):
            loc_without_active += 1
        if (thesis_live or thesis_reached) and not loc:
            thesis_without_loc += 1
        if thesis_live and not active_live:
            thesis_live_without_active_live += 1
        if e0 and not thesis_live and not r.get("THESIS_LOST"):
            # E0 at day end may follow later loss; mint/emit required live. Count only if never live.
            if not thesis_reached:
                e0_without_thesis_live += 1
        if e1 and not thesis_reached:
            e1_without_thesis_live += 1
        if (e0 or e1) and not thesis_reached:
            exec_without_thesis_live += 1
        if bool(r.get("THESIS_READY")) and (not thesis_live) and bool(r.get("thesis_id")) and not r.get("THESIS_LOST"):
            thesis_ready_is_id += 1
    out = {
        "ACTIVE_without_SEED": active_without_seed,
        "LOCATION_without_ACTIVE": loc_without_active,
        "THESIS_READY_without_LOCATION": thesis_without_loc,
        "THESIS_LIVE_without_ACTIVE_LIVE": thesis_live_without_active_live,
        "E0_without_THESIS_REACHED": e0_without_thesis_live,
        "E1_without_THESIS_REACHED": e1_without_thesis_live,
        "EXECUTION_READY_without_THESIS_REACHED": exec_without_thesis_live,
        "THESIS_READY_reported_as_id_while_not_live": thesis_ready_is_id,
        "1M_created_location_n": int(counts.get("1M_created_location_n") or 0),
        "1M_changed_direction_n": int(counts.get("1M_changed_direction_n") or 0),
        "1M_changed_opening_drive_n": int(counts.get("1M_changed_opening_drive_n") or 0),
        "THESIS_REVIVED_BY_EXECUTION_n": int(counts.get("THESIS_REVIVED_BY_EXECUTION_n") or 0),
        "identity_mismatch_n": int(counts.get("identity_mismatch_n") or 0),
        "SAME_BAR_ENTRY": int(walked.get("same_bar_entry_n") or 0),
        "HIDDEN_1M_THESIS_PARITY": bool(walked.get("HIDDEN_1M_THESIS_PARITY")),
        "E0_EMIT_FAIL_CLOSED_NOT_LIVE": int(counts.get("E0_EMIT_FAIL_CLOSED_NOT_LIVE") or 0),
    }
    skip = {"HIDDEN_1M_THESIS_PARITY"}
    out["violations"] = sum(int(v) for k, v in out.items() if k not in skip and isinstance(v, int) and k != "E0_EMIT_FAIL_CLOSED_NOT_LIVE")
    if not out["HIDDEN_1M_THESIS_PARITY"]:
        out["violations"] += int(counts.get("HIDDEN_1M_THESIS_PARITY_FAIL") or 1)
    return out
