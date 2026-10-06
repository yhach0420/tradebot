"""Phase 2 precommit writes only its OUT. Does not write Phase 2 outcomes or mutate V4/Phase 0/1."""
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
OUT = NATIVE / "results" / "causal_driver" / "phase2_usdjpy_standalone_lead_precommit"
CACHE = NATIVE / "results" / "causal_driver" / "_work" / "phase2_usdjpy_standalone_lead_precommit"
PHASE0_OUT = NATIVE / "results" / "causal_driver" / "phase0_foundation"
PHASE1_OUT = NATIVE / "results" / "causal_driver" / "phase1_usdjpy_historical_adapter"
UNIVERSE_MANIFEST = (
    NATIVE / "results" / "research" / "daytrade_historical_research_foundation_v2" / "research_pool_manifest.json"
)
MINUTE_REF = NATIVE / "data" / "reference" / "daytrade_historical_v2" / "minute"
FOUNDATION_REPORT = NATIVE / "results" / "research" / "daytrade_historical_research_foundation_v2" / "report.json"
V4_SRC = NATIVE / "src" / "research" / "pb1_v4_clarified_machine_correction_v4"
CS_SRC = NATIVE / "src" / "research" / "pb1_v4_complete_strategy_build_and_economic_validation"
PRIOR = (
    V4_SRC,
    CS_SRC,
    PHASE0_OUT,
    PHASE1_OUT,
    NATIVE / "results" / "research" / "usd_jpy_sector_symbol_response_v1",
    NATIVE / "results" / "research" / "expand_causal_driver_catalog_v1" / "usd_jpy_sector_symbol_response_v1",
    NATIVE / "data" / "reference" / "daytrade_historical_v2",
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
    assert (PHASE0_OUT / "report.json").is_file()
    assert (PHASE1_OUT / "report.json").is_file()
    assert "phase2_usdjpy_standalone_lead_precommit" in str(OUT).replace("\\", "/")
    assert "src/results" not in str(OUT).replace("\\", "/")
    assert V4_SRC.resolve() not in {OUT.resolve(), CACHE.resolve()}
    assert CS_SRC.resolve() not in {OUT.resolve(), CACHE.resolve()}
    assert PHASE1_OUT.resolve() not in {OUT.resolve(), CACHE.resolve()}
