"""Research writes only this OUT. Parent specs and frozen machines are read-only."""
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
OUT = RESEARCH_ROOT / "pb1_v4_clarified_machine_correction_v2"
CACHE = RESEARCH_ROOT / "_work" / "pb1_v4_clarified_machine_correction_v2"
CLARIFIED_SPEC_OUT = RESEARCH_ROOT / "pb1_v4_semantic_spec_clarification"
CLARIFIED_SPEC_SRC = NATIVE / "src" / "research" / "pb1_v4_semantic_spec_clarification"
READY_SPEC_OUT = RESEARCH_ROOT / "pb1_v4_opening_drive_location_reaccel_spec"
READY_SPEC_SRC = NATIVE / "src" / "research" / "pb1_v4_opening_drive_location_reaccel_spec"
CORRECTED_OUT = RESEARCH_ROOT / "pb1_v4_implementation_correction"
CORRECTED_SRC = NATIVE / "src" / "research" / "pb1_v4_implementation_correction"
LEAKED_OUT = RESEARCH_ROOT / "pb1_v4_machine_implementation"
LEAKED_SRC = NATIVE / "src" / "research" / "pb1_v4_machine_implementation"
PARENT_MACHINE_OUT = RESEARCH_ROOT / "pb1_v4_clarified_machine_implementation"
PARENT_MACHINE_SRC = NATIVE / "src" / "research" / "pb1_v4_clarified_machine_implementation"
PARENT_MACHINE_CACHE = RESEARCH_ROOT / "_work" / "pb1_v4_clarified_machine_implementation"
PARITY_RCA_OUT = RESEARCH_ROOT / "pb1_v4_clarified_machine_parity_rca"
PARITY_RCA_SRC = NATIVE / "src" / "research" / "pb1_v4_clarified_machine_parity_rca"
RCA_OUT = RESEARCH_ROOT / "pb1_v4_implementation_correction_rca"
FACE_RCA_OUT = RESEARCH_ROOT / "pb1_v3_2_face_failure_rca"
FACE_RCA_CACHE = RESEARCH_ROOT / "_work" / "pb1_v3_2_face_failure_rca"
V32_OUT = RESEARCH_ROOT / "pb1_v3_2_opening_drive_and_reacceleration_semantics"
FOUNDATION_OUT = RESEARCH_ROOT / "daytrade_historical_research_foundation_v2"
CONFIRMATION_OUT = RESEARCH_ROOT / "old_confirmation"
FROZEN_VALIDATION_OUT = RESEARCH_ROOT / "frozen_validation"
PRIOR = (
    CLARIFIED_SPEC_OUT,
    CLARIFIED_SPEC_SRC,
    READY_SPEC_OUT,
    READY_SPEC_SRC,
    CORRECTED_OUT,
    CORRECTED_SRC,
    LEAKED_OUT,
    LEAKED_SRC,
    PARENT_MACHINE_OUT,
    PARENT_MACHINE_SRC,
    PARENT_MACHINE_CACHE,
    PARITY_RCA_OUT,
    PARITY_RCA_SRC,
    RCA_OUT,
    FACE_RCA_OUT,
    FACE_RCA_CACHE,
    V32_OUT,
    FOUNDATION_OUT,
    CONFIRMATION_OUT,
    FROZEN_VALIDATION_OUT,
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
