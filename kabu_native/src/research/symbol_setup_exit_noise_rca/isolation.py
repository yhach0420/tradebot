"""Writes only the exit-noise RCA directory."""
from __future__ import annotations

import os
from pathlib import Path

from research.am_c0_indicator_exit.isolation import FORBIDDEN_WRITE_PREFIXES, TODAY

_ = TODAY
NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "symbol_setup_exit_noise_rca_v1"
PRIOR = (
    NATIVE / "results" / "research" / "symbol_setup_directional_volume_mechanism_v1",
    NATIVE / "results" / "research" / "symbol_setup_directional_volume_precommit_v1",
    NATIVE / "results" / "research" / "symbol_setup_pullback_range_break_mechanism_v1",
    NATIVE / "results" / "research" / "symbol_setup_baseline_complete_strategy_development_v1",
    NATIVE / "src" / "research" / "simple_tech_entry_family",
    NATIVE / "src" / "research" / "symbol_setup_baseline_complete_strategy_precommit" / "runner.py",
    NATIVE / "data" / "market_capture",
)


def write_overlap_n() -> int:
    n = 0
    ws = str(OUT.resolve())
    forbidden = [p.resolve() for p in PRIOR if p.exists()]
    for pref in FORBIDDEN_WRITE_PREFIXES:
        if pref.exists():
            forbidden.append(pref.resolve())
    for item in forbidden:
        fs = str(item)
        if ws == fs or ws.startswith(fs + os.sep) or fs.startswith(ws + os.sep):
            n += 1
    return n
