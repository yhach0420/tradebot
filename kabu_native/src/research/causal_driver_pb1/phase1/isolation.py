"""Phase 1 writes only phase1_usdjpy_historical_adapter. Phase 0 / V4 / CS / legacy Jetta raw stay read-only."""
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
OUT = NATIVE / "results" / "causal_driver" / "phase1_usdjpy_historical_adapter"
CACHE = NATIVE / "results" / "causal_driver" / "_work" / "phase1_usdjpy_historical_adapter"
PHASE0_OUT = NATIVE / "results" / "causal_driver" / "phase0_foundation"
DESIGN_MD = NATIVE / "docs" / "architecture" / "causal_driver_pb1_system_design_v1.md"
DESIGN_MANIFEST = NATIVE / "results" / "design" / "causal_driver_pb1_system_design_v1" / "design_manifest.json"
UNIVERSE_MANIFEST = (
    NATIVE / "results" / "research" / "daytrade_historical_research_foundation_v2" / "research_pool_manifest.json"
)
V4_SRC = NATIVE / "src" / "research" / "pb1_v4_clarified_machine_correction_v4"
CS_SRC = NATIVE / "src" / "research" / "pb1_v4_complete_strategy_build_and_economic_validation"
PAPER_RUNNER = NATIVE / "src" / "small_paper" / "paper_trade_checked_runner.py"
UNIVERSE_RUNTIME = NATIVE / "src" / "universe" / "core10_dynamic40.py"
LEGACY_JETTA = (
    NATIVE
    / "results"
    / "research"
    / "_work"
    / "expand_causal_driver_catalog_v1"
    / "usd_jpy_sector_symbol_response_v1"
    / "jetta"
    / "candles_minute"
)
LEGACY_USDJPY_PACKAGE = NATIVE / "src" / "research" / "usd_jpy_sector_symbol_response_v1"
PRIOR = (
    V4_SRC,
    CS_SRC,
    PHASE0_OUT,
    LEGACY_JETTA,
    LEGACY_USDJPY_PACKAGE,
    NATIVE / "results" / "research" / "pb1_v4_clarified_machine_correction_v4",
    NATIVE / "results" / "research" / "pb1_v4_complete_strategy_build_and_economic_validation",
    NATIVE / "results" / "research" / "pb1_v4_complete_strategy_economic_confirmation1",
    NATIVE / "data" / "reference" / "daytrade_historical_v2",
    NATIVE / "results" / "research" / "expand_causal_driver_catalog_v1" / "usd_jpy_sector_symbol_response_v1",
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
    assert (NATIVE / "results" / "causal_driver" / "phase0_foundation" / "report.json").is_file()
    assert "causal_driver" in str(OUT).replace("\\", "/")
    assert "phase1_usdjpy_historical_adapter" in str(OUT).replace("\\", "/")
    assert "phase0_foundation" not in str(OUT).replace("\\", "/")
    assert str(NATIVE / "src") not in str(OUT).replace("\\", "/")
    assert V4_SRC.resolve() not in {OUT.resolve(), CACHE.resolve()}
    assert CS_SRC.resolve() not in {OUT.resolve(), CACHE.resolve()}
    assert LEGACY_JETTA.resolve() not in {OUT.resolve(), CACHE.resolve()}
    assert PHASE0_OUT.resolve() not in {OUT.resolve(), CACHE.resolve()}
