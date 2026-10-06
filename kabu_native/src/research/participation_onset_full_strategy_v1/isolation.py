"""Research-only writes. Prior family OUT/CACHE read-only. Stress unread. Burned holdout unread."""
from __future__ import annotations

import os
from pathlib import Path

from research.am_c0_indicator_exit.isolation import (
    FORBIDDEN_WRITE_PREFIXES,
    TODAY,
    input_active_file_n,
    set_research_priority_below_normal,
    snapshot,
)

NATIVE = Path(__file__).resolve().parents[3]
RESEARCH_ROOT = NATIVE / "results" / "research"
OUT = RESEARCH_ROOT / "participation_onset_full_strategy_v1"
CACHE = RESEARCH_ROOT / "_work" / "participation_onset_full_strategy_v1"
PRIOR = (
    RESEARCH_ROOT / "recovery_sequence_full_strategy_architecture_v1",
    RESEARCH_ROOT / "simple_full_strategy_discovery_v1",
    RESEARCH_ROOT / "e4_x2_z3_causal_concentration_recheck_v1",
    RESEARCH_ROOT / "new_entry_breakout_continuation_v1",
    RESEARCH_ROOT / "breakout_continuation_locked_holdout_alpha_v1",
    RESEARCH_ROOT / "entry_edge_measurement_decomposition_v1",
    RESEARCH_ROOT / "new_entry_vwap_rejection_reclaim_v1",
    RESEARCH_ROOT / "new_entry_failed_breakdown_reclaim_v1",
    RESEARCH_ROOT / "am_entry_research_final_decision",
    RESEARCH_ROOT / "simple_tech_entry_family",
    RESEARCH_ROOT / "dynamic_anchor_definition_freeze_p2_0",
    RESEARCH_ROOT / "dynamic_anchor_confirmation_precommit_p2_0b",
)


def write_overlap_n(active_capture: str, paper_session: str) -> int:
    n = 0
    writes = [OUT.resolve(), CACHE.resolve()]
    forbidden = []
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
    "snapshot",
    "input_active_file_n",
    "write_overlap_n",
    "set_research_priority_below_normal",
]
