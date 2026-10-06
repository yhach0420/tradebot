"""Fold-local structural eligibility parity. Training blocks only. No economics."""
from __future__ import annotations

from typing import Any

from research.c4_portfolio_crowding_full_strategy_v2 import FROZEN_ELIGIBLE_IDS, MIN_INFORMATIVE_BLOCK_N
from research.systematic_state_transition_library_precommit_v1 import FOLD_BLOCKS

BLOCK_IDS = tuple(FOLD_BLOCKS.keys())


def _reject_n(row: dict[str, Any], block_id: str) -> int:
    key = f"C4_REJECT_N_{block_id}"
    if key not in row or row[key] is None:
        return 0
    return int(row[key])


def train_informative_n(row: dict[str, Any], *, held_out: str) -> int:
    n = 0
    for bid in BLOCK_IDS:
        if bid == held_out:
            continue
        if _reject_n(row, bid) > 0:
            n += 1
    return n


def train_eligible_ids(block_rows: list[dict[str, Any]], *, held_out: str) -> list[str]:
    out: list[str] = []
    for row in block_rows:
        eid = str(row.get("ENTRY_ID") or "")
        if train_informative_n(row, held_out=held_out) >= int(MIN_INFORMATIVE_BLOCK_N):
            out.append(eid)
    return out


def is_handoff(entry_id: str) -> bool:
    return str(entry_id).startswith("ST_HANDOFF_NEXT__")


def evaluate_fold_local_parity(block_rows: list[dict[str, Any]]) -> dict[str, Any]:
    frozen = list(FROZEN_ELIGIBLE_IDS)
    folds: list[dict[str, Any]] = []
    n_by: dict[str, int] = {}
    all_match = True
    any_handoff = False
    detail_rows: list[dict[str, Any]] = []
    if len(block_rows) != 25:
        all_match = False
    for held in BLOCK_IDS:
        ids = train_eligible_ids(block_rows, held_out=held)
        n_by[held] = len(ids)
        handoff_ids = [i for i in ids if is_handoff(i)]
        if handoff_ids:
            any_handoff = True
        match = ids == frozen
        if not match:
            all_match = False
        folds.append(
            {
                "held_out": held,
                "train_blocks": [b for b in BLOCK_IDS if b != held],
                "TRAIN_ELIGIBLE_IDS": ids,
                "FULL_DEV_FROZEN_ELIGIBLE_IDS": frozen,
                "TRAIN_ELIGIBLE_EQ_FROZEN": bool(match),
                "FOLD_LOCAL_ELIGIBLE_ENTRY_N": len(ids),
                "HANDOFF_TRAIN_ELIGIBLE_IDS": handoff_ids,
            }
        )
        for row in block_rows:
            eid = str(row.get("ENTRY_ID") or "")
            info_n = train_informative_n(row, held_out=held)
            detail_rows.append(
                {
                    "held_out": held,
                    "ENTRY_ID": eid,
                    "TRAIN_C4_INFORMATIVE_BLOCK_N": info_n,
                    "TRAIN_C4_ATTRIBUTION_ELIGIBLE": info_n >= int(MIN_INFORMATIVE_BLOCK_N),
                    "HANDOFF": is_handoff(eid),
                }
            )
    pass_flag = (
        bool(all_match)
        and not any_handoff
        and all(int(n_by.get(b) or 0) == 5 for b in BLOCK_IDS)
        and len(block_rows) == 25
    )
    return {
        "FOLD_LOCAL_ELIGIBILITY_PARITY_PASS": bool(pass_flag),
        "FOLD_LOCAL_ELIGIBLE_ENTRY_N_B1": int(n_by.get("B1") or 0),
        "FOLD_LOCAL_ELIGIBLE_ENTRY_N_B2": int(n_by.get("B2") or 0),
        "FOLD_LOCAL_ELIGIBLE_ENTRY_N_B3": int(n_by.get("B3") or 0),
        "FOLD_LOCAL_ELIGIBLE_ENTRY_N_B4": int(n_by.get("B4") or 0),
        "FOLD_LOCAL_ELIGIBLE_ENTRY_N_B5": int(n_by.get("B5") or 0),
        "ANY_HANDOFF_TRAIN_ELIGIBLE": bool(any_handoff),
        "FULL_DEV_FROZEN_ELIGIBLE_IDS": frozen,
        "folds": folds,
        "rows": detail_rows,
        "HELD_OUT_INTERVENTION_USED": False,
        "PNL_USED": False,
        "PF_USED": False,
        "FILLS_USED": False,
        "TRADES_USED": False,
        "NEW_THRESHOLD": False,
        "NEW_C4_POLICY": False,
    }
