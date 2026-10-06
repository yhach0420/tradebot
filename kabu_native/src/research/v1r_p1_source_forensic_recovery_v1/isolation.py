"""Research-only writes for P1 source forensic recovery. No Runtime/main-src mutation."""
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
GIT_ROOT = NATIVE.parent
RESEARCH_ROOT = NATIVE / "results" / "research"
OUT = RESEARCH_ROOT / "v1r_p1_source_forensic_recovery_v1"
CACHE = RESEARCH_ROOT / "_work" / "v1r_p1_source_forensic_recovery_v1"
RECOVERED = OUT / "recovered_source"
P1_OUT = RESEARCH_ROOT / "current_runtime_full_capture_recalc_p1"
PRIOR_EXT_OUT = RESEARCH_ROOT / "v1r_frozen_p1_strategy_extension_through_20260902_v1"


def write_overlap_n(active_capture: str, paper_session: str) -> int:
    n = 0
    writes = [OUT.resolve(), CACHE.resolve(), RECOVERED.resolve()]
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
    "GIT_ROOT",
    "TODAY",
    "OUT",
    "CACHE",
    "RECOVERED",
    "P1_OUT",
    "PRIOR_EXT_OUT",
    "snapshot",
    "input_active_file_n",
    "write_overlap_n",
    "set_research_priority_below_normal",
]
