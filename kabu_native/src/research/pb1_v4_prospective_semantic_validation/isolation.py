"""Research writes only this OUT. V4 and sealed holdouts are read-only."""
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
OUT = RESEARCH_ROOT / "pb1_v4_prospective_semantic_validation"
CACHE = RESEARCH_ROOT / "_work" / "pb1_v4_prospective_semantic_validation"
V4_SRC = NATIVE / "src" / "research" / "pb1_v4_clarified_machine_correction_v4"
V4_OUT = RESEARCH_ROOT / "pb1_v4_clarified_machine_correction_v4"
PREFLIGHT_OUT = RESEARCH_ROOT / "pb1_v4_prospective_semantic_validation_preflight"
FREEZE_SRC = NATIVE / "src" / "research" / "pb1_v4_clarified_machine_correction_v4_freeze_and_prospective_prep"
CLARIFIED_SPEC_SRC = NATIVE / "src" / "research" / "pb1_v4_semantic_spec_clarification"
CONFIRMATION_OUT = RESEARCH_ROOT / "old_confirmation"
FROZEN_VALIDATION_OUT = RESEARCH_ROOT / "frozen_validation"
CAPTURE_ROOT = NATIVE / "data" / "market_capture"
CONTEXT_CAPTURE_ROOT = NATIVE / "data" / "market_context_capture"
INTRADAY_ROOT = NATIVE / "data" / "intraday_1m"
PARQUET_MINUTE = NATIVE / "data" / "reference" / "daytrade_historical_v2" / "minute"
PRIOR = (
    V4_SRC,
    V4_OUT,
    FREEZE_SRC,
    CLARIFIED_SPEC_SRC,
    PREFLIGHT_OUT,
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
