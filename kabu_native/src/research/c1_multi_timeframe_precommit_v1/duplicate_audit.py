"""Full Strategy identity vs closed families. V7 diagnostic is not automatic duplicate. No backfill."""
from __future__ import annotations

from typing import Any

COMPARE_ARCHITECTURES = (
    "SIMPLE_TECH_V25_V29",
    "SIMPLE_TECH_V7_TIMEFRAME_ROLE_RCA",
    "SIMPLE_FULL_STRATEGY_75_GRID",
    "SIMPLE_TECH_BRANCH_U",
    "SIMPLE_TECH_BRANCH_P",
    "BREAKOUT_CONTINUATION",
    "VWAP_REJECTION_RECLAIM",
    "FAILED_BREAKDOWN_RECLAIM",
    "E4_X2_Z3",
    "RECOVERY_SEQUENCE",
    "PARTICIPATION_ONSET",
    "SYSTEMATIC_STATE_TRANSITION_25",
    "E1_X6_SCORE_JOINT",
    "E1_X6R3_CONT_PULL_BREAK",
    "E1_X6_TAER",
    "RPFE",
    "VCIE",
    "E1_X7_PFQ",
    "AM_C0",
    "DYNAMIC_ANCHOR_TRAIL10",
    "SIMPLE_TECH_ENTRY_FAMILY",
    "C4_PORTFOLIO_CROWDING",
)


def _why(c: dict[str, Any]) -> tuple[str, str, str]:
    cid = str(c["CANDIDATE_ID"])
    sid = str(c["STATE_ID"])
    htf = str(c["HTF_ID"])
    why = (
        "Full Strategy identity is 1m FALSE→TRUE onset of one family plus last completed "
        f"same-family {htf} standing context as-of t0, then X1_IMMEDIATE_ASK + Z3_TWO_BAR_WEAKNESS "
        "+ CAP=5 + same-symbol + slot release. That identity was not executed. "
        "V7 evaluated native 3m/5m roles on the HTF clock (and a leaky same-bucket diagnostic join); "
        "it did not run 1m onset + completed HTF context + X1 + Z3 + CAP5. "
        "TEMPORAL_STATE_TRANSITION is 1m persist/handoff with no HTF as-of. "
        "Simple-Tech AND-grid, 75-grid first-cross, Recovery 3-state, Participation 10s T1, "
        "Branch U/P EXIT, E4 first-cross, VWAP reclaim, Breakout P1-P3, RPFE/VCIE/PFQ/C0/X6 "
        "are different machines. Sharing EMA/BB/RCI/volume/VWAP primitives is not automatic exclude."
    )
    nearest = "SIMPLE_TECH_V7_TIMEFRAME_ROLE_RCA / SYSTEMATIC_STATE_TRANSITION_25"
    if sid == "S_CLOSE_ABOVE_VWAP":
        nearest = "E4_X2_Z3 / VWAP_REJECTION_RECLAIM / SYSTEMATIC_STATE_TRANSITION_25"
        why += f" {cid}: HTF VWAP is standing 1m session VWAP as-of HTF finalize, not E4 first-cross."
    elif sid == "S_VOL_CONFIRM_1M":
        nearest = "PARTICIPATION_ONSET / SIMPLE_FULL E2 / SYSTEMATIC_STATE_TRANSITION_25"
    elif sid == "S_MA_TREND_UP":
        nearest = "SIMPLE_TECH_ENTRY_FAMILY / SYSTEMATIC_STATE_TRANSITION_25"
    elif sid == "S_BB_ABOVE_MID":
        nearest = "SIMPLE_TECH_BRANCH_P / SIMPLE_TECH_V25_V29"
    elif sid == "S_RCI_ABOVE_NEG80":
        nearest = "SIMPLE_TECH_ENTRY_FAMILY / SIMPLE_TECH_BRANCH_U"
    return "KEEP", nearest, why


def audit_raw_library(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for c in raw:
        action, nearest, why = _why(c)
        out.append(
            {
                "CANDIDATE_ID": c["CANDIDATE_ID"],
                "TEMPLATE": c["TEMPLATE"],
                "STATE_ID": c["STATE_ID"],
                "HTF_ID": c["HTF_ID"],
                "ACTION": action,
                "MATCHING_ARCHITECTURE_ID": nearest if action != "KEEP" else "",
                "NEAREST_ARCHITECTURE": nearest,
                "EXACT_DUPLICATE": False,
                "SEMANTIC_DUPLICATE": False,
                "CLOSED_LINEAGE_REPRODUCTION": False,
                "WHY": why,
                "BACKFILL": False,
                "V7_AUTOMATIC_DUPLICATE": False,
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
        "CANDIDATE11": False,
    }
    if counts["FINAL_CANDIDATE_N"] > counts["RAW_CANDIDATE_N"]:
        raise RuntimeError("BACKFILL_DETECTED")
    if counts["RAW_CANDIDATE_N"] > 10:
        raise RuntimeError("RAW_CANDIDATE_N_GT_10")
    return kept, counts
