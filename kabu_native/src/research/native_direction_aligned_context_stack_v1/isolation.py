"""Research writes only this OUT and CACHE. Prior artifacts are read-only."""
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
OUT = RESEARCH_ROOT / "native_direction_aligned_context_stack_v1"
CACHE = RESEARCH_ROOT / "_work" / "native_direction_aligned_context_stack_v1"
PARENT_OUT = RESEARCH_ROOT / "r14_trend_incrementality_rca_v1"
STACK_OUT = RESEARCH_ROOT / "native_causal_context_stack_discovery_v1"
FOUNDATION_OUT = RESEARCH_ROOT / "daytrade_historical_research_foundation_v2"
CONFIRMATION_OUT = RESEARCH_ROOT / "old_confirmation"
FROZEN_VALIDATION_OUT = RESEARCH_ROOT / "frozen_validation"
PRIOR = (
    PARENT_OUT,
    STACK_OUT,
    RESEARCH_ROOT / "native_participation_x_sr_context_discovery_v1",
    RESEARCH_ROOT / "support_resistance_mechanism_to_complete_strategy_v1",
    RESEARCH_ROOT / "sr_a2_bracketed_structure_complete_strategy_v1",
    RESEARCH_ROOT / "support_resistance_first_interaction_matched_causal_test_v1",
    RESEARCH_ROOT / "support_resistance_face_valid_first_interaction_rebuild_v1",
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
