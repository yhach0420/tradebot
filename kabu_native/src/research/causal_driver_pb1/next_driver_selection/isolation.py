"""Selection writes only next_driver_selection_v1. Does not mutate Phase2/V4/CS."""
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

NATIVE = Path(__file__).resolve().parents[4]
OUT = NATIVE / "results" / "causal_driver" / "next_driver_selection_v1"
CACHE = NATIVE / "results" / "causal_driver" / "_work" / "next_driver_selection_v1"
PHASE0_OUT = NATIVE / "results" / "causal_driver" / "phase0_foundation"
PHASE1_OUT = NATIVE / "results" / "causal_driver" / "phase1_usdjpy_historical_adapter"
PHASE2_OUT = NATIVE / "results" / "causal_driver" / "phase2_usdjpy_standalone_lead_discovery"
PRECOMMIT_V11_OUT = NATIVE / "results" / "causal_driver" / "phase2_usdjpy_standalone_lead_precommit_v1_1"
PRECOMMIT_V1_OUT = NATIVE / "results" / "causal_driver" / "phase2_usdjpy_standalone_lead_precommit"
MINUTE_REF = NATIVE / "data" / "reference" / "daytrade_historical_v2" / "minute"
CAPTURE = NATIVE / "data" / "market_context_capture"
JQUANTS = NATIVE / "data" / "reference" / "jquants"
V4_SRC = NATIVE / "src" / "research" / "pb1_v4_clarified_machine_correction_v4"
CS_SRC = NATIVE / "src" / "research" / "pb1_v4_complete_strategy_build_and_economic_validation"
PRIOR = (
    V4_SRC,
    CS_SRC,
    PHASE0_OUT,
    PHASE1_OUT,
    PHASE2_OUT,
    PRECOMMIT_V11_OUT,
    PRECOMMIT_V1_OUT,
    NATIVE / "data" / "reference" / "daytrade_historical_v2",
    JQUANTS,
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


def assert_write_root() -> None:
    assert (PHASE2_OUT / "report.json").is_file()
    assert (PRECOMMIT_V11_OUT / "report.json").is_file()
    assert str(OUT).replace("\\", "/").endswith("next_driver_selection_v1")
    assert PHASE2_OUT.resolve() not in {OUT.resolve(), CACHE.resolve()}
    assert V4_SRC.resolve() not in {OUT.resolve(), CACHE.resolve()}
    assert CS_SRC.resolve() not in {OUT.resolve(), CACHE.resolve()}
    assert "src/results" not in str(OUT).replace("\\", "/")
