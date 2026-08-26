"""Fixed-trade × TRAIL10 state at Fixed signal. No retune."""
from __future__ import annotations

from typing import Any, Optional

PRIOR_EDGE = "TRAIL10_STATE_TRUE_WITH_PRIOR_EDGE"
STATE_FALSE = "TRAIL10_STATE_FALSE"
NOT_EVALUABLE = "TRAIL10_NOT_EVALUABLE"
NO_PRIOR = "NO_PRIOR_TRAIL10_EDGE_THIS_SESSION"


def last_eval_at_or_before(
    evals: list[dict[str, Any]],
    *,
    symbol: str,
    session: str,
    signal_t: float,
) -> Optional[dict[str, Any]]:
    last = None
    for ev in evals:
        if str(ev.get("symbol")) != str(symbol):
            continue
        if str(ev.get("session") or "") != str(session):
            continue
        g = ev.get("g")
        if g is None:
            continue
        if float(g) <= float(signal_t) + 1e-12:
            last = ev
    return last


def has_prior_edge(
    anchors: list[dict[str, Any]],
    *,
    symbol: str,
    session: str,
    signal_t: float,
) -> bool:
    for a in anchors:
        if str(a.get("symbol")) != str(symbol):
            continue
        if str(a.get("session") or "") != str(session):
            continue
        g = a.get("g")
        if g is None:
            continue
        if float(g) <= float(signal_t) + 1e-12:
            return True
    return False


def classify_fixed_trade(
    *,
    symbol: str,
    session: str,
    signal_t: float,
    anchors: list[dict[str, Any]],
    evals: list[dict[str, Any]],
) -> dict[str, Any]:
    prior = has_prior_edge(anchors, symbol=symbol, session=session, signal_t=signal_t)
    last = last_eval_at_or_before(evals, symbol=symbol, session=session, signal_t=signal_t)
    if prior:
        label = PRIOR_EDGE
    elif last is None:
        label = NO_PRIOR
    elif str(last.get("status") or "") != "EVALUABLE":
        label = NOT_EVALUABLE
    elif last.get("trail10_state") is False:
        label = STATE_FALSE
    else:
        label = NO_PRIOR
    return {
        "has_prior_trail10_edge": prior,
        "state_at_fixed_signal": label,
        "last_g": None if last is None else last.get("g"),
        "last_status": None if last is None else last.get("status"),
        "last_trail10_state": None if last is None else last.get("trail10_state"),
    }
