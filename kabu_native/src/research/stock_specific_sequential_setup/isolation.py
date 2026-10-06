"""Writes only the sequential-setup directory."""
from __future__ import annotations

import os
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "stock_specific_sequential_setup_architecture_v1"
MINUTE_REF = NATIVE / "data" / "reference" / "daytrade_historical_v2" / "minute"
PRIOR = (
    NATIVE / "results" / "research" / "symbol_setup_exit_causal_evidence_v1",
    NATIVE / "results" / "research" / "symbol_setup_exit_noise_rca_v1",
    NATIVE / "src" / "research" / "simple_tech_entry_family",
    NATIVE / "data" / "market_capture",
    MINUTE_REF,
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
