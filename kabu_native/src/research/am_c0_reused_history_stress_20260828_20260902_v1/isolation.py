"""Research-only writes. No Runtime/Capture mutation. No C0 artifact rewrite."""
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
OUT = RESEARCH_ROOT / "am_c0_reused_history_stress_20260828_20260902_v1"
CACHE = RESEARCH_ROOT / "_work" / "am_c0_reused_history_stress_20260828_20260902_v1"
C0_DECISION_OUT = RESEARCH_ROOT / "am_entry_research_final_decision"
EXIT_DECISION_OUT = RESEARCH_ROOT / "am_exit_research_final_decision"
C14_PATH = RESEARCH_ROOT / "v1r_exit_v2_prospective_activation" / "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14.json"
REGIME_LABELED = RESEARCH_ROOT / "_work_cache" / "am_entry_temporal_regime_information" / "labeled_am_regime.json"
LABELED_X14 = RESEARCH_ROOT / "_work_cache" / "am_entry_information_expansion" / "labeled_am_x14.json"


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
    "C0_DECISION_OUT",
    "EXIT_DECISION_OUT",
    "C14_PATH",
    "REGIME_LABELED",
    "LABELED_X14",
    "snapshot",
    "input_active_file_n",
    "write_overlap_n",
    "set_research_priority_below_normal",
]
