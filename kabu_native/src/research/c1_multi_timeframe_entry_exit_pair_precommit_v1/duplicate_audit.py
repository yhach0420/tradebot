"""Complete Full Strategy identity only. Primitive reuse is not duplicate. No backfill."""
from __future__ import annotations

from typing import Any

from research.c1_multi_timeframe_entry_exit_pair_precommit_v1 import EXECUTION_ID, EXIT_IDS

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
    "C1_MULTI_TIMEFRAME_Z3_ONLY_ROUTE",
)


def _identity(c: dict[str, Any]) -> tuple[Any, ...]:
    return (
        c["ENTRY_STATE_MACHINE"],
        c["HTF_ID"],
        c["HTF_WIDTH_SEC"],
        c["HTF_CAUSAL_ASOF"],
        c["EXECUTION"],
        c["EXIT"],
        c["CAP"],
        c["same_symbol"],
        c["occupancy"],
        c["slot_release"],
        c["reentry"],
    )


def _why(c: dict[str, Any]) -> tuple[str, str, str, bool]:
    cid = str(c["CANDIDATE_ID"])
    z = str(c["EXIT"])
    prior_z3 = z == "Z3_TWO_BAR_WEAKNESS"
    why = (
        "Full Strategy identity is 1m FALSE→TRUE onset of one family plus last completed "
        f"same-family {c['HTF_ID']} standing context as-of t0, then {EXECUTION_ID} + {z} "
        "+ CAP=5 + same-symbol + occupancy + slot release + reentry. "
        "SIMPLE_FULL 75-grid uses E1-E5 first-cross ENTRY and a different X1 "
        "(X1_PASSIVE_BID_THEN_CANCEL), so sharing Zi names is not exact duplicate. "
        "Old ENTRY+Zi failure does not auto-exclude a new MTF ENTRY+Zi pair. "
        "ST persist/handoff, Recovery R2, Participation T1, Branch U/P, E4 first-cross, "
        "V7 native HTF clock, and closed Simple-Tech families are different machines. "
        "Primitive reuse of EMA/BB/RCI/volume/VWAP/Zi is not automatic exclude."
    )
    nearest = "SIMPLE_FULL_STRATEGY_75_GRID / C1_MULTI_TIMEFRAME_Z3_ONLY_ROUTE"
    if prior_z3:
        why += (
            " This Z3 pair was previously evaluated on the superseded Z3-only C1 route; "
            "that route is not a closed-family exclude. It is retained so the 50-pair "
            "library is ranked jointly. Z3_ONLY_C1_CLOSURE_FORBIDDEN."
        )
    if EXECUTION_ID not in cid:
        return "EXCLUDE_DUPLICATE", "EXECUTION_ID_AMBIGUITY", "bare X1 identity", False
    return "KEEP", nearest, why, prior_z3


def audit_raw_library(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[tuple[Any, ...], str] = {}
    out = []
    for c in raw:
        ident = _identity(c)
        action, nearest, why, prior_z3 = _why(c)
        exact = False
        if ident in seen:
            action = "EXCLUDE_DUPLICATE"
            exact = True
            why = f"Exact complete Full Strategy identity already listed as {seen[ident]}."
            nearest = seen[ident]
        else:
            seen[ident] = str(c["CANDIDATE_ID"])
        out.append(
            {
                "CANDIDATE_ID": c["CANDIDATE_ID"],
                "ENTRY_ID": c["ENTRY_ID"],
                "EXIT": c["EXIT"],
                "EXECUTION": c["EXECUTION"],
                "HTF_ID": c["HTF_ID"],
                "STATE_ID": c["STATE_ID"],
                "TEMPLATE": c["TEMPLATE"],
                "ACTION": action,
                "MATCHING_ARCHITECTURE_ID": nearest if action != "KEEP" else "",
                "NEAREST_ARCHITECTURE": nearest,
                "EXACT_DUPLICATE": exact,
                "SEMANTIC_DUPLICATE": False,
                "CLOSED_LINEAGE_REPRODUCTION": False,
                "PRIOR_Z3_ONLY_ROUTE_EVALUATED": prior_z3,
                "PRIOR_Z3_ONLY_ROUTE_EXCLUDED": False,
                "OLD_ENTRY_ZI_FAILURE_AUTO_EXCLUDE": False,
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
        "RAW_STRATEGY_N": len(raw),
        "DUPLICATE_N": dup_n,
        "CLOSED_LINEAGE_N": closed_n,
        "UNKNOWN_IDENTITY_N": unknown,
        "FINAL_STRATEGY_N": len(kept),
        "BACKFILL": False,
        "CANDIDATE11": False,
        "EXIT_N": len(EXIT_IDS),
        "ENTRY_N": 10,
    }
    if counts["FINAL_STRATEGY_N"] > counts["RAW_STRATEGY_N"]:
        raise RuntimeError("BACKFILL_DETECTED")
    if counts["RAW_STRATEGY_N"] > 50:
        raise RuntimeError("RAW_STRATEGY_N_GT_50")
    return kept, counts
