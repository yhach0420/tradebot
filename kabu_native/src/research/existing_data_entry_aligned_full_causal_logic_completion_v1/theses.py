"""ENTRY_THESIS_OBJECT for every raw PERSIST_NEXT / HANDOFF_NEXT. Pre-economics. One EXIT each."""
from __future__ import annotations

from typing import Any

from research.existing_data_entry_aligned_full_causal_logic_completion_v1 import (
    RCI_ID,
    SELECTABLE_N,
    STATE_IDS,
    STRUCTURAL_IDS,
    STRUCTURALLY_INELIGIBLE_N,
    VOL_ID,
)
from research.systematic_state_transition_library_precommit_v1.library import handoff_id, persist_id
from research.systematic_state_transition_library_precommit_v1.state_registry import available_states, build_state_registry

ROLES = {
    "S_MA_TREND_UP": "STRUCTURAL_TREND",
    "S_BB_ABOVE_MID": "STRUCTURAL_PRICE_ACCEPTANCE",
    "S_RCI_ABOVE_NEG80": "DIRECTIONAL_MOMENTUM_RECOVERY",
    "S_VOL_CONFIRM_1M": "TRANSIENT_PARTICIPATION_CONFIRMATION",
    "S_CLOSE_ABOVE_VWAP": "STRUCTURAL_LOCATION_ACCEPTANCE",
}


def _exit_id(holding: list[str]) -> str:
    return "Z_THESIS_INVALIDATE__" + "__".join(holding)


def _invalidation(holding: list[str]) -> str:
    parts = [f"{s}=FALSE" for s in holding]
    joined = " OR ".join(parts)
    return f"first later completed bar where {joined} (known FALSE). UNKNOWN does not invalidate."


def _holding_handoff(a: str, b: str) -> tuple[list[str], list[str]]:
    struct = [s for s in (a, b) if s in STRUCTURAL_IDS]
    vol = [VOL_ID] if VOL_ID in (a, b) else []
    if len(struct) == 2:
        return [a, b], []
    if len(struct) == 1:
        return list(struct), vol
    return [RCI_ID], [VOL_ID]


