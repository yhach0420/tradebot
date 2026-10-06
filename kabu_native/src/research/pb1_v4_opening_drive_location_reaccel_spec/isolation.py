"""Research writes only this OUT. Frozen V3.2 / RCA / parents are read-only."""
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
OUT = RESEARCH_ROOT / "pb1_v4_opening_drive_location_reaccel_spec"
CACHE = RESEARCH_ROOT / "_work" / "pb1_v4_opening_drive_location_reaccel_spec"
RCA_OUT = RESEARCH_ROOT / "pb1_v3_2_face_failure_rca"
RCA_CACHE = RESEARCH_ROOT / "_work" / "pb1_v3_2_face_failure_rca"
V32_OUT = RESEARCH_ROOT / "pb1_v3_2_opening_drive_and_reacceleration_semantics"
V32_CACHE = RESEARCH_ROOT / "_work" / "pb1_v3_2_opening_drive_and_reacceleration_semantics"
UNSEEN_OUT = RESEARCH_ROOT / "pb1_v3_2_discovery_unseen_face_verify"
V31_OUT = RESEARCH_ROOT / "pb1_v3_1_face_validity_fix"
V3_OUT = RESEARCH_ROOT / "pb1_playbook_redesign_v3"
V2_OUT = RESEARCH_ROOT / "pb1_opening_range_continuation_face_valid_v2"
V1_OUT = RESEARCH_ROOT / "pb1_opening_range_continuation_face_valid_v1"
FOUNDATION_OUT = RESEARCH_ROOT / "daytrade_historical_research_foundation_v2"
CONFIRMATION_OUT = RESEARCH_ROOT / "old_confirmation"
FROZEN_VALIDATION_OUT = RESEARCH_ROOT / "frozen_validation"
PRIOR = (
    RCA_OUT,
    RCA_CACHE,
    V32_OUT,
    V32_CACHE,
    UNSEEN_OUT,
    V31_OUT,
    V3_OUT,
    V2_OUT,
    V1_OUT,
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
