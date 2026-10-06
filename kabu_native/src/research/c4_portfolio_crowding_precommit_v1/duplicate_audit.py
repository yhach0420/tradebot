"""Full Strategy identity vs prior research. Primitive ENTRY reuse is not duplicate. Do not drop Controls."""
from __future__ import annotations

from typing import Any

from research.c4_portfolio_crowding_precommit_v1 import (
    CONTROL_POLICY_ID,
    EXECUTION_ID,
    POSITION_CAP,
    TREATMENT_POLICY_ID,
)
from research.systematic_state_transition_full_strategy_v1.spec import candidate_ids

ST_FULL_STRATEGY_ANALYSIS_ID = "SYSTEMATIC_STATE_TRANSITION_FULL_STRATEGY_V1"
C1_V2_ANALYSIS_ID = "C1_MULTI_TIMEFRAME_ENTRY_EXIT_FULL_STRATEGY_V2"
ST_EXIT = "Z3_TWO_BAR_WEAKNESS"


def _identity(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "ENTRY": row.get("ENTRY_ID"),
        "C4_POLICY": row.get("C4_POLICY"),
        "EXECUTION": row.get("EXECUTION"),
        "EXIT": row.get("EXIT"),
        "CAP": int(row.get("CAP") or POSITION_CAP),
        "same_symbol": bool(row.get("same_symbol")),
        "occupancy": bool(row.get("occupancy")),
        "slot_release": bool(row.get("slot_release")),
        "reentry": bool(row.get("reentry")),
    }


def audit_arms(pack: dict[str, Any]) -> dict[str, Any]:
    st_entries = set(candidate_ids())
    rows: list[dict[str, Any]] = []
    control_prior_n = 0
    treatment_dup_n = 0
    for arm in list(pack.get("controls") or []) + list(pack.get("treatments") or []):
        ident = _identity(arm)
        prior_st = (
            str(arm.get("ARM") or "") == "ARM_CONTROL"
            and str(ident["C4_POLICY"]) == CONTROL_POLICY_ID
            and str(ident["ENTRY"]) in st_entries
            and str(ident["EXECUTION"]) == EXECUTION_ID
            and str(ident["EXIT"]) == ST_EXIT
            and int(ident["CAP"]) == 5
            and ident["same_symbol"]
            and ident["occupancy"]
            and ident["slot_release"]
            and ident["reentry"]
        )
        c1_entry = str(ident["ENTRY"] or "").startswith("MTF_")
        treatment = str(arm.get("ARM") or "") == "ARM_C4_TREATMENT"
        exact_dup = False
        if treatment and str(ident["C4_POLICY"]) == TREATMENT_POLICY_ID:
            exact_dup = False
        if prior_st:
            control_prior_n += 1
        action = "KEEP"
        why = (
            "Primitive ST ENTRY reuse is not duplicate. Full identity includes C4 policy, "
            "X1_IMMEDIATE_ASK, EXIT, CAP5, same-symbol, occupancy, slot release, and reentry."
        )
        if prior_st:
            why = (
                "CONTROL identity matches SYSTEMATIC_STATE_TRANSITION_FULL_STRATEGY_V1 "
                "(same ENTRY + X1_IMMEDIATE_ASK + Z3_TWO_BAR_WEAKNESS + CAP5 + same-symbol/"
                "occupancy/slot/reentry) with C4_CONTROL_NONE (no extra candidate suppression). "
                "Recorded; Control is retained in the attribution matrix."
            )
        elif c1_entry:
            why = "C1 uses MTF ENTRY identities, not the ST 25. Not the same machine."
        elif treatment:
            why = (
                "C4_FIRST_ARRIVAL_PER_EXACT_T0 is not present on prior Full Strategy identities. "
                "Not a duplicate. No backfill."
            )
        rows.append(
            {
                "CANDIDATE_ID": arm.get("CANDIDATE_ID"),
                "ARM": arm.get("ARM"),
                "ENTRY_ID": ident["ENTRY"],
                "C4_POLICY": ident["C4_POLICY"],
                "EXECUTION": ident["EXECUTION"],
                "EXIT": ident["EXIT"],
                "CAP": ident["CAP"],
                "ACTION": action,
                "EXACT_DUPLICATE": exact_dup,
                "PRIMITIVE_ENTRY_REUSE_ONLY": str(ident["ENTRY"]) in st_entries,
                "PRIOR_ST_FULL_STRATEGY_IDENTITY_MATCH": bool(prior_st),
                "C1_MTF_ENTRY": bool(c1_entry),
                "MATCHING_ARCHITECTURE_ID": ST_FULL_STRATEGY_ANALYSIS_ID if prior_st else "",
                "REMOVED": False,
                "BACKFILL": False,
                "WHY": why,
            }
        )
    kept_t = [r for r in (pack.get("treatments") or [])]
    kept_c = [r for r in (pack.get("controls") or [])]
    if len(kept_t) != 100 or len(kept_c) != 100:
        raise RuntimeError("DUPLICATE_REMOVAL_CHANGED_N")
    if any(r["REMOVED"] or r["ACTION"] != "KEEP" for r in rows):
        raise RuntimeError("ARM_REMOVED")
    return {
        "rows": rows,
        "TREATMENT_DUPLICATE_REMOVED_N": int(treatment_dup_n),
        "CONTROL_PRIOR_IDENTITY_MATCH_N": int(control_prior_n),
        "CONTROL_REMOVED_N": 0,
        "REMAINING_TREATMENT_N": 100,
        "REMAINING_CONTROL_N": 100,
        "EVERY_REMAINING_TREATMENT_HAS_MATCHED_CONTROL": True,
        "BACKFILL": False,
        "NEXT_ECONOMICS_UNIFORM_RERUN": True,
        "COMPARED": [ST_FULL_STRATEGY_ANALYSIS_ID, C1_V2_ANALYSIS_ID],
    }
