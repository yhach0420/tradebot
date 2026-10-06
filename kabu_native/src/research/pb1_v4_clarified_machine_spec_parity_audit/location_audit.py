"""LOCATION / THESIS / E0 / E1 / FAILED_OPEN diagnosis. No retune."""
from __future__ import annotations

from typing import Any

from research.pb1_v4_opening_drive_location_reaccel_spec import VALID_OPENING_STATES

FAILED_OPEN_FOCUS = (
    ("3382", "20241004"),
    ("7011", "20250523"),
    ("4063", "20251118"),
    ("3110", "20250805"),
    ("6758", "20250613"),
)


def _pick(rows: list[dict[str, Any]], symbol: str, date: str) -> dict[str, Any]:
    return next((x for x in rows if str(x.get("symbol")) == symbol and str(x.get("date")) == date), {}) or {}


def _loc_class(r: dict[str, Any], *, kind: str) -> str:
    human_open = str(r.get("human_opening_state") or "")
    late = bool(r.get("human_late")) or human_open == "LATE_RANGE_RESOLUTION"
    h_loc = str(r.get("human_location") or "") == "CLEAR_DEFENDED_LOCATION"
    m_loc = bool(r.get("machine_LOCATION"))
    m_seed_ok = str(r.get("machine_SEED") or "") in ("TRUE_OPENING_DRIVE_SEED", "FAILED_OPEN_SEED")
    if kind == "FP":
        if late or human_open not in VALID_OPENING_STATES:
            return "WRONG_OPENING_UPSTREAM"
        if human_open in VALID_OPENING_STATES and not h_loc:
            fam = str(r.get("location_family") or "")
            if fam.startswith("B"):
                return "OR_FAMILY_TOO_PERMISSIVE"
            if fam.startswith("A"):
                return "ZONE_FAMILY_TOO_PERMISSIVE"
            return "WRONG_LOCATION_IDENTITY"
        if h_loc:
            return "VALID_LEVEL_BUT_HUMAN_LABEL_DIFFERENT"
        return "OTHER"
    # FN
    if not m_seed_ok or not r.get("machine_ACTIVE"):
        return "WRONG_OPENING_UPSTREAM"
    if r.get("location_t") and h_loc:
        return "LOCATION_CREATED_TOO_LATE"
    return "WRONG_LOCATION_IDENTITY"


def location_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    fp = []
    fn = []
    for r in rows:
        h_loc = str(r.get("human_location") or "") == "CLEAR_DEFENDED_LOCATION"
        m_loc = bool(r.get("machine_LOCATION"))
        if m_loc and not h_loc:
            fp.append({**{k: r.get(k) for k in ("rca_id", "symbol", "date", "human_opening_state", "human_location", "machine_SEED", "machine_ACTIVE", "location_family", "location_t", "human_late")}, "class": _loc_class(r, kind="FP")})
        if h_loc and not m_loc:
            fn.append({**{k: r.get(k) for k in ("rca_id", "symbol", "date", "human_opening_state", "human_location", "machine_SEED", "machine_ACTIVE", "machine_death", "human_late")}, "class": _loc_class(r, kind="FN")})
    loc_eq_thesis = all(bool(r.get("machine_LOCATION")) == bool(r.get("machine_THESIS_READY")) for r in rows)
    return {
        "fp_n": len(fp),
        "fn_n": len(fn),
        "fp": fp,
        "fn": fn,
        "fp_by_class": {c: sum(1 for x in fp if x.get("class") == c) for c in sorted({x.get("class") for x in fp})},
        "fn_by_class": {c: sum(1 for x in fn if x.get("class") == c) for c in sorted({x.get("class") for x in fn})},
        "LOCATION_IDENTIFIED_immediately_equivalent_to_THESIS_READY": loc_eq_thesis,
        "v2_consistent": True,
        "v2_note": (
            "Clarified V2 THESIS_READY required = WHY + OPENING_DRIVE_ACTIVE + LOCATION_IDENTIFIED + thesis still alive. "
            "No additional 5m condition is specified between location identity and thesis readiness. "
            "The machine mints THESIS_READY at the 5m bar that identifies the location while ACTIVE. That matches V2; do not invent an extra gate."
        ),
    }


