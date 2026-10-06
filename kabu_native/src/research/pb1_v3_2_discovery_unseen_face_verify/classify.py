"""Blinded human labels. Filled after actual chart inspection. Not machine self-match."""
from __future__ import annotations

from collections import Counter
from typing import Any


def _lab(
    pattern: str,
    opening: str,
    retest: str,
    location: str,
    trigger: str,
    *,
    note: str,
    late: bool = False,
) -> dict[str, Any]:
    return {
        "pattern": pattern,
        "opening": opening,
        "retest": retest,
        "location": location,
        "trigger": trigger,
        "late_stalled_leak": late,
        "note": note,
        "future_used": False,
    }


# Blinded chart review of V3.2 Discovery-unseen holdout. Daily/5m/1m through trigger only.
HUMAN_LABELS: dict[int, dict[str, Any]] = {
    1: _lab("QUESTIONABLE", "AMBIGUOUS_OPEN", "STALE_OR_EXHAUSTED", "QUESTIONABLE_LOCATION", "WEAK_REACCELERATION", late=True, note="Open dump then grind through OR_HIGH to 1700; 10:15 trigger is a leftover leak after the bounce already completed."),
    2: _lab("CLEAR_CONTINUATION", "CLEAR_DIRECTIONAL_AUCTION", "VALID_FIRST_RETEST", "CLEAR_DEFENDED_LOCATION", "VALID_REACCELERATION", note="Gap-up sold into a sustained dump; OR_LOW leave then first retest and continuation. Genuine failed-open bearish drive."),
    3: _lab("QUESTIONABLE", "AMBIGUOUS_OPEN", "VALID_FIRST_RETEST", "QUESTIONABLE_LOCATION", "WEAK_REACCELERATION", note="Tight crawl under OR then a micro drip; not a distinctive opening dump even though a first retest exists."),
    4: _lab("QUESTIONABLE", "TWO_SIDED_OR_RANGE", "VALID_FIRST_RETEST", "QUESTIONABLE_LOCATION", "WEAK_REACCELERATION", note="Flat opening range; tiny OR_HIGH leak with no visible expansion."),
    5: _lab("QUESTIONABLE", "TWO_SIDED_OR_RANGE", "VALID_FIRST_RETEST", "QUESTIONABLE_LOCATION", "WEAK_REACCELERATION", note="Tight grind along OR_HIGH; EARLY_REVERSAL tag does not match a human directional auction."),
    6: _lab("QUESTIONABLE", "AMBIGUOUS_OPEN", "VALID_FIRST_RETEST", "QUESTIONABLE_LOCATION", "WEAK_REACCELERATION", note="Micro OR_HIGH poke on a bounce tape; trigger expansion is vs a tiny retest bar, not a real drive."),
    7: _lab("NOT_CONTINUATION", "TWO_SIDED_OR_RANGE", "QUESTIONABLE_RETEST", "QUESTIONABLE_LOCATION", "INVALID_TRIGGER", note="Almost a flat line at 155 with R=0.4; congestion, not an opening-range continuation."),
    8: _lab("NOT_CONTINUATION", "AMBIGUOUS_OPEN", "QUESTIONABLE_RETEST", "QUESTIONABLE_LOCATION", "WEAK_REACCELERATION", note="Daily waterfall; tiny OR_HIGH leak on a falling tape is not opening continuation."),
    9: _lab("QUESTIONABLE", "AMBIGUOUS_OPEN", "VALID_FIRST_RETEST", "QUESTIONABLE_LOCATION", "WEAK_REACCELERATION", note="Early but microscopic OR_HIGH leak into the PDH cluster; no distinctive auction."),
    10: _lab("QUESTIONABLE", "AMBIGUOUS_OPEN", "VALID_FIRST_RETEST", "QUESTIONABLE_LOCATION", "WEAK_REACCELERATION", note="Slow drip under OR_LOW on a daily uptrend; not a failed-open dump."),
    11: _lab("QUESTIONABLE", "AMBIGUOUS_OPEN", "STALE_OR_EXHAUSTED", "QUESTIONABLE_LOCATION", "WEAK_REACCELERATION", late=True, note="Hour of range then 09:53–09:59 leftover leak; opening thesis already spent."),
    12: _lab("QUESTIONABLE", "TWO_SIDED_OR_RANGE", "VALID_FIRST_RETEST", "QUESTIONABLE_LOCATION", "WEAK_REACCELERATION", note="Extremely tight OR_HIGH crawl; micro cross, not reacceleration."),
    13: _lab("QUESTIONABLE", "AMBIGUOUS_OPEN", "VALID_FIRST_RETEST", "QUESTIONABLE_LOCATION", "WEAK_REACCELERATION", note="Early OR_LOW poke but the 1m is a flat line; no visible directional auction."),
    14: _lab("NOT_CONTINUATION", "AMBIGUOUS_OPEN", "VALID_FIRST_RETEST", "QUESTIONABLE_LOCATION", "WEAK_REACCELERATION", note="Countertrend OR_LOW leak at the high of a vertical daily uptrend; not opening continuation."),
    15: _lab("QUESTIONABLE", "AMBIGUOUS_OPEN", "VALID_FIRST_RETEST", "QUESTIONABLE_LOCATION", "WEAK_REACCELERATION", note="Small dip-and-grind back through OR_HIGH into overhead bands on a rolling daily."),
    16: _lab("QUESTIONABLE", "AMBIGUOUS_OPEN", "STALE_OR_EXHAUSTED", "QUESTIONABLE_LOCATION", "WEAK_REACCELERATION", late=True, note="21-minute sit after 09:37 break; 09:59 trigger is a stalled leftover drip."),
    17: _lab("QUESTIONABLE", "AMBIGUOUS_OPEN", "STALE_OR_EXHAUSTED", "QUESTIONABLE_LOCATION", "WEAK_REACCELERATION", late=True, note="19-minute grind after 09:16 break; tight CLEARED_ZONE leak, not a drive."),
    18: _lab("QUESTIONABLE", "TWO_SIDED_OR_RANGE", "QUESTIONABLE_RETEST", "QUESTIONABLE_LOCATION", "WEAK_REACCELERATION", late=True, note="Late-morning micro OR_HIGH leak on a flat open; not reacceleration."),
    19: _lab("QUESTIONABLE", "AMBIGUOUS_OPEN", "VALID_FIRST_RETEST", "QUESTIONABLE_LOCATION", "WEAK_REACCELERATION", note="Parabolic daily; 1m is a flat 10000-yen crawl through OR_HIGH into PDH."),
    20: _lab("QUESTIONABLE", "AMBIGUOUS_OPEN", "VALID_FIRST_RETEST", "QUESTIONABLE_LOCATION", "WEAK_REACCELERATION", note="Early OR_LOW drip exists but it is a small crawl, not a distinctive opening selloff."),
    21: _lab("QUESTIONABLE", "AMBIGUOUS_OPEN", "VALID_FIRST_RETEST", "QUESTIONABLE_LOCATION", "WEAK_REACCELERATION", note="Tiny OR_HIGH leak on a recovering daily; no visible opening drive or expansion."),
}


