"""Research writes only this OUT and CACHE. Prior S/R artifacts are read-only."""
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
OUT = RESEARCH_ROOT / "sr_a2_bracketed_structure_complete_strategy_v1"
CACHE = RESEARCH_ROOT / "_work" / "sr_a2_bracketed_structure_complete_strategy_v1"
PARENT_OUT = RESEARCH_ROOT / "support_resistance_mechanism_to_complete_strategy_v1"
NOT_STRATEGY_OUT = RESEARCH_ROOT / "support_resistance_matched_separation_not_a_strategy_v1"
MATCHED_OUT = RESEARCH_ROOT / "support_resistance_first_interaction_matched_causal_test_v1"
REBUILD_OUT = RESEARCH_ROOT / "support_resistance_face_valid_first_interaction_rebuild_v1"
FOUNDATION_OUT = RESEARCH_ROOT / "daytrade_historical_research_foundation_v2"
CONFIRMATION_OUT = RESEARCH_ROOT / "old_confirmation"
FROZEN_VALIDATION_OUT = RESEARCH_ROOT / "frozen_validation"
PRIOR = (
    PARENT_OUT,
    NOT_STRATEGY_OUT,
    MATCHED_OUT,
    REBUILD_OUT,
    RESEARCH_ROOT / "support_resistance_test_design_audit_v1",
    RESEARCH_ROOT / "multi_touch_daily_zone_1m_price_action_v1",
    FOUNDATION_OUT,
    RESEARCH_ROOT / "fixed_daytrade_universe_v1",
    RESEARCH_ROOT / "cause_first_mechanism_discovery_v1",
    RESEARCH_ROOT / "causal_path_to_complete_strategy_v1",
    CONFIRMATION_OUT,
    FROZEN_VALIDATION_OUT,
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
    "MATCHED_OUT",
    "REBUILD_OUT",
    "FOUNDATION_OUT",
    "CONFIRMATION_OUT",
    "FROZEN_VALIDATION_OUT",
    "PRIOR",
    "snapshot",
    "write_overlap_n",
    "set_research_priority_below_normal",
]
