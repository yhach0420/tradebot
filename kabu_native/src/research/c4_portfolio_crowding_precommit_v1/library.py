"""25 ST ENTRY × 4 EXIT × CONTROL + TREATMENT = 200 arms. No 201st. No backfill."""
from __future__ import annotations

from typing import Any

from research.c4_portfolio_crowding_precommit_v1 import (
    CONTROL_POLICY_ID,
    EXECUTION_ID,
    KEPT_EXIT_IDS,
    POSITION_CAP,
    SHARES,
    TOTAL_ARM_N,
    TREATMENT_POLICY_ID,
)
from research.c4_portfolio_crowding_precommit_v1.spec import arm_id
from research.systematic_state_transition_full_strategy_v1.spec import frozen_library


def build_matched_library(entries: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    base = list(entries) if entries is not None else frozen_library()
    if len(base) != 25:
        raise RuntimeError("ENTRY_N")
    controls: list[dict[str, Any]] = []
    treatments: list[dict[str, Any]] = []
    matrix: list[dict[str, Any]] = []
    for entry in base:
        eid = str(entry["CANDIDATE_ID"])
        for exit_id in KEPT_EXIT_IDS:
            cid_c = arm_id(eid, CONTROL_POLICY_ID, exit_id)
            cid_t = arm_id(eid, TREATMENT_POLICY_ID, exit_id)
            shared = {
                "ENTRY_ID": eid,
                "TEMPLATE": entry.get("TEMPLATE"),
                "STATE_A": entry.get("STATE_A"),
                "STATE_B": entry.get("STATE_B"),
                "FAMILY_A": entry.get("FAMILY_A"),
                "FAMILY_B": entry.get("FAMILY_B"),
                "ENTRY_STATE_MACHINE": entry.get("ENTRY_STATE_MACHINE"),
                "EVALUATION_CLOCK": entry.get("EVALUATION_CLOCK"),
                "EXECUTION": EXECUTION_ID,
                "EXIT": exit_id,
                "SHARES": int(SHARES),
                "CAP": int(POSITION_CAP),
                "same_symbol": True,
                "occupancy": True,
                "slot_release": True,
                "reentry": True,
                "CROSS_FAMILY": False,
                "BACKFILL": False,
            }
            control = {
                **shared,
                "CANDIDATE_ID": cid_c,
                "ARM": "ARM_CONTROL",
                "C4_POLICY": CONTROL_POLICY_ID,
                "WINNER_ELIGIBLE": False,
                "ATTRIBUTION_REFERENCE": True,
            }
            treatment = {
                **shared,
                "CANDIDATE_ID": cid_t,
                "ARM": "ARM_C4_TREATMENT",
                "C4_POLICY": TREATMENT_POLICY_ID,
                "WINNER_ELIGIBLE": True,
                "ATTRIBUTION_REFERENCE": False,
            }
            controls.append(control)
            treatments.append(treatment)
            matrix.append(
                {
                    "ENTRY_ID": eid,
                    "EXIT": exit_id,
                    "EXECUTION": EXECUTION_ID,
                    "CONTROL_ID": cid_c,
                    "TREATMENT_ID": cid_t,
                    "MATCHED": True,
                    "CONTROL_ELIGIBLE_AS_WINNER": False,
                }
            )
    if len(controls) != 100 or len(treatments) != 100:
        raise RuntimeError("ARM_N")
    if any("Z4_TRAILING_STRUCTURE" in r["CANDIDATE_ID"] for r in controls + treatments):
        raise RuntimeError("Z4")
    if any("__X1__" in r["CANDIDATE_ID"] for r in controls + treatments):
        raise RuntimeError("BARE_X1")
    if any(r["CONTROL_ID"] == r["TREATMENT_ID"] for r in matrix):
        raise RuntimeError("CONTROL_EQ_TREATMENT")
    if not all(r["MATCHED"] for r in matrix):
        raise RuntimeError("UNMATCHED")
    ids = [r["CANDIDATE_ID"] for r in controls + treatments]
    if len(set(ids)) != TOTAL_ARM_N:
        raise RuntimeError("ARM_ID_COLLISION")
    return {
        "entries": base,
        "controls": controls,
        "treatments": treatments,
        "matched_matrix": matrix,
        "ENTRY_N": 25,
        "EXIT_N": 4,
        "MATCHED_STRATEGY_N": 100,
        "CONTROL_ARM_N": 100,
        "TREATMENT_ARM_N": 100,
        "TOTAL_ARM_N": 200,
        "EVERY_TREATMENT_HAS_MATCHED_CONTROL": True,
    }
