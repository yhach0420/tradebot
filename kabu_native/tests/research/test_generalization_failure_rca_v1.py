"""GENERALIZATION_FAILURE_RCA_V1. Existing-artifact RCA. No new economics."""
from __future__ import annotations

from research.generalization_failure_rca_v1 import (
    ANALYSIS_ID,
    CASE_A,
    CASE_B,
    EXPECTED_C1_G1G2_N,
    EXPECTED_RECOVERY_G1G2_N,
    EXPECTED_ST_G1G2_N,
    NEXT_IF_A,
    PRIMARY_E1_STRATEGY_N,
    ST_WINNER_ID,
)
from research.generalization_failure_rca_v1.analyze import decide, gate_run_state
from research.generalization_failure_rca_v1.spec import canonical_spec


def test_spec_pins_no_new_computation():
    spec = canonical_spec()
    assert spec["ANALYSIS_ID"] == ANALYSIS_ID == "GENERALIZATION_FAILURE_RCA_V1"
    assert spec["NEW_REPLAY"] is False
    assert spec["NEW_FULL_CAUSAL_RUN"] is False
    assert spec["RAW_CAPTURE_READ_N"] == 0
    assert spec["PRIMARY_E1_STRATEGY_N"] == PRIMARY_E1_STRATEGY_N == 13
    assert spec["EXPECTED_ST_G1G2_N"] == EXPECTED_ST_G1G2_N == 7
    assert spec["EXPECTED_C1_G1G2_N"] == EXPECTED_C1_G1G2_N == 4
    assert spec["EXPECTED_RECOVERY_G1G2_N"] == EXPECTED_RECOVERY_G1G2_N == 2
    assert spec["E4_PRIMARY_VOTE"] is False
    assert spec["E4_CAN_CHANGE_CASE"] is False
    assert spec["G6_FALSE_WITHOUT_RUN_IS_NOT_FAILURE"] is True


def test_g6_not_run_is_not_computed_not_fail():
    row = {
        "pnl": 98730.0,
        "PF": 2.23,
        "g_table": {
            "G1_TOTAL_PNL": True,
            "G2_PF": True,
            "G3_DAY_SIGNS": False,
            "G4_EX_BEST": False,
            "G5_PNL_PLUS_MAXDD": True,
            "G6_CAUSAL_EX_TOP1": False,
            "G6_RAN": False,
        },
        "CAUSAL_EX_TOP1": None,
        "EX_BEST": -12670.0,
        "positive_days": 4,
        "negative_days": 6,
        "MaxDD": -47000.0,
    }
    g = gate_run_state(row)
    assert g["G6_RAN"] is False
    assert g["G6_VALUE"] is False
    assert g["CAUSAL_EX_TOP1_STATUS"] == "NOT_COMPUTED"


def test_primary_cohort_and_case_a():
    pack = decide()
    assert pack["PRIMARY_E1_STRATEGY_N"] == 13
    assert pack["MANUAL_SELECTION"] is False
    assert pack["gate_counts"]["FALSE_VALUE_WITH_NOT_RUN_MISCLASSIFIED_N"] == 0
    assert pack["gate_counts"]["G6_RAN_TRUE_N"] == 9
    assert pack["gate_counts"]["G6_RAN_FALSE_N"] == 4
    c1 = [c for c in pack["primary_cohort"] if c["library"] == "C1"]
    assert len(c1) == 4
    assert all(c["G6_RAN"] is False for c in c1)
    assert all(c["CAUSAL_EX_TOP1_STATUS"] == "NOT_COMPUTED" for c in c1)
    assert all(c["CAUSAL_SYMBOL_COLLAPSE"] is None for c in c1)
    assert pack["l1_symbol"]["classification"] == "MIXED_SYMBOL_DEPENDENCE"
    assert pack["l1_symbol"]["SYMBOL_EVALUABLE_N"] == 7
    assert pack["l1_symbol"]["SYMBOL_COLLAPSE_N"] == 6
    assert pack["l1_symbol"]["SYMBOL_SURVIVE_N"] == 1
    assert pack["l2_symbol"]["classification"] == "CONSISTENT_SYMBOL_COLLAPSE"
    assert pack["l2_symbol"]["SYMBOL_EVALUABLE_N"] == 2
    assert pack["l2_symbol"]["SYMBOL_COLLAPSE_N"] == 2
    assert pack["CROSS_LINEAGE_SYMBOL_MODE_SUPPORT"] is True
    assert pack["CROSS_LINEAGE_DAY_MODE_SUPPORT"] is True
    assert pack["selection_surface"]["ST_WINNER_ID"] == ST_WINNER_ID
    assert pack["selection_surface"]["L1_SELECTION_SURFACE_INSTABILITY_SUPPORTED"] is True
    assert pack["selection_surface"]["L1_ALL_EDGE_EXPLAINED_BY_SYMBOL"] is False
    assert pack["decision"]["CASE"] == "A"
    assert pack["decision"]["VERDICT"] == CASE_A
    assert pack["decision"]["NEXT"] == NEXT_IF_A
    assert pack["decision"]["VERDICT"] != CASE_B
    assert pack["joint_episode"]["D3_DECISION_ELIGIBLE"] is False
    assert pack["guards"]["SYMBOL_FILTER_CREATED"] is False
    assert pack["guards"]["NEW_REPLAY"] is False
    assert pack["e4_secondary"]["E4_CAN_CHANGE_CASE"] is False
    assert pack["l3_control"]["L3_PRIMARY_VOTE"] is False
    assert pack["shared_top_symbol"]["ids"] == ["285A"]
    assert pack["shared_best_day"]["support"] is False


def test_same_symbol_id_is_not_the_mode():
    pack = decide()
    assert pack["CROSS_LINEAGE_SYMBOL_MODE_SUPPORT"] is True
    assert pack["specific_driver"]["SHARED_TOP_SYMBOL"]["support"] is True
    note = str(pack["specific_driver"]["NOTE"])
    assert "failure mode" in note
    assert pack["lineage_matrix"]["SELECTION_INSTABILITY"]["L2"] == "NOT_APPLICABLE"
    assert pack["lineage_matrix"]["EPISODE_CONCENTRATION"]["L1"] == "NOT_COMPUTED"
    assert pack["daily_comovement"]["INFERENTIAL_SIGNIFICANCE_CLAIMED"] is False
    assert pack["daily_comovement"]["PAIR_N"] == 0
