"""Attribution-eligible ENTRYs × 4 EXIT × matched Control/Treatment. No backfill."""
from __future__ import annotations

from typing import Any

from research.c4_portfolio_crowding_precommit_v1 import KEPT_EXIT_IDS
from research.c4_portfolio_crowding_precommit_v2 import (
    CONTROL_POLICY_ID,
    EXECUTION_ID,
    POSITION_CAP,
    SHARES,
    TREATMENT_POLICY_ID,
)
from research.c4_portfolio_crowding_precommit_v2.spec import arm_id
from research.systematic_state_transition_full_strategy_v1.spec import frozen_library


def build_eligible_library(eligible_ids: list[str]) -> dict[str, Any]:
    by = {str(e["CANDIDATE_ID"]): e for e in frozen_library()}
    unknown = [i for i in eligible_ids if i not in by]
    if unknown:
        raise RuntimeError("UNKNOWN_ELIGIBLE:" + ",".join(unknown))
    base = [dict(by[i]) for i in eligible_ids]
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
                "EXECUTION": EXECUTION_ID,
                "EXIT": exit_id,
                "SHARES": int(SHARES),
                "CAP": int(POSITION_CAP),
                "same_symbol": True,
                "occupancy": True,
                "slot_release": True,
                "reentry": True,
                "SESSION": "AM",
                "event_time_causal": True,
                "BACKFILL": False,
            }
            controls.append(
                {
                    **shared,
                    "CANDIDATE_ID": cid_c,
                    "ARM": "ARM_CONTROL",
                    "C4_POLICY": CONTROL_POLICY_ID,
                    "WINNER_ELIGIBLE": False,
                    "ATTRIBUTION_REFERENCE": True,
                }
            )
            treatments.append(
                {
                    **shared,
                    "CANDIDATE_ID": cid_t,
                    "ARM": "ARM_C4_TREATMENT",
                    "C4_POLICY": TREATMENT_POLICY_ID,
                    "WINNER_ELIGIBLE": True,
                    "ATTRIBUTION_REFERENCE": False,
                }
            )
            matrix.append(
                {
                    "ENTRY_ID": eid,
                    "EXIT": exit_id,
                    "EXECUTION": EXECUTION_ID,
                    "CONTROL_ID": cid_c,
                    "TREATMENT_ID": cid_t,
                    "MATCHED": True,
                    "DIFFERS_ONLY_BY_C4_POLICY": True,
                    "CONTROL_ELIGIBLE_AS_WINNER": False,
                }
            )
    n_entry = len(eligible_ids)
    matched = n_entry * 4
    if len(controls) != matched or len(treatments) != matched:
        raise RuntimeError("ELIGIBLE_ARM_N")
    if any("Z4_TRAILING_STRUCTURE" in r["CANDIDATE_ID"] for r in controls + treatments):
        raise RuntimeError("Z4")
    if any(r["CONTROL_ID"] == r["TREATMENT_ID"] for r in matrix):
        raise RuntimeError("CONTROL_EQ_TREATMENT")
    if not all(r["MATCHED"] for r in matrix):
        raise RuntimeError("UNMATCHED")
    return {
        "eligible_ids": list(eligible_ids),
        "controls": controls,
        "treatments": treatments,
        "matched_matrix": matrix,
        "ELIGIBLE_ENTRY_N": n_entry,
        "ELIGIBLE_MATCHED_STRATEGY_N": matched,
        "ELIGIBLE_CONTROL_ARM_N": matched,
        "ELIGIBLE_TREATMENT_ARM_N": matched,
        "ELIGIBLE_TOTAL_ARM_N": matched * 2,
        "EVERY_TREATMENT_HAS_MATCHED_CONTROL": True,
        "CONTROL_ELIGIBLE_AS_WINNER": False,
        "BACKFILL": False,
    }
