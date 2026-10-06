"""Closed-lineage identity audit. No economics. No CSB retune."""
from __future__ import annotations

from typing import Any

from research.new_entry_breakout_continuation_v1 import HIGH_WINDOW, VOLUME_WINDOW
from research.new_entry_breakout_continuation_v1.rule import EXACT_RULE_TEXT
from research.new_full_strategy_architecture_construction_v2 import ARCHITECTURE_ID
from research.recovery_sequence_full_strategy_architecture_v1.entries import ENTRY_RULE_TEXT


def closed_lineage_audit() -> dict[str, Any]:
    candidate = (
        "Pre-existing completed 5m EMA21 numeric price level; 1m resistance TEST of that "
        "frozen LEVEL_VERSION_ID; later Close through the SAME level with S_VOL_CONFIRM_1M; "
        "BREAK_LEVEL frozen at confirmation; EXIT when Close < BREAK_LEVEL (support-conversion fail)."
    )
    rows = [
        {
            "LINEAGE_ID": "BREAKOUT_CONTINUATION",
            "THEIR_MECHANISM": EXACT_RULE_TEXT,
            "DISTINCT_BECAUSE": (
                f"Breakout P1 is Close > max(previous {int(HIGH_WINDOW)} Highs), P2 is Volume > median "
                f"of previous {int(VOLUME_WINDOW)} with no 1.5x multiplier, P3 is Close > session VWAP, "
                "first-cross of P1. No prior TEST of a pre-existing HTF MA price, no frozen BREAK_LEVEL, "
                "no support-failure EXIT of that same level."
            ),
            "EXACT_OR_SEMANTIC_MATCH": False,
        },
        {
            "LINEAGE_ID": "C1_MULTI_TIMEFRAME",
            "THEIR_MECHANISM": (
                "1m FALSE→TRUE onset of a family predicate plus last completed 3m/5m bar of the "
                "SAME predicate as standing confirmation (asof finalize_t <= 1m t0)."
            ),
            "DISTINCT_BECAUSE": (
                "C1 confirms a boolean technical STATE on HTF. This uses 5m EMA21 as a numeric "
                "STRUCTURAL_PRICE_LEVEL with a TEST→BREAK→SUPPORT-FAIL price-interaction lifecycle. "
                "HTF trend_up / same-family state is not used. TEST asof is finalize_t <= START_T[i], "
                "stricter than C1's finalize_t <= 1m finalize_t."
            ),
            "EXACT_OR_SEMANTIC_MATCH": False,
        },
        {
            "LINEAGE_ID": "RECOVERY_SEQUENCE",
            "THEIR_MECHANISM": str(ENTRY_RULE_TEXT),
            "DISTINCT_BECAUSE": (
                "Recovery is next-bar reclaim/acceptance after a VWAP (or predefined) broken location. "
                "This is rejection at pre-existing HTF resistance, then subsequent break of that "
                "resistance and later support-conversion failure."
            ),
            "EXACT_OR_SEMANTIC_MATCH": False,
        },
        {
            "LINEAGE_ID": "SYSTEMATIC_STATE_TRANSITION",
            "THEIR_MECHANISM": (
                "PERSIST_NEXT or HANDOFF_NEXT on five frozen 1m booleans (MA trend_up, BB-mid, "
                "RCI>-80, volume confirm, Close>VWAP). EXIT Z3."
            ),
            "DISTINCT_BECAUSE": (
                "ST is temporal occupancy of 1m booleans, not an HTF numeric level episode. "
                "This does not use persist/handoff, BB, RCI, VWAP, or Z3."
            ),
            "EXACT_OR_SEMANTIC_MATCH": False,
        },
        {
            "LINEAGE_ID": "VWAP_REJECTION_RECLAIM",
            "THEIR_MECHANISM": "VWAP rejection then reclaim chase family.",
            "DISTINCT_BECAUSE": "This architecture forbids VWAP entry/exit/filter. Level is 5m EMA21, not VWAP.",
            "EXACT_OR_SEMANTIC_MATCH": False,
        },
        {
            "LINEAGE_ID": "FAILED_BREAKDOWN_RECLAIM",
            "THEIR_MECHANISM": "Failed breakdown reclaim of a lost downside location.",
            "DISTINCT_BECAUSE": (
                "This is upside resistance test then break of a pre-existing HTF MA, not a failed "
                "breakdown reclaim."
            ),
            "EXACT_OR_SEMANTIC_MATCH": False,
        },
        {
            "LINEAGE_ID": "SIMPLE_FULL",
            "THEIR_MECHANISM": "Per-name first-cross of 1m E1-E5 conjunction × X-grid × Z1-Z5.",
            "DISTINCT_BECAUSE": "Not an E-grid first-cross. EXIT is support-fail of frozen BREAK_LEVEL, not Z-grid.",
            "EXACT_OR_SEMANTIC_MATCH": False,
        },
        {
            "LINEAGE_ID": "CSB",
            "THEIR_MECHANISM": (
                "Cross-sectional breadth of 1m trend_up expanding on a common timer, gating "
                "per-name FALSE→TRUE onset. EXIT Z_MA_TREND_LOSS. Closed DEV G1-G6 fail."
            ),
            "DISTINCT_BECAUSE": (
                "CSB is other-names' trend_up count. This is own-name interaction with a numeric "
                "5m EMA21 level. HTF_TREND_BOOLEAN_USED=false. CSB is closed and not retuned."
            ),
            "EXACT_OR_SEMANTIC_MATCH": False,
        },
    ]
    match = any(bool(r.get("EXACT_OR_SEMANTIC_MATCH")) for r in rows)
    return {
        "ARCHITECTURE_ID": ARCHITECTURE_ID,
        "CANDIDATE_MECHANISM": candidate,
        "COMPARED": rows,
        "CLOSEST_PRIOR_ARCHITECTURE": "C1_MULTI_TIMEFRAME",
        "CLOSED_LINEAGE_MATCH": bool(match),
        "STRUCTURALLY_DISTINCT": not bool(match),
        "DISTINCT_FROM_BREAKOUT": True,
        "DISTINCT_FROM_C1": True,
        "DISTINCT_FROM_RECOVERY": True,
        "DISTINCT_FROM_ST": True,
        "AUDIT_DONE": True,
    }
