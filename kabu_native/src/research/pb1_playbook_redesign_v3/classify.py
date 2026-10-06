"""Blinded human labels. Filled after actual chart inspection. Not machine self-match."""
from __future__ import annotations

from collections import Counter
from typing import Any


def _lab(
    pattern: str,
    location: str,
    retest: str,
    trigger: str,
    *,
    a: bool,
    b: bool,
    c: bool,
    d: bool,
    e: bool,
    f: bool,
    g: bool,
    h: bool,
    i: bool,
    note: str,
) -> dict[str, Any]:
    return {
        "pattern": pattern,
        "location": location,
        "retest_lab": retest,
        "trigger_lab": trigger,
        "A_opening_continuation": a,
        "B_impulse_directional": b,
        "C_left_or": c,
        "D_first_meaningful_retest": d,
        "E_coherent_level_defended": e,
        "F_micro_reclaim_reasonable": f,
        "G_visible_room": g,
        "H_already_reached_objective": h,
        "I_already_extended_stale": i,
        "note": note,
        "future_used": False,
    }


# Blinded chart review of V3 FACE_VERIFY sample 01–67. Daily/5m/1m through trigger only.
# Labels are visual face validity, not machine self-match and not future PnL.
HUMAN_LABELS: dict[int, dict[str, Any]] = {
    1: _lab("QUESTIONABLE", "QUESTIONABLE_ROUTE", "AMBIGUOUS", "WEAK_RECLAIM", a=False, b=False, c=True, d=True, e=True, f=False, g=False, h=False, i=True, note="09:36 micro leak along OR_HIGH; range drip not a distinctive opening continuation."),
    2: _lab("CLEAR_CONTINUATION", "CLEAR_ROUTE", "FRESH_RETEST", "VALID_RECLAIM", a=True, b=True, c=True, d=True, e=True, f=True, g=True, h=False, i=False, note="Opening drive from OR_LOW, genuine leave, first OR_HIGH retest, reclaim."),
    3: _lab("QUESTIONABLE", "QUESTIONABLE_ROUTE", "AMBIGUOUS", "WEAK_RECLAIM", a=False, b=False, c=True, d=True, e=True, f=False, g=False, h=False, i=False, note="R=3 grind at 1460 on a daily downtrend bounce; micro congestion."),
    4: _lab("QUESTIONABLE", "QUESTIONABLE_ROUTE", "AMBIGUOUS", "WEAK_RECLAIM", a=False, b=False, c=True, d=True, e=True, f=False, g=False, h=False, i=False, note="Post-split crawl along OR_HIGH; not a real opening impulse."),
    5: _lab("QUESTIONABLE", "QUESTIONABLE_ROUTE", "AMBIGUOUS", "WEAK_RECLAIM", a=False, b=False, c=True, d=True, e=True, f=False, g=False, h=True, i=True, note="09:41 late OR_HIGH sit already at/above PDH; late range resolution."),
    6: _lab("CLEAR_CONTINUATION", "CLEAR_ROUTE", "FRESH_RETEST", "VALID_RECLAIM", a=True, b=True, c=True, d=True, e=True, f=True, g=True, h=False, i=False, note="Opening drip through OR_LOW, leave, first retest, reclaim."),
    7: _lab("CLEAR_CONTINUATION", "CLEAR_ROUTE", "FRESH_RETEST", "VALID_RECLAIM", a=True, b=True, c=True, d=True, e=True, f=True, g=True, h=False, i=False, note="Opening high then dump through OR_LOW 09:25; hold below; room down."),
    8: _lab("QUESTIONABLE", "QUESTIONABLE_ROUTE", "AMBIGUOUS", "WEAK_RECLAIM", a=False, b=False, c=True, d=True, e=True, f=False, g=False, h=False, i=False, note="R=3.5 crawl under OR_HIGH; 09:30 leak not distinctive."),
    9: _lab("QUESTIONABLE", "QUESTIONABLE_ROUTE", "EXHAUSTED_RETEST", "WEAK_RECLAIM", a=False, b=False, c=True, d=True, e=True, f=False, g=False, h=False, i=True, note="09:17 break then 24m grind before retest 09:41; already stale-feeling."),
    10: _lab("CLEAR_CONTINUATION", "QUESTIONABLE_ROUTE", "FRESH_RETEST", "VALID_RECLAIM", a=True, b=True, c=True, d=True, e=True, f=True, g=False, h=False, i=False, note="Opening drive 8500 through OR_HIGH ~8880 is real, but pink resistance bands sit immediately overhead."),
    11: _lab("QUESTIONABLE", "QUESTIONABLE_ROUTE", "AMBIGUOUS", "WEAK_RECLAIM", a=False, b=False, c=True, d=True, e=True, f=False, g=False, h=False, i=True, note="Daily downtrend; 09:37 leak of OR_HIGH on a falling tape."),
    12: _lab("CLEAR_CONTINUATION", "CLEAR_ROUTE", "FRESH_RETEST", "VALID_RECLAIM", a=True, b=True, c=True, d=True, e=True, f=True, g=True, h=False, i=False, note="Opening grind-up, 09:15 OR_HIGH break, short leave, reclaim."),
    13: _lab("CLEAR_CONTINUATION", "CLEAR_ROUTE", "FRESH_RETEST", "VALID_RECLAIM", a=True, b=True, c=True, d=True, e=True, f=True, g=True, h=False, i=False, note="Gap-up sold; 09:19 OR_LOW break, 09:25 retest; directional opening dump."),
    14: _lab("QUESTIONABLE", "QUESTIONABLE_ROUTE", "AMBIGUOUS", "WEAK_RECLAIM", a=False, b=False, c=True, d=True, e=True, f=False, g=False, h=False, i=True, note="09:53 late R=8 crawl on a daily uptrend; late range leak."),
    15: _lab("QUESTIONABLE", "QUESTIONABLE_ROUTE", "FRESH_RETEST", "WEAK_RECLAIM", a=False, b=True, c=True, d=True, e=True, f=False, g=False, h=False, i=False, note="Opening dump sequence exists but planned_R=0.3 yen is not a tradable continuation R."),
    16: _lab("QUESTIONABLE", "QUESTIONABLE_ROUTE", "EXHAUSTED_RETEST", "WEAK_RECLAIM", a=False, b=True, c=True, d=True, e=True, f=False, g=False, h=False, i=True, note="Opening dump then 21m grind; trigger 09:57 is stale."),
    17: _lab("QUESTIONABLE", "QUESTIONABLE_ROUTE", "FRESH_RETEST", "WEAK_RECLAIM", a=False, b=False, c=True, d=True, e=True, f=False, g=False, h=False, i=False, note="Early 09:16 break but tape is a tight crawl, not a distinctive impulse."),
    18: _lab("QUESTIONABLE", "QUESTIONABLE_ROUTE", "EXHAUSTED_RETEST", "WEAK_RECLAIM", a=False, b=False, c=True, d=True, e=True, f=False, g=False, h=False, i=True, note="09:52 break / 10:07 trigger; late morning, not an opening continuation."),
    19: _lab("QUESTIONABLE", "QUESTIONABLE_ROUTE", "FRESH_RETEST", "WEAK_RECLAIM", a=False, b=False, c=True, d=True, e=True, f=False, g=False, h=False, i=False, note="Grind along OR then 09:17 leak; tight, not distinctive."),
    20: _lab("QUESTIONABLE", "QUESTIONABLE_ROUTE", "FRESH_RETEST", "WEAK_RECLAIM", a=False, b=False, c=True, d=True, e=True, f=False, g=False, h=False, i=False, note="Post-split ~1000 yen name; R=5 micro leak after a daily crash."),
    21: _lab("QUESTIONABLE", "QUESTIONABLE_ROUTE", "FRESH_RETEST", "WEAK_RECLAIM", a=False, b=False, c=True, d=True, e=True, f=False, g=False, h=False, i=False, note="09:16 tiny OR_HIGH break then immediate retest; leak not a drive."),
    22: _lab("CLEAR_CONTINUATION", "CLEAR_ROUTE", "FRESH_RETEST", "VALID_RECLAIM", a=True, b=True, c=True, d=True, e=True, f=True, g=True, h=False, i=False, note="Cleared PDH/OR cluster then first retest of the far side; coherent hold."),
    23: _lab("QUESTIONABLE", "QUESTIONABLE_ROUTE", "EXHAUSTED_RETEST", "WEAK_RECLAIM", a=False, b=False, c=True, d=True, e=True, f=False, g=False, h=False, i=True, note="09:38 break, trigger 10:06 after a long grind; late."),
    24: _lab("QUESTIONABLE", "QUESTIONABLE_ROUTE", "FRESH_RETEST", "WEAK_RECLAIM", a=False, b=False, c=True, d=True, e=True, f=False, g=False, h=False, i=False, note="R=5 crawl along OR_HIGH; 09:29 leak."),
    25: _lab("CLEAR_CONTINUATION", "CLEAR_ROUTE", "FRESH_RETEST", "VALID_RECLAIM", a=True, b=True, c=True, d=True, e=True, f=True, g=True, h=False, i=False, note="Opening drive 22600→23000, 09:25 break, 09:29 retest; R=105."),
    26: _lab("QUESTIONABLE", "QUESTIONABLE_ROUTE", "FRESH_RETEST", "WEAK_RECLAIM", a=False, b=False, c=True, d=True, e=True, f=False, g=False, h=False, i=False, note="R=8 grind at 3200 along OR_HIGH."),
    27: _lab("QUESTIONABLE", "QUESTIONABLE_ROUTE", "EXHAUSTED_RETEST", "WEAK_RECLAIM", a=False, b=False, c=True, d=True, e=True, f=False, g=False, h=False, i=True, note="09:52 / 10:08 late morning leak after sitting under OR_HIGH."),
    28: _lab("QUESTIONABLE", "QUESTIONABLE_ROUTE", "FRESH_RETEST", "WEAK_RECLAIM", a=False, b=True, c=True, d=True, e=True, f=False, g=False, h=False, i=False, note="Opening jump then grind; 09:28 R=7 leak."),
    29: _lab("QUESTIONABLE", "QUESTIONABLE_ROUTE", "FRESH_RETEST", "WEAK_RECLAIM", a=False, b=False, c=True, d=True, e=True, f=False, g=False, h=False, i=False, note="Tiny 09:25 leak after a daily dump; not a directional opening drive."),
    30: _lab("CLEAR_CONTINUATION", "CLEAR_ROUTE", "FRESH_RETEST", "VALID_RECLAIM", a=True, b=True, c=True, d=True, e=True, f=True, g=True, h=False, i=False, note="Opening dump through 1700, cleared zone, first far-side retest."),
    31: _lab("CLEAR_CONTINUATION", "CLEAR_ROUTE", "FRESH_RETEST", "VALID_RECLAIM", a=True, b=True, c=True, d=True, e=True, f=True, g=True, h=False, i=False, note="Opening dump 4120→4050, 09:16 break, 09:23 retest of OR_LOW."),
    32: _lab("QUESTIONABLE", "QUESTIONABLE_ROUTE", "AMBIGUOUS", "WEAK_RECLAIM", a=False, b=False, c=True, d=True, e=True, f=False, g=False, h=False, i=True, note="09:54 / 10:01 late range leak; R=7."),
    33: _lab("CLEAR_CONTINUATION", "CLEAR_ROUTE", "FRESH_RETEST", "VALID_RECLAIM", a=True, b=True, c=True, d=True, e=True, f=True, g=True, h=False, i=False, note="09:15 opening dump, leave, first retest of cleared zone at 09:31."),
    34: _lab("QUESTIONABLE", "QUESTIONABLE_ROUTE", "AMBIGUOUS", "WEAK_RECLAIM", a=False, b=False, c=True, d=True, e=True, f=False, g=False, h=False, i=True, note="09:46 late break after a long morning grind."),
    35: _lab("QUESTIONABLE", "QUESTIONABLE_ROUTE", "FRESH_RETEST", "WEAK_RECLAIM", a=False, b=False, c=True, d=True, e=True, f=False, g=False, h=False, i=False, note="09:23 tiny leak of OR_LOW; small leave, not a distinctive dump."),
    36: _lab("QUESTIONABLE", "QUESTIONABLE_ROUTE", "FRESH_RETEST", "WEAK_RECLAIM", a=False, b=False, c=True, d=True, e=True, f=False, g=False, h=False, i=False, note="R=2.5 micro OR_LOW leak."),
    37: _lab("QUESTIONABLE", "QUESTIONABLE_ROUTE", "FRESH_RETEST", "WEAK_RECLAIM", a=False, b=False, c=True, d=True, e=True, f=False, g=False, h=False, i=False, note="CLEARED_ZONE but R=2; not a face-valid continuation R."),
    38: _lab("QUESTIONABLE", "QUESTIONABLE_ROUTE", "FRESH_RETEST", "WEAK_RECLAIM", a=False, b=False, c=True, d=True, e=True, f=False, g=False, h=False, i=False, note="ZONE_NOT_CLEARED crawl along OR_LOW; leak not a dump."),
    39: _lab("CLEAR_CONTINUATION", "CLEAR_ROUTE", "FRESH_RETEST", "VALID_RECLAIM", a=True, b=True, c=True, d=True, e=True, f=True, g=True, h=False, i=False, note="09:17 dump, cleared zone, 09:19 retest of far side."),
    40: _lab("QUESTIONABLE", "QUESTIONABLE_ROUTE", "EXHAUSTED_RETEST", "WEAK_RECLAIM", a=False, b=False, c=True, d=True, e=True, f=False, g=False, h=False, i=True, note="16m leave before retest; R=7 grind."),
    41: _lab("QUESTIONABLE", "QUESTIONABLE_ROUTE", "AMBIGUOUS", "WEAK_RECLAIM", a=False, b=False, c=True, d=True, e=True, f=False, g=False, h=False, i=True, note="09:49 R=3 late leak."),
    42: _lab("CLEAR_CONTINUATION", "CLEAR_ROUTE", "FRESH_RETEST", "VALID_RECLAIM", a=True, b=True, c=True, d=True, e=True, f=True, g=True, h=False, i=False, note="Opening drive, 09:25 break of OR/zone, later first far-side retest."),
    43: _lab("QUESTIONABLE", "QUESTIONABLE_ROUTE", "AMBIGUOUS", "WEAK_RECLAIM", a=False, b=False, c=True, d=True, e=True, f=False, g=False, h=False, i=True, note="09:37 break / 09:54 retest after sitting on OR_HIGH."),
    44: _lab("QUESTIONABLE", "QUESTIONABLE_ROUTE", "EXHAUSTED_RETEST", "WEAK_RECLAIM", a=False, b=True, c=True, d=True, e=True, f=False, g=False, h=True, i=True, note="Opening drive 5950→6200 already completed; 09:49 OR leak is a late leftover."),
    45: _lab("QUESTIONABLE", "QUESTIONABLE_ROUTE", "FRESH_RETEST", "WEAK_RECLAIM", a=False, b=False, c=True, d=True, e=True, f=False, g=False, h=False, i=False, note="09:33 grind leak of OR_HIGH."),
    46: _lab("QUESTIONABLE", "QUESTIONABLE_ROUTE", "FRESH_RETEST", "WEAK_RECLAIM", a=False, b=False, c=True, d=True, e=True, f=False, g=False, h=False, i=False, note="09:15 tiny OR_HIGH leak; R~9.7 micro."),
    47: _lab("CLEAR_CONTINUATION", "CLEAR_ROUTE", "FRESH_RETEST", "VALID_RECLAIM", a=True, b=True, c=True, d=True, e=True, f=True, g=True, h=False, i=False, note="Opening drive 4700→4930, 09:22 break, 09:25 retest of OR_HIGH."),
    48: _lab("QUESTIONABLE", "QUESTIONABLE_ROUTE", "FRESH_RETEST", "WEAK_RECLAIM", a=False, b=False, c=True, d=True, e=True, f=False, g=False, h=False, i=False, note="Tiny leak at 20600 after a daily crash; not an opening drive."),
    49: _lab("QUESTIONABLE", "QUESTIONABLE_ROUTE", "AMBIGUOUS", "WEAK_RECLAIM", a=False, b=True, c=True, d=True, e=True, f=False, g=False, h=False, i=False, note="Opening bounce then 14m; pink band immediately above 4450."),
    50: _lab("QUESTIONABLE", "QUESTIONABLE_ROUTE", "FRESH_RETEST", "WEAK_RECLAIM", a=False, b=False, c=True, d=True, e=True, f=False, g=False, h=False, i=False, note="R=5 OR leak into overhead pink bands."),
    51: _lab("QUESTIONABLE", "QUESTIONABLE_ROUTE", "FRESH_RETEST", "WEAK_RECLAIM", a=False, b=False, c=True, d=True, e=True, f=False, g=False, h=False, i=False, note="R=7 grind along 3150 OR_HIGH."),
    52: _lab("NOT_CONTINUATION", "QUESTIONABLE_ROUTE", "EXHAUSTED_RETEST", "INVALID_TRIGGER", a=False, b=False, c=True, d=False, e=True, f=False, g=False, h=True, i=True, note="Break 09:39, trigger 10:10 after 30m; not an opening continuation."),
    53: _lab("CLEAR_CONTINUATION", "CLEAR_ROUTE", "FRESH_RETEST", "VALID_RECLAIM", a=True, b=True, c=True, d=True, e=True, f=True, g=True, h=False, i=False, note="Opening dump through the zone, 09:19–09:23 far-side retest."),
    54: _lab("CLEAR_CONTINUATION", "CLEAR_ROUTE", "FRESH_RETEST", "VALID_RECLAIM", a=True, b=True, c=True, d=True, e=True, f=True, g=True, h=False, i=False, note="Opening dump from 56100 through OR_LOW; first retest of cleared zone."),
    55: _lab("CLEAR_CONTINUATION", "CLEAR_ROUTE", "FRESH_RETEST", "VALID_RECLAIM", a=True, b=True, c=True, d=True, e=True, f=True, g=True, h=False, i=False, note="09:15 opening dump, 09:17 first retest of cleared OR/zone."),
    56: _lab("QUESTIONABLE", "QUESTIONABLE_ROUTE", "AMBIGUOUS", "WEAK_RECLAIM", a=False, b=False, c=True, d=True, e=True, f=False, g=False, h=False, i=True, note="09:44 late leak after a long morning grind."),
    57: _lab("QUESTIONABLE", "QUESTIONABLE_ROUTE", "FRESH_RETEST", "WEAK_RECLAIM", a=False, b=False, c=True, d=True, e=True, f=False, g=False, h=False, i=False, note="09:37 R=9 crawl of OR_LOW."),
    58: _lab("CLEAR_CONTINUATION", "CLEAR_ROUTE", "FRESH_RETEST", "VALID_RECLAIM", a=True, b=True, c=True, d=True, e=True, f=True, g=True, h=False, i=False, note="Opening dump, 09:30 break of cleared zone, 09:33 far-side retest."),
    59: _lab("CLEAR_CONTINUATION", "CLEAR_ROUTE", "FRESH_RETEST", "VALID_RECLAIM", a=True, b=True, c=True, d=True, e=True, f=True, g=True, h=False, i=False, note="Opening dump 3470→3370 through the zone; 09:18–09:20 retest."),
    60: _lab("NOT_CONTINUATION", "QUESTIONABLE_ROUTE", "EXHAUSTED_RETEST", "INVALID_TRIGGER", a=False, b=False, c=True, d=False, e=True, f=False, g=False, h=True, i=True, note="10:00 OR_LOW leak after an hour of range; not opening continuation."),
    61: _lab("QUESTIONABLE", "QUESTIONABLE_ROUTE", "AMBIGUOUS", "WEAK_RECLAIM", a=False, b=False, c=True, d=True, e=True, f=False, g=False, h=False, i=True, note="09:49 R=7 late leak."),
    62: _lab("QUESTIONABLE", "QUESTIONABLE_ROUTE", "FRESH_RETEST", "WEAK_RECLAIM", a=False, b=True, c=True, d=True, e=True, f=False, g=False, h=False, i=False, note="Opening drip then R=5 OR_LOW leak; too micro."),
    63: _lab("CLEAR_CONTINUATION", "CLEAR_ROUTE", "FRESH_RETEST", "VALID_RECLAIM", a=True, b=True, c=True, d=True, e=True, f=True, g=True, h=False, i=False, note="09:15 opening dump, 09:26 first OR_LOW retest still in the opening window."),
    64: _lab("QUESTIONABLE", "QUESTIONABLE_ROUTE", "FRESH_RETEST", "WEAK_RECLAIM", a=False, b=False, c=True, d=True, e=True, f=False, g=False, h=False, i=False, note="R=5 09:34 crawl of OR_HIGH."),
    65: _lab("CLEAR_CONTINUATION", "CLEAR_ROUTE", "FRESH_RETEST", "VALID_RECLAIM", a=True, b=True, c=True, d=True, e=True, f=True, g=True, h=False, i=False, note="09:15 OR_HIGH break after opening grind-up; 09:30 first retest."),
    66: _lab("QUESTIONABLE", "QUESTIONABLE_ROUTE", "EXHAUSTED_RETEST", "WEAK_RECLAIM", a=False, b=True, c=True, d=True, e=True, f=False, g=False, h=True, i=True, note="Opening dump already happened; 19m later retest of OR_LOW is leftover."),
    67: _lab("QUESTIONABLE", "QUESTIONABLE_ROUTE", "AMBIGUOUS", "WEAK_RECLAIM", a=False, b=False, c=True, d=True, e=True, f=False, g=False, h=False, i=True, note="09:56 / 10:03 late morning leak, not an opening continuation."),
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
                "trigger_primary": ev.get("trigger_primary"),
                "structural_route": ev.get("structural_route"),
                "zone_class": ev.get("zone_class"),
                "defended_level_type": ev.get("defended_level_type"),
                "break_t": ev.get("break_t"),
                "retest_t": ev.get("retest_t"),
                "trigger_t": ev.get("trigger_t"),
                "chart": f"sample_{sid:02d}_{ev.get('symbol')}_{ev.get('date')}_{ev.get('direction')}.png",
                "reviewed": reviewed,
                "machine_self_match_used": False,
                **lab,
            }
        )
    n = sum(1 for r in labeled if r.get("reviewed"))
    patterns = Counter(str(r.get("pattern")) for r in labeled if r.get("reviewed"))
    locs = Counter(str(r.get("location")) for r in labeled if r.get("reviewed"))
    rets = Counter(str(r.get("retest_lab")) for r in labeled if r.get("reviewed"))
    trigs = Counter(str(r.get("trigger_lab")) for r in labeled if r.get("reviewed"))
    clear = int(patterns.get("CLEAR_CONTINUATION") or 0)
    blocked = int(locs.get("STRUCTURALLY_BLOCKED") or 0)
    return {
        "reviewed_n": n,
        "sample_n": len(labeled),
        "missing_label_n": missing,
        "actual_manual_blinded_review": bool(n == len(labeled) and missing == 0 and n > 0),
        "machine_self_match_used": False,
        "CLEAR_CONTINUATION": clear,
        "QUESTIONABLE": int(patterns.get("QUESTIONABLE") or 0),
        "NOT_CONTINUATION": int(patterns.get("NOT_CONTINUATION") or 0),
        "clear_share": float(clear / n) if n else None,
        "CLEAR_ROUTE": int(locs.get("CLEAR_ROUTE") or 0),
        "QUESTIONABLE_ROUTE": int(locs.get("QUESTIONABLE_ROUTE") or 0),
        "STRUCTURALLY_BLOCKED": blocked,
        "structurally_blocked_leak_share": float(blocked / n) if n else None,
        "FRESH_RETEST": int(rets.get("FRESH_RETEST") or 0),
        "EXHAUSTED_RETEST": int(rets.get("EXHAUSTED_RETEST") or 0),
        "AMBIGUOUS_RETEST": int(rets.get("AMBIGUOUS") or 0),
        "fresh_retest_share": float((rets.get("FRESH_RETEST") or 0) / n) if n else None,
        "VALID_RECLAIM": int(trigs.get("VALID_RECLAIM") or 0),
        "WEAK_RECLAIM": int(trigs.get("WEAK_RECLAIM") or 0),
        "INVALID_TRIGGER": int(trigs.get("INVALID_TRIGGER") or 0),
        "valid_reclaim_share": float((trigs.get("VALID_RECLAIM") or 0) / n) if n else None,
        "rows": labeled,
        "future_hidden": True,
        "outcome_used": False,
    }
