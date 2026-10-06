"""Read-only Runtime/Capture snapshot. Research writes only simple_tech_strategy paths."""
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
RESEARCH_OUT = NATIVE / "results" / "research" / "simple_tech_strategy"
RESEARCH_CACHE = RESEARCH_OUT / "_work"
V20_OUT = RESEARCH_OUT / "v20_frozen_portfolio_economics"
V21_OUT = RESEARCH_OUT / "v21_sizing_attribution_rca"
V22_OUT = RESEARCH_OUT / "v22_sizing_execution_capacity_rca"


def write_overlap_n(active_capture: str, paper_session: str) -> int:
    n = 0
    writes = [
        RESEARCH_OUT.resolve(),
        RESEARCH_CACHE.resolve(),
        V20_OUT.resolve(),
        V21_OUT.resolve(),
        V22_OUT.resolve(),
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
    "V20_OUT",
    "V21_OUT",
    "V22_OUT",
    "snapshot",
    "input_active_file_n",
    "write_overlap_n",
    "set_research_priority_below_normal",
]
