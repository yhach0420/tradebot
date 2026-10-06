"""One mismatch class per reviewed disagreement. Persist exact reason."""
from __future__ import annotations

from typing import Any

from research.pb1_v4_clarified_machine_spec_parity_audit.reconstruct import earliest_human_known_t
from research.pb1_v4_opening_drive_location_reaccel_spec import VALID_OPENING_STATES

VALID_SEEDS = ("TRUE_OPENING_DRIVE_SEED", "FAILED_OPEN_SEED")
INVALID_AT_0915 = ("TWO_SIDED_OPEN", "FLAT_OR_CRAWL", "MICRO_OR_LEAK")


def _seed_valid(seed: Any) -> bool:
    return str(seed or "") in VALID_SEEDS


def classify_row(r: dict[str, Any], *, loc: dict[str, Any] | None = None) -> dict[str, Any]:
    human = str(r.get("human_opening_state") or "")
    late = bool(r.get("human_late")) or human == "LATE_RANGE_RESOLUTION"
    known = earliest_human_known_t(human, late=late)
    m_seed = _seed_valid(r.get("machine_SEED"))
    m_active = bool(r.get("machine_ACTIVE"))
    m_loc = bool(r.get("machine_LOCATION"))
    m_thesis = bool(r.get("machine_THESIS_READY"))
    h_valid = human in VALID_OPENING_STATES
    path = dict(r.get("path") or {})
    loc_t = r.get("location_t")
    cls = None
    reason = None
    layer = None

    if late and m_thesis:
        tiny = bool(path.get("would_stale_without_tiny_resets")) or int(path.get("tiny_reset_n") or 0) > 0
        loc_early = bool(loc_t) and str(loc_t) <= "09:24"
        if tiny and (not loc_early or str(loc_t or "") >= "09:30"):
            cls = "MACHINE_SPEC_VIOLATION"
            reason = (
                "Human LATE still THESIS_READY at location time. "
                "ACTIVE stayed alive because a new directional extreme, including a tiny one, reset stall. "
                "Clarified V2 requires continued directional auction, not any new high/low."
            )
            layer = "ACTIVE"
        elif loc_early:
            cls = "TIMING_LABEL_MISMATCH"
            reason = (
                f"Location identified at {loc_t}. Human LATE is usually not knowable at 09:15 and may not yet "
                "be observable at that early location time. Seed may have been a plausible drive that later went stale."
            )
            layer = "LOCATION"
        else:
            cls = "MACHINE_SPEC_VIOLATION"
            reason = (
                "Human LATE remains ACTIVE/THESIS at location-identification time. "
                "Unwind requires remaining disp < 0.25*peak; leftover drift with residual displacement never dies. "
                "V2 says ACTIVE dies when stale range-resolution develops or the directional auction clearly ends."
            )
            layer = "ACTIVE"
    elif human in INVALID_AT_0915 and m_seed:
        later_killed = not m_thesis
        if later_killed:
            cls = "EXPECTED_REPRESENTATION_DIFFERENCE"
            reason = (
                f"09:15 machine minted {r.get('machine_SEED')} on a human {human} chart. "
                "ACTIVE later died, so the seed may have been a plausible 09:15 read that was later correctly killed."
            )
            layer = "SEED"
        elif human == "TWO_SIDED_OPEN":
            cls = "MACHINE_SPEC_VIOLATION"
            reason = (
                "Human TWO_SIDED_OPEN remains TRUE/FAILED seed and THESIS_READY. "
                "two_sided_balance requires an uncommitted close loc, so a committed finish with a substantial "
                "opposite bar still mints TRUE."
            )
            layer = "SEED"
        elif human == "FLAT_OR_CRAWL":
            cls = "MACHINE_SPEC_VIOLATION"
            reason = (
                "Human FLAT_OR_CRAWL remains TRUE seed + ACTIVE/THESIS. "
                "Same-clock scale and continued-intent state accepted a crawl as a drive."
            )
            layer = "SEED"
        else:
            cls = "MACHINE_SPEC_VIOLATION"
            reason = "Human MICRO_OR_LEAK remains a valid seed and live thesis."
            layer = "SEED"
    elif human in INVALID_AT_0915 and not m_seed:
        cls = None
        reason = "human invalid opening correctly rejected at seed"
        layer = "SEED"
    elif h_valid and not late and not m_seed:
        cls = "MACHINE_SPEC_VIOLATION" if human == "FAILED_OPEN_THEN_REAL_DRIVE" else "HUMAN_LABEL_AMBIGUITY"
        reason = f"Human {human} but machine seed {r.get('machine_SEED')}"
        layer = "SEED"
    elif h_valid and not late and m_seed and not m_loc:
        cls = "IMPLEMENTATION_ASSUMPTION"
        reason = f"Valid opening seed but no LOCATION_IDENTIFIED (death={r.get('machine_death')})"
        layer = "LOCATION"
    elif h_valid and not late and m_loc and str(r.get("human_location") or "") != "CLEAR_DEFENDED_LOCATION":
        cls = "VALID_LEVEL_BUT_HUMAN_LABEL_DIFFERENT"
        reason = "Machine identified a location the human did not call CLEAR_DEFENDED_LOCATION"
        layer = "LOCATION"
        # remap to allowed mismatch class
        cls = "EXPECTED_REPRESENTATION_DIFFERENCE"
    elif m_loc != (str(r.get("human_location") or "") == "CLEAR_DEFENDED_LOCATION"):
        cls = "EXPECTED_REPRESENTATION_DIFFERENCE"
        reason = "LOCATION vs human CLEAR_DEFENDED_LOCATION disagreement after opening is aligned or LATE-excluded"
        layer = "LOCATION"
    else:
        cls = "EXPECTED_REPRESENTATION_DIFFERENCE"
        reason = "Residual layer disagreement without a structural V2 contradiction at the scored time"
        layer = "OTHER"

    disagreed = False
    if layer == "SEED" and known == "09:15":
        disagreed = (h_valid != m_seed)
    if layer == "ACTIVE":
        disagreed = True
    if layer == "LOCATION" and cls in (
        "MACHINE_SPEC_VIOLATION",
        "TIMING_LABEL_MISMATCH",
        "EXPECTED_REPRESENTATION_DIFFERENCE",
        "IMPLEMENTATION_ASSUMPTION",
    ):
        h_loc = str(r.get("human_location") or "") == "CLEAR_DEFENDED_LOCATION"
        disagreed = h_loc != m_loc or bool(late and m_thesis)

    return {
        "rca_id": r.get("rca_id"),
        "symbol": r.get("symbol"),
        "date": r.get("date"),
        "human_opening_state": human,
        "human_late": late,
        "human_known_t": known,
        "machine_SEED": r.get("machine_SEED"),
        "machine_ACTIVE": m_active,
        "machine_LOCATION": m_loc,
        "machine_THESIS_READY": m_thesis,
        "location_t": loc_t,
        "layer": layer,
        "mismatch_class": cls if disagreed else None,
        "reason": reason if disagreed else "aligned_at_scored_time",
        "disagreed": disagreed,
    }


def classify_all(rows: list[dict[str, Any]], *, loc_audit: dict[str, Any] | None = None) -> dict[str, Any]:
    out = [classify_row(r, loc=loc_audit) for r in rows]
    disagreed = [x for x in out if x.get("disagreed")]
    by = {}
    for x in disagreed:
        k = str(x.get("mismatch_class") or "OTHER")
        by[k] = int(by.get(k) or 0) + 1
    return {
        "n": len(out),
        "disagreed_n": len(disagreed),
        "by_class": by,
        "rows": out,
        "material_violation_rows": [x for x in disagreed if x.get("mismatch_class") == "MACHINE_SPEC_VIOLATION"],
        "material_violation_n": sum(1 for x in disagreed if x.get("mismatch_class") == "MACHINE_SPEC_VIOLATION"),
    }
