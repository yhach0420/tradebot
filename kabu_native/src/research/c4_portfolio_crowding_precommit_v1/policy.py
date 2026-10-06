"""Exact C4 first-arrival policy. Freeze before counts. No future uniqueness. No backfill."""
from __future__ import annotations

from typing import Any

from research.c1_multi_timeframe_precommit_v1.spec import dumps_sha256
from research.c4_portfolio_crowding_precommit_v1 import (
    C4_SCOPE,
    CONTROL_POLICY_ID,
    TREATMENT_POLICY_ID,
)

POLICY_SOURCE_TEXT = (
    "For each ENTRY_ID independently: last_seen_t0 = NONE. "
    "Process raw pre-admission candidates in canonical capture_sequence order. "
    "For candidate r: if r.t0 != last_seen_t0 then C4_PASS=true and last_seen_t0=r.t0; "
    "else C4_PASS=false and reject_reason=C4_REJECT_LATER_SAME_T0. "
    "Maximum one candidate per exact ENTRY_ID × exact t0. No rounding. "
    "Decision is made immediately when the candidate arrives. "
    "FUTURE_SAME_T0_COUNT_REQUIRED=false. SAME_T0_BACKFILL=false. "
    "Do not promote a later same-t0 candidate if the first fails X1, is stale, "
    "fails CAP, fails same-symbol, or does not fill. "
    "C4 may not inspect future execution success, future occupancy, future slot release, or PnL. "
    "Forbidden: same-minute grouping, 1/5/10-sec buckets, rolling window, occupancy-k, "
    "candidate-count threshold, filesystem/symbol/quality/PnL order."
)

EVENT_ORDER = (
    "1 raw ENTRY signal",
    "2 C4_FIRST_ARRIVAL_PER_EXACT_T0",
    "3 X1 execution search",
    "4 same-symbol / CAP / occupancy",
    "5 ENTRY fill",
    "6 technical EXIT",
    "7 EXIT fill",
    "8 slot release",
)


def policy_contract() -> dict[str, Any]:
    return {
        "POLICY_ID": TREATMENT_POLICY_ID,
        "CONTROL_POLICY_ID": CONTROL_POLICY_ID,
        "AMBIGUOUS_NAME_FORBIDDEN": "C4_FIRST_UNIQUE_T0",
        "SCOPE": C4_SCOPE,
        "EXACT_T0_SEMANTICS": "r.t0 != last_seen_t0 with no rounding",
        "FIRST_ARRIVAL_WINS": True,
        "FUTURE_UNIQUENESS_FORBIDDEN": True,
        "FUTURE_SAME_T0_COUNT_REQUIRED": False,
        "SAME_T0_BACKFILL": False,
        "CANONICAL_ORDER_FIELD": "capture_sequence",
        "CANONICAL_ORDER_SOURCE": "market_push rec['sequence']",
        "SOURCE_EVENT_ORDER_CAUSAL": True,
        "FILESYSTEM_ORDER_USED": False,
        "DATAFRAME_ROW_ORDER_USED": False,
        "PYTHON_DICT_ORDER_USED": False,
        "LEXICOGRAPHIC_SYMBOL_ORDER_USED": False,
        "QUALITY_ORDER_USED": False,
        "PNL_ORDER_USED": False,
        "EVENT_ORDER": list(EVENT_ORDER),
        "SOURCE_TEXT": POLICY_SOURCE_TEXT,
        "DIFFERENT_ENTRY_IDENTITIES_NOT_POOLED": True,
    }


def policy_sha256() -> str:
    return dumps_sha256(policy_contract())


def apply_c4_first_arrival(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Online first-arrival. Sort key is capture_sequence only. No future same-t0 count."""
    ordered = sorted(rows, key=lambda r: int(r["source_event_seq"]))
    last_seen: float | None = None
    passed: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for r in ordered:
        t0 = r["t0"]
        rec = dict(r)
        if last_seen is None or t0 != last_seen:
            rec["C4_PASS"] = True
            rec["reject_reason"] = None
            passed.append(rec)
            last_seen = t0
        else:
            rec["C4_PASS"] = False
            rec["reject_reason"] = "C4_REJECT_LATER_SAME_T0"
            rejected.append(rec)
    return {
        "passed": passed,
        "rejected": rejected,
        "C4_PASS_N": len(passed),
        "C4_REJECT_LATER_SAME_T0_N": len(rejected),
        "RAW_SIGNAL_N": len(ordered),
        "SAME_T0_BACKFILL": False,
        "FUTURE_SAME_T0_COUNT_REQUIRED": False,
    }
