"""Unused-mechanism search. No forced proposals. No PnL. No event counts."""
from __future__ import annotations

from typing import Any

EXCLUDED_PROPOSAL_TYPES = (
    "another pullback/reacceleration strategy",
    "another VWAP reclaim",
    "another resistance-break V4 variant",
    "another static MA/BB/RCI/Volume AND",
    "another 1m state first-cross",
    "another PERSIST/HANDOFF",
    "another HTF same-state confirmation",
    "another cross-sectional MA breadth variant",
    "another first-arrival crowding overlay",
    "another previous-high breakout",
    "another timeframe substitution",
)


def novelty_audit() -> dict[str, Any]:
    considered = [
        {
            "CONSIDERED_ID": "BB_VOL_IMPULSE_MA_PULLBACK_X1_Z_SUPPORT_FAIL",
            "NEAREST_CLOSED_LINEAGE": "SIMPLE_TECH_PULLBACK_V1",
            "WHY_NOT_SAME_MECHANISM": (
                "It is the same mechanism: impulse/trend, pullback to MA, rebound. "
                "Volume-before and support-fail EXIT do not change the decision object."
            ),
            "STRUCTURAL_NOVELTY_REASON": None,
            "EXCLUDED_AS": "pullback/reacceleration",
            "ELEVATED_TO_PROPOSAL": False,
        },
        {
            "CONSIDERED_ID": "HTF_RANGE_MIDPOINT_TEST_BREAK",
            "NEAREST_CLOSED_LINEAGE": "V4_HTF5_MA_RESISTANCE_EPISODE",
            "WHY_NOT_SAME_MECHANISM": (
                "Replacing 5m EMA21 with a 5m range midpoint (or 3m/10m/15m EMA) keeps "
                "TEST → later break of a pre-existing HTF numeric level → support-fail EXIT. "
                "That is a V4 resistance-break variant and/or timeframe substitution."
            ),
            "STRUCTURAL_NOVELTY_REASON": None,
            "EXCLUDED_AS": "resistance-break V4 variant / timeframe substitution",
            "ELEVATED_TO_PROPOSAL": False,
        },
        {
            "CONSIDERED_ID": "CSB_VOLUME_BREADTH_ONSET",
            "NEAREST_CLOSED_LINEAGE": "CSB_MA_ONSET",
            "WHY_NOT_SAME_MECHANISM": (
                "Decision object remains other-names' 1m boolean count gating own-name onset. "
                "Swapping trend_up for volume_confirm is a CSB MA-breadth variant."
            ),
            "STRUCTURAL_NOVELTY_REASON": None,
            "EXCLUDED_AS": "cross-sectional MA breadth variant",
            "ELEVATED_TO_PROPOSAL": False,
        },
        {
            "CONSIDERED_ID": "BB_WIDTH_COMPRESS_RELEASE",
            "NEAREST_CLOSED_LINEAGE": "SYSTEMATIC_STATE_TRANSITION",
            "WHY_NOT_SAME_MECHANISM": (
                "Onset of a new 1m technical boolean (width expansion after contraction) is "
                "the same class as ST 1m first-cross / new STATE_ID pulse. Already rejected "
                "in NEW_FULL_STRATEGY_ARCHITECTURE_PRECOMMIT_V1 as CLOSED_LINEAGE_MATCH."
            ),
            "STRUCTURAL_NOVELTY_REASON": None,
            "EXCLUDED_AS": "1m state first-cross",
            "ELEVATED_TO_PROPOSAL": False,
        },
        {
            "CONSIDERED_ID": "GAP_FILL_TO_PRIOR_CLOSE",
            "NEAREST_CLOSED_LINEAGE": "RECOVERY_SEQUENCE",
            "WHY_NOT_SAME_MECHANISM": (
                "Overnight gap then fill toward the lost prior close is reclaim of a broken "
                "location on a later bar. Same recovery/reclaim lifecycle. Gap-size would also "
                "require a new threshold search."
            ),
            "STRUCTURAL_NOVELTY_REASON": None,
            "EXCLUDED_AS": "VWAP/location reclaim class; new threshold search",
            "ELEVATED_TO_PROPOSAL": False,
        },
        {
            "CONSIDERED_ID": "BOARD_IMBALANCE_PRIMARY_ALPHA",
            "NEAREST_CLOSED_LINEAGE": "SIMPLE_TECH_PULLBACK_V1",
            "WHY_NOT_SAME_MECHANISM": (
                "Board already exists as execution freshness and as a pullback-family filter "
                "(board_support). Promoting imbalance ratio to primary ENTRY requires a new "
                "threshold and was treated as BOARD_PRIMARY_ALPHA ineligible in CSB precommit. "
                "Causal board RCA already exists. Not a justified unused technical lifecycle."
            ),
            "STRUCTURAL_NOVELTY_REASON": None,
            "EXCLUDED_AS": "filter/execution reuse; new threshold search",
            "ELEVATED_TO_PROPOSAL": False,
        },
        {
            "CONSIDERED_ID": "OPEN_STRENGTH_STANDALONE_CAP5",
            "NEAREST_CLOSED_LINEAGE": "OR_OVERLAY",
            "WHY_NOT_SAME_MECHANISM": (
                "Dropping PBv2-reject and raising cap_or=1 to CAP=5 is the documented "
                "NEW_DERIVATIVE of Production OR, not an unused mechanism. Also overlaps "
                "previous-high / day-high breakout."
            ),
            "STRUCTURAL_NOVELTY_REASON": None,
            "EXCLUDED_AS": "OR overlay rescue / previous-high breakout",
            "ELEVATED_TO_PROPOSAL": False,
        },
        {
            "CONSIDERED_ID": "STATIC_MA_BB_RCI_VOLUME_AND",
            "NEAREST_CLOSED_LINEAGE": "SIMPLE_FULL",
            "WHY_NOT_SAME_MECHANISM": (
                "Conjunction of existing 1m technical bits is SIMPLE_FULL / ST state occupancy. "
                "Forbidden as another static MA/BB/RCI/Volume AND."
            ),
            "STRUCTURAL_NOVELTY_REASON": None,
            "EXCLUDED_AS": "static MA/BB/RCI/Volume AND",
            "ELEVATED_TO_PROPOSAL": False,
        },
    ]
    elevated = [c for c in considered if c.get("ELEVATED_TO_PROPOSAL")]
    return {
        "EXCLUDED_PROPOSAL_TYPES": list(EXCLUDED_PROPOSAL_TYPES),
        "CONSIDERED_NOT_PROPOSED": considered,
        "CONSIDERED_N": len(considered),
        "ELEVATED_TO_PROPOSAL_N": len(elevated),
        "NO_FORCED_NEWNESS": True,
        "PULLBACK_RESCUE_IN_PROPOSALS": False,
        "V4_RESISTANCE_RETUNE_IN_PROPOSALS": False,
        "CSB_RETUNE_IN_PROPOSALS": False,
        "UNUSED_STRUCTURAL_MECHANISM_FOUND": False,
        "REASON_NO_PROPOSAL": (
            "Every unused-looking lifecycle on already-available causal technical information "
            "matches a closed family at mechanism level, is an excluded proposal type, or "
            "requires a new threshold search. No complete Full Strategy is elevated."
        ),
    }


def proposals() -> list[dict[str, Any]]:
    return []
