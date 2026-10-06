"""GENERALIZATION_FAILURE_COMPONENT_RCA_V1. Coverage-corrected two-component RCA."""
from __future__ import annotations

from research.generalization_failure_component_rca_v1 import (
    ANALYSIS_ID,
    CASE_A,
    EXPECTED_VALID_N,
    LOW_SUPPORT_IDS,
    NEXT_IF_A,
    PRIMARY_COMPONENT,
    RESIDUAL_BOTTLENECK,
    ST_WINNER_ID,
)
from research.generalization_failure_component_rca_v1.analyze import decide, original_coverage_pass
from research.generalization_failure_component_rca_v1.spec import canonical_spec


def test_spec_pins_coverage_and_no_temporal_split():
    spec = canonical_spec()
    assert spec["ANALYSIS_ID"] == ANALYSIS_ID == "GENERALIZATION_FAILURE_COMPONENT_RCA_V1"
    assert spec["COVERAGE_FROM_ORIGINAL_GATE"] is True
    assert spec["COVERAGE_INFER_FROM_TRADE_N_ONLY"] is False
    assert spec["FIRST_3_BLOCK_FORBIDDEN"] is True
    assert spec["LAST_2_BLOCK_FORBIDDEN"] is True
    assert spec["TEMPORAL_SPLIT"] is False
    assert spec["L1_WIDE_SELECTION_INSTABILITY_PROVEN"] is False
    assert spec["NEW_REPLAY"] is False
    assert spec["EXPECTED_VALID_N"] == EXPECTED_VALID_N == 10


def test_coverage_uses_original_gate_not_trade_n():
    assert original_coverage_pass({"gate": "COVERAGE_FAIL", "trade_n": 100}) is False
    assert original_coverage_pass({"gate": "ECONOMIC_FAIL", "trade_n": 4}) is True
    assert original_coverage_pass({"gate": "PASS", "trade_n": 75}) is True


def test_valid_cohort_excludes_low_support_and_case_a():
    pack = decide()
    assert pack["ORIGINAL_G1G2_N"] == 13
    assert pack["LOW_SUPPORT_DIAGNOSTIC_N"] == 3
    assert pack["PRIMARY_VALID_COHORT_N"] == 10
    valid_ids = {c["candidate_id"] for c in pack["valid_cohort"]}
    low_ids = {c["candidate_id"] for c in pack["low_support"]}
    assert low_ids == set(LOW_SUPPORT_IDS)
    assert valid_ids.isdisjoint(set(LOW_SUPPORT_IDS))
    for c in pack["low_support"]:
        assert c["original_gate"] == "COVERAGE_FAIL"
        assert c["VALID_PRIMARY"] is False
        assert c["STABILITY_RAN"] is False
        assert c["STABILITY_STATUS"] == "NOT_COMPUTED"
        assert c["SELECTION_FAILURE"] is False
    winner = next(c for c in pack["valid_cohort"] if c["candidate_id"] == ST_WINNER_ID)
    assert winner["COVERAGE_PASS"] is True
    assert winner["G1G6_ALL_PASS"] is True
    assert winner["STABILITY_RAN"] is True
    assert winner["STABILITY_PASS"] is False
    assert winner["RESIDUAL_SELECTION_AFTER_CONCENTRATION_PASS"] is True
    residual_ids = [
        c["candidate_id"]
        for c in pack["all_g1g2"]
        if c.get("RESIDUAL_SELECTION_AFTER_CONCENTRATION_PASS")
    ]
    assert residual_ids == [ST_WINNER_ID]
    assert all(c["SELECTION_FAILURE"] is False for c in pack["all_g1g2"] if c["candidate_id"] != ST_WINNER_ID)
    assert pack["stability_counts"]["STABILITY_RAN_N"] == 1
    assert pack["stability_counts"]["STABILITY_NOT_RUN_N"] == 12
    assert pack["stability_counts"]["STABILITY_NOT_RUN_MISCLASSIFIED_N"] == 0
    assert pack["l1_symbol"]["classification"] == "MIXED_SYMBOL_DEPENDENCE"
    assert pack["l1_symbol"]["SYMBOL_EVALUABLE_N"] == 4
    assert pack["l1_symbol"]["SYMBOL_COLLAPSE_N"] == 3
    assert pack["l1_symbol"]["SYMBOL_SURVIVE_N"] == 1
    assert pack["l2_symbol"]["classification"] == "CONSISTENT_SYMBOL_COLLAPSE"
    assert pack["decision"]["COMPONENT_STRUCTURE"] == "SEQUENTIAL_TWO_COMPONENT_FAILURE"
    assert pack["decision"]["PRIMARY_COMPONENT"] == PRIMARY_COMPONENT
    assert pack["decision"]["RESIDUAL_BOTTLENECK"] == RESIDUAL_BOTTLENECK
    assert pack["decision"]["L1_WIDE_SELECTION_INSTABILITY_PROVEN"] is False
    assert pack["decision"]["L2_SELECTION_INSTABILITY_PROVEN"] is False
    assert pack["decision"]["VERDICT"] == CASE_A
    assert pack["decision"]["NEXT"] == NEXT_IF_A == "SELECTION_SURFACE_MECHANISM_RCA_V1"
    assert "FIRST_3_BLOCK_TOTAL_PNL" not in pack["winner"]
    assert "LAST_2_BLOCK_TOTAL_PNL" not in pack["winner"]
    assert [b["block"] for b in pack["winner"]["blocks"]] == ["B1", "B2", "B3", "B4", "B5"]
    assert pack["guards"]["NEW_REPLAY"] is False
    assert pack["guards"]["SYMBOL_FILTER_CREATED"] is False
