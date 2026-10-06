"""Finite O1/O2/O3 library. Frozen before selectable candidate outcomes. No new thresholds."""
from __future__ import annotations

from typing import Any

from research.causal_mechanism_representation_expansion_v1 import OPERATORS, RECLAIM_ACCEPT_IDS
from research.profitable_move_mechanism_discovery_v1.library import CROSS_PAIRS, RAW_PREDICATE_IDS
from research.profitable_move_mechanism_discovery_v1.predicates import FAMILY, SOURCE_IDENTITY
from research.recovery_sequence_full_strategy_architecture_v1.entries import ENTRY_RULE_TEXT

TEMPLATE_RANK = {name: i for i, name in enumerate(OPERATORS)}


def _src(pid: str) -> str:
    return str(SOURCE_IDENTITY[pid])


def _fam(pid: str) -> str:
    return str(FAMILY[pid])


def _card(
    mech_id: str,
    template: str,
    primitives: tuple[str, ...],
    definition: str,
    *,
    reclaim_id: str | None = None,
) -> dict[str, Any]:
    fams = [_fam(p) for p in primitives] if primitives and primitives[0] in FAMILY else []
    return {
        "MECHANISM_ID": mech_id,
        "TEMPLATE": template,
        "PRIMITIVES": list(primitives),
        "PRIMITIVE_N": int(len(primitives)),
        "FAMILIES": fams,
        "DEFINITION": definition,
        "SOURCE_IDENTITIES": {p: _src(p) for p in primitives if p in SOURCE_IDENTITY},
        "TEMPLATE_RANK": int(TEMPLATE_RANK[template]),
        "RECLAIM_ID": reclaim_id,
        "ONSET_OF": None,
    }


def persist_id(pid: str) -> str:
    return f"O1_PERSIST_NEXT__{pid}"


def handoff_id(a: str, b: str) -> str:
    return f"O2_HANDOFF_NEXT__{a}__{b}"


def reclaim_id(rid: str) -> str:
    return f"O3_RECLAIM_ACCEPT_NEXT__{rid}"


def candidate_library() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for pid in RAW_PREDICATE_IDS:
        out.append(
            _card(
                persist_id(pid),
                "O1_PERSIST_NEXT",
                (pid,),
                (
                    f"At completed evaluation r: {pid} transitions FALSE→TRUE; do not signal. "
                    f"Exact next evaluation c=r+1: if {pid}[c]==TRUE then signal, else abort."
                ),
            )
        )
    for a, b in CROSS_PAIRS:
        out.append(
            _card(
                handoff_id(a, b),
                "O2_HANDOFF_NEXT",
                (a, b),
                (
                    f"Ordered {a}→{b}. At completed evaluation r: {a} transitions FALSE→TRUE. "
                    f"Exact next evaluation c=r+1: if {a}[c]==TRUE AND {b}[c] transitions "
                    "FALSE→TRUE then signal, else abort."
                ),
            )
        )
    for rid in RECLAIM_ACCEPT_IDS:
        out.append(
            _card(
                reclaim_id(rid),
                "O3_RECLAIM_ACCEPT_NEXT",
                ("S_CLOSE_ABOVE_VWAP",),
                str(ENTRY_RULE_TEXT[rid]),
                reclaim_id=rid,
            )
        )
    ids = [str(r["MECHANISM_ID"]) for r in out]
    assert len(ids) == len(set(ids))
    expected = int(len(RAW_PREDICATE_IDS)) + int(len(CROSS_PAIRS)) + int(len(RECLAIM_ACCEPT_IDS))
    assert len(out) == expected
    return out


def library_by_id() -> dict[str, dict[str, Any]]:
    return {str(r["MECHANISM_ID"]): r for r in candidate_library()}


EXACT_PRIOR = {
    "O3_RECLAIM_ACCEPT_NEXT__R2": "RECOVERY_R2_X1_Z3",
    "O3_RECLAIM_ACCEPT_NEXT__R1": "RECOVERY_R1_X1_Z3",
    "O3_RECLAIM_ACCEPT_NEXT__R3": "RECOVERY_R3_X1_Z3",
    "O2_HANDOFF_NEXT__S_CLOSE_ABOVE_VWAP__S_RCI_ABOVE_NEG80": (
        "ST_HANDOFF_NEXT__S_CLOSE_ABOVE_VWAP__S_RCI_ABOVE_NEG80"
    ),
}


def classify_candidate(cand: dict[str, Any]) -> dict[str, Any]:
    mid = str(cand["MECHANISM_ID"])
    exact = EXACT_PRIOR.get(mid)
    if exact:
        klass = "A_EXACT_CLOSED_STRATEGY_MATCH"
        nearest = exact
    else:
        klass = "B_SAME_INFORMATION_NEW_DECISION_MECHANISM"
        nearest = "TEMPORAL_OPERATOR_EXPANSION"
    return {
        "MECHANISM_ID": mid,
        "EXACT_PRIOR_TEST_MATCH": bool(exact),
        "EXACT_CLOSED_LINEAGE": exact,
        "NEAREST_CLOSED_LINEAGE": nearest,
        "LINEAGE_CLASS": klass,
        "ELIGIBLE_AS_NEW_ARCHITECTURE": klass
        in ("B_SAME_INFORMATION_NEW_DECISION_MECHANISM", "C_GENUINELY_NEW_INFORMATION_MECHANISM"),
    }


def classify_library(cands: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [classify_candidate(c) for c in cands]
