"""Read-only Runtime/Capture snapshot. Research writes only simple_tech_redesign paths."""
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
RESEARCH_OUT = NATIVE / "results" / "research" / "simple_tech_redesign"
RESEARCH_CACHE = RESEARCH_OUT / "_work"
V22_OUT = RESEARCH_OUT / "v22_entry_coverage_rca"
V23_OUT = RESEARCH_OUT / "v23_stale_execution_coverage_rca"
V24_OUT = RESEARCH_OUT / "v24_board_freshness_semantics_correction_replay"
V25_OUT = RESEARCH_OUT / "v25_corrected_execution_baseline_reconciliation"
V26_OUT = RESEARCH_OUT / "v26_joint_coverage_technical_exit_rca"
V27_OUT = RESEARCH_OUT / "v27_exit_state_sequence_rca"
V28_OUT = RESEARCH_OUT / "v28_fallback_3m_ema_persistence_exit"
V29_OUT = RESEARCH_OUT / "v29_terminal_failure_sequence_rca"
LIFECYCLE_OUT = RESEARCH_OUT / "exit_lifecycle_branch_rca"
BRANCH_U_OUT = RESEARCH_OUT / "branch_u_bb_lower_exit_one_shot"
BRANCH_U_CAUSAL_OUT = RESEARCH_OUT / "branch_u_full_causal_portfolio_replay"
BRANCH_U_HOLDOUT_OUT = RESEARCH_OUT / "branch_u_temporal_holdout_v1"
CAUSAL_BOARD_RCA_OUT = RESEARCH_OUT / "causal_board_information_rca"
PRE_CAP_CANDIDATE_OUT = RESEARCH_OUT / "pre_cap_entry_quality_candidate_v1"
PRE_CAP_HARD_REJECT_RCA_OUT = RESEARCH_OUT / "pre_cap_hard_reject_failure_rca"
BRANCH_U_FALSE_BREAK_RCA_OUT = RESEARCH_OUT / "branch_u_false_break_sequence_rca"
EXIT_RESIDUAL_RCA_OUT = RESEARCH_OUT / "exit_residual_loss_architecture_rca"
EXIT_COMPOSITION_SHIFT_RCA_OUT = RESEARCH_OUT / "exit_failure_composition_shift_rca"
PTF_POST_BE_RCA_OUT = RESEARCH_OUT / "ptf_post_be_state_transition_rca"
PROVEN_FAILURE_ACTIONABILITY_OUT = RESEARCH_OUT / "proven_failure_causal_actionability_gate"
ENTRY_THESIS_INVALIDATION_RCA_OUT = RESEARCH_OUT / "entry_thesis_invalidation_exit_rca"
ENTRY_ANCHORED_PULLBACK_RCA_OUT = RESEARCH_OUT / "entry_anchored_pullback_structure_exit_rca"
ENTRY_ANCHORED_FLOOR_BREAK_CANDIDATE_OUT = RESEARCH_OUT / "entry_anchored_floor_break_candidate_v1"
SLOT_RELEASE_MARGINAL_RCA_OUT = RESEARCH_OUT / "slot_release_marginal_admission_quality_rca"
PRECAP_TIMING_RCA_OUT = RESEARCH_OUT / "precap_marginal_quality_timing_confounding_rca"
PRECAP_PROSPECTIVE_V1_OUT = RESEARCH_OUT / "precap_marginal_quality_prospective_v1"
PRECAP_EXISTING_MECHANISM_V1_OUT = RESEARCH_OUT / "precap_entry_quality_existing_data_mechanism_v1"
PRECAP_EXISTING_MECHANISM_V1_R1_OUT = RESEARCH_OUT / "precap_entry_quality_existing_data_mechanism_v1_r1"
PRECAP_T3_SETUP_SEQUENCE_V1_OUT = RESEARCH_OUT / "precap_t3_setup_sequence_mechanism_v1"
POST_BE_SWING_FLOOR_EXIT_V1_OUT = RESEARCH_OUT / "post_be_causal_swing_floor_exit_v1"
BRANCH_P_VWAP_CLOSE_EXIT_V1_OUT = RESEARCH_OUT / "branch_p_vwap_close_exit_v1"
BRANCH_P_BB_STRUCTURE_EXIT_V1_OUT = RESEARCH_OUT / "branch_p_bb_structure_exit_v1"
E4_FALLBACK_NO_ADVERSE_PRICE_V1_OUT = RESEARCH_OUT / "e4_fallback_no_adverse_price_v1"
BRANCH_U_EMA_STRUCTURE_EXIT_V1_OUT = RESEARCH_OUT / "branch_u_ema_structure_exit_v1"
BRANCH_U_RESIDUAL_ACTIONABILITY_V1_OUT = RESEARCH_OUT / "branch_u_residual_actionability_v1"
BRANCH_U_RCI_THEN_VWAP_EXIT_V1_OUT = RESEARCH_OUT / "branch_u_rci_then_vwap_exit_v1"
BRANCH_U_RCI_VOLUME_CONFIRM_EXIT_V1_OUT = RESEARCH_OUT / "branch_u_rci_volume_confirm_exit_v1"
STRATEGY_LEVEL_ENTRY_REBASE_V1_OUT = RESEARCH_OUT / "strategy_level_entry_rebase_v1"


