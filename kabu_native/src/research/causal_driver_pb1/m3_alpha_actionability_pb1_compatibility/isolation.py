"""Writes only the diagnostic directory."""
from __future__ import annotations

import os
from pathlib import Path

from research.am_c0_indicator_exit.isolation import FORBIDDEN_WRITE_PREFIXES, TODAY
from research.causal_driver_pb1.sector_state_alpha_complete_economic.isolation import OUT as ECON_OUT
from research.causal_driver_pb1.sector_state_alpha_complete_precommit.isolation import OUT as PRECOMMIT_OUT

_ = TODAY
NATIVE = Path(__file__).resolve().parents[4]
OUT = NATIVE / "results" / "causal_driver" / "m3_alpha_actionability_pb1_compatibility_v1"
PRIOR = (
    ECON_OUT,
    PRECOMMIT_OUT,
    NATIVE / "src" / "research" / "pb1_v4_clarified_machine_correction_v4",
    NATIVE / "src" / "research" / "causal_driver_pb1" / "sector_state_alpha_complete_precommit",
    NATIVE / "runtime",
)


def write_overlap_n() -> int:
    n = 0
    ws = str(OUT.resolve())
    forbidden = [p.resolve() for p in PRIOR if p.exists()]
    for pref in FORBIDDEN_WRITE_PREFIXES:
        if pref.exists():
            forbidden.append(pref.resolve())
    for f in forbidden:
        fs = str(f)
        if ws == fs or ws.startswith(fs + os.sep) or fs.startswith(ws + os.sep):
            n += 1
    return n


def assert_write_root() -> None:
    assert str(OUT).replace("\\", "/").endswith("m3_alpha_actionability_pb1_compatibility_v1")
