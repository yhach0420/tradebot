"""Research-only writes. Prior OUT/reports read-only. Stress unread. No harvest."""
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
from research.or_overlay_causal_contribution_reconciliation_v1 import STRESS_DAYS

NATIVE = Path(__file__).resolve().parents[3]
RESEARCH_ROOT = NATIVE / "results" / "research"
OUT = RESEARCH_ROOT / "or_overlay_causal_contribution_reconciliation_v1"
PAPER = NATIVE / "results" / "small_paper"
W27 = NATIVE / "results" / "reports" / "phase687w27_pm_or_slot_policy_comparison"
PRIOR = (
    RESEARCH_ROOT / "existing_architecture_exhaustion_review_v1",
    RESEARCH_ROOT / "participation_onset_full_strategy_v1",
    RESEARCH_ROOT / "recovery_sequence_full_strategy_architecture_v1",
    W27,
)


def stress_path_touch_n(paths: list[Path]) -> int:
    n = 0
    for p in paths:
        s = str(p).replace("\\", "/")
        for d in STRESS_DAYS:
            if f"/{d}/" in s or s.endswith(f"/{d}"):
                n += 1
    return n


def write_overlap_n(active_capture: str, paper_session: str) -> int:
    n = 0
    writes = [OUT.resolve()]
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
    "PAPER",
    "W27",
    "snapshot",
    "input_active_file_n",
    "write_overlap_n",
    "set_research_priority_below_normal",
    "stress_path_touch_n",
]
