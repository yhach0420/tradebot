"""Map RCA 19 CLEAR + curated negatives onto the V4 layer stack. No economics."""
from __future__ import annotations

from typing import Any

from research.pb1_v3_2_face_failure_rca.second_pass import HUMAN_LABELS
from research.pb1_v4_opening_drive_location_reaccel_spec import EXEMPLAR_FAILED_OPEN, VALID_OPENING_STATES

# Curated unique negatives covering each V4 reject layer. Not the auto-matched duplicates.
CURATED_NEGATIVES: tuple[dict[str, Any], ...] = (
    {
        "rca_id": 1,
        "symbol": "7011",
        "date": "20241205",
        "direction": "bull",
        "fail_layer": "FIVE_M_OPENING_DRIVE",
        "opening_state": "MICRO_OR_LEAK",
        "why": "Flat-to-micro 5m; 09:36 OR_HIGH leak is a 1m event. Trade does not exist on 5m.",
    },
    {
        "rca_id": 3,
        "symbol": "6963",
        "date": "20241227",
        "direction": "bull",
        "fail_layer": "FIVE_M_OPENING_DRIVE",
        "opening_state": "TWO_SIDED_OPEN",
        "why": "Two-sided 5m auction. 1m cannot invent a directional drive.",
    },
    {
        "rca_id": 6,
        "symbol": "9983",
        "date": "20241205",
        "direction": "bull",
        "fail_layer": "FIVE_M_OPENING_DRIVE",
        "opening_state": "FLAT_OR_CRAWL",
        "why": "Same-direction 5m crawl, not a drive. Stock-selection/in-play can still look busy.",
    },
    {
        "rca_id": 15,
        "symbol": "9432",
        "date": "20250402",
        "direction": "bear",
        "fail_layer": "MEANINGFUL_PRICE_LOCATION",
        "opening_state": "TRUE_OPENING_DRIVE",
        "why": "5m dump exists; there is no visibly defensible location / planned R. Reject at location, not at 1m.",
    },
    {
        "rca_id": 44,
        "symbol": "6871",
        "date": "20250922",
        "direction": "bull",
        "fail_layer": "OPENING_THESIS_LOST",
        "opening_state": "LATE_RANGE_RESOLUTION",
        "why": "Opening drive already completed; leftover OR leak. Thesis is over.",
    },
    {
        "rca_id": 68,
        "symbol": "6963",
        "date": "20241002",
        "direction": "bull",
        "fail_layer": "FIVE_M_OPENING_DRIVE",
        "opening_state": "LATE_RANGE_RESOLUTION",
        "why": "V3.2 EARLY_REVERSAL false positive: bounce already done; 10:15 leftover. Not 3382.",
    },
    {
        "rca_id": 72,
        "symbol": "3382",
        "date": "20241115",
        "direction": "bull",
        "fail_layer": "FIVE_M_OPENING_DRIVE",
        "opening_state": "MICRO_OR_LEAK",
        "why": "Same symbol as the exemplar, different day: OR_HIGH grind, not a visible failed-open then drive.",
    },
    {
        "rca_id": 74,
        "symbol": "9432",
        "date": "20250110",
        "direction": "bear",
        "fail_layer": "FIVE_M_OPENING_DRIVE",
        "opening_state": "TWO_SIDED_OPEN",
        "why": "Tiny early-dip / OR-half style false positive. 5m is a flat line.",
    },
    {
        "rca_id": 75,
        "symbol": "6273",
        "date": "20250120",
        "direction": "bull",
        "fail_layer": "WHY_THIS_STOCK_TODAY",
        "opening_state": "FLAT_OR_CRAWL",
        "why": "Daily waterfall + tiny OR_HIGH leak. Daily context explains why it looks absurd; rejection is still 5m/not-a-drive.",
    },
    {
        "rca_id": 78,
        "symbol": "9501",
        "date": "20250311",
        "direction": "bear",
        "fail_layer": "OPENING_THESIS_LOST",
        "opening_state": "LATE_RANGE_RESOLUTION",
        "why": "Hour of range then leftover leak. Thesis lost before 1m.",
    },
    {
        "rca_id": 79,
        "symbol": "8001",
        "date": "20250609",
        "direction": "bull",
        "fail_layer": "FIVE_M_OPENING_DRIVE",
        "opening_state": "FLAT_OR_CRAWL",
        "why": "Distinctive gap but visually ordinary 5m crawl. IN-PLAY too broad; reject at 5m drive.",
    },
    {
        "rca_id": 81,
        "symbol": "8035",
        "date": "20250701",
        "direction": "bear",
        "fail_layer": "FIVE_M_OPENING_DRIVE",
        "opening_state": "TWO_SIDED_OPEN",
        "why": "Countertrend micro leak at the high of a vertical daily. 5m does not have a bearish opening drive.",
    },
    {
        "rca_id": 88,
        "symbol": "7182",
        "date": "20251112",
        "direction": "bull",
        "fail_layer": "FIVE_M_OPENING_DRIVE",
        "opening_state": "MICRO_OR_LEAK",
        "why": "Tiny OR_HIGH leak. Micro-cross would be illegal 1m rescue.",
    },
)


