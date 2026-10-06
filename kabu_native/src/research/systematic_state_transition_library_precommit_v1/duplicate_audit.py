"""Semantic identity vs closed architectures. Share primitives ≠ automatic exclude. No backfill."""
from __future__ import annotations

from typing import Any

COMPARE_ARCHITECTURES = (
    "SIMPLE_TECH_V25_V29",
    "SIMPLE_FULL_STRATEGY_75_GRID",
    "SIMPLE_TECH_BRANCH_U",
    "SIMPLE_TECH_BRANCH_P",
    "BREAKOUT_CONTINUATION",
    "VWAP_REJECTION_RECLAIM",
    "FAILED_BREAKDOWN_RECLAIM",
    "E4_X2_Z3",
    "RECOVERY_SEQUENCE",
    "PARTICIPATION_ONSET",
    "E1_X6_SCORE_JOINT",
    "E1_X6R3_CONT_PULL_BREAK",
    "E1_X6_TAER",
    "RPFE",
    "VCIE",
    "E1_X7_PFQ",
    "AM_C0",
    "DYNAMIC_ANCHOR_TRAIL10",
    "SIMPLE_TECH_ENTRY_FAMILY",
)

# Identity unit: ENTRY state machine + event timing + lookback + threshold + execution + EXIT + CAP/same-symbol/slot-release.
# PERSIST_NEXT / HANDOFF_NEXT on completed 1m + X1_IMMEDIATE_ASK + Z3_TWO_BAR_WEAKNESS + CAP=5
# is not first-cross static AND, not 10s T1 onset, not 3-state recovery, not quote-inferred impulse.


def _note(candidate: dict[str, Any]) -> tuple[str, str, str]:
    """Return ACTION, MATCHING_ARCHITECTURE_ID, WHY."""
    tmpl = str(candidate["TEMPLATE"])
    a = str(candidate["STATE_A"])
    b = str(candidate.get("STATE_B") or "")
    nearest = "SIMPLE_FULL_STRATEGY_75_GRID"
    why_common = (
        "Full Strategy identity requires same ENTRY state machine, timing, lookback, "
        "threshold, execution, EXIT, CAP, same-symbol, and slot-release. "
        "This candidate is TEMPORAL_STATE_TRANSITION ("
        f"{tmpl}) on completed 1m with X1_IMMEDIATE_ASK + Z3_TWO_BAR_WEAKNESS + CAP=5. "
        "Sharing a primitive with a closed family is not automatic exclude."
    )
    if tmpl == "PERSIST_NEXT":
        if a == "S_CLOSE_ABOVE_VWAP":
            nearest = "E4_X2_Z3 / SIMPLE_FULL E4_VWAP_RECLAIM_SIMPLE / VWAP_REJECTION_RECLAIM"
            why = (
                why_common
                + " Closest: E4 first-cross Close from below VWAP, signal on the cross bar, "
                "X2 mid (E4_X2_Z3) or 75-grid X1/X2/X3. PERSIST_NEXT signals only if still "
                "above VWAP on the next completed bar. Reclaim used Low<VWAP then Close>VWAP, not this machine."
            )
            return "KEEP", nearest, why
        if a == "S_VOL_CONFIRM_1M":
            nearest = "PARTICIPATION_ONSET / SIMPLE_FULL E2_VOLUME_CONTINUATION"
            why = (
                why_common
                + " Closest: P1_X1_Z3 is 10s T1 volume_percentile_60s>=0.648... FALSE→TRUE, not 1m "
                "volume_confirm persist. E2 is Close>prevClose AND Volume>median10 first-cross "
                "(window 10, no 1.5x, extra price conjunct)."
            )
            return "KEEP", nearest, why
        if a == "S_MA_TREND_UP":
            nearest = "SIMPLE_TECH_ENTRY_FAMILY"
            why = (
                why_common
                + " Closest: V1 setup_ready ANDs trend_up with pullback+RCI+volume+BB-upper on the same bar. "
                "PERSIST_NEXT is trend_up onset plus one-bar persistence only."
            )
            return "KEEP", nearest, why
        if a == "S_BB_ABOVE_MID":
            nearest = "SIMPLE_TECH_BRANCH_P / SIMPLE_TECH_V25_V29"
            why = (
                why_common
                + " Closest: V26 D_BB_STRUCTURE_LOSS and Branch P use Close<bb_mid as EXIT, not "
                "PERSIST_NEXT of Close>bb_mid as ENTRY."
            )
            return "KEEP", nearest, why
        if a == "S_RCI_ABOVE_NEG80":
            nearest = "SIMPLE_TECH_ENTRY_FAMILY / SIMPLE_TECH_BRANCH_U"
            why = (
                why_common
                + " Closest: reversal_rci is a one-bar -80 cross used inside same-bar V1 AND; "
                "Branch U uses rci9<=-80 as EXIT. PERSIST_NEXT is standing rci9>-80 for one extra bar."
            )
            return "KEEP", nearest, why
    if tmpl == "HANDOFF_NEXT":
        if {a, b} == {"S_MA_TREND_UP", "S_RCI_ABOVE_NEG80"}:
            nearest = "SIMPLE_TECH_ENTRY_FAMILY"
            why = (
                why_common
                + f" Closest: V1 same-bar TREND+RCI (+pullback+volume). HANDOFF {a}→{b} is ordered "
                "two-state next-bar only, no pullback, no 3-state search."
            )
            return "KEEP", nearest, why
        if {a, b} == {"S_CLOSE_ABOVE_VWAP", "S_VOL_CONFIRM_1M"}:
            nearest = "BREAKOUT_CONTINUATION / SIMPLE_FULL E1/E2"
            why = (
                why_common
                + f" Closest: Breakout same-bar P2 AND P3 first-cross; 75-grid E1/E2 same-bar AND. "
                f"HANDOFF {a}→{b} is sequential next-bar, not static AND, not 5-high breakout P1."
            )
            return "KEEP", nearest, why
        if a == "S_CLOSE_ABOVE_VWAP" or b == "S_CLOSE_ABOVE_VWAP":
            nearest = "VWAP_REJECTION_RECLAIM / E4_X2_Z3"
            why = (
                why_common
                + f" VWAP location is one ordered operand only. HANDOFF {a}→{b} is not E4 first-cross "
                "and not Low<VWAP reclaim sequence."
            )
            return "KEEP", nearest, why
        if a == "S_VOL_CONFIRM_1M" or b == "S_VOL_CONFIRM_1M":
            nearest = "PARTICIPATION_ONSET / DYNAMIC_ANCHOR_TRAIL10"
            why = (
                why_common
                + f" 1m volume_confirm is not 10s T1 onset and not Dynamic Anchor 10-minute confirmation. "
                f"HANDOFF {a}→{b}."
            )
            return "KEEP", nearest, why
        nearest = "SIMPLE_TECH_ENTRY_FAMILY / SIMPLE_FULL_STRATEGY_75_GRID"
        why = (
            why_common
            + f" HANDOFF {a}→{b} is a 2-state next-bar machine. Recovery Sequence is 3-state with "
            "variable wait (forbidden here). RPFE/VCIE/PFQ/C0/X6 use different primitives."
        )
        return "KEEP", nearest, why
    return "CASE_E_UNKNOWN_TEMPLATE", "", "TEMPLATE not PERSIST_NEXT or HANDOFF_NEXT"


