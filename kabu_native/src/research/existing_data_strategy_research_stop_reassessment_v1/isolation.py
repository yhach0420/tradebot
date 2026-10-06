"""Research writes only. Prior OUT immutable. Holdout/Stress unread. Runtime 0/0/0."""
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
from research.existing_data_strategy_research_stop_reassessment_v1 import BURNED_HOLDOUT_DAYS, STRESS_DAYS

NATIVE = Path(__file__).resolve().parents[3]
RESEARCH_ROOT = NATIVE / "results" / "research"
OUT = RESEARCH_ROOT / "existing_data_strategy_research_stop_reassessment_v1"
CACHE = RESEARCH_ROOT / "_work" / "existing_data_strategy_research_stop_reassessment_v1"
PRIOR = (
    RESEARCH_ROOT / "full_causal_fdg_relative_geometry_migration_v1",
    RESEARCH_ROOT / "full_causal_strategy_architecture_from_information_object_v1",
    RESEARCH_ROOT / "discovery_information_object_expansion_decision_v1",
    RESEARCH_ROOT / "full_causal_mechanism_discovery_v1",
    RESEARCH_ROOT / "causal_mechanism_representation_expansion_v1",
    RESEARCH_ROOT / "profitable_move_mechanism_discovery_v1",
    RESEARCH_ROOT / "discovery_search_space_reassessment_v1",
    RESEARCH_ROOT / "systematic_state_transition_full_strategy_v1",
    RESEARCH_ROOT / "simple_full_strategy_discovery_v1",
    RESEARCH_ROOT / "c4_portfolio_crowding_full_strategy_v2",
    RESEARCH_ROOT / "c1_multi_timeframe_entry_exit_full_strategy_v2",
    RESEARCH_ROOT / "recovery_sequence_full_strategy_architecture_v1",
    RESEARCH_ROOT / "participation_onset_full_strategy_v1",
    RESEARCH_ROOT / "new_full_strategy_architecture_inventory_and_freeze_v3",
    RESEARCH_ROOT / "new_architecture_class_rethink_v1",
)


def stress_path_touch_n(paths: list[Path]) -> int:
    n = 0
    for p in paths:
        s = str(p).replace("\\", "/")
        for d in STRESS_DAYS:
            if f"/{d}/" in s or s.endswith(f"/{d}") or f"_{d}." in s or f"day_{d}." in s:
                n += 1
        if "/20260903" in s or "/20260904" in s or "/20260905" in s or "/20260906" in s:
            n += 1
    return n


def holdout_path_touch_n(paths: list[Path]) -> int:
    n = 0
    for p in paths:
        s = str(p).replace("\\", "/")
        for d in BURNED_HOLDOUT_DAYS:
            if f"/{d}/" in s or s.endswith(f"/{d}") or f"_{d}." in s or f"day_{d}." in s:
                n += 1
    return n


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
    "PRIOR",
    "RESEARCH_ROOT",
    "snapshot",
    "input_active_file_n",
    "write_overlap_n",
    "stress_path_touch_n",
    "holdout_path_touch_n",
    "set_research_priority_below_normal",
]
