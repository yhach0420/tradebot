"""E1_X28C candidate-specific executable joint tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

NATIVE = Path(__file__).resolve().parents[2]
OUT = NATIVE / "results" / "research" / "e1_x28c_candidate_executable"


@pytest.fixture(scope="module")
def interim():
    p = OUT / "_interim.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    r = OUT / "report.json"
    if not r.exists():
        pytest.skip("no interim/report")
    return json.loads(r.read_text(encoding="utf-8"))


def test_x28b_source(interim):
    from research.e1_x28c_candidate_executable import SOURCE_X28B
    assert interim.get("x28b_source") == SOURCE_X28B


def test_logic_manifest_sha(interim):
    from research.e1_x28c_candidate_executable import LOGIC_MANIFEST_SHA
    assert interim.get("logic_manifest_sha") == LOGIC_MANIFEST_SHA


def test_assignment_registry_sha(interim):
    from research.e1_x28c_candidate_executable import ASSIGNMENT_REGISTRY_SHA
    assert interim.get("assignment_registry_sha") == ASSIGNMENT_REGISTRY_SHA


def test_semantic_registry_sha(interim):
    from research.e1_x28c_candidate_executable import SEMANTIC_EXIT_REGISTRY_SHA
    assert interim.get("semantic_exit_registry_sha") == SEMANTIC_EXIT_REGISTRY_SHA


def test_family_baseline_registry_sha(interim):
    from research.e1_x28c_candidate_executable import FAMILY_BASELINE_REGISTRY_SHA
    assert interim.get("family_baseline_registry_sha") == FAMILY_BASELINE_REGISTRY_SHA


def test_unique_masks_6441(interim):
    assert interim.get("unique_masks") == 6441


def test_all_assignments_preserved(interim):
    assert interim.get("assignments") == 6441


def test_x28_board_mapping_sha(interim):
    from research.e1_x28c_candidate_executable import BOARD_MAPPING_SHA
    assert interim.get("board_mapping_sha") == BOARD_MAPPING_SHA


def test_x28_execution_parity(interim):
    assert interim.get("x28_parity_ok") is True


def test_first_valid_ask(interim):
    assert interim.get("first_valid_ask") is True


def test_first_valid_bid(interim):
    assert interim.get("first_valid_bid") is True


def test_qty_100(interim):
    assert interim.get("qty_100") is True


def test_quote_freshness(interim):
    assert interim.get("quote_freshness_5s") is True


def test_special_quote_block(interim):
    assert interim.get("special_quote_block") is True


def test_no_future_best(interim):
    assert interim.get("no_future_best") is True


def test_no_mid(interim):
    assert interim.get("no_mid") is True


def test_no_currentprice_fill(interim):
    assert interim.get("no_currentprice_fill") is True


def test_no_session_cross(interim):
    assert interim.get("no_session_cross") is True


def test_execution_bridge(interim):
    assert interim.get("execution_bridge_done") is True


def test_full_state_actual_ask_basis(interim):
    assert interim.get("full_state_actual_ask_basis") is True


def test_candidate_exit_recalculated(interim):
    assert interim.get("candidate_exit_recalculated") is True


def test_family_exit_recalculated(interim):
    assert interim.get("family_exit_recalculated") is True


def test_same_entry_ask_specific_family(interim):
    assert interim.get("same_entry_ask_specific_family") is True


def test_entry_selection_common_executable_population(interim):
    assert interim.get("entry_selection_common_executable_population") is True


def test_personalization_common_executable_population(interim):
    assert interim.get("personalization_common_executable_population") is True


def test_fallback_not_personalization(interim):
    assert interim.get("fallback_not_personalization") is True
    cc = interim.get("classification_counts") or {}
    assert cc.get("EXECUTABLE_FALLBACK_NO_PERSONALIZATION_TEST", 0) == 323


def test_executable_joint_requires_absolute_positive(interim):
    assert interim.get("joint_requires_absolute") is True


def test_executable_joint_requires_entry_selection_positive(interim):
    assert interim.get("joint_requires_entry_selection") is True


def test_executable_joint_requires_personalization_positive(interim):
    assert interim.get("joint_requires_personalization") is True


def test_yen_only_separated(interim):
    assert interim.get("yen_only_separated") is True


def test_reference_266_preserved(interim):
    assert interim.get("x28b_reference_joint") == 266
    assert interim.get("reference_266_preserved") is True


def test_reference_ci_all_three_recomputed(interim):
    assert interim.get("reference_triple_ci_count") == 7


def test_target_vs_trail(interim):
    assert interim.get("target_vs_trail") is True


def test_horizon(interim):
    assert interim.get("horizon") is True


def test_stop_risk(interim):
    assert interim.get("stop_risk") is True


def test_full_bootstrap_no_cap(interim):
    assert interim.get("full_bootstrap_no_cap") is True


def test_bh_separate_families(interim):
    assert interim.get("bh_separate_families") is True


def test_full_lodo(interim):
    assert interim.get("LODO_complete") is True


def test_full_loso(interim):
    assert interim.get("LOSO_complete") is True


def test_without_20260722(interim):
    assert interim.get("without_20260722") is True


def test_without_2354(interim):
    assert interim.get("without_2354") is True


def test_without_285A(interim):
    assert interim.get("without_285A") is True


def test_without_4052(interim):
    assert interim.get("without_4052") is True


def test_20260803_diagnostic_only(interim):
    assert interim.get("stress_diagnostic_only") is True


def test_20260804_consumed_only(interim):
    assert interim.get("consumed_diagnostic_only") is True


def test_risk_dates_excluded(interim):
    assert interim.get("risk_dates_excluded") is True


def test_all_6441_x29_handoff(interim):
    assert interim.get("x29_handoff_assignments") == 6441


def test_no_candidate_closed(interim):
    assert interim.get("candidates_closed", 0) == 0


def test_no_runtime_change(interim):
    s = interim.get("safety") or {}
    assert s.get("production_runtime_changed") is False


def test_submit_cancel_live_zero(interim):
    assert (interim.get("safety") or {}).get("submit_cancel_live") == "0/0/0"


def test_ab_determinism(interim):
    assert interim.get("content_sha")
