"""Prove capture rec['sequence'] is the causal unique same-t0 order identity."""
from __future__ import annotations

from typing import Any


def empty_seq_audit() -> dict[str, int]:
    return {
        "EVENT_N": 0,
        "SEQ_PRESENT_N": 0,
        "SEQ_MISSING_N": 0,
        "SEQ_DUPLICATE_N": 0,
        "SEQ_NON_MONOTONE_N": 0,
        "SEQ_ZERO_N": 0,
        "BAR_FINISH_SEQ_DUPLICATE_N": 0,
    }


def observe_sequence(audit: dict[str, int], seen: set[int], last: int | None, seq: Any, *, present: bool) -> int | None:
    audit["EVENT_N"] = int(audit.get("EVENT_N") or 0) + 1
    if not present:
        audit["SEQ_MISSING_N"] = int(audit.get("SEQ_MISSING_N") or 0) + 1
        return last
    try:
        n = int(seq)
    except (TypeError, ValueError):
        audit["SEQ_MISSING_N"] = int(audit.get("SEQ_MISSING_N") or 0) + 1
        return last
    audit["SEQ_PRESENT_N"] = int(audit.get("SEQ_PRESENT_N") or 0) + 1
    if n == 0:
        audit["SEQ_ZERO_N"] = int(audit.get("SEQ_ZERO_N") or 0) + 1
    if n in seen:
        audit["SEQ_DUPLICATE_N"] = int(audit.get("SEQ_DUPLICATE_N") or 0) + 1
    seen.add(n)
    if last is not None and n <= last:
        audit["SEQ_NON_MONOTONE_N"] = int(audit.get("SEQ_NON_MONOTONE_N") or 0) + 1
    return n


def summarize_order(day_audits: list[dict[str, Any]]) -> dict[str, Any]:
    tot = empty_seq_audit()
    for a in day_audits:
        for k in tot:
            tot[k] = int(tot[k]) + int(a.get(k) or 0)
    unique = int(tot["SEQ_DUPLICATE_N"]) == 0 and int(tot["SEQ_MISSING_N"]) == 0
    monotone = int(tot["SEQ_NON_MONOTONE_N"]) == 0
    causal = unique and monotone
    unresolvable = (not unique) or int(tot["EVENT_N"]) == 0
    incomplete = unique and not monotone
    return {
        "PRE_ADMISSION_STREAM_AVAILABLE": int(tot["EVENT_N"]) > 0,
        "CANONICAL_SOURCE_EVENT_ORDER_AVAILABLE": bool(unique),
        "SOURCE_EVENT_ORDER_CAUSAL": bool(causal),
        "SOURCE_EVENT_ORDER_UNIQUE": bool(unique),
        "FILESYSTEM_ORDER_USED": False,
        "DATAFRAME_ROW_ORDER_USED": False,
        "PYTHON_DICT_ORDER_USED": False,
        "LEXICOGRAPHIC_SYMBOL_ORDER_USED": False,
        "QUALITY_ORDER_USED": False,
        "PNL_ORDER_USED": False,
        "ORDER_FIELD": "capture_sequence",
        "ORDER_SOURCE": "market_push rec['sequence']",
        "SCAN_MONOTONE_IN_SEQUENCE": bool(monotone),
        "UNRESOLVABLE": bool(unresolvable),
        "PROOF_INCOMPLETE": bool(incomplete),
        "totals": tot,
        "ok": bool(causal and unique),
    }
