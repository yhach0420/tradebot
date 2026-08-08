"""Process one event through E1_X5 — always via process_e1_x5_event (Paper canonical).

G1 confirmation is handled inside decision_core via session.confirm_on_independent_push
when the session is an E1X5GuardSession. No duplicated ENTRY/EXIT/score path.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Optional

from small_paper.e1_x5_decision_core import E1X5EventDecision, process_e1_x5_event
from small_paper.e1_x5_g1_confirmation_guard import E1X5GuardSession


def process_e1_x5_guard_event(
    *,
    provider: Any,
    session: E1X5GuardSession,
    symbol: str,
    payload: Mapping[str, Any],
    day: Optional[str] = None,
    event_sequence: Optional[int] = None,
    event_id: str = "",
    decision_time: Optional[datetime] = None,
) -> E1X5EventDecision:
    """Canonical one-event path for BASE and all G1 variants."""
    return process_e1_x5_event(
        provider=provider,
        session=session,
        symbol=symbol,
        payload=payload,
        day=day,
        event_sequence=event_sequence,
        event_id=event_id,
        decision_time=decision_time,
    )
