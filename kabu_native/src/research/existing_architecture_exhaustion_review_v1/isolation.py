"""Research-only writes. Prior family OUT/reports read-only. Stress unread. No harvest."""
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
OUT = RESEARCH_ROOT / "existing_architecture_exhaustion_review_v1"
CACHE = RESEARCH_ROOT / "_work" / "existing_architecture_exhaustion_review_v1"
REPORTS = NATIVE / "results" / "reports"
PRIOR = (
    RESEARCH_ROOT / "participation_onset_full_strategy_v1",
    RESEARCH_ROOT / "recovery_sequence_full_strategy_architecture_v1",
    RESEARCH_ROOT / "simple_full_strategy_discovery_v1",
    RESEARCH_ROOT / "e4_x2_z3_causal_concentration_recheck_v1",
    RESEARCH_ROOT / "new_entry_breakout_continuation_v1",
    RESEARCH_ROOT / "breakout_continuation_locked_holdout_alpha_v1",
    RESEARCH_ROOT / "new_entry_vwap_rejection_reclaim_v1",
    RESEARCH_ROOT / "new_entry_failed_breakdown_reclaim_v1",
    RESEARCH_ROOT / "am_entry_research_final_decision",
    RESEARCH_ROOT / "am_c0_reused_history_stress_20260828_20260902_v1",
    RESEARCH_ROOT / "simple_tech_entry_family",
    RESEARCH_ROOT / "simple_tech_redesign",
    RESEARCH_ROOT / "v1r_p1_source_forensic_recovery_v1",
    RESEARCH_ROOT / "v1r_frozen_p1_strategy_extension_through_20260902_v1",
    RESEARCH_ROOT / "realistic_price_flow_entry",
    RESEARCH_ROOT / "volume_confirmed_impulse_entry",
    RESEARCH_ROOT / "global_quote_semantic_audit",
    RESEARCH_ROOT / "dynamic_anchor_pnl_test_p2_2",
    RESEARCH_ROOT / "e1_x6_redesign_20260721_20260731",
    RESEARCH_ROOT / "e1_x6_fcrr_phase_b_entry_revision",
    RESEARCH_ROOT / "e1_x15_rpfe_incremental_entry",
    REPORTS / "phase687w27_pm_or_slot_policy_comparison",
    REPORTS / "phase534_report.json",
    REPORTS / "phase551_report.json",
)


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
        p = extra if extra.is_file() else extra
        if p.exists():
            forbidden.append(p.resolve())
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
