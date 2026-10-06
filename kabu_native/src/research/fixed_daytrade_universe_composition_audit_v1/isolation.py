"""Research writes only. Frozen V1 / panel / Runtime / Paper / 20260914 capture immutable."""
from __future__ import annotations

import os
from pathlib import Path

from research.am_c0_indicator_exit.isolation import (
    FORBIDDEN_WRITE_PREFIXES,
    TODAY,
    set_research_priority_below_normal,
    snapshot,
)

NATIVE = Path(__file__).resolve().parents[3]
RESEARCH_ROOT = NATIVE / "results" / "research"
OUT = RESEARCH_ROOT / "fixed_daytrade_universe_composition_audit_v1"
CACHE = RESEARCH_ROOT / "_work" / "fixed_daytrade_universe_composition_audit_v1"
REF_JQUANTS = NATIVE / "data" / "reference" / "jquants"
FREEZE_OUT = RESEARCH_ROOT / "fixed_daytrade_universe_v1"
PANEL_OUT = RESEARCH_ROOT / "aligned_historical_panel_v1"
PRIOR = (
    FREEZE_OUT,
    PANEL_OUT,
    RESEARCH_ROOT / "run_20260914_day2_futures_plus_first_live_breadth_v1",
    RESEARCH_ROOT / "futures_x_stock_state_day2_confirmation_v1",
    RESEARCH_ROOT / "current_day1_information_close_v1",
)


def write_overlap_n(active_capture: str, paper_session: str) -> int:
    n = 0
    writes = [OUT.resolve(), CACHE.resolve(), REF_JQUANTS.resolve()]
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


__all__ = [
    "NATIVE",
    "TODAY",
    "OUT",
    "CACHE",
    "REF_JQUANTS",
    "FREEZE_OUT",
    "PANEL_OUT",
    "PRIOR",
    "RESEARCH_ROOT",
    "snapshot",
    "write_overlap_n",
    "set_research_priority_below_normal",
]
