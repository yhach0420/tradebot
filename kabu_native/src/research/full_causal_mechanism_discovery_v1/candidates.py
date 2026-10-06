"""Complete Full Causal candidate identities. Closed ST/Recovery references tagged before economics."""
from __future__ import annotations

from typing import Any

from research.causal_mechanism_representation_expansion_v1.library import candidate_library
from research.causal_mechanism_representation_expansion_v1.spec import dumps_sha256
from research.full_causal_mechanism_discovery_v1 import (
    EXECUTION_ID,
    EXIT_O1,
    EXIT_O2,
    EXIT_O3,
    POSITION_CAP,
    SAME_SYMBOL_BLOCK,
    SESSION_FLATTEN_HM,
    SHARES,
)
from research.full_causal_mechanism_discovery_v1.mapping import mapping_sha256
from research.systematic_state_transition_full_strategy_v1.spec import frozen_library

OCCUPANCY_SEMANTICS = (
    "Signal does not reserve a slot. Occupancy starts at actual ENTRY fill. "
    "Occupancy ends at actual EXIT fill. ENTRY_PENDING is not occupancy. "
    "EXIT_PENDING remains occupancy."
)
SLOT_RELEASE_SEMANTICS = "Slot release occurs only at actual EXIT fill."
REENTRY_SEMANTICS = (
    "Reentry allowed only after actual slot release AND after a newly valid mechanism signal."
)
SESSION_SEMANTICS = (
    "At 11:29 JST: no new ENTRY signal admission; expire ENTRY_PENDING; no ENTRY fill after "
    "flatten boundary. Open positions not already EXIT_PENDING become SESSION_EXIT_PENDING, "
    "then first fresh causal Bid1, EXIT fill, SLOT_RELEASE. No backward quote walk. No synthetic fill."
)


def _exit_for(template: str) -> str:
    if template == "O1_PERSIST_NEXT":
        return EXIT_O1
    if template == "O2_HANDOFF_NEXT":
        return EXIT_O2
    if template == "O3_RECLAIM_ACCEPT_NEXT":
        return EXIT_O3
    raise ValueError(template)


def strategy_id(mechanism_id: str, exit_id: str) -> str:
    return f"FC__{mechanism_id}__{EXECUTION_ID}__{exit_id}"


def st_entry_identity(mech: dict[str, Any]) -> str | None:
    template = str(mech["TEMPLATE"])
    prim = list(mech.get("PRIMITIVES") or [])
    if template == "O1_PERSIST_NEXT" and prim:
        return f"ST_PERSIST_NEXT__{prim[0]}"
    if template == "O2_HANDOFF_NEXT" and len(prim) >= 2:
        return f"ST_HANDOFF_NEXT__{prim[0]}__{prim[1]}"
    if template == "O3_RECLAIM_ACCEPT_NEXT":
        rid = str(mech.get("RECLAIM_ID") or "")
        if rid:
            return f"RECOVERY_{rid}_X1_Z3"
    return None


def closed_st_ids() -> set[str]:
    return {str(r["CANDIDATE_ID"]) for r in frozen_library()}


def complete_strategy(mech: dict[str, Any], *, st_ids: set[str] | None = None) -> dict[str, Any]:
    if st_ids is None:
        st_ids = closed_st_ids()
    template = str(mech["TEMPLATE"])
    exit_id = _exit_for(template)
    sid = strategy_id(str(mech["MECHANISM_ID"]), exit_id)
    entry_id = st_entry_identity(mech)
    o3 = template == "O3_RECLAIM_ACCEPT_NEXT"
    st_hit = bool(entry_id) and (str(entry_id) in st_ids or o3)
    return {
        "STRATEGY_ID": sid,
        "MECHANISM_ID": str(mech["MECHANISM_ID"]),
        "OPERATOR": template,
        "PREDICATES": list(mech.get("PRIMITIVES") or []),
        "PATH": list(mech.get("PRIMITIVES") or []),
        "RECLAIM_ID": mech.get("RECLAIM_ID"),
        "DEFINITION": str(mech.get("DEFINITION") or ""),
        "EXECUTION_ID": EXECUTION_ID,
        "TECHNICAL_EXIT_ID": exit_id,
        "CAP": int(POSITION_CAP),
        "SHARES": int(SHARES),
        "SAME_SYMBOL": bool(SAME_SYMBOL_BLOCK),
        "OCCUPANCY": OCCUPANCY_SEMANTICS,
        "SLOT_RELEASE": SLOT_RELEASE_SEMANTICS,
        "REENTRY": REENTRY_SEMANTICS,
        "SESSION": SESSION_SEMANTICS,
        "SESSION_FLATTEN_HM": list(SESSION_FLATTEN_HM),
        "EXACT_CLOSED_ENTRY_IDENTITY": bool(st_hit),
        "CLOSED_ENTRY_ID": entry_id if st_hit else None,
        "SELECTABLE": not bool(st_hit),
        "CONTROL_OR_CLOSED_REFERENCE": bool(st_hit),
        "MAPPING_SHA256": mapping_sha256(),
    }


def candidate_set() -> list[dict[str, Any]]:
    st_ids = closed_st_ids()
    out = [complete_strategy(m, st_ids=st_ids) for m in candidate_library()]
    ids = [r["STRATEGY_ID"] for r in out]
    if len(ids) != len(set(ids)):
        raise RuntimeError("DUPLICATE_STRATEGY_ID")
    if len(out) != 42:
        raise RuntimeError("CANDIDATE_N")
    return out


def candidate_set_payload(rows: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    xs = rows if rows is not None else candidate_set()
    keys = (
        "STRATEGY_ID",
        "MECHANISM_ID",
        "OPERATOR",
        "PREDICATES",
        "EXECUTION_ID",
        "TECHNICAL_EXIT_ID",
        "CAP",
        "SAME_SYMBOL",
        "OCCUPANCY",
        "SLOT_RELEASE",
        "REENTRY",
        "SESSION",
        "SELECTABLE",
        "EXACT_CLOSED_ENTRY_IDENTITY",
        "CLOSED_ENTRY_ID",
    )
    return [{k: r[k] for k in keys} for r in xs]


def candidate_set_sha256(rows: list[dict[str, Any]] | None = None) -> str:
    return dumps_sha256(candidate_set_payload(rows))


_FROZEN: dict[str, Any] | None = None


def freeze_candidate_set() -> dict[str, Any]:
    global _FROZEN
    if _FROZEN is not None:
        return _FROZEN
    rows = candidate_set()
    closed = [r for r in rows if r.get("EXACT_CLOSED_ENTRY_IDENTITY")]
    selectable = [r for r in rows if r.get("SELECTABLE")]
    _FROZEN = {
        "candidates": rows,
        "FULL_CAUSAL_CANDIDATE_N": int(len(rows)),
        "CLOSED_REFERENCE_N": int(len(closed)),
        "SELECTABLE_FULL_CAUSAL_CANDIDATE_N": int(len(selectable)),
        "FULL_CAUSAL_CANDIDATE_SET_SHA256": candidate_set_sha256(rows),
        "CANDIDATE_SET_FROZEN_BEFORE_ECONOMICS": True,
        "SELECTABLE_IDS": [r["STRATEGY_ID"] for r in selectable],
        "CLOSED_IDS": [r["STRATEGY_ID"] for r in closed],
        "by_id": {str(r["STRATEGY_ID"]): r for r in rows},
    }
    return _FROZEN


def uses_breadth(row: dict[str, Any]) -> bool:
    return "S_BREADTH_EXPANDING" in list(row.get("PREDICATES") or row.get("PATH") or [])
