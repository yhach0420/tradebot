"""Writes only the breakout-acceptance directory."""
from __future__ import annotations

import os
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "event_time_breakout_acceptance_entry_v1"
PRIOR = (
    NATIVE / "results" / "research" / "event_time_volume_confirmed_impulse_v1",
    NATIVE / "results" / "research" / "event_time_volume_confirmed_impulse_entry_v1",
    NATIVE / "data" / "market_capture",
    NATIVE / "data" / "reference" / "daytrade_historical_v2" / "minute",
)


def write_overlap_n() -> int:
    n = 0
    ws = str(OUT.resolve())
    for item in PRIOR:
        if not item.exists():
            continue
        fs = str(item.resolve())
        if ws == fs or ws.startswith(fs + os.sep) or fs.startswith(ws + os.sep):
            n += 1
    return n
