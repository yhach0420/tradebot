"""Research writes only this OUT. Frozen V4 ENTRY and sealed economic holdouts stay read-only."""
from __future__ import annotations

import os
from pathlib import Path

from research.am_c0_indicator_exit.isolation import (
    FORBIDDEN_WRITE_PREFIXES,
    TODAY,
    set_research_priority_below_normal,
    snapshot,
)

_ = TODAY

NATIVE = Path(__file__).resolve().parents[3]
RESEARCH_ROOT = NATIVE / "results" / "research"
OUT = RESEARCH_ROOT / "pb1_v4_complete_strategy_build_and_economic_validation"
CACHE = RESEARCH_ROOT / "_work" / "pb1_v4_complete_strategy_build_and_economic_validation"
V4_SRC = NATIVE / "src" / "research" / "pb1_v4_clarified_machine_correction_v4"
V4_OUT = RESEARCH_ROOT / "pb1_v4_clarified_machine_correction_v4"
V4_CACHE = RESEARCH_ROOT / "_work" / "pb1_v4_clarified_machine_correction_v4"
V4_TEST = NATIVE / "tests" / "research" / "test_pb1_v4_clarified_machine_correction_v4.py"
V4_RUNNER = NATIVE / "scripts" / "run_pb1_v4_clarified_machine_correction_v4.py"
OC_OUT = RESEARCH_ROOT / "pb1_v4_frozen_old_confirmation_blind_validation"
FV_OUT = RESEARCH_ROOT / "pb1_v4_frozen_validation_blind_confirmation"
PROSPECTIVE_OUT = RESEARCH_ROOT / "pb1_v4_prospective_semantic_validation"
PRIOR = (
    V4_SRC,
    V4_OUT,
    V4_CACHE,
    V4_TEST,
    V4_RUNNER,
    OC_OUT,
    FV_OUT,
    PROSPECTIVE_OUT,
)


def write_overlap_n(active_capture: str, paper_session: str) -> int:
    n = 0
    writes = [OUT.resolve(), CACHE.resolve()]
    forbidden: list[Path] = []
    if active_capture:
        forbidden.append(Path(active_capture).resolve())
    if paper_session:
        forbidden.append(Path(paper_session).resolve())
    for pref in FORBIDDEN_WRITE_PREFIXES:
        if pref.exists():
            forbidden.append(pref.resolve())
    for extra in PRIOR:
        if extra.exists():
            forbidden.append(extra.resolve())
    for w in writes:
        ws = str(w)
        for f in forbidden:
            fs = str(f)
            if ws == fs or ws.startswith(fs + os.sep) or fs.startswith(ws + os.sep):
                n += 1
    return n
