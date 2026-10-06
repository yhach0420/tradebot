"""Research-only writes for frozen P1 V1R extension. No Runtime/Capture mutation."""
from __future__ import annotations

import os
from pathlib import Path

from research.am_c0_indicator_exit.isolation import (
    FORBIDDEN_WRITE_PREFIXES,
    TODAY,
    input_active_file_n,
    set_research_priority_below_normal,
    snapshot,
)

NATIVE = Path(__file__).resolve().parents[3]
RESEARCH_ROOT = NATIVE / "results" / "research"
OUT = RESEARCH_ROOT / "v1r_frozen_p1_strategy_extension_through_20260902_v1"
CACHE = RESEARCH_ROOT / "_work" / "v1r_frozen_p1_strategy_extension_through_20260902_v1"
PIN_DIR = CACHE / "pinned_p1_source"
P1_OUT = RESEARCH_ROOT / "current_runtime_full_capture_recalc_p1"
P03_OUT = RESEARCH_ROOT / "exact_runtime_replay_20260820_p0_3"
P04_OUT = RESEARCH_ROOT / "exact_vs_fast_replay_parity_p0_4"
P02_OUT = RESEARCH_ROOT / "runtime_ingest_sequence_hole_fix_20260820"
SIMPLE_TECH_REBASE_OUT = RESEARCH_ROOT / "simple_tech_redesign" / "strategy_level_entry_rebase_v1"


def write_overlap_n(active_capture: str, paper_session: str) -> int:
    n = 0
    writes = [OUT.resolve(), CACHE.resolve(), PIN_DIR.resolve()]
    forbidden = []
    if active_capture:
        forbidden.append(Path(active_capture).resolve())
    if paper_session:
        forbidden.append(Path(paper_session).resolve())
    for pref in FORBIDDEN_WRITE_PREFIXES:
        if pref.exists():
            forbidden.append(pref.resolve())
    for w in writes:
        ws = str(w)
        for f in forbidden:
            fs = str(f)
            if ws == fs or ws.startswith(fs + os.sep) or fs.startswith(ws + os.sep):
                n += 1
    return n


__all__ = [
    "NATIVE",
    "TODAY",
    "OUT",
    "CACHE",
    "PIN_DIR",
    "P1_OUT",
    "P03_OUT",
    "P04_OUT",
    "P02_OUT",
    "SIMPLE_TECH_REBASE_OUT",
    "snapshot",
    "input_active_file_n",
    "write_overlap_n",
    "set_research_priority_below_normal",
]
