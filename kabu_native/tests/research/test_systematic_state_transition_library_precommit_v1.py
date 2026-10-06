"""Systematic state-transition library precommit. No harvest. No 84-grid. No economics."""
from __future__ import annotations

from research.systematic_state_transition_library_precommit_v1 import (
    ANALYSIS_ID,
    CASE_A,
    CASE_B,
    DEVELOPMENT_DAYS,
    FOLD_BLOCKS,
    NEXT_IF_A,
    PFQ_RECON_FORBIDDEN,
)
from research.systematic_state_transition_library_precommit_v1.analyze import build_answers, decide
from research.systematic_state_transition_library_precommit_v1.duplicate_audit import apply_prune, audit_raw_library
from research.systematic_state_transition_library_precommit_v1.library import build_raw_library
from research.systematic_state_transition_library_precommit_v1.spec import canonical_spec
from research.systematic_state_transition_library_precommit_v1.state_registry import build_state_registry


def test_contract_bans_84_grid_pfq_economics():
    spec = canonical_spec()
    assert spec["ANALYSIS_ID"] == ANALYSIS_ID
    assert spec["PRIOR_84_GRID_EXECUTED"] is False
    assert spec["PRIOR_84_GRID_REJECTED_AS_REDUNDANT"] is True
    assert spec["STATIC_AND_GRID"] is False
    assert spec["THRESHOLD_TUNED_THIS_RUN"] is False
    assert spec["ECONOMICS_RUN"] is False
    assert spec["PFQ_REVIVAL"] is False
    assert spec["PFQ_RECON_RUN"] is False
    assert spec["PFQ_RECON_FORBIDDEN"] == PFQ_RECON_FORBIDDEN
    assert spec["SIZING"] is False
    assert spec["STRESS_OPEN"] is False
    assert spec["FUTURE_DATA"] is False
    assert spec["CANDIDATE_BACKFILL"] is False
    assert spec["MAX_RAW_CANDIDATE_N"] == 25
    assert spec["ENTRY_TEMPLATES"] == ["PERSIST_NEXT", "HANDOFF_NEXT"]
    assert spec["NEW_EXIT"] is False
    assert tuple(spec["DEVELOPMENT_DAYS"]) == DEVELOPMENT_DAYS
    assert spec["BLOCKED_OOS"] is False
    assert spec["INTERNAL_VALIDATION"] is False
    assert spec["TRUE_OOS"] is False
    assert spec["CERTIFIED"] is False


def test_five_states_raw_25_no_backfill():
    reg = build_state_registry()
    assert len(reg) == 5
    assert all(r["THRESHOLD_TUNED_THIS_RUN"] is False for r in reg)
    assert all(r["PNL_USED"] is False for r in reg)
    assert all(r["LOOKAHEAD"] is False for r in reg)
    raw = build_raw_library(reg)
    assert len(raw) == 25
    assert all(c["TEMPLATE"] in ("PERSIST_NEXT", "HANDOFF_NEXT") for c in raw)
    assert sum(1 for c in raw if c["TEMPLATE"] == "PERSIST_NEXT") == 5
    assert sum(1 for c in raw if c["TEMPLATE"] == "HANDOFF_NEXT") == 20
    dup = audit_raw_library(raw)
    final, counts = apply_prune(raw, dup)
    assert counts["BACKFILL"] is False
    assert counts["FINAL_CANDIDATE_N"] <= counts["RAW_CANDIDATE_N"] <= 25
    assert len(final) == counts["FINAL_CANDIDATE_N"]


def test_folds_and_case_a_when_library_nonempty():
    pack = decide()
    assert pack["EXISTING_ARCHITECTURE_EXHAUSTED"] is True
    assert pack["REMAINING_ELIGIBLE_ARCHITECTURE_IDS"] == []
    assert pack["PRIOR_84_GRID_EXECUTED"] is False
    assert pack["ECONOMICS_RUN"] is False
    assert pack["THRESHOLD_TUNED_THIS_RUN"] is False
    assert pack["STATIC_AND_GRID"] is False
    assert pack["CANDIDATE_BACKFILL"] is False
    assert pack["OR_ECONOMICS_OPENED"] is False
    assert pack["PFQ_REVIVAL_ALLOWED"] is False
    assert pack["folds"]["blocks"] == {k: list(v) for k, v in FOLD_BLOCKS.items()}
    assert pack["folds"]["classification"] == "REUSED_HISTORY_BLOCKED_STABILITY"
    answers = build_answers(pack)
    assert answers["2_or_economics_opened"] is False
    assert answers["4_pfq_revival_allowed"] is False
    assert answers["6_EXISTING_ARCHITECTURE_EXHAUSTED"] is True
    assert answers["10_threshold_tuned"] is False
    assert answers["12_static_AND_grid_used"] is False
    assert answers["18_candidate_backfill_performed"] is False
    assert answers["23_blocked_OOS"] is False
    assert answers["24_internal_validation"] is False
    assert answers["25_classification"] == "REUSED_HISTORY_BLOCKED_STABILITY"
    assert answers["32_burned_Holdout_read"] is False
    assert answers["33_Stress_read"] is False
    assert answers["34_future_used"] is False
    assert answers["35_Sizing_ran"] is False
    assert answers["36_economics_run"] is False
    assert answers["43_Runtime_changed"] is False
    assert answers["44_submit_cancel_live"] == "0/0/0"
    assert answers["45_TRUE_OOS"] is False
    assert answers["46_CERTIFIED"] is False
    assert pack["counts"]["RAW_CANDIDATE_N"] == 25
    if pack["counts"]["FINAL_CANDIDATE_N"] >= 1:
        assert pack["CASE_NAME"] == CASE_A
        assert pack["NEXT"] == NEXT_IF_A
        assert pack["NEXT"] != PFQ_RECON_FORBIDDEN
    else:
        assert pack["CASE_NAME"] == CASE_B
    assert answers["47_VERDICT"] == pack["VERDICT"]
    assert answers["48_NEXT"] == pack["NEXT"]
    assert len(str(answers["37_STATE_REGISTRY_SHA256"])) == 64
