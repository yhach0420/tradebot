"""C4 V2 seen-set first-arrival. Per ENTRY_ID per trading day. No contiguity. No backfill."""
from __future__ import annotations

from typing import Any

from research.c1_multi_timeframe_precommit_v1.spec import dumps_sha256
from research.c4_portfolio_crowding_precommit_v2 import (
    C4_SCOPE,
    CONTROL_POLICY_ID,
    TREATMENT_POLICY_ID,
)

POLICY_SOURCE_TEXT = (
    "For each ENTRY_ID independently, per trading session/day: seen_t0 = empty set. "
    "Process raw pre-admission signals strictly by capture_sequence. "
    "For candidate r: if r.t0 not in seen_t0 then C4_PASS=true and seen_t0.add(r.t0); "
    "else C4_PASS=false and reject_reason=C4_REJECT_LATER_SAME_T0. "
    "Reset seen_t0 at each trading day/session. Exact t0 only. No rounding. "
    "Decision is made immediately when the candidate arrives. "
    "FUTURE_SAME_T0_COUNT_REQUIRED=false. SAME_T0_BACKFILL=false. "
    "Do not promote a later same-t0 candidate if the first fails X1, freshness, CAP, "
    "same-symbol, or fill. "
    "Forbidden: 1/5-sec buckets, same-minute buckets, rolling windows, candidate-count thresholds."
)

EVENT_ORDER = (
    "1 raw ENTRY signal",
    "2 C4 V2 gate",
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
        "CORRECTION_CLASS": "SEMANTIC_IMPLEMENTATION_CORRECTION",
        "PRIOR_POLICY_ID": "C4_FIRST_ARRIVAL_PER_EXACT_T0",
        "PRIOR_IMPLEMENTATION": "last_seen_t0 contiguity",
        "SCOPE": C4_SCOPE,
        "EXACT_T0_SEMANTICS": "r.t0 not in seen_t0 with no rounding; reset per day",
        "FIRST_ARRIVAL_WINS": True,
        "FUTURE_UNIQUENESS_FORBIDDEN": True,
        "FUTURE_SAME_T0_COUNT_REQUIRED": False,
        "SAME_T0_BACKFILL": False,
        "DAY_RESET": True,
        "CANONICAL_ORDER_FIELD": "capture_sequence",
        "CANONICAL_ORDER_SOURCE": "market_push rec['sequence']",
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


def apply_c4_v2(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Online first-arrival via seen-set. Sort by capture_sequence only. Reset per day."""
    ordered = sorted(rows, key=lambda r: int(r["source_event_seq"]))
    seen_by_day: dict[str, set[Any]] = {}
    passed: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for r in ordered:
        day = str(r.get("date") or "")
        t0 = r["t0"]
        rec = dict(r)
        seen = seen_by_day.setdefault(day, set())
        if t0 not in seen:
            rec["C4_PASS"] = True
            rec["reject_reason"] = None
            passed.append(rec)
            seen.add(t0)
        else:
            rec["C4_PASS"] = False
            rec["reject_reason"] = "C4_REJECT_LATER_SAME_T0"
            rejected.append(rec)
    return {
        "passed": passed,
        "rejected": rejected,
        "C4_PASS_N": len(passed),
        "C4_REJECT_N": len(rejected),
        "C4_REJECT_LATER_SAME_T0_N": len(rejected),
        "RAW_SIGNAL_N": len(ordered),
        "SAME_T0_BACKFILL": False,
        "FUTURE_SAME_T0_COUNT_REQUIRED": False,
        "DAY_RESET": True,
    }
