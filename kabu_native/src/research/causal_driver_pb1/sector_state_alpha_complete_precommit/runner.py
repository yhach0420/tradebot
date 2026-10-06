"""Executable complete-strategy event rules. No prices, no PnL, no orders."""
from __future__ import annotations

from typing import Any

LAST_ENTRY_ADMISSION_CLOCK = "11:24"
FINAL_ALPHA_CLOCK = "11:25"
Q60_OFF = 0.1111111111111111


def admission_decision(*, qualification_clock: str) -> dict[str, Any]:
    clock = str(qualification_clock)[:5]
    if clock > LAST_ENTRY_ADMISSION_CLOCK:
        return {"admitted": False, "reason": "REJECT_ALPHA_OBSERVABILITY_END_BEFORE_FILL"}
    return {"admitted": True, "reason": ""}


def final_clock_actions(
    *,
    driver_available: bool,
    driver_value: float | None,
    open_positions: list[dict[str, Any]],
    fills_from_1124: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """11:25 order: final driver, Q60, then observability fail-close. No new admit."""
    events: list[dict[str, str]] = []
    positions = [dict(p) for p in open_positions] + [dict(p) for p in fills_from_1124]
    if not driver_available:
        for pos in positions:
            events.append({
                "symbol": str(pos.get("symbol") or ""),
                "event": "FAIL_CLOSE",
                "reason": "ALPHA_OBSERVABILITY_WINDOW_END",
                "exit": "next_executable_open",
            })
        return events
    value = float(driver_value if driver_value is not None else 0.0)
    for pos in positions:
        if value <= Q60_OFF:
            events.append({
                "symbol": str(pos.get("symbol") or ""),
                "event": "ALPHA_THESIS_INVALIDATION",
                "reason": "Q60_STATE_LOSS",
                "exit": "next_executable_open",
            })
        else:
            events.append({
                "symbol": str(pos.get("symbol") or ""),
                "event": "FAIL_CLOSE",
                "reason": "ALPHA_OBSERVABILITY_WINDOW_END",
                "exit": "next_executable_open",
            })
    return events