def write_overlap_n(active_capture: str, paper_session: str) -> int:
    n = 0
    writes = [
        RESEARCH_OUT.resolve(),
        RESEARCH_CACHE.resolve(),
        V22_OUT.resolve(),
        V23_OUT.resolve(),
        V24_OUT.resolve(),
        V25_OUT.resolve(),
        V26_OUT.resolve(),
        V27_OUT.resolve(),
        V28_OUT.resolve(),
        V29_OUT.resolve(),
        LIFECYCLE_OUT.resolve(),
        BRANCH_U_OUT.resolve(),
        BRANCH_U_CAUSAL_OUT.resolve(),
        BRANCH_U_HOLDOUT_OUT.resolve(),
        CAUSAL_BOARD_RCA_OUT.resolve(),
        PRE_CAP_CANDIDATE_OUT.resolve(),
        PRE_CAP_HARD_REJECT_RCA_OUT.resolve(),
        BRANCH_U_FALSE_BREAK_RCA_OUT.resolve(),
        EXIT_RESIDUAL_RCA_OUT.resolve(),
        EXIT_COMPOSITION_SHIFT_RCA_OUT.resolve(),
        PTF_POST_BE_RCA_OUT.resolve(),
        PROVEN_FAILURE_ACTIONABILITY_OUT.resolve(),
        ENTRY_THESIS_INVALIDATION_RCA_OUT.resolve(),
        ENTRY_ANCHORED_PULLBACK_RCA_OUT.resolve(),
        ENTRY_ANCHORED_FLOOR_BREAK_CANDIDATE_OUT.resolve(),
        SLOT_RELEASE_MARGINAL_RCA_OUT.resolve(),
        PRECAP_TIMING_RCA_OUT.resolve(),
        PRECAP_PROSPECTIVE_V1_OUT.resolve(),
        PRECAP_EXISTING_MECHANISM_V1_OUT.resolve(),
        PRECAP_EXISTING_MECHANISM_V1_R1_OUT.resolve(),
        PRECAP_T3_SETUP_SEQUENCE_V1_OUT.resolve(),
        POST_BE_SWING_FLOOR_EXIT_V1_OUT.resolve(),
        BRANCH_P_VWAP_CLOSE_EXIT_V1_OUT.resolve(),
        BRANCH_P_BB_STRUCTURE_EXIT_V1_OUT.resolve(),
        E4_FALLBACK_NO_ADVERSE_PRICE_V1_OUT.resolve(),
        BRANCH_U_EMA_STRUCTURE_EXIT_V1_OUT.resolve(),
        BRANCH_U_RESIDUAL_ACTIONABILITY_V1_OUT.resolve(),
        BRANCH_U_RCI_THEN_VWAP_EXIT_V1_OUT.resolve(),
        BRANCH_U_RCI_VOLUME_CONFIRM_EXIT_V1_OUT.resolve(),
        STRATEGY_LEVEL_ENTRY_REBASE_V1_OUT.resolve(),
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
    "V22_OUT",
    "V23_OUT",
    "V24_OUT",
    "V25_OUT",
    "V26_OUT",
    "V27_OUT",
    "V28_OUT",
    "V29_OUT",
    "LIFECYCLE_OUT",
    "BRANCH_U_OUT",
    "BRANCH_U_CAUSAL_OUT",
    "BRANCH_U_HOLDOUT_OUT",
    "CAUSAL_BOARD_RCA_OUT",
    "PRE_CAP_CANDIDATE_OUT",
    "PRE_CAP_HARD_REJECT_RCA_OUT",
    "BRANCH_U_FALSE_BREAK_RCA_OUT",
    "EXIT_RESIDUAL_RCA_OUT",
    "EXIT_COMPOSITION_SHIFT_RCA_OUT",
    "PTF_POST_BE_RCA_OUT",
    "PROVEN_FAILURE_ACTIONABILITY_OUT",
    "ENTRY_THESIS_INVALIDATION_RCA_OUT",
    "ENTRY_ANCHORED_PULLBACK_RCA_OUT",
    "ENTRY_ANCHORED_FLOOR_BREAK_CANDIDATE_OUT",
    "SLOT_RELEASE_MARGINAL_RCA_OUT",
    "PRECAP_TIMING_RCA_OUT",
    "PRECAP_PROSPECTIVE_V1_OUT",
    "PRECAP_EXISTING_MECHANISM_V1_OUT",
    "PRECAP_EXISTING_MECHANISM_V1_R1_OUT",
    "PRECAP_T3_SETUP_SEQUENCE_V1_OUT",
    "POST_BE_SWING_FLOOR_EXIT_V1_OUT",
    "BRANCH_P_VWAP_CLOSE_EXIT_V1_OUT",
    "BRANCH_P_BB_STRUCTURE_EXIT_V1_OUT",
    "E4_FALLBACK_NO_ADVERSE_PRICE_V1_OUT",
    "BRANCH_U_EMA_STRUCTURE_EXIT_V1_OUT",
    "BRANCH_U_RESIDUAL_ACTIONABILITY_V1_OUT",
    "BRANCH_U_RCI_THEN_VWAP_EXIT_V1_OUT",
    "BRANCH_U_RCI_VOLUME_CONFIRM_EXIT_V1_OUT",
    "STRATEGY_LEVEL_ENTRY_REBASE_V1_OUT",
    "snapshot",
    "input_active_file_n",
    "write_overlap_n",
    "set_research_priority_below_normal",
]