def _fail_layer_from_label(lab: dict[str, Any]) -> str | None:
    state = str(lab.get("sp_opening_state") or "")
    loc = str(lab.get("sp_location") or "")
    if state not in VALID_OPENING_STATES:
        if state == "LATE_RANGE_RESOLUTION" or lab.get("sp_late"):
            return "OPENING_THESIS_LOST"
        return "FIVE_M_OPENING_DRIVE"
    if loc != "CLEAR_DEFENDED_LOCATION":
        return "MEANINGFUL_PRICE_LOCATION"
    if str(lab.get("sp_retest") or "") == "STALE_OR_EXHAUSTED":
        return "OPENING_THESIS_LOST"
    if str(lab.get("sp_trigger") or "") != "VALID_REACCELERATION":
        return "ONE_M_EXECUTION"
    return None


def map_positives(bind: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for p in list((bind.get("exemplars") or {}).get("positive") or []):
        rid = int(p.get("rca_id") or 0)
        lab = dict(HUMAN_LABELS.get(rid) or {})
        loc_ok = str(lab.get("sp_location") or "") == "CLEAR_DEFENDED_LOCATION"
        trig_ok = str(lab.get("sp_trigger") or "") == "VALID_REACCELERATION"
        opening_ok = str(lab.get("sp_opening_state") or "") in VALID_OPENING_STATES
        fail = _fail_layer_from_label(lab)
        v4_stack = opening_ok and loc_ok and trig_ok and fail is None
        is_3382 = (str(p.get("symbol")), str(p.get("date")), str(p.get("direction"))) == EXEMPLAR_FAILED_OPEN
        rows.append(
            {
                "rca_id": rid,
                "symbol": p.get("symbol"),
                "date": p.get("date"),
                "direction": p.get("direction"),
                "why_this_stock_today": "distinctive opening activity that a trader would already see on daily+5m (not xs/TV alone)",
                "five_m_opening_drive": str(lab.get("sp_opening_state") or p.get("opening_state")),
                "location": str(lab.get("sp_location_kind") or p.get("location_kind")),
                "location_valid_for_v4": loc_ok,
                "retest": str(lab.get("sp_retest") or "VALID_FIRST_RETEST"),
                "five_m_continuation_state": (
                    "hold of defended location and renewed directional progress already visible on 5m"
                    if v4_stack
                    else "drive or location incomplete; 5m would not already want the trade"
                ),
                "one_m_execution_cue": (
                    "optional 1m state-change versus NORMAL_1M_RANGE after 5m continuation"
                    if trig_ok
                    else "1m confirmation weak; do not rescue with a micro-cross"
                ),
                "why_clear": p.get("why_clear") or lab.get("sp_note"),
                "v4_full_stack": v4_stack,
                "fail_layer_if_not_full_stack": fail,
                "is_3382_exemplar": is_3382,
                "future_used": False,
            }
        )
    return rows


def map_negatives() -> list[dict[str, Any]]:
    out = []
    for n in CURATED_NEGATIVES:
        lab = dict(HUMAN_LABELS.get(int(n["rca_id"])) or {})
        out.append(
            {
                **n,
                "sp_pattern": lab.get("sp_pattern"),
                "stock_selection_fail": n["fail_layer"] == "WHY_THIS_STOCK_TODAY",
                "five_m_drive_fail": n["fail_layer"] == "FIVE_M_OPENING_DRIVE",
                "location_fail": n["fail_layer"] == "MEANINGFUL_PRICE_LOCATION",
                "thesis_alive_fail": n["fail_layer"] == "OPENING_THESIS_LOST",
                "one_m_execution_fail": n["fail_layer"] == "ONE_M_EXECUTION",
                "one_m_must_not_rescue": True,
                "future_used": False,
            }
        )
    return out


def build_exemplar_maps(bind: dict[str, Any]) -> dict[str, Any]:
    pos = map_positives(bind)
    neg = map_negatives()
    return {
        "positive_n": len(pos),
        "positive_full_v4_stack_n": sum(1 for r in pos if r.get("v4_full_stack")),
        "positive_drive_but_location_fail_n": sum(1 for r in pos if r.get("fail_layer_if_not_full_stack") == "MEANINGFUL_PRICE_LOCATION"),
        "positive": pos,
        "negative_n": len(neg),
        "negative": neg,
        "fail_layer_counts": {
            k: sum(1 for r in neg if r.get("fail_layer") == k)
            for k in (
                "WHY_THIS_STOCK_TODAY",
                "FIVE_M_OPENING_DRIVE",
                "MEANINGFUL_PRICE_LOCATION",
                "OPENING_THESIS_LOST",
                "ONE_M_EXECUTION",
            )
        },
        "3382_present": any(r.get("is_3382_exemplar") for r in pos),
        "auto_matched_rca_negatives_not_reused": True,
        "note": "RCA auto-matching duplicated a few negatives; V4 spec uses a unique layer-covering set.",
    }
