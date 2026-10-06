"""Research writes only this OUT and CACHE. Parent group-sequence results are read-only."""
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
OUT = RESEARCH_ROOT / "freeze_group_mechanism_definitions_v1"
CACHE = RESEARCH_ROOT / "_work" / "freeze_group_mechanism_definitions_v1"
PARENT_OUT = RESEARCH_ROOT / "behavior_group_sequence_mechanism_v1"
ATLAS_OUT = RESEARCH_ROOT / "one_minute_native_playbook_discovery_v1"
FREEZE_OUT = RESEARCH_ROOT / "fixed_daytrade_universe_v1"
FOUNDATION_OUT = RESEARCH_ROOT / "daytrade_historical_research_foundation_v2"
CAUSE_FIRST_OUT = RESEARCH_ROOT / "cause_first_mechanism_discovery_v1"
CAUSAL_PATH_OUT = RESEARCH_ROOT / "causal_path_to_complete_strategy_v1"
HIGHER_MAG_OUT = RESEARCH_ROOT / "higher_magnitude_state_discovery_v1"
EXTERNAL_OUT = RESEARCH_ROOT / "identify_minimum_missing_external_causal_information_v1"
MAPPING_OUT = RESEARCH_ROOT / "sector_symbol_driver_response_mapping_v1"
CME_OUT = RESEARCH_ROOT / "expand_causal_driver_catalog_v1" / "true_cme_nq_es_cost_and_coverage_audit_v1"
PRIOR = (
    PARENT_OUT,
    ATLAS_OUT,
    FREEZE_OUT,
    FOUNDATION_OUT,
    CAUSE_FIRST_OUT,
    CAUSAL_PATH_OUT,
    HIGHER_MAG_OUT,
    EXTERNAL_OUT,
    MAPPING_OUT,
    CME_OUT,
    RESEARCH_ROOT / "expand_causal_driver_catalog_v1",
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


__all__ = [
    "NATIVE",
    "TODAY",
    "OUT",
    "CACHE",
    "PARENT_OUT",
    "ATLAS_OUT",
    "FREEZE_OUT",
    "FOUNDATION_OUT",
    "CAUSE_FIRST_OUT",
    "CAUSAL_PATH_OUT",
    "HIGHER_MAG_OUT",
    "EXTERNAL_OUT",
    "MAPPING_OUT",
    "CME_OUT",
    "PRIOR",
    "snapshot",
    "write_overlap_n",
    "set_research_priority_below_normal",
]
