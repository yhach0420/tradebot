"""Research-only writes. No Runtime/Capture mutation. No prior-family rewrite."""
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
OUT = RESEARCH_ROOT / "new_entry_breakout_continuation_v1"
CACHE = RESEARCH_ROOT / "_work" / "new_entry_breakout_continuation_v1"
C0_DECISION_OUT = RESEARCH_ROOT / "am_entry_research_final_decision"
SIMPLE_TECH_OUT = RESEARCH_ROOT / "simple_tech_entry_family"
STRESS_C0_OUT = RESEARCH_ROOT / "am_c0_reused_history_stress_20260828_20260902_v1"


def write_overlap_n(active_capture: str, paper_session: str) -> int:
    n = 0
    writes = [OUT.resolve(), CACHE.resolve()]
    forbidden = []
    if active_capture:
        forbidden.append(Path(active_capture).resolve())
    if paper_session:
        forbidden.append(Path(paper_session).resolve())
    for pref in FORBIDDEN_WRITE_PREFIXES:
        if pref.exists():
            forbidden.append(pref.resolve())
    for extra in (C0_DECISION_OUT, SIMPLE_TECH_OUT, STRESS_C0_OUT):
        if extra.exists():
            forbidden.append(extra.resolve())
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
    "snapshot",
    "input_active_file_n",
    "write_overlap_n",
    "set_research_priority_below_normal",
]
