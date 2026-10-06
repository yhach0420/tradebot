"""TARGET V4 M4 eligibility. Calendar + t0-known state only. No future events."""
from __future__ import annotations

from collections import Counter
from typing import Any, Optional

from research.e1_x34a_execution_policy.executable_board import (
    PREOPEN_ITAYOSE_SIGNS,
    SPECIAL_QUOTE_SIGNS,
    STATE_NOT_OPENED,
    STATE_PREOPEN_ITAYOSE,
    STATE_SPECIAL_QUOTE,
    STATE_SPECIAL_QUOTE_FIELD,
)
from research.target_price_contract_v3.analyze import _f
from research.target_price_contract_v4.analyze import first_fail_m4, row_primary_m4

ITAYOSE_STATES = {STATE_NOT_OPENED, STATE_PREOPEN_ITAYOSE}
SPECIAL_STATES = {STATE_SPECIAL_QUOTE, STATE_SPECIAL_QUOTE_FIELD}


def modeling_bucket(row: dict[str, Any]) -> str:
    if row_primary_m4(row):
        return "PRIMARY_MODELING_ELIGIBLE"
    if not row.get("tse_t1_cont"):
        return "STRUCTURAL_INELIGIBLE"
    if row.get("empty"):
        return "STRUCTURAL_INELIGIBLE"
    if not row.get("tse_t0_cont"):
        return "STRUCTURAL_INELIGIBLE"
    st = str(row.get("last_state") or "")
    ask_s, bid_s = str(row.get("last_ask_sign") or ""), str(row.get("last_bid_sign") or "")
    if st in ITAYOSE_STATES or ask_s in PREOPEN_ITAYOSE_SIGNS or bid_s in PREOPEN_ITAYOSE_SIGNS:
        return "STRUCTURAL_INELIGIBLE"
    if st in SPECIAL_STATES or ask_s in SPECIAL_QUOTE_SIGNS or bid_s in SPECIAL_QUOTE_SIGNS or row.get("last_special"):
        return "STRUCTURAL_INELIGIBLE"
    if _f(row.get("m4_t0_px")) is None:
        return "STRUCTURAL_INELIGIBLE"
    if _f(row.get("m4_t1_px")) is None:
        return "UNEXPECTED_TARGET_MISSING"
    return "STRUCTURAL_INELIGIBLE"


def target_v4(row: dict[str, Any]) -> Optional[float]:
    if not row_primary_m4(row):
        return None
    p0 = _f(row.get("m4_t0_px"))
    p1 = _f(row.get("m4_t1_px"))
    if p0 is None or p1 is None or p0 <= 0:
        return None
    return float(p1 / p0 - 1.0)


def rebase(rows: list[dict[str, Any]]) -> dict[str, Any]:
    c = Counter(modeling_bucket(r) for r in rows)
    n = len(rows)
    elig = int(c.get("PRIMARY_MODELING_ELIGIBLE") or 0)
    struct = int(c.get("STRUCTURAL_INELIGIBLE") or 0)
    unexp = int(c.get("UNEXPECTED_TARGET_MISSING") or 0)
    itayose_contam = 0
    special_contam = 0
    auction_contam = 0
    future = 0
    for r in rows:
        if not row_primary_m4(r):
            continue
        st = str(r.get("last_state") or "")
        age = _f(r.get("m4_t0_age"))
        lag = _f(r.get("last_lag"))
        if st in ITAYOSE_STATES and age is not None and lag is not None and abs(age - lag) < 1e-6:
            itayose_contam += 1
        if st in SPECIAL_STATES and age is not None and lag is not None and abs(age - lag) < 1e-6:
            special_contam += 1
        if r.get("t1_phase") in {"PM_CLOSING_AUCTION", "PM_MARKET_CLOSE"}:
            auction_contam += 1
        if bool(r.get("m4_future_event")):
            future += 1
    stop = bool(unexp > 20 and unexp / max(n, 1) > 0.005)
    fail_c = Counter(first_fail_m4(r) or "NONE" for r in rows if modeling_bucket(r) != "PRIMARY_MODELING_ELIGIBLE")
    return {
        "ROWS_TOTAL": n,
        "TARGET_V4_ROWS": elig,
        "PRIMARY_MODELING_ELIGIBLE": elig,
        "STRUCTURAL_INELIGIBLE_ROWS": struct,
        "UNEXPECTED_TARGET_MISSING": unexp,
        "ITAYOSE_TARGET_CONTAMINATION": itayose_contam,
        "SPECIAL_QUOTE_CONTAMINATION": special_contam,
        "CLOSING_AUCTION_ENDPOINT_CONTAMINATION": auction_contam,
        "FUTURE_EVENT_USE": future,
        "STOP_UNEXPECTED_MISSING": stop,
        "counts": dict(c),
        "first_fail_non_primary": dict(fail_c),
    }
