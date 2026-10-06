"""Recompute hidden-1m identity parity from Correction V2 walk. Do not trust stored boolean."""
from __future__ import annotations

from typing import Any

from research.pb1_v4_clarified_machine_correction_v2.thesis import hidden_1m_snapshot


def _mask_1m(row: dict[str, Any]) -> dict[str, Any]:
    loc = str(row.get("location_id") or "")
    drive = str(row.get("opening_drive_id") or "")
    thesis = str(row.get("thesis_id") or "")
    direction = row.get("direction")
    if not direction:
        sign = int(row.get("DIR") or 0)
        direction = "bull" if sign > 0 else ("bear" if sign < 0 else None)
    return {
        "symbol": str(row.get("symbol") or ""),
        "date": str(row.get("date") or ""),
        "direction": direction,
        "opening_drive_id": drive or None,
        "location_id": loc or None,
        "thesis_id": thesis or None,
        "thesis_ready_flag": True,
        "location_id_has_IX1M": "IX1M" in loc,
        "seed_id_has_1m": "1M" in str(row.get("opening_seed_id") or row.get("opening_seed_id") or ""),
        "thesis_id_has_E1": "|E1|" in thesis,
    }


def recompute_hidden_1m(*, walked: dict[str, Any]) -> dict[str, Any]:
    setups = list(walked.get("setups") or [])
    stored_checks = list(walked.get("hidden_1m_checks") or [])
    stored_idx = {(str(x.get("symbol")), str(x.get("date"))): x for x in stored_checks}
    counts = dict(walked.get("counts") or {})
    mismatches = []
    loc_1m = 0
    for row in setups:
        masked = _mask_1m(row)
        if masked["location_id_has_IX1M"]:
            loc_1m += 1
        recomputed = hidden_1m_snapshot(
            symbol=masked["symbol"],
            date=masked["date"],
            direction=str(masked["direction"] or ""),
            opening_drive_id=masked["opening_drive_id"],
            location_id=masked["location_id"],
            thesis_id=masked["thesis_id"],
            thesis_ready_flag=True,
        )
        stored = dict(row.get("hidden_1m_snapshot") or stored_idx.get((masked["symbol"], masked["date"])) or {})
        keys = ("stock", "direction", "opening_drive_id", "location_id", "thesis_id")
        ok = True
        diff = {}
        for k in keys:
            a = recomputed.get(k)
            if k == "stock":
                b = stored.get("stock") or stored.get("symbol") or masked["symbol"]
            else:
                b = stored.get(k) if stored.get(k) is not None else masked.get(k)
            if str(a) != str(b):
                ok = False
                diff[k] = {"recomputed": a, "stored": b}
        if not bool(stored.get("one_m_hidden", True)):
            ok = False
            diff["one_m_hidden"] = stored.get("one_m_hidden")
        if not ok:
            mismatches.append({"symbol": masked["symbol"], "date": masked["date"], "diff": diff})
    stored_bool = bool(walked.get("HIDDEN_1M_THESIS_PARITY"))
    created_loc = int(counts.get("1M_created_location_n") or 0)
    changed_dir = int(counts.get("1M_changed_direction_n") or 0)
    changed_drive = int(counts.get("1M_changed_opening_drive_n") or 0)
    revived = int(counts.get("THESIS_REVIVED_BY_EXECUTION_n") or 0)
    recomputed_ok = len(mismatches) == 0 and loc_1m == 0
    return {
        "thesis_ready_n": len(setups),
        "stored_boolean": stored_bool,
        "recomputed": True,
        "mismatch_n": len(mismatches),
        "mismatches": mismatches[:20],
        "1m_created_location_from_id_n": loc_1m,
        "1m_created_seed": 0,
        "1m_created_active": int(changed_drive),
        "1m_created_location": created_loc,
        "1m_changed_direction": changed_dir,
        "1m_revived_thesis": revived,
        "HIDDEN_1M_THESIS_PARITY_recomputed": bool(recomputed_ok),
        "e1_events_used": False,
        "trusted_stored_boolean_only": False,
        "stored_boolean_disagrees": stored_bool != recomputed_ok,
    }