def audit_raw_library(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for c in raw:
        action, match, why = _note(c)
        out.append(
            {
                "CANDIDATE_ID": c["CANDIDATE_ID"],
                "TEMPLATE": c["TEMPLATE"],
                "STATE_A": c["STATE_A"],
                "STATE_B": c["STATE_B"],
                "ACTION": action,
                "MATCHING_ARCHITECTURE_ID": match if action != "KEEP" else "",
                "NEAREST_ARCHITECTURE": match,
                "EXACT_DUPLICATE": False,
                "SEMANTIC_DUPLICATE": False,
                "CLOSED_LINEAGE_REPRODUCTION": False,
                "WHY": why,
                "BACKFILL": False,
                "COMPARED_FAMILIES": ",".join(COMPARE_ARCHITECTURES),
            }
        )
    return out


def apply_prune(raw: list[dict[str, Any]], dup_map: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    by = {r["CANDIDATE_ID"]: r for r in dup_map}
    kept = []
    dup_n = 0
    closed_n = 0
    unknown = 0
    for c in raw:
        row = by[c["CANDIDATE_ID"]]
        act = str(row["ACTION"])
        if act == "KEEP":
            kept.append(dict(c))
        elif act == "EXCLUDE_DUPLICATE":
            dup_n += 1
        elif act == "EXCLUDE_CLOSED_LINEAGE":
            closed_n += 1
        else:
            unknown += 1
    counts = {
        "RAW_CANDIDATE_N": len(raw),
        "DUPLICATE_CANDIDATE_N": dup_n,
        "CLOSED_LINEAGE_CANDIDATE_N": closed_n,
        "UNKNOWN_IDENTITY_N": unknown,
        "FINAL_CANDIDATE_N": len(kept),
        "BACKFILL": False,
    }
    if counts["FINAL_CANDIDATE_N"] > counts["RAW_CANDIDATE_N"]:
        raise RuntimeError("BACKFILL_DETECTED")
    if counts["RAW_CANDIDATE_N"] > 25:
        raise RuntimeError("RAW_CANDIDATE_N_GT_25")
    return kept, counts
