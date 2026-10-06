"""Research writes only. Parent OUT immutable. Holdout/Stress/future unread. Runtime 0/0/0."""
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
from research.post_open_execution_tax_decomposition_v1 import BURNED_HOLDOUT_DAYS, STRESS_DAYS

NATIVE = Path(__file__).resolve().parents[3]
RESEARCH_ROOT = NATIVE / "results" / "research"
OUT = RESEARCH_ROOT / "post_open_execution_tax_decomposition_v1"
CACHE = RESEARCH_ROOT / "_work" / "post_open_execution_tax_decomposition_v1"
PARENT_OUT = RESEARCH_ROOT / "post_open_causal_upside_mechanism_discovery_v1"
PARENT_CACHE = RESEARCH_ROOT / "_work" / "post_open_causal_upside_mechanism_discovery_v1"
PRIOR = (
    PARENT_OUT,
    RESEARCH_ROOT / "prior_close_recapture_sustained_mechanism_v1",
    RESEARCH_ROOT / "post_open_prior_close_recapture_full_strategy_v1",
    RESEARCH_ROOT / "passive_wait5_precommit",
)


def stress_path_touch_n(paths: list[Path]) -> int:
    n = 0
    for p in paths:
        s = str(p).replace("\\", "/")
        for d in STRESS_DAYS:
            if f"/{d}/" in s or s.endswith(f"/{d}") or f"_{d}." in s or f"day_{d}." in s:
                n += 1
        if "/20260903" in s or "/20260904" in s or "/20260907" in s:
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
    "PARENT_OUT",
    "PARENT_CACHE",
    "PRIOR",
    "RESEARCH_ROOT",
    "snapshot",
    "input_active_file_n",
    "write_overlap_n",
    "stress_path_touch_n",
    "holdout_path_touch_n",
    "set_research_priority_below_normal",
]
