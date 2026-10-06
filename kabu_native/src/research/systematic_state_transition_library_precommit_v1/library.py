"""Raw PERSIST_NEXT + HANDOFF_NEXT library. N + N*(N-1) <= 25. No 26th. No static AND."""
from __future__ import annotations

from typing import Any

from research.systematic_state_transition_library_precommit_v1 import ENTRY_TEMPLATES, POSITION_CAP, SHARES
from research.systematic_state_transition_library_precommit_v1.spec import (
    execution_contract,
    exit_contract,
    portfolio_contract,
)
from research.systematic_state_transition_library_precommit_v1.state_registry import available_states, build_state_registry

PERSIST_TEXT = (
    "At completed 1m bar r: S transitions FALSE→TRUE; do not enter. "
    "Exact next completed evaluation point c=r+1: if S[c]==TRUE then signal, else abort. "
    "No extra wait."
)
HANDOFF_TEXT = (
    "Ordered A→B. At completed 1m bar r: A transitions FALSE→TRUE. "
    "Exact next evaluation point c=r+1: if A[c]==TRUE AND B[c] transitions FALSE→TRUE "
    "then signal, else abort. No extra wait."
)


def _shared_tail() -> dict[str, Any]:
    return {
        "EXECUTION": execution_contract()["EXEC_ID"],
        "EXIT": exit_contract()["EXIT_ID"],
        "SHARES": int(SHARES),
        "CAP": int(POSITION_CAP),
        "same_symbol": True,
        "occupancy": True,
        "slot_release": True,
        "reentry": True,
        "STATIC_AND": False,
        "VARIABLE_WAIT": False,
        "THREE_STATE_SEQUENCE": False,
        "OR_EXPRESSION": False,
        "WEIGHTED_SCORE": False,
    }


def persist_id(state_id: str) -> str:
    return f"ST_PERSIST_NEXT__{state_id}"


def handoff_id(a: str, b: str) -> str:
    return f"ST_HANDOFF_NEXT__{a}__{b}"


def build_raw_library(registry: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    states = available_states(registry if registry is not None else build_state_registry())
    n = len(states)
    if n > 5:
        raise RuntimeError("AVAILABLE_STATE_N_GT_5")
    raw: list[dict[str, Any]] = []
    tail = _shared_tail()
    for s in states:
        sid = str(s["STATE_ID"])
        raw.append(
            {
                "CANDIDATE_ID": persist_id(sid),
                "TEMPLATE": "PERSIST_NEXT",
                "STATE_A": sid,
                "STATE_B": "",
                "FAMILY_A": s["FAMILY"],
                "FAMILY_B": "",
                "ENTRY_STATE_MACHINE": (
                    f"At completed 1m bar r: {sid} transitions FALSE→TRUE; do not enter. "
                    f"Exact next completed evaluation point c=r+1: if {sid}[c]==TRUE then signal, else abort. "
                    "No extra wait."
                ),
                "EVALUATION_CLOCK": "COMPLETED_1M_BAR",
                **tail,
            }
        )
    for a in states:
        for b in states:
            if a["STATE_ID"] == b["STATE_ID"]:
                continue
            aid = str(a["STATE_ID"])
            bid = str(b["STATE_ID"])
            raw.append(
                {
                    "CANDIDATE_ID": handoff_id(aid, bid),
                    "TEMPLATE": "HANDOFF_NEXT",
                    "STATE_A": aid,
                    "STATE_B": bid,
                    "FAMILY_A": a["FAMILY"],
                    "FAMILY_B": b["FAMILY"],
                    "ENTRY_STATE_MACHINE": (
                        f"Ordered {aid} → {bid}. At completed 1m bar r: {aid} transitions FALSE→TRUE. "
                        f"Exact next evaluation point c=r+1: if {aid}[c]==TRUE AND {bid}[c] transitions "
                        "FALSE→TRUE then signal, else abort. No extra wait."
                    ),
                    "EVALUATION_CLOCK": "COMPLETED_1M_BAR",
                    **tail,
                }
            )
    if len(raw) != n + n * max(n - 1, 0):
        raise RuntimeError("RAW_LIBRARY_COUNT_MISMATCH")
    if len(raw) > 25:
        raise RuntimeError("RAW_CANDIDATE_N_GT_25")
    ids = [r["CANDIDATE_ID"] for r in raw]
    if len(ids) != len(set(ids)):
        raise RuntimeError("RAW_LIBRARY_ID_COLLISION")
    assert tuple(ENTRY_TEMPLATES) == ("PERSIST_NEXT", "HANDOFF_NEXT")
    return raw
