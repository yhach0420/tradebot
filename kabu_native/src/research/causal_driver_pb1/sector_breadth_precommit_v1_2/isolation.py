"""V1.2 writes only sector_breadth_dispersion_precommit_v1_2. Does not overwrite V1, V1.1, or discovery."""
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
OUT = NATIVE / "results" / "causal_driver" / "sector_breadth_dispersion_precommit_v1_2"
CACHE = NATIVE / "results" / "causal_driver" / "_work" / "sector_breadth_dispersion_precommit_v1_2"
V11_OUT = NATIVE / "results" / "causal_driver" / "sector_breadth_dispersion_precommit_v1_1"
V1_OUT = NATIVE / "results" / "causal_driver" / "sector_breadth_dispersion_precommit_v1"
DISC_OUT = NATIVE / "results" / "causal_driver" / "sector_breadth_dispersion_discovery_v1"
LL_DISC_OUT = NATIVE / "results" / "causal_driver" / "cross_sectional_leader_laggard_discovery_v1"
LL_PRE_V11 = NATIVE / "results" / "causal_driver" / "cross_sectional_leader_laggard_precommit_v1_1"
USDJPY_CORRECTED_OUT = NATIVE / "results" / "causal_driver" / "phase2_usdjpy_contract_corrected_rerun_v1"
V4_SRC = NATIVE / "src" / "research" / "pb1_v4_clarified_machine_correction_v4"
CS_SRC = NATIVE / "src" / "research" / "pb1_v4_complete_strategy_build_and_economic_validation"
PRIOR = (
    V4_SRC,
    CS_SRC,
    V11_OUT,
    V1_OUT,
    DISC_OUT,
    LL_DISC_OUT,
    LL_PRE_V11,
    USDJPY_CORRECTED_OUT,
    NATIVE / "results" / "causal_driver" / "next_driver_selection_v1",
    NATIVE / "results" / "causal_driver" / "phase0_foundation",
    NATIVE / "results" / "causal_driver" / "phase1_usdjpy_historical_adapter",
    NATIVE / "data" / "reference" / "daytrade_historical_v2",
    NATIVE / "data" / "reference" / "jquants",
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
    assert (V11_OUT / "report.json").is_file()
    assert (DISC_OUT / "report.json").is_file()
    assert str(OUT).replace("\\", "/").endswith("sector_breadth_dispersion_precommit_v1_2")
    assert V11_OUT.resolve() not in {OUT.resolve(), CACHE.resolve()}
    assert V1_OUT.resolve() not in {OUT.resolve(), CACHE.resolve()}
    assert DISC_OUT.resolve() not in {OUT.resolve(), CACHE.resolve()}
    assert V4_SRC.resolve() not in {OUT.resolve(), CACHE.resolve()}
    assert "src/results" not in str(OUT).replace("\\", "/")
