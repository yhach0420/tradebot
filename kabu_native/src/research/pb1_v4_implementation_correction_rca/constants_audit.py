"""Map every eligibility-affecting constant to frozen spec language. No rule change."""
from __future__ import annotations

from typing import Any

from research.pb1_v4_implementation_correction import (
    ACCEPTANCE_FAIL_CLOSES,
    BREAK_BEYOND_ATR_FRAC,
    BREAK_BEYOND_OR_FRAC,
    BREAK_CLOSE_LOC,
    CONFLUENCE_N1M,
    E1_BODY_N1M,
    E1_NET_N1M,
    E1_RANGE_N1M,
    FAIL_COUNTER_BODY_FRAC,
    FAIL_COUNTER_MIN,
    FAIL_DRIVE_DISP_MIN,
    FAIL_EXTEND_MAX_BARS,
    FAILED_ATTEMPT_N,
    FLAT_DISP_MAX,
    FLAT_RANGE_MAX,
    LAST_BREAK,
    LAST_TRIGGER,
    LEAVE_EXT_OR_FRAC,
    MIN_AWAY_BARS,
    OR_KNOWN_FROM,
    OR_RECROSS_CLOSES,
    S0_ATR_RANGE_MIN,
    S0_GAP_ATR_MIN,
    S0_GAP_RANGE_MIN,
    S0_RANGE_MIN,
    S4_BODY_FRAC,
    S4_CLOSE_LOC,
    S4_RANGE_OVER_N1M,
    S4_RANGE_OVER_OPEN5,
    STALL_5M_BARS,
    TRUE_BODY_FRAC_MIN,
    TRUE_COUNTER_FRAC,
    TRUE_DISP_MIN,
    TRUE_N_SAME_MIN,
    TRUE_RANGE_MIN,
    UNWIND_FRAC,
)
from research.pb1_v4_opening_drive_location_reaccel_spec.specification import (
    failed_open_then_real_drive_def,
    five_m_continuation_def,
    location_def,
    true_opening_drive_def,
)


EXPLICIT = "EXPLICITLY_IN_FROZEN_SPEC"
ENCODING = "SEMANTIC_ENCODING_OF_FROZEN_SPEC"
ASSUMPTION = "IMPLEMENTATION_ASSUMPTION_ONLY"


