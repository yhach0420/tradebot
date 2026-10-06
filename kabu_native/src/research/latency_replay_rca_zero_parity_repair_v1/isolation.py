"""Writes only the repair-audit directory."""
from __future__ import annotations

import os
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "latency_replay_rca_zero_parity_repair_v1"
PRIOR = (
    NATIVE / "results" / "research" / "event_time_impulse_complete_strategy_v2",
    NATIVE / "results" / "research" / "latency_replay_integrity_holdtime_audit_v1",
    NATIVE / "data" / "market_capture",
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
