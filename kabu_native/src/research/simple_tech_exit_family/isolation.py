"""Read-only Runtime/Capture snapshot. Research writes only simple_tech_exit_family paths."""
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
RESEARCH_OUT = NATIVE / "results" / "research" / "simple_tech_exit_family"
RESEARCH_CACHE = RESEARCH_OUT / "_work"
V14_OUT = RESEARCH_OUT / "v14_exit_state_path_rca"
V15_OUT = RESEARCH_OUT / "v15_be_reloss_mechanism"
V16_OUT = RESEARCH_OUT / "v16_bar_confirmed_be_reloss"
V17_OUT = RESEARCH_OUT / "v17_two_bar_be_reloss"
V18_OUT = RESEARCH_OUT / "v18_fixed180_exit"
V19_OUT = RESEARCH_OUT / "v19_exit_structure_verification"


def write_overlap_n(active_capture: str, paper_session: str) -> int:
    n = 0
    writes = [
        RESEARCH_OUT.resolve(),
        RESEARCH_CACHE.resolve(),
        V14_OUT.resolve(),
        V15_OUT.resolve(),
        V16_OUT.resolve(),
        V17_OUT.resolve(),
        V18_OUT.resolve(),
        V19_OUT.resolve(),
    ]
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
    "TODAY",
    "RESEARCH_OUT",
    "RESEARCH_CACHE",
    "V14_OUT",
    "V15_OUT",
    "V16_OUT",
    "V17_OUT",
    "V18_OUT",
    "V19_OUT",
    "snapshot",
    "input_active_file_n",
    "write_overlap_n",
    "set_research_priority_below_normal",
]