def apply_labels(sample: list[dict[str, Any]]) -> dict[str, Any]:
    labeled: list[dict[str, Any]] = []
    missing = 0
    for ev in sample:
        sid = int(ev.get("sample_id") or 0)
        lab = dict(HUMAN_LABELS.get(sid) or {})
        reviewed = bool(lab)
        if not reviewed:
            missing += 1
        labeled.append(
            {
                "sample_id": sid,
                "symbol": ev.get("symbol"),
                "date": ev.get("date"),
                "block": ev.get("block"),
                "direction": ev.get("direction"),
                "DIR": ev.get("DIR"),
                "auction": ev.get("auction"),
                "trigger_time": ev.get("trigger_t"),
                "entry_time": ev.get("entry_t"),
                "structural_route": ev.get("structural_route"),
                "zone_class": ev.get("zone_class"),
                "defended_level_type": ev.get("defended_level_type"),
                "planned_R": ev.get("planned_R"),
                "chart": f"sample_{sid:02d}_{ev.get('symbol')}_{ev.get('date')}_{ev.get('direction')}.png",
                "reviewed": reviewed,
                "machine_self_match_used": False,
                "independent_face_sample": True,
                **lab,
            }
        )
    n = sum(1 for r in labeled if r.get("reviewed"))
    patterns = Counter(str(r.get("pattern")) for r in labeled if r.get("reviewed"))
    openings = Counter(str(r.get("opening")) for r in labeled if r.get("reviewed"))
    retests = Counter(str(r.get("retest")) for r in labeled if r.get("reviewed"))
    locs = Counter(str(r.get("location")) for r in labeled if r.get("reviewed"))
    trigs = Counter(str(r.get("trigger")) for r in labeled if r.get("reviewed"))
    clear = int(patterns.get("CLEAR_CONTINUATION") or 0)
    q = int(patterns.get("QUESTIONABLE") or 0)
    notn = int(patterns.get("NOT_CONTINUATION") or 0)
    drive_ok = int(openings.get("CLEAR_DIRECTIONAL_AUCTION") or 0)
    early = [r for r in labeled if r.get("reviewed") and str(r.get("auction")) == "EARLY_REVERSAL_THEN_DOMINANT_DRIVE"]
    early_ok = sum(1 for r in early if r.get("opening") == "CLEAR_DIRECTIONAL_AUCTION")
    first_ok = int(retests.get("VALID_FIRST_RETEST") or 0)
    loc_ok = int(locs.get("CLEAR_DEFENDED_LOCATION") or 0)
    reacc_ok = int(trigs.get("VALID_REACCELERATION") or 0)
    reacc_weak = int(trigs.get("WEAK_REACCELERATION") or 0)
    late_n = int(sum(1 for r in labeled if r.get("reviewed") and r.get("late_stalled_leak")))
    blocked = int(locs.get("STRUCTURALLY_BAD") or 0)
    return {
        "reviewed_n": n,
        "missing_n": missing,
        "universe_n": len(sample),
        "all_unseen_events_reviewed": missing == 0 and n == len(sample) and n > 0,
        "actual_manual_blinded_review": missing == 0 and n == len(sample) and n > 0,
        "CLEAR_CONTINUATION": clear,
        "QUESTIONABLE": q,
        "NOT_CONTINUATION": notn,
        "clear_share": float(clear / n) if n else None,
        "questionable_share": float(q / n) if n else None,
        "not_share": float(notn / n) if n else None,
        "opening_counts": dict(openings),
        "retest_counts": dict(retests),
        "location_counts": dict(locs),
        "trigger_counts": dict(trigs),
        "opening_drive_valid_n": drive_ok,
        "opening_drive_valid_share": float(drive_ok / n) if n else None,
        "early_reversal_n": len(early),
        "early_reversal_drive_valid_n": early_ok,
        "early_reversal_drive_valid_share": float(early_ok / len(early)) if early else None,
        "valid_first_retest_n": first_ok,
        "valid_first_retest_share": float(first_ok / n) if n else None,
        "clear_defended_location_n": loc_ok,
        "clear_defended_location_share": float(loc_ok / n) if n else None,
        "valid_reacceleration_n": reacc_ok,
        "valid_reacceleration_share": float(reacc_ok / n) if n else None,
        "weak_reacceleration_n": reacc_weak,
        "weak_reacceleration_share": float(reacc_weak / n) if n else None,
        "late_stalled_leak_n": late_n,
        "late_stalled_leak_share": float(late_n / n) if n else None,
        "structurally_blocked_leak_n": blocked,
        "structurally_blocked_leak_share": float(blocked / n) if n else None,
        "future_used": False,
        "rows": labeled,
    }
