"""Research writes only this OUT. Frozen V4 machine / spec / parents are read-only."""
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
OUT = RESEARCH_ROOT / "pb1_v4_implementation_integrity_and_spec_parity_audit"
CACHE = RESEARCH_ROOT / "_work" / "pb1_v4_implementation_integrity_and_spec_parity_audit"
V4_OUT = RESEARCH_ROOT / "pb1_v4_machine_implementation"
V4_CACHE = RESEARCH_ROOT / "_work" / "pb1_v4_machine_implementation"
SPEC_OUT = RESEARCH_ROOT / "pb1_v4_opening_drive_location_reaccel_spec"
SPEC_CACHE = RESEARCH_ROOT / "_work" / "pb1_v4_opening_drive_location_reaccel_spec"
RCA_OUT = RESEARCH_ROOT / "pb1_v3_2_face_failure_rca"
RCA_CACHE = RESEARCH_ROOT / "_work" / "pb1_v3_2_face_failure_rca"
V32_OUT = RESEARCH_ROOT / "pb1_v3_2_opening_drive_and_reacceleration_semantics"
V32_CACHE = RESEARCH_ROOT / "_work" / "pb1_v3_2_opening_drive_and_reacceleration_semantics"
V31_OUT = RESEARCH_ROOT / "pb1_v3_1_face_validity_fix"
V3_OUT = RESEARCH_ROOT / "pb1_playbook_redesign_v3"
V2_OUT = RESEARCH_ROOT / "pb1_opening_range_continuation_face_valid_v2"
FOUNDATION_OUT = RESEARCH_ROOT / "daytrade_historical_research_foundation_v2"
CONFIRMATION_OUT = RESEARCH_ROOT / "old_confirmation"
FROZEN_VALIDATION_OUT = RESEARCH_ROOT / "frozen_validation"
PRIOR = (
    V4_OUT,
    V4_CACHE,
    SPEC_OUT,
    SPEC_CACHE,
    RCA_OUT,
    RCA_CACHE,
    V32_OUT,
    V32_CACHE,
    V31_OUT,
    V3_OUT,
    V2_OUT,
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
