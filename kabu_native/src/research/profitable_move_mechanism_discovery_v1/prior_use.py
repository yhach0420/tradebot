"""Closed-lineage identity map. Exact Full Strategy identities cannot be selected as new."""
from __future__ import annotations

from typing import Any

from research.new_full_strategy_architecture_inventory_and_freeze_v3.inventory import closed_architecture_inventory

EXACT_CLOSED = {
    "B_ONSET_S_CLOSE_ABOVE_VWAP": "E4_VWAP_RECLAIM",
    "E_ONSET_S_MA_TREND_UP__CTX_S_BREADTH_EXPANDING": "CSB_MA_ONSET",
}

NEAREST = {
    "A_S_MA_TREND_UP": "SYSTEMATIC_STATE_TRANSITION",
    "B_ONSET_S_MA_TREND_UP": "CSB_MA_ONSET",
    "A_S_BB_ABOVE_MID": "SYSTEMATIC_STATE_TRANSITION",
    "B_ONSET_S_BB_ABOVE_MID": "SYSTEMATIC_STATE_TRANSITION",
    "A_S_RCI_ABOVE_NEG80": "SYSTEMATIC_STATE_TRANSITION",
    "B_ONSET_S_RCI_ABOVE_NEG80": "SYSTEMATIC_STATE_TRANSITION",
    "A_S_VOL_CONFIRM_1M": "PARTICIPATION_ONSET",
    "B_ONSET_S_VOL_CONFIRM_1M": "PARTICIPATION_ONSET",
    "A_S_CLOSE_ABOVE_VWAP": "E4_VWAP_RECLAIM",
    "A_S_CLOSE_ABOVE_EMA21": "SYSTEMATIC_STATE_TRANSITION",
    "B_ONSET_S_CLOSE_ABOVE_EMA21": "SYSTEMATIC_STATE_TRANSITION",
    "A_S_BID_GT_ASK_QTY": "SIMPLE_TECH_PULLBACK_V1",
    "B_ONSET_S_BID_GT_ASK_QTY": "SIMPLE_TECH_PULLBACK_V1",
    "A_S_BOARD_SUPPORT": "SIMPLE_TECH_PULLBACK_V1",
    "B_ONSET_S_BOARD_SUPPORT": "SIMPLE_TECH_PULLBACK_V1",
    "A_S_PULLBACK_SETUP": "SIMPLE_TECH_PULLBACK_V1",
    "B_ONSET_S_PULLBACK_SETUP": "SIMPLE_TECH_PULLBACK_V1",
    "A_S_PRICE_ACTION": "SIMPLE_TECH_PULLBACK_V1",
    "B_ONSET_S_PRICE_ACTION": "SIMPLE_TECH_PULLBACK_V1",
    "A_S_BREADTH_EXPANDING": "CSB_MA_ONSET",
    "B_ONSET_S_BREADTH_EXPANDING": "CSB_MA_ONSET",
    "C_S_MA_TREND_UP__S_VOL_CONFIRM_1M": "SYSTEMATIC_STATE_TRANSITION",
    "C_S_PULLBACK_SETUP__S_VOL_CONFIRM_1M": "SIMPLE_TECH_PULLBACK_V1",
    "C_S_PULLBACK_SETUP__S_BID_GT_ASK_QTY": "SIMPLE_TECH_PULLBACK_V1",
    "D_S_PULLBACK_SETUP__THEN_S_PRICE_ACTION": "SIMPLE_TECH_PULLBACK_V1",
    "E_ONSET_S_PULLBACK_SETUP__CTX_S_VOL_CONFIRM_1M": "SIMPLE_TECH_PULLBACK_V1",
    "E_ONSET_S_MA_TREND_UP__CTX_S_VOL_CONFIRM_1M": "SYSTEMATIC_STATE_TRANSITION",
    "C_S_MA_TREND_UP__S_BREADTH_EXPANDING": "CSB_MA_ONSET",
    "D_S_BREADTH_EXPANDING__THEN_S_MA_TREND_UP": "CSB_MA_ONSET",
}


def _family_overlap(cand: dict[str, Any], lineage_id: str) -> bool:
    fams = set(str(x) for x in (cand.get("FAMILIES") or []))
    if lineage_id in ("SYSTEMATIC_STATE_TRANSITION", "C1_MULTI_TIMEFRAME") and fams & {
        "MA",
        "BB",
        "RCI",
        "VOLUME",
        "VWAP",
    }:
        return True
    if lineage_id == "CSB_MA_ONSET" and fams & {"MA", "CROSS_SECTION"}:
        return True
    if lineage_id == "E4_VWAP_RECLAIM" and "VWAP" in fams:
        return True
    if lineage_id == "SIMPLE_FULL" and fams & {"VWAP", "PRICE_CHANGE", "VOLUME"}:
        return True
    if lineage_id.startswith("SIMPLE_TECH") and fams & {"PULLBACK", "PRICE_ACTION", "BOARD", "MA"}:
        return True
    if lineage_id == "PARTICIPATION_ONSET" and "VOLUME" in fams:
        return True
    if lineage_id == "V4_HTF5_MA_RESISTANCE_EPISODE" and fams & {"MA", "VOLUME"}:
        return True
    if lineage_id == "BREAKOUT_CONTINUATION" and fams & {"PRICE_ACTION", "VWAP", "VOLUME"}:
        return True
    return False


def classify_candidate(cand: dict[str, Any]) -> dict[str, Any]:
    mid = str(cand["MECHANISM_ID"])
    exact = EXACT_CLOSED.get(mid)
    nearest = NEAREST.get(mid) or exact
    if nearest is None:
        for card in closed_architecture_inventory():
            lid = str(card["LINEAGE_ID"])
            if _family_overlap(cand, lid):
                nearest = lid
                break
    if nearest is None:
        nearest = "NONE"
    if exact:
        klass = "A_EXACT_CLOSED_STRATEGY_MATCH"
    elif nearest != "NONE":
        klass = "B_SAME_INFORMATION_NEW_DECISION_MECHANISM"
    else:
        klass = "C_GENUINELY_NEW_INFORMATION_MECHANISM"
    return {
        "MECHANISM_ID": mid,
        "EXACT_PRIOR_TEST_MATCH": bool(exact),
        "EXACT_CLOSED_LINEAGE": exact,
        "NEAREST_CLOSED_LINEAGE": nearest,
        "LINEAGE_CLASS": klass,
        "ELIGIBLE_AS_NEW_ARCHITECTURE": klass in (
            "B_SAME_INFORMATION_NEW_DECISION_MECHANISM",
            "C_GENUINELY_NEW_INFORMATION_MECHANISM",
        ),
    }


def classify_library(cands: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [classify_candidate(c) for c in cands]
