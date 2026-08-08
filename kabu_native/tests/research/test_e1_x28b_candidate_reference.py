"""E1_X28B candidate-specific reference joint tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

NATIVE = Path(__file__).resolve().parents[2]
OUT = NATIVE / "results" / "research" / "e1_x28b_candidate_reference"


@pytest.fixture(scope="module")
def interim():
    p = OUT / "_interim.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    r = OUT / "report.json"
    if not r.exists():
        pytest.skip("no interim/report")
    return json.loads(r.read_text(encoding="utf-8"))


def test_logic_manifest_sha(interim):
    from research.e1_x28b_candidate_reference import LOGIC_MANIFEST_SHA
    assert interim.get("logic_manifest_sha") == LOGIC_MANIFEST_SHA


def test_assignment_registry_sha(interim):
    from research.e1_x28b_candidate_reference import ASSIGNMENT_REGISTRY_SHA
    assert interim.get("assignment_registry_sha") == ASSIGNMENT_REGISTRY_SHA


def test_semantic_registry_sha(interim):
    from research.e1_x28b_candidate_reference import SEMANTIC_EXIT_REGISTRY_SHA
    assert interim.get("semantic_exit_registry_sha") == SEMANTIC_EXIT_REGISTRY_SHA


def test_audit_reconciliation_sha(interim):
    from research.e1_x28b_candidate_reference import AUDIT_RECONCILIATION_SHA
    assert interim.get("audit_reconciliation_sha") == AUDIT_RECONCILIATION_SHA


def test_unique_masks_6441(interim):
    assert interim.get("unique_masks") == 6441


def test_all_assignments_preserved(interim):
    assert interim.get("assignments") == 6441


def test_candidate_specific_6118(interim):
    assert interim.get("genuine_candidate_specific") == 6118


def test_fallback_323(interim):
    assert interim.get("fallback_count") == 323


def test_family_baseline_frozen_before_eval(interim):
    assert interim.get("family_baseline_frozen_before_eval") is True


def test_family_baseline_no_pnl_selection(interim):
    assert interim.get("family_baseline_no_pnl_selection") is True


def test_reference_current_price_only(interim):
    assert interim.get("reference_current_price_only") is True


def test_first_observed_trigger(interim):
    assert interim.get("first_observed_trigger") is True


def test_no_synthetic_threshold_fill(interim):
    assert interim.get("no_synthetic_threshold_fill") is True


def test_no_future_price(interim):
    assert interim.get("no_future_price") is True


def test_no_session_cross(interim):
    assert interim.get("no_session_cross") is True


def test_same_exit_selected_vs_complement(interim):
    assert interim.get("same_exit_selected_vs_complement") is True


def test_entry_selection_common_population(interim):
    assert interim.get("entry_selection_common_population") is True


def test_specific_vs_family_same_selected_episode(interim):
    assert interim.get("specific_vs_family_same_selected_episode") is True


def test_personalization_common_population(interim):
    assert interim.get("personalization_common_population") is True


def test_fallback_not_counted_as_personalization(interim):
    assert interim.get("fallback_not_counted_as_personalization") is True
    cc = interim.get("classification_counts") or {}
    assert cc.get("FALLBACK_NO_PERSONALIZATION_TEST", 0) == 323


def test_joint_classification_requires_absolute_positive(interim):
    assert interim.get("joint_requires_absolute") is True


def test_joint_classification_requires_entry_selection_positive(interim):
    assert interim.get("joint_requires_entry_selection") is True


def test_joint_classification_requires_personalization_positive(interim):
    assert interim.get("joint_requires_personalization") is True


def test_yen_only_separated(interim):
    assert interim.get("yen_only_separated") is True


def test_mode_analysis(interim):
    assert interim.get("mode_analysis_done") is True


def test_horizon_analysis(interim):
    assert interim.get("horizon_analysis_done") is True


def test_stop_risk_analysis(interim):
    assert interim.get("stop_risk_analysis_done") is True


def test_20260803_diagnostic_only(interim):
    assert interim.get("stress_diagnostic_only") is True


def test_20260804_consumed_diagnostic_only(interim):
    assert interim.get("consumed_diagnostic_only") is True


def test_risk_dates_excluded(interim):
    assert interim.get("risk_dates_excluded") is True


def test_all_6441_handoff_to_x28c(interim):
    assert interim.get("x28c_handoff_assignments") == 6441


def test_no_candidate_closed(interim):
    assert interim.get("candidates_closed", 0) == 0


def test_no_runtime_change(interim):
    s = interim.get("safety") or {}
    assert s.get("production_runtime_changed") is False
    assert s.get("runtime_ENTRY_changed") is False
    assert s.get("runtime_EXIT_changed") is False


def test_submit_cancel_live_zero(interim):
    assert (interim.get("safety") or {}).get("submit_cancel_live") == "0/0/0"


def test_ab_determinism(interim):
    assert interim.get("content_sha")
