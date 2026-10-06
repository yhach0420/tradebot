"""Reconstruct what S0–S4 actually count. They are not a strict event funnel."""
from __future__ import annotations

from collections import Counter
from typing import Any


def proto_id(row: dict[str, Any]) -> str:
    return f"{row.get('symbol')}|{row.get('date')}|{row.get('DIR')}"


def symbol_date(row: dict[str, Any]) -> tuple[str, str]:
    return str(row.get("symbol")), str(row.get("date"))


def _setup_identity(s: dict[str, Any]) -> dict[str, Any]:
    symbol = str(s.get("symbol"))
    date = str(s.get("date"))
    direction = s.get("direction") or s.get("DIR")
    opening = str(s.get("opening_state") or "")
    break_t = str(s.get("break_t") or "")
    retest_t = str(s.get("retest_t") or "")
    eligible = str(s.get("SETUP_ELIGIBLE_AT") or s.get("setup_eligible_at") or "")
    return {
        "setup_id": s.get("setup_id") or f"{symbol}|{date}|{direction}|{eligible}",
        "symbol": symbol,
        "date": date,
        "direction": direction,
        "opening_drive_id": f"{symbol}|{date}|{direction}|{opening}",
        "break_id": f"{symbol}|{date}|{direction}|{break_t}",
        "retest_id": f"{symbol}|{date}|{direction}|{retest_t}",
        "break_t": break_t,
        "retest_t": retest_t,
        "opening_state": opening,
        "SETUP_ELIGIBLE_AT": eligible,
        "location_family": s.get("location_family"),
    }


def _stage_block(
    *,
    name: str,
    meaning: str,
    days: list[dict[str, Any]],
    prev_days: list[dict[str, Any]] | None,
    next_days: list[dict[str, Any]] | None,
    setup_ids: list[str],
    death_counter: Counter[str],
) -> dict[str, Any]:
    sd = {symbol_date(r) for r in days}
    proto = {proto_id(r) for r in days}
    prev_sd = {symbol_date(r) for r in (prev_days or [])}
    next_sd = {symbol_date(r) for r in (next_days or [])}
    transition_in_n = len(sd) if prev_days is None else len(sd & prev_sd) if prev_days else len(sd)
    if prev_days is None:
        transition_in_n = len(sd)
    else:
        transition_in_n = len(sd)
    transition_out_to_next_n = len(sd & next_sd) if next_days is not None else None
    transition_out_death_n = len(sd - next_sd) if next_days is not None else None
    return {
        "reported_name": name,
        "meaning": meaning,
        "is_strict_funnel_count": False,
        "event_n": len(days),
        "unique_symbol_date_n": len(sd),
        "unique_setup_id_n": len(set(setup_ids)),
        "unique_proto_id_n": len(proto),
        "transition_in_n": transition_in_n,
        "transition_out_n": transition_out_to_next_n if next_days is not None else None,
        "transition_out_to_next_n": transition_out_to_next_n,
        "transition_out_death_n": transition_out_death_n,
        "top_deaths": dict(death_counter.most_common(12)),
    }


