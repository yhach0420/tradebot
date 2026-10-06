"""Writes only sector_state_symbol_transmission_precommit_v1_1."""
from __future__ import annotations

import os
from pathlib import Path

from research.am_c0_indicator_exit.isolation import (
    FORBIDDEN_WRITE_PREFIXES,
    TODAY,
    set_research_priority_below_normal,
    snapshot,
)
from research.causal_driver_pb1.sector_state_transmission_precommit.isolation import (
    DISC_V2_OUT,
    OUT as BLOCKED_V1_OUT,
    PARENT_OUT,
    V13_OUT,
)

_ = TODAY

NATIVE = Path(__file__).resolve().parents[4]
OUT = NATIVE / "results" / "causal_driver" / "sector_state_symbol_transmission_precommit_v1_1"
V4_SRC = NATIVE / "src" / "research" / "pb1_v4_clarified_machine_correction_v4"
CS_SRC = NATIVE / "src" / "research" / "pb1_v4_complete_strategy_build_and_economic_validation"
PRIOR = (
    V4_SRC,
    CS_SRC,
    PARENT_OUT,
    V13_OUT,
    DISC_V2_OUT,
    BLOCKED_V1_OUT,
    NATIVE / "results" / "causal_driver" / "sector_breadth_dispersion_precommit_v1_2",
    NATIVE / "data" / "reference" / "daytrade_historical_v2",
    NATIVE / "data" / "reference" / "jquants",
)


def write_overlap_n(active_capture: str, paper_session: str) -> int:
    n = 0
    writes = [OUT.resolve()]
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
    assert (BLOCKED_V1_OUT / "report.json").is_file()
    assert str(OUT).replace("\\", "/").endswith("sector_state_symbol_transmission_precommit_v1_1")
    assert OUT.resolve() != BLOCKED_V1_OUT.resolve()
    assert "src/results" not in str(OUT).replace("\\", "/")