def inventory() -> dict[str, Any]:
    true_req = list(true_opening_drive_def().get("required_concepts") or [])
    fail_req = list(failed_open_then_real_drive_def().get("required_concepts") or [])
    loc = location_def()
    s4 = five_m_continuation_def()
    rows = [
        {"name": "S0_RANGE_MIN", "value": S0_RANGE_MIN, "class": ENCODING, "maps_to": "visible movement vs own normal opening 5m", "eligibility": True},
        {"name": "S0_GAP_ATR_MIN", "value": S0_GAP_ATR_MIN, "class": ENCODING, "maps_to": "gap distinctive vs own ATR", "eligibility": True},
        {"name": "S0_GAP_RANGE_MIN", "value": S0_GAP_RANGE_MIN, "class": ENCODING, "maps_to": "gap assist still requires opening range", "eligibility": True},
        {"name": "S0_ATR_RANGE_MIN", "value": S0_ATR_RANGE_MIN, "class": ASSUMPTION, "maps_to": "fallback when opening-5m baseline unavailable", "eligibility": True},
        {"name": "TRUE_DISP_MIN", "value": TRUE_DISP_MIN, "class": ENCODING, "maps_to": "meaningful directional displacement", "eligibility": True, "frozen_concept": true_req[0] if true_req else None},
        {"name": "TRUE_RANGE_MIN", "value": TRUE_RANGE_MIN, "class": ENCODING, "maps_to": "movement vs normal opening 5m range", "eligibility": True},
        {"name": "TRUE_N_SAME_MIN", "value": TRUE_N_SAME_MIN, "class": ENCODING, "maps_to": "directional 5m bodies (count), not continued intent", "eligibility": True},
        {"name": "TRUE_COUNTER_FRAC", "value": TRUE_COUNTER_FRAC, "class": ENCODING, "maps_to": "limited counter-auction", "eligibility": True},
        {"name": "TRUE_BODY_FRAC_MIN", "value": TRUE_BODY_FRAC_MIN, "class": ENCODING, "maps_to": "directional 5m bodies, not doji/crawl tape (mean only)", "eligibility": True},
        {"name": "continued_directional_intent", "value": None, "class": ASSUMPTION, "maps_to": "frozen TRUE required_concept with NO live numeric/clause", "eligibility": False, "encoded": False},
        {"name": "first_and_last_opening_bar_same_direction", "value": "NOT IN LIVE s1._true_ok", "class": ASSUMPTION, "maps_to": "definitions text leftover; not an eligibility rule", "eligibility": False, "live": False},
        {"name": "FAIL_COUNTER_MIN", "value": FAIL_COUNTER_MIN, "class": ENCODING, "maps_to": "real initial counter-move visible on 5m", "eligibility": True, "frozen_concept": fail_req[0] if fail_req else None},
        {"name": "FAIL_COUNTER_BODY_FRAC", "value": FAIL_COUNTER_BODY_FRAC, "class": ENCODING, "maps_to": "large counter-body, not a 1m dip", "eligibility": True},
        {"name": "FAIL_DRIVE_DISP_MIN", "value": FAIL_DRIVE_DISP_MIN, "class": ENCODING, "maps_to": "real opposite directional 5m auction", "eligibility": True},
        {"name": "FAIL_EXTEND_MAX_BARS", "value": FAIL_EXTEND_MAX_BARS, "class": ASSUMPTION, "maps_to": "not in frozen spec; clock-like cutoff", "eligibility": True, "spec_unsupported": True},
        {"name": "FLAT_DISP_MAX", "value": FLAT_DISP_MAX, "class": ENCODING, "maps_to": "FLAT_OR_CRAWL vs drive", "eligibility": True},
        {"name": "FLAT_RANGE_MAX", "value": FLAT_RANGE_MAX, "class": ENCODING, "maps_to": "FLAT_OR_CRAWL vs drive", "eligibility": True},
        {"name": "completed_5m_leave", "value": "five_m_fully_beyond entire bar past OR", "class": ASSUMPTION, "maps_to": "spec says real drive/leave, not completed-bar-fully-beyond", "eligibility": True, "spec_explicit": False},
        {"name": "completed_5m_or_hold", "value": "tests OR + close continuation side + loc>=0.50 on completed 5m", "class": ASSUMPTION, "maps_to": "spec says visible hold/reject; does not require waiting for 5m close", "eligibility": True, "spec_explicit": False},
        {"name": "CONFLUENCE_N1M", "value": CONFLUENCE_N1M, "class": ASSUMPTION, "maps_to": "identity distance, not a score; not in frozen spec as 1.5", "eligibility": False},
        {"name": "FAILED_ATTEMPT_N", "value": FAILED_ATTEMPT_N, "class": ENCODING, "maps_to": "multiple failed breaks", "eligibility": True},
        {"name": "OR_RECROSS_CLOSES", "value": OR_RECROSS_CLOSES, "class": ENCODING, "maps_to": "price repeatedly recrosses OR", "eligibility": True},
        {"name": "BREAK_BEYOND_OR_FRAC", "value": BREAK_BEYOND_OR_FRAC, "class": ASSUMPTION, "maps_to": "inherited V2 break geometry; spec says real leave, not 0.08", "eligibility": True},
        {"name": "BREAK_BEYOND_ATR_FRAC", "value": BREAK_BEYOND_ATR_FRAC, "class": ASSUMPTION, "maps_to": "inherited V2 break geometry", "eligibility": True},
        {"name": "BREAK_CLOSE_LOC", "value": BREAK_CLOSE_LOC, "class": ASSUMPTION, "maps_to": "inherited V2 break close location", "eligibility": True},
        {"name": "MIN_AWAY_BARS", "value": MIN_AWAY_BARS, "class": ASSUMPTION, "maps_to": "1m leave distance; family B now uses completed 5m fully-beyond", "eligibility": True},
        {"name": "LEAVE_EXT_OR_FRAC", "value": LEAVE_EXT_OR_FRAC, "class": ASSUMPTION, "maps_to": "1m leave extension; not the frozen leave definition", "eligibility": True},
        {"name": "LAST_BREAK", "value": LAST_BREAK, "class": ASSUMPTION, "maps_to": "clock-like last-break cutoff; spec forbids clock thesis-loss", "eligibility": True, "spec_unsupported": True},
        {"name": "LAST_TRIGGER", "value": LAST_TRIGGER, "class": ASSUMPTION, "maps_to": "session flatten window, not frozen semantic", "eligibility": True},
        {"name": "OR_KNOWN_FROM", "value": OR_KNOWN_FROM, "class": ENCODING, "maps_to": "OR15 known only after 09:14 close", "eligibility": True},
        {"name": "STALL_5M_BARS", "value": STALL_5M_BARS, "class": ASSUMPTION, "maps_to": "no-expansion stall before break; not a frozen clock", "eligibility": True},
        {"name": "S4_CLOSE_LOC", "value": S4_CLOSE_LOC, "class": ENCODING, "maps_to": "renewed directional progress on 5m", "eligibility": True, "frozen_s4": s4.get("must_show")},
        {"name": "S4_BODY_FRAC", "value": S4_BODY_FRAC, "class": ENCODING, "maps_to": "loss of counter-pressure / directional body", "eligibility": True},
        {"name": "S4_RANGE_OVER_OPEN5", "value": S4_RANGE_OVER_OPEN5, "class": ASSUMPTION, "maps_to": "S4 range scale; spec says not a huge breakout", "eligibility": True},
        {"name": "S4_RANGE_OVER_N1M", "value": S4_RANGE_OVER_N1M, "class": ASSUMPTION, "maps_to": "S4 range scale vs 1m noise", "eligibility": True},
        {"name": "RETEST_EXTREME_BREACH", "value": "close through retest high/low", "class": ENCODING, "maps_to": "hold of defended location lost", "eligibility": True},
        {"name": "ACCEPTANCE_FAIL_CLOSES", "value": ACCEPTANCE_FAIL_CLOSES, "class": ASSUMPTION, "maps_to": "inherited V3 persistent acceptance", "eligibility": True},
        {"name": "E1_NET_N1M", "value": E1_NET_N1M, "class": ENCODING, "maps_to": "1m execution families; never creates eligibility", "eligibility": False},
        {"name": "E1_RANGE_N1M", "value": E1_RANGE_N1M, "class": ENCODING, "maps_to": "1m execution", "eligibility": False},
        {"name": "E1_BODY_N1M", "value": E1_BODY_N1M, "class": ENCODING, "maps_to": "1m execution", "eligibility": False},
        {"name": "or_touched_is_not_valid", "value": True, "class": EXPLICIT, "maps_to": loc.get("or_touched_is_not_valid"), "eligibility": True},
        {"name": "numeric_threshold_frozen_in_spec", "value": False, "class": EXPLICIT, "maps_to": "TRUE and FAILED_OPEN numeric_threshold_frozen=false", "eligibility": False},
    ]
    return {
        "rows": rows,
        "FAIL_EXTEND_MAX_BARS_explicitly_supported_by_frozen_spec": False,
        "completed_5m_leave_explicitly_required_by_frozen_spec": False,
        "completed_5m_retest_hold_explicitly_required_by_frozen_spec": False,
        "first_last_same_dir_live_in_corrected_s1": False,
        "continued_intent_encoded": "no",
        "spec_unsupported_implementation_assumptions": [
            "FAIL_EXTEND_MAX_BARS=6",
            "completed 5m bar fully beyond OR as leave",
            "completed 5m OR hold as the only valid hold evidence",
            "LAST_BREAK=10:00 clock cutoff",
        ],
        "true_required_concepts": true_req,
        "failed_open_required_concepts": fail_req,
        "any_rule_changed": False,
    }