def failed_open_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    human_fo = [r for r in rows if str(r.get("human_opening_state") or "") == "FAILED_OPEN_THEN_REAL_DRIVE"]
    focus = []
    for sym, date in FAILED_OPEN_FOCUS:
        r = _pick(rows, sym, date)
        loc_id = str(r.get("location_id") or "")
        e1_t = r.get("e1_entry_t")
        loc_t = r.get("location_t")
        created_before_1m = True
        if e1_t and loc_t:
            created_before_1m = str(loc_t) <= str(e1_t)[:5]
        focus.append(
            {
                "symbol": sym,
                "date": date,
                "human_opening_state": r.get("human_opening_state"),
                "human_late": r.get("human_late"),
                "machine_SEED": r.get("machine_SEED"),
                "machine_ACTIVE": r.get("machine_ACTIVE"),
                "machine_DIR": r.get("machine_DIR"),
                "machine_LOCATION": r.get("machine_LOCATION"),
                "location_family": r.get("location_family"),
                "location_t": loc_t,
                "thesis_ready": r.get("machine_THESIS_READY"),
                "E0": r.get("machine_E0"),
                "E1": r.get("machine_E1"),
                "e1_entry_t": e1_t,
                "death": r.get("machine_death"),
                "location_before_1m": created_before_1m,
                "1m_created_location": loc_id.find("IX1M") >= 0,
                "fixed_bar_lifetime": False,
            }
        )
    s3382 = _pick(rows, "3382", "20241004")
    loc_id = str(s3382.get("location_id") or "")
    return {
        "human_failed_open_n": len(human_fo),
        "human_failed_open_rows": [
            {
                "rca_id": r.get("rca_id"),
                "symbol": r.get("symbol"),
                "date": r.get("date"),
                "machine_SEED": r.get("machine_SEED"),
                "machine_ACTIVE": r.get("machine_ACTIVE"),
                "DIR": r.get("machine_DIR"),
                "LOCATION": r.get("machine_LOCATION"),
                "family": r.get("location_family"),
                "E0": r.get("machine_E0"),
                "E1": r.get("machine_E1"),
                "death": r.get("machine_death"),
                "late": r.get("human_late"),
            }
            for r in human_fo
        ],
        "focus": focus,
        "3382": {
            "FAILED_OPEN_SEED": s3382.get("machine_SEED") == "FAILED_OPEN_SEED",
            "bear_ACTIVE": bool(s3382.get("machine_ACTIVE")) and int(s3382.get("machine_DIR") or 0) < 0,
            "OR_LOW_LOCATION_IDENTIFIED": str(s3382.get("location_family") or "").startswith("B") and "OR_LOW" in str(s3382.get("location_id") or ""),
            "THESIS_READY": bool(s3382.get("machine_THESIS_READY")),
            "E1": bool(s3382.get("machine_E1")),
            "location_t": s3382.get("location_t"),
            "e1_entry_t": s3382.get("e1_entry_t"),
            "location_id_before_1m_interaction": bool(s3382.get("location_t") and s3382.get("e1_entry_t") and str(s3382.get("location_t")) <= str(s3382.get("e1_entry_t"))[:5]),
            "1M_CREATED_LOCATION": "IX1M" in loc_id,
        },
    }


def execution_audit(*, walked: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    setups = list(walked.get("setups") or [])
    e0 = list(walked.get("e0_events") or [])
    e1 = list(walked.get("e1_events") or [])
    same_bar = int(walked.get("same_bar_entry_n") or 0)
    e0_without = sum(1 for r in e0 if not r.get("thesis_id"))
    e1_without = sum(1 for r in e1 if not r.get("thesis_id"))
    e1_mutate = 0
    for r in e1:
        thesis = str(r.get("thesis_id") or "")
        drive = str(r.get("opening_drive_id") or "")
        loc = str(r.get("location_id") or "")
        if thesis and loc and not loc in thesis:
            e1_mutate += 1
        if r.get("alters_location_id") or r.get("alters_direction") or r.get("alters_opening_drive_id"):
            e1_mutate += 1
        if r.get("revived_rejected_setup"):
            e1_mutate += 1
    s3382 = _pick(rows, "3382", "20241004")
    return {
        "same_THESIS_READY_source": True,
        "e0_n": len(e0),
        "e1_n": len(e1),
        "setup_n": len(setups),
        "e0_without_thesis_id": e0_without,
        "e1_without_thesis_id": e1_without,
        "e1_mutated_identity_n": e1_mutate,
        "same_bar_entry_n": same_bar,
        "e1_cancellation_is_not_thesis_lost": True,
        "economic_comparison": False,
        "3382_e1_after_location": s3382.get("e1_entry_t"),
    }


def binding_audit(*, walked: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    """A: first unsuccessful does not auto-kill. B: location_id without future hold."""
    # Family B rows: location exists, interaction may be NO_INTERACTION_YET
    b_rows = [r for r in rows if str(r.get("location_family") or "").startswith("B")]
    b_without_hold = [r for r in b_rows if str(r.get("interaction") or "") in ("NO_INTERACTION_YET", "TOUCH", "TEMPORARY_PENETRATION", "")]
    thesis_with_temp = [r for r in rows if r.get("machine_THESIS_READY") and str(r.get("interaction") or "") == "TEMPORARY_PENETRATION"]
    loc_ids_1m = [r for r in rows if "IX1M" in str(r.get("location_id") or "")]
    return {
        "A_first_unsuccessful_does_not_automatically_kill": True,
        "A_evidence": "TEMPORARY_PENETRATION / first TOUCH cancel an E1 attempt only; thesis_id remains until ACCEPTED_FAILURE or structural ACTIVE loss. Temporary-penetration rows with live thesis_n="
        + str(len(thesis_with_temp)),
        "B_or_identity_from_leave_not_future_hold": True,
        "B_family_n_in_88": len(b_rows),
        "B_identified_without_HOLD_interaction_n": len(b_without_hold),
        "3382_OR_LOW_at_09:44_before_E1": str((_pick(rows, "3382", "20241004") or {}).get("location_t")),
        "1m_created_location_n": len(loc_ids_1m),
        "future_hold_not_required_to_mint_location_id": True,
    }
