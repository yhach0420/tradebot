"""Fail-closed counters. Shadow code must not call these mutators."""
from __future__ import annotations

PB1_INVOCATION_N = 0
ENTRY_CREATED_N = 0
SUBMIT_N = 0
CANCEL_N = 0
LIVE_N = 0


def forbid_pb1() -> None:
    raise RuntimeError("PB1_FORBIDDEN_IN_SHADOW")


def forbid_entry() -> None:
    raise RuntimeError("ENTRY_FORBIDDEN_IN_SHADOW")


def forbid_submit() -> None:
    raise RuntimeError("SUBMIT_FORBIDDEN_IN_SHADOW")


def forbid_cancel() -> None:
    raise RuntimeError("CANCEL_FORBIDDEN_IN_SHADOW")


def forbid_live() -> None:
    raise RuntimeError("LIVE_ORDER_FORBIDDEN_IN_SHADOW")


def counters() -> dict[str, int]:
    return {
        "PB1_INVOCATION_N": PB1_INVOCATION_N,
        "ENTRY_CREATED_N": ENTRY_CREATED_N,
        "submit": SUBMIT_N,
        "cancel": CANCEL_N,
        "live": LIVE_N,
    }
