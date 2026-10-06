"""Read-only Runtime/Capture snapshot. Research writes only simple_tech_entry_family paths."""
from __future__ import annotations

import os
from pathlib import Path

from research.am_c0_indicator_exit.isolation import (
    FORBIDDEN_WRITE_PREFIXES,
    TODAY,
    advanced,
    input_active_file_n,
    set_research_priority_below_normal,
    snapshot,
)

NATIVE = Path(__file__).resolve().parents[3]
RESEARCH_OUT = NATIVE / "results" / "research" / "simple_tech_entry_family"
RESEARCH_CACHE = NATIVE / "results" / "research" / "simple_tech_entry_family" / "_work"
V1_OUT = RESEARCH_OUT / "v1"
V2_OUT = RESEARCH_OUT / "v2"
V3_OUT = RESEARCH_OUT / "v3_exit_neutral"
V4_OUT = RESEARCH_OUT / "v4_volume_quality_rca"
V4_PR_OUT = RESEARCH_OUT / "v4_persistence_rule"
V5_OUT = RESEARCH_OUT / "v5_reversal_quality_rca"
V6_OUT = RESEARCH_OUT / "v6_trend_pullback_stage_rca"
V6_PR_OUT = RESEARCH_OUT / "v6_pullback_rule"
V7_OUT = RESEARCH_OUT / "v7_timeframe_role_rca"
V8_OUT = RESEARCH_OUT / "v8_architecture_role_rca"
V9_OUT = RESEARCH_OUT / "v9_trend_context_rca"
V10_OUT = RESEARCH_OUT / "v10_rci_board_role_rca"
V11_OUT = RESEARCH_OUT / "v11_signal_execution_cost_rca"
V12_OUT = RESEARCH_OUT / "v12_entry_execution"
V13_OUT = RESEARCH_OUT / "v13_entry_execution_structure_verification"


def write_overlap_n(active_capture: str, paper_session: str) -> int:
    n = 0
    writes = [
        RESEARCH_OUT.resolve(),
        RESEARCH_CACHE.resolve(),
        V1_OUT.resolve(),
        V2_OUT.resolve(),
        V3_OUT.resolve(),
        V4_OUT.resolve(),
        V4_PR_OUT.resolve(),
        V5_OUT.resolve(),
        V6_OUT.resolve(),
        V6_PR_OUT.resolve(),
        V7_OUT.resolve(),
        V8_OUT.resolve(),
        V9_OUT.resolve(),
        V10_OUT.resolve(),
        V11_OUT.resolve(),
        V12_OUT.resolve(),
        V13_OUT.resolve(),
    ]
    forbidden = []
    if active_capture:
        forbidden.append(Path(active_capture).resolve())
    if paper_session:
        forbidden.append(Path(paper_session).resolve())
    for pref in FORBIDDEN_WRITE_PREFIXES:
        if pref.exists():
            forbidden.append(pref.resolve())
    for w in writes:
        ws = str(w)
        for f in forbidden:
            fs = str(f)
            if ws == fs or ws.startswith(fs + os.sep) or fs.startswith(ws + os.sep):
                n += 1
    return n


__all__ = [
    "TODAY",
    "RESEARCH_OUT",
    "RESEARCH_CACHE",
    "V1_OUT",
    "V2_OUT",
    "V3_OUT",
    "V4_OUT",
    "V4_PR_OUT",
    "V5_OUT",
    "V6_OUT",
    "V6_PR_OUT",
    "V7_OUT",
    "V8_OUT",
    "V9_OUT",
    "V10_OUT",
    "V11_OUT",
    "V12_OUT",
    "V13_OUT",
    "snapshot",
    "advanced",
    "input_active_file_n",
    "write_overlap_n",
    "set_research_priority_below_normal",
]
