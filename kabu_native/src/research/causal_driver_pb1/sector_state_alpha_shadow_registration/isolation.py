"""Writes only sector_state_alpha_shadow_registration_v1."""
from __future__ import annotations

import os
from pathlib import Path

from research.am_c0_indicator_exit.isolation import FORBIDDEN_WRITE_PREFIXES, TODAY
from research.causal_driver_pb1.sector_state_alpha_precommit.isolation import OUT as PRECOMMIT_OUT
from research.causal_driver_pb1.sector_state_alpha_shadow.isolation import OUT as SHADOW_OUT
from research.causal_driver_pb1.sector_state_transmission.isolation import OUT as TRANSMISSION_OUT

_ = TODAY
NATIVE = Path(__file__).resolve().parents[4]
OUT = NATIVE / "results" / "causal_driver" / "sector_state_alpha_shadow_registration_v1"
PRIOR = (
    PRECOMMIT_OUT,
    SHADOW_OUT,
    TRANSMISSION_OUT,
    NATIVE / "src" / "research" / "causal_driver_pb1" / "sector_state_alpha_shadow",
    NATIVE / "runtime",
    NATIVE / "data" / "reference" / "daytrade_historical_v2",
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
    assert (SHADOW_OUT / "report.json").is_file()
    assert OUT.resolve() != SHADOW_OUT.resolve()
    assert str(OUT).replace("\\", "/").endswith("sector_state_alpha_shadow_registration_v1")
