"""Diagnostic observer. Follows frozen PATH order from the source signal index and stops at the exit decision."""
from __future__ import annotations

from typing import Any

from research.event_time_impulse_complete_strategy_v2.rules import (
    activity_lost,
    exhausted,
    participation_lost,
    ratchet,
    reconfirm,
    support_failure,
)


def observe_from_signal(book: dict[str, Any], signal_index: int) -> dict[str, Any]:
    """Replay one position the way the frozen PATH handler does. Diagnostic only."""
    times = book["t"]
    n = int(times.size)
    index = int(signal_index)
    support = float(book["pre_high"][index])
    last = float(times[index])
    ratchets = 0
    events: list[dict[str, Any]] = []
    reason = None
    i = index + 1
    while i < n:
        price = float(book["px"][i])
        event_t = float(times[i])
        before = support
        is_reconfirm = False
        decision = "continue"
        if price == price and price > 0.0:
            if reconfirm(
                bool(book["vol10"][i]),
                bool(book["vol30"][i]),
                bool(book["buy"][i]),
                float(book["classified"][i]),
                bool(book["tick"][i]),
                bool(book["break"][i]),
            ):
                is_reconfirm = True
                last = event_t
                updated = ratchet(float(book["pre_high"][i]), support)
                if updated > support:
                    support = updated
                    ratchets += 1
                    decision = "ratchet"
            if support_failure(price, support):
                reason = "BREAK_SUPPORT_FAILURE"
                decision = "exit"
            elif exhausted(
                participation_lost(float(book["classified"][i]), float(book["ask10"][i]), float(book["bid10"][i])),
                activity_lost(bool(book["vol10"][i]), bool(book["vol30"][i]), bool(book["tick"][i])),
                event_t - last,
            ):
                reason = "IMPULSE_EXHAUSTED"
                decision = "exit"
        events.append(
            {
                "event_t": event_t,
                "source_index": i,
                "px": price,
                "pre_high": float(book["pre_high"][i]),
                "reconfirm": is_reconfirm,
                "support_before": before,
                "decision": decision,
                "support_after": support,
            }
        )
        if reason is not None:
            break
        i += 1
    if reason is None:
        reason = "SESSION_FLAT"
    return {"ratchet_n": ratchets, "reason": reason, "events": events, "final_support": support}
