"""Fixed-trade × P2-1 Dynamic state. Uses canonical confirm ledger; no nearest-T1 invention."""
from __future__ import annotations

from typing import Any, Optional

from research.dynamic_anchor_p2_0b import (
    CONFIRMATION_NOT_EVALUABLE,
    CONFIRMED,
    REJECTED,
    SESSION_INCOMPLETE,
)
from research.dynamic_anchor_p2_1 import CAPTURE_BOUNDARY_INCOMPLETE

INCOMPLETE = {SESSION_INCOMPLETE, CAPTURE_BOUNDARY_INCOMPLETE}


def classify_dynamic_state(
    *,
    date: str,
    symbol: str,
    session: str,
    signal_t: float,
    confirms: list[dict[str, Any]],
) -> dict[str, Any]:
    """Primary state at Fixed signal_time from the same-session P2-1 confirm ledger.

    ANCHOR_ACTIVE if the latest T1 with t0 <= signal has not reached t1.
    Otherwise LAST_C1_* from that latest T1's C1 status.
    State and Dynamic ENTRY/fill outcome are separate fields (caller joins outcome).
    """
    prior = []
    for c in confirms:
        if str(c.get("date")) != str(date):
            continue
        if str(c.get("symbol")) != str(symbol):
            continue
        if str(c.get("session")) != str(session):
            continue
        t0 = c.get("t0")
        if t0 is None:
            continue
        if float(t0) <= float(signal_t) + 1e-12:
            prior.append(c)
    if not prior:
        return {
            "primary_state": "NO_PRIOR_T1_THIS_SESSION",
            "latest_t0": None,
            "latest_t1": None,
            "c1_status": None,
            "c1_reason": None,
            "fixed_signal_minus_t0_sec": None,
            "fixed_signal_minus_t1_sec": None,
            "has_prior_t1": False,
            "entry_during_anchor_active": False,
            "prior_c1_rejected": False,
            "c1_confirmed_before_entry": False,
        }
    latest = max(prior, key=lambda c: (float(c["t0"]), float(c["t1"] or 0.0)))
    t0 = float(latest["t0"])
    t1_raw = latest.get("t1")
    t1 = float(t1_raw) if t1_raw is not None else None
    status = str(latest.get("status") or "")
    active = bool(t1 is not None and t0 <= float(signal_t) + 1e-12 and float(signal_t) + 1e-12 < t1)
    if active:
        state = "ANCHOR_ACTIVE"
    elif status == CONFIRMED:
        state = "LAST_C1_CONFIRMED"
    elif status == REJECTED:
        state = "LAST_C1_REJECTED"
    elif status == CONFIRMATION_NOT_EVALUABLE:
        state = "LAST_C1_NOT_EVALUABLE"
    else:
        state = "LAST_SESSION_INCOMPLETE"
    confirmed_before = bool((not active) and status == CONFIRMED and t1 is not None and t1 <= float(signal_t) + 1e-12)
    rejected = bool((not active) and status == REJECTED)
    return {
        "primary_state": state,
        "latest_t0": t0,
        "latest_t1": t1,
        "c1_status": status or None,
        "c1_reason": latest.get("reason"),
        "fixed_signal_minus_t0_sec": round(float(signal_t) - t0, 6),
        "fixed_signal_minus_t1_sec": None if t1 is None else round(float(signal_t) - t1, 6),
        "has_prior_t1": True,
        "entry_during_anchor_active": active,
        "prior_c1_rejected": rejected,
        "c1_confirmed_before_entry": confirmed_before,
    }


def lookup_terminal(
    terminals: dict[tuple[str, str, Optional[float]], dict[str, Any]],
    *,
    date: str,
    symbol: str,
    t1: Optional[float],
) -> dict[str, Any]:
    if t1 is None:
        return {"entry_terminal": None, "fill_terminal": None, "canonical_terminal_outcome": None}
    key = (str(date), str(symbol), round(float(t1), 6))
    row = terminals.get(key)
    if not row:
        return {"entry_terminal": None, "fill_terminal": None, "canonical_terminal_outcome": None}
    return {
        "entry_terminal": row.get("entry_terminal"),
        "fill_terminal": row.get("fill_terminal"),
        "canonical_terminal_outcome": row.get("canonical_terminal_outcome"),
    }
