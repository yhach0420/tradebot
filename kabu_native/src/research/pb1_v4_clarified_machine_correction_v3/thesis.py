"""THESIS_READY and THESIS_LOST. No 1m required to create thesis."""
from __future__ import annotations

from typing import Any


def thesis_ready(*, why: bool, active: bool, location: bool, lost: bool) -> bool:
    return bool(why and active and location and not lost)


def hidden_1m_snapshot(
    *,
    symbol: str,
    date: str,
    direction: str,
    opening_drive_id: str | None,
    location_id: str | None,
    thesis_id: str | None,
    thesis_ready_flag: bool,
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "date": date,
        "stock": symbol,
        "direction": direction,
        "opening_drive_id": opening_drive_id,
        "location_id": location_id,
        "thesis_id": thesis_id,
        "thesis_ready": bool(thesis_ready_flag),
        "one_m_hidden": True,
    }
