"""Playbook families P1–P5 plus PDH control. Selection by path separation, not PnL."""
from __future__ import annotations

from typing import Any

SPECS = (
    {
        "playbook_id": "P1",
        "name": "RESISTANCE_BREAK_ACCEPT",
        "thesis": "Multi-touch resistance is broken and two subsequent closes remain above the zone high.",
        "exit": "Close back below the zone low (former resistance lost).",
        "execution": "BREAKOUT_MARKET_LIKE",
    },
    {
        "playbook_id": "P2",
        "name": "RESISTANCE_BREAK_RETEST_HOLD",
        "thesis": "Historical sellers repeatedly acted in the zone; after a break, price returns and sellers cannot regain the zone.",
        "exit": "Close back below the zone low.",
        "execution": "BREAKOUT_MARKET_LIKE",
        "primary": True,
    },
    {
        "playbook_id": "P3",
        "name": "RESISTANCE_TO_SUPPORT_FLIP",
        "thesis": "Accepted break converts former resistance into support.",
        "exit": "Close back below the zone low.",
        "execution": "BREAKOUT_MARKET_LIKE",
    },
    {
        "playbook_id": "P4",
        "name": "SUPPORT_REJECTION_BOUNCE",
        "thesis": "Price enters a multi-touch support zone and rejects without a closing break.",
        "exit": "Close back below the zone low.",
        "execution": "BREAKOUT_MARKET_LIKE",
    },
    {
        "playbook_id": "P5",
        "name": "SUPPORT_FALSE_BREAK_RECLAIM",
        "thesis": "Support is broken then immediately reclaimed; sellers could not hold below the zone.",
        "exit": "Close back below the zone low.",
        "execution": "BREAKOUT_MARKET_LIKE",
    },
    {
        "playbook_id": "PDH",
        "name": "PB_PDH_BREAK",
        "thesis": "Simple previous-day-high close-through. Control, not a zone.",
        "exit": "Close back below PDH.",
        "execution": "BREAKOUT_MARKET_LIKE",
        "control": True,
    },
    {
        "playbook_id": "P2_LIMIT",
        "name": "RESISTANCE_BREAK_RETEST_PASSIVE_LIMIT",
        "thesis": "Same as P2 but resting buy limit at former resistance zone high after a causal break.",
        "exit": "Close back below the zone low.",
        "execution": "RETEST_PASSIVE_LIMIT",
        "primary_limit": True,
    },
)


def specs_by_id() -> dict[str, dict[str, Any]]:
    return {s["playbook_id"]: s for s in SPECS}
