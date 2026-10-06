"""A1-A8 eligibility. No PnL. No new replay."""
from __future__ import annotations

from typing import Any

GATES = (
    "A1_COMPLETE_FULL_STRATEGY",
    "A2_CAUSAL_IMPLEMENTABLE",
    "A3_DATA_AVAILABLE_ON_DEV",
    "A4_STRUCTURALLY_DISTINCT",
    "A5_EXPLAINABLE",
    "A6_NO_NEW_THRESHOLD_SEARCH_REQUIRED",
    "A7_COVERAGE_PLAUSIBLE",
    "A8_NO_CLOSED_LINEAGE_RESCUE",
)

REQUIRED_COMPLETE = (
    "ARCHITECTURE_ID",
    "CORE_MECHANISM",
    "CAUSAL_EVENT_SEQUENCE",
    "ENTRY_STATE_MACHINE",
    "EXECUTION_RULE",
    "EXIT_STATE_MACHINE",
    "CAP_BEHAVIOR",
    "SAME_SYMBOL_BEHAVIOR",
    "OCCUPANCY_BEHAVIOR",
    "SLOT_RELEASE",
    "REENTRY",
    "SESSION_CLOSE",
    "EXPECTED_COVERAGE_REASON",
    "STRUCTURAL_NOVELTY_REASON",
)


def _complete(p: dict[str, Any]) -> bool:
    if p.get("PLACEHOLDER"):
        return False
    for k in REQUIRED_COMPLETE:
        v = p.get(k)
        if v in (None, "", [], {}):
            return False
    exec_u = dict(p.get("EXECUTION_RULE") or {})
    if not exec_u.get("EXEC_ID"):
        return False
    ex = dict(p.get("EXIT_STATE_MACHINE") or {})
    if not ex.get("EXIT_ID"):
        return False
    return True


def _int(v: Any, default: int) -> int:
    if v is None or v is False:
        return default
    return int(v)


def evaluate_proposal(p: dict[str, Any]) -> dict[str, Any]:
    a1 = _complete(p)
    a2 = bool(p.get("WHAT_EVENT_CREATES_ELIGIBILITY")) and "future" not in str(p.get("WHEN_IT_BECOMES_KNOWN") or "").lower()
    a3 = True
    a4 = p.get("CLOSED_LINEAGE_MATCH") is False
    a5 = bool(p.get("CORE_MECHANISM")) and bool(p.get("STRUCTURAL_NOVELTY_REASON"))
    a6 = _int(p.get("DISCRETIONARY_DOF"), 99) == 0
    a7 = _int(p.get("COVERAGE_PLAUSIBILITY_RANK"), 9) <= 1
    a8 = p.get("CLOSED_LINEAGE_MATCH") is False
    if p.get("VWAP_ENTRY_USED") or p.get("VWAP_EXIT_USED") or p.get("VWAP_FILTER_USED"):
        a8 = False
        a4 = False
    if p.get("BOARD_PRIMARY_ALPHA"):
        a8 = False
        a4 = False
    gates = {
        "A1_COMPLETE_FULL_STRATEGY": bool(a1),
        "A2_CAUSAL_IMPLEMENTABLE": bool(a2),
        "A3_DATA_AVAILABLE_ON_DEV": bool(a3),
        "A4_STRUCTURALLY_DISTINCT": bool(a4),
        "A5_EXPLAINABLE": bool(a5),
        "A6_NO_NEW_THRESHOLD_SEARCH_REQUIRED": bool(a6),
        "A7_COVERAGE_PLAUSIBLE": bool(a7),
        "A8_NO_CLOSED_LINEAGE_RESCUE": bool(a8),
    }
    eligible = all(gates[k] for k in GATES)
    return {
        "ARCHITECTURE_ID": p.get("ARCHITECTURE_ID"),
        "PROPOSAL_ELIGIBLE": bool(eligible),
        "CLOSED_LINEAGE_MATCH": p.get("CLOSED_LINEAGE_MATCH"),
        **gates,
    }


def selection_key(p: dict[str, Any]) -> tuple[Any, ...]:
    return (
        _int(p.get("DISCRETIONARY_DOF"), 99),
        _int(p.get("SIGNAL_PRIMITIVE_N"), 99),
        _int(p.get("STATE_MACHINE_SIZE"), 99),
        _int(p.get("COVERAGE_PLAUSIBILITY_RANK"), 99),
        _int(p.get("IMPLEMENTATION_AMBIGUITY_RANK"), 99),
        str(p.get("ARCHITECTURE_ID") or ""),
    )
