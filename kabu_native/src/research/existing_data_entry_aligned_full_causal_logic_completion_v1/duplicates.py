"""Exact complete-strategy duplicate check. Prior ST+Z3 is not a duplicate."""
from __future__ import annotations

from typing import Any

from research.existing_data_entry_aligned_full_causal_logic_completion_v1.theses import library_rows

# Prior complete identities: ENTRY + X1 + TECHNICAL EXIT.
# SYSTEMATIC_STATE_TRANSITION_FULL_STRATEGY_V1 used Z3_TWO_BAR_WEAKNESS for every ST ENTRY.
# FULL_CAUSAL_MECHANISM_DISCOVERY_V1 used Z_O1/Z_O2/Z_O3, not Z_THESIS_INVALIDATE.
PRIOR_COMPLETE_IDENTITIES = (
    # (ENTRY_ID_OR_MECHANISM, EXEC, TECHNICAL_EXIT)
    ("ST_PERSIST_NEXT__S_MA_TREND_UP", "X1_IMMEDIATE_ASK", "Z3_TWO_BAR_WEAKNESS"),
    ("ST_PERSIST_NEXT__S_BB_ABOVE_MID", "X1_IMMEDIATE_ASK", "Z3_TWO_BAR_WEAKNESS"),
    ("ST_PERSIST_NEXT__S_RCI_ABOVE_NEG80", "X1_IMMEDIATE_ASK", "Z3_TWO_BAR_WEAKNESS"),
    ("ST_PERSIST_NEXT__S_VOL_CONFIRM_1M", "X1_IMMEDIATE_ASK", "Z3_TWO_BAR_WEAKNESS"),
    ("ST_PERSIST_NEXT__S_CLOSE_ABOVE_VWAP", "X1_IMMEDIATE_ASK", "Z3_TWO_BAR_WEAKNESS"),
    ("O1_PERSIST_NEXT__S_MA_TREND_UP", "X1_IMMEDIATE_ASK", "Z_O1_STATE_INVALIDATION"),
    ("O2_HANDOFF_NEXT__S_CLOSE_ABOVE_VWAP__S_RCI_ABOVE_NEG80", "X1_IMMEDIATE_ASK", "Z_O2_HANDOFF_STATE_INVALIDATION"),
)


def identity_key(entry_id: str, exec_id: str, exit_id: str) -> tuple[str, str, str]:
    return (str(entry_id), str(exec_id), str(exit_id))


def audit_duplicates(lib: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = lib if lib is not None else library_rows()
    prior = {identity_key(a, b, c) for a, b, c in PRIOR_COMPLETE_IDENTITIES}
    hits = []
    kept = []
    for r in rows:
        key = identity_key(str(r["STRATEGY_ID"]), "X1_IMMEDIATE_ASK", str(r["TECHNICAL_EXIT_ID"]))
        if key in prior:
            hits.append({"STRATEGY_ID": r["STRATEGY_ID"], "KEY": list(key), "ACTION": "EXCLUDE"})
        else:
            kept.append(r)
    return {
        "PRIOR_COMPARED_N": len(prior),
        "EXACT_PRIOR_DUPLICATE_N": len(hits),
        "DUPLICATES": hits,
        "KEPT_N": len(kept),
        "KEPT": kept,
        "NOTE": (
            "Prior ST ENTRY + X1 + Z3 is not an exact duplicate of ST ENTRY + X1 + thesis invalidation. "
            "Candidates are not closed merely because the ENTRY was previously tested."
        ),
    }