def build_theses() -> list[dict[str, Any]]:
    states = available_states(build_state_registry())
    ids = [str(s["STATE_ID"]) for s in states]
    if tuple(ids) != STATE_IDS:
        raise RuntimeError("STATE_ID_MISMATCH")
    rows: list[dict[str, Any]] = []
    for sid in ids:
        eligible = sid != VOL_ID
        holding = [sid] if eligible else []
        rows.append(
            {
                "ENTRY_ID": persist_id(sid),
                "ENTRY_TYPE": "PERSIST_NEXT",
                "SOURCE_STATE": sid,
                "DESTINATION_STATE": sid,
                "A": sid,
                "B": "",
                "SOURCE_ROLE": ROLES[sid],
                "DESTINATION_ROLE": ROLES[sid],
                "A_ROLE": ROLES[sid],
                "B_ROLE": "",
                "ENTRY_REASON": (
                    f"{sid} known FALSE→TRUE at completed bar r and remains TRUE at c=r+1. "
                    "ENTRY at c."
                ),
                "TRIGGER_STATES": [sid],
                "CONFIRMATION_STATES": [sid],
                "HOLDING_THESIS_STATES": holding,
                "TRANSIENT_STATES": [sid] if sid == VOL_ID else [],
                "INVALIDATION_CONDITION": _invalidation(holding) if eligible else "",
                "TECHNICAL_EXIT_ID": _exit_id(holding) if eligible else "",
                "COMPLETE_STRATEGY_ELIGIBLE": eligible,
                "INELIGIBLE_REASON": "" if eligible else "NO_DURABLE_HOLDING_THESIS",
                "EXIT_VARIANTS_PER_ENTRY": 1 if eligible else 0,
            }
        )
    for a in ids:
        for b in ids:
            if a == b:
                continue
            holding, transient = _holding_handoff(a, b)
            if a in STRUCTURAL_IDS and b in STRUCTURAL_IDS:
                reason = (
                    f"Durable structure {a} appears, then durable structure {b} joins while {a} remains. "
                    "Holding thesis is both structures."
                )
            elif (a in STRUCTURAL_IDS) != (b in STRUCTURAL_IDS) and RCI_ID in (a, b):
                st = a if a in STRUCTURAL_IDS else b
                reason = (
                    f"Structural {st} is the holding thesis; RCI is momentum confirmation. "
                    "Do not exit merely because RCI returns below -80."
                )
            elif (a in STRUCTURAL_IDS) != (b in STRUCTURAL_IDS) and VOL_ID in (a, b):
                st = a if a in STRUCTURAL_IDS else b
                reason = (
                    f"Structural {st} is the holding thesis; VOL is transient participation. "
                    "Do not exit merely because volume returns to normal."
                )
            else:
                reason = (
                    "RCI is the only directional non-transient state; VOL is transient confirmation. "
                    "Exit when RCI is known FALSE."
                )
            rows.append(
                {
                    "ENTRY_ID": handoff_id(a, b),
                    "ENTRY_TYPE": "HANDOFF_NEXT",
                    "SOURCE_STATE": a,
                    "DESTINATION_STATE": b,
                    "A": a,
                    "B": b,
                    "SOURCE_ROLE": ROLES[a],
                    "DESTINATION_ROLE": ROLES[b],
                    "A_ROLE": ROLES[a],
                    "B_ROLE": ROLES[b],
                    "ENTRY_REASON": (
                        f"At r: {a} known FALSE→TRUE. At c=r+1: {a} remains TRUE AND {b} known FALSE→TRUE. "
                        + reason
                    ),
                    "TRIGGER_STATES": [a],
                    "CONFIRMATION_STATES": [b],
                    "HOLDING_THESIS_STATES": holding,
                    "TRANSIENT_STATES": transient,
                    "INVALIDATION_CONDITION": _invalidation(holding),
                    "TECHNICAL_EXIT_ID": _exit_id(holding),
                    "COMPLETE_STRATEGY_ELIGIBLE": True,
                    "INELIGIBLE_REASON": "",
                    "EXIT_VARIANTS_PER_ENTRY": 1,
                }
            )
    if len(rows) != 25:
        raise RuntimeError("THESIS_ROW_N")
    ineligible = [r for r in rows if not r["COMPLETE_STRATEGY_ELIGIBLE"]]
    if len(ineligible) != int(STRUCTURALLY_INELIGIBLE_N):
        raise RuntimeError("INELIGIBLE_N")
    if ineligible[0]["ENTRY_ID"] != persist_id(VOL_ID):
        raise RuntimeError("PERSIST_VOL_NOT_INELIGIBLE")
    unresolved = [r for r in rows if r["COMPLETE_STRATEGY_ELIGIBLE"] is None]
    if unresolved:
        raise RuntimeError("UNRESOLVED_THESIS")
    selectable = [r for r in rows if r["COMPLETE_STRATEGY_ELIGIBLE"]]
    if len(selectable) != int(SELECTABLE_N):
        raise RuntimeError("SELECTABLE_N")
    for r in selectable:
        if int(r["EXIT_VARIANTS_PER_ENTRY"]) != 1:
            raise RuntimeError("EXIT_VARIANTS")
        if not r["HOLDING_THESIS_STATES"] or not r["TECHNICAL_EXIT_ID"]:
            raise RuntimeError("MISSING_EXIT")
    return rows


def selectable_theses(rows: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    return [r for r in (rows if rows is not None else build_theses()) if r["COMPLETE_STRATEGY_ELIGIBLE"]]


def thesis_by_id(rows: list[dict[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
    return {str(r["ENTRY_ID"]): r for r in (rows if rows is not None else build_theses())}


def library_rows(rows: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    out = []
    for r in selectable_theses(rows):
        rec = dict(r)
        rec["STRATEGY_ID"] = str(r["ENTRY_ID"])
        rec["EXECUTION"] = "X1_IMMEDIATE_ASK"
        rec["CAP"] = 5
        rec["Z3_CANDIDATE"] = False
        rec["UNIVERSAL_EXIT"] = False
        out.append(rec)
    return out