def audit_counts(walked: dict[str, Any]) -> dict[str, Any]:
    funnel = list(walked.get("funnel_days") or [])
    setups = list(walked.get("setups") or [])
    e0 = list(walked.get("e0_events") or [])
    e1 = list(walked.get("e1_events") or [])
    reported = dict(walked.get("counts") or {})

    s0 = [r for r in funnel if r.get("S0")]
    s1 = [r for r in funnel if r.get("S1")]
    s2 = [r for r in funnel if r.get("S2")]
    s3 = [r for r in funnel if r.get("S3")]
    s4d = [r for r in funnel if r.get("S4")]
    geom = s3
    loc = s2

    setup_ids = [str(s.get("setup_id")) for s in setups if s.get("setup_id")]
    s4_ids_by_sd = {symbol_date(s): str(s.get("setup_id")) for s in setups}

    def deaths(rows: list[dict[str, Any]]) -> Counter[str]:
        c: Counter[str] = Counter()
        for r in rows:
            if r.get("death"):
                c[str(r.get("death"))] += 1
        return c

    s0_block = _stage_block(
        name="S0",
        meaning="DAY_REACH: unique symbol-days with DISTINCTIVE_OPENING_ACTIVITY_V4 (S0.ok). Not a setup event.",
        days=s0,
        prev_days=None,
        next_days=s1,
        setup_ids=[],
        death_counter=deaths([r for r in funnel if r.get("S0") and not r.get("S1")]),
    )
    s1_block = _stage_block(
        name="S1",
        meaning="DAY_REACH: unique symbol-days that locked a valid 5m opening drive (TRUE or FAILED_OPEN). Not a setup event.",
        days=s1,
        prev_days=s0,
        next_days=s3,
        setup_ids=[],
        death_counter=deaths([r for r in s1 if not r.get("S3")]),
    )
    s3_block = _stage_block(
        name="S3_REPORTED",
        meaning=(
            "DAY_REACH GEOMETRIC_FIRST_RETEST_HOLD: unique symbol-days where a leave+return held "
            "the OR boundary close. Set in machine.py BEFORE classify_s2. This is NOT spec S3 "
            "(BREAK_RETEST after valid location). Rename: GEOMETRIC_RETEST_HOLD_DAY_N."
        ),
        days=geom,
        prev_days=s1,
        next_days=s2,
        setup_ids=[],
        death_counter=deaths([r for r in geom if not r.get("S2")]),
    )
    s2_block = _stage_block(
        name="S2_REPORTED",
        meaning=(
            "DAY_REACH LOCATION_PASS: unique symbol-days where classify_s2 returned ok AFTER geometric retest. "
            "Implies geometric hold. This is spec S2 (MEANINGFUL_PRICE_LOCATION) evaluated out of order."
        ),
        days=loc,
        prev_days=s3,
        next_days=s4d,
        setup_ids=[s4_ids_by_sd[symbol_date(r)] for r in loc if symbol_date(r) in s4_ids_by_sd],
        death_counter=deaths([r for r in loc if not r.get("S4")]),
    )
    s4_block = _stage_block(
        name="S4",
        meaning="SETUP_EVENT: symbol-days / setups that fired FIVE_M_CONTINUATION_STATE and minted setup_id. setup_n.",
        days=s4d,
        prev_days=s2,
        next_days=None,
        setup_ids=setup_ids,
        death_counter=Counter(),
    )
    s4_block["event_n"] = len(setups)
    s4_block["unique_setup_id_n"] = len(set(setup_ids))
    s4_block["is_strict_funnel_count"] = False

    strict = {
        "label": "STRICT_SPEC_PREFIX_FUNNEL (reconstructed; S2 location then S3 retest)",
        "S0_DAY_N": len(s0),
        "S1_DAY_N": len(s1),
        "S2_LOCATION_PASS_DAY_N": len(s2),
        "S3_RETEST_AFTER_VALID_LOCATION_DAY_N": len(s2),
        "S4_SETUP_ELIGIBLE_N": len(setups),
        "note": (
            "Implementation evaluates geometric retest (flagged S3) then location (flagged S2). "
            "A day cannot pass location without geometric hold, so spec-S3 after spec-S2 equals S2_DAY_N. "
            "Reported S3=761 is GEOMETRIC_RETEST_HOLD_DAY_N, not spec S3."
        ),
    }

    reported_s = {
        "S0": int(reported.get("S0") or 0),
        "S1": int(reported.get("S1") or 0),
        "S2": int(reported.get("S2") or 0),
        "S3": int(reported.get("S3") or 0),
        "S4": int(reported.get("S4") or 0),
    }
    why_s3_gt_s2 = (
        "Reported S3 is geometric first-retest hold; reported S2 is location pass after that hold. "
        "Many geometric holds fail location, so S3_DAY_N > S2_DAY_N. "
        "A strict S0→S1→S2→S3→S4 event funnel cannot have S3>S2; these are not funnel counts."
    )

    return {
        "strict_event_funnel": False,
        "counts_are_day_reach_flags": True,
        "why_reported_S3_gt_S2": why_s3_gt_s2,
        "reported_walk_counts": reported_s,
        "reconstructed_day_n": {
            "S0": len(s0),
            "S1": len(s1),
            "S2": len(s2),
            "S3": len(s3),
            "S4_funnel_flag": len(s4d),
            "S4_setup_rows": len(setups),
            "E0_rows": len(e0),
            "E1_rows": len(e1),
        },
        "rename": {
            "S0": "S0_DAY_REACH_N / DISTINCTIVE_OPENING_ACTIVITY_DAY_N",
            "S1": "S1_DAY_REACH_N / VALID_OPENING_DRIVE_DAY_N",
            "S2": "S2_LOCATION_PASS_DAY_N",
            "S3": "GEOMETRIC_RETEST_HOLD_DAY_N (NOT spec S3)",
            "S4": "S4_SETUP_ELIGIBLE_N",
        },
        "stages": {
            "S0": s0_block,
            "S1": s1_block,
            "S2": s2_block,
            "S3": s3_block,
            "S4": s4_block,
        },
        "strict_spec_prefix_funnel": strict,
        "s2_s3_evaluation_order": "IMPLEMENTATION: geometric retest (S3 flag) THEN location (S2 flag). SPEC: S2 location THEN S3 break/retest.",
        "e0_n": len(e0),
        "e1_n": len(e1),
        "same_bar_entry_n": int(walked.get("same_bar_entry_n") or 0),
        "e1_cancel_n": int(reported.get("E1_CANCEL") or 0),
    }
