"""RESEARCH_OBJECTIVE_REBASE_V1. Existing-artifact synthesis. No new economics."""
from __future__ import annotations

from research.research_objective_rebase_v1 import (
    ANALYSIS_ID,
    C4_PRIMARY_CLOSE_REASON,
    C5_CREATED,
    CASE_A,
    CASE_B,
    CASE_C,
    INDEPENDENT_INFORMATION_LINEAGE_N,
    LINEAGE_IDS,
    NEW_REPLAY,
    NEXT_IF_B,
    OVERLAY_ID,
)
from research.research_objective_rebase_v1.analyze import decide, g1_g2
from research.research_objective_rebase_v1.sources import load_sources, ranking
from research.research_objective_rebase_v1.spec import canonical_spec


def test_spec_pins_lineage_unit_and_no_new_economics():
    spec = canonical_spec()
    assert spec["ANALYSIS_ID"] == ANALYSIS_ID == "RESEARCH_OBJECTIVE_REBASE_V1"
    assert spec["INDEPENDENT_INFORMATION_LINEAGE_N"] == INDEPENDENT_INFORMATION_LINEAGE_N == 3
    assert tuple(spec["LINEAGE_IDS"]) == LINEAGE_IDS
    assert spec["OVERLAY_ID"] == OVERLAY_ID
    assert spec["C5_CREATED"] is False is C5_CREATED
    assert spec["NEW_REPLAY"] is False is NEW_REPLAY
    assert spec["CANDIDATE_COUNT_WEIGHTED_VOTE"] is False
    assert spec["ANY_CANDIDATE_FAMILY_VOTE"] is False
    assert spec["C4_PRIMARY_CLOSE_REASON"] == C4_PRIMARY_CLOSE_REASON == "ABSOLUTE_ECONOMIC_PASS_ZERO"
    assert spec["C4_PRIMARY_CLOSE_REASON_NOT"] == "I2_ZERO"


def test_c4_pin_and_overlay_not_lineage():
    pack = decide()
    c4 = pack["c4"]
    assert c4["ANALYSIS_ID"] == "C4_PORTFOLIO_CROWDING_FULL_STRATEGY_V2"
    assert c4["VERDICT"] == "C4_PORTFOLIO_CROWDING_FULL_STRATEGY_NO_ROBUST_CANDIDATE"
    assert c4["NEXT"] == "RESEARCH_OBJECTIVE_REBASE_V1"
    assert c4["FOLD_LOCAL_ELIGIBILITY_PARITY_PASS"] is True
    assert c4["CANARY_HARD_PASS"] is True
    assert c4["ARM_RERUN_N"] == 40
    assert c4["COVERAGE_PASS_N"] == 20
    assert c4["TREATMENT_ABSOLUTE_PASS_N"] == 0
    assert c4["WINNER"] is None
    assert c4["C4_PRIMARY_CLOSE_REASON"] == "ABSOLUTE_ECONOMIC_PASS_ZERO"
    assert c4["C4_COUNTED_AS_ALPHA_LINEAGE"] is False
    assert c4["OVERLAY_RESCUE"] == "OVERLAY_RESCUE_NOT_SUPPORTED"
    assert c4["I2_PASS_N"] == 0
    assert pack["architecture_space"]["C1_C4_ALL_CLOSED"] is True
    assert pack["architecture_space"]["C5_CREATED"] is False


def test_independent_lineage_n_and_pseudo_replication_guard():
    pack = decide()
    d = pack["decision"]
    assert d["ROBUST_LINEAGE_N"] == 0
    assert d["AGGREGATE_EDGE_LINEAGE_N"] == 2
    assert d["NO_AGGREGATE_EDGE_LINEAGE_N"] == 1
    assert d["GENERALIZATION_FAILURE_LINEAGE_N"] == 2
    assert d["C4_INCLUDED_IN_LINEAGE_COUNTS"] is False
    assert pack["l1"]["EVIDENCE_LEVEL"] == "E1_AGGREGATE_EDGE_OBSERVED"
    assert pack["l2"]["EVIDENCE_LEVEL"] == "E1_AGGREGATE_EDGE_OBSERVED"
    assert pack["l3"]["EVIDENCE_LEVEL"] == "E0_NO_AGGREGATE_EDGE"
    assert pack["pseudo_replication_guard"]["PASS"] is True
    assert pack["pseudo_replication_guard"]["CANDIDATE_COUNT_WEIGHTED_VOTE"] is False
    ids = {r["ANALYSIS_ID"] for r in pack["source_inventory"]}
    assert "C4_PORTFOLIO_CROWDING_FULL_STRATEGY_V2" in ids
    assert OVERLAY_ID not in d["LINEAGE_EVIDENCE_LEVELS"]


def test_case_b_generalization_rca_not_strategy():
    pack = decide()
    d = pack["decision"]
    assert d["CASE"] == "B"
    assert d["PRIMARY_DEFICIENCY"] == "AGGREGATE_EDGE_GENERALIZATION_FAILURE"
    assert d["VERDICT"] == CASE_B
    assert d["NEXT"] == NEXT_IF_B == "GENERALIZATION_FAILURE_RCA_V1"
    assert d["NEXT_CREATES_STRATEGY_IMMEDIATELY"] is False
    assert d["NEXT_IS_RCA_ONLY"] is True
    assert d["FIRST_RCA_OUTPUT"] == "WHAT DIMENSION EXPLAINS INSTABILITY?"
    assert d["VERDICT"] != CASE_A
    assert d["VERDICT"] != CASE_C
    assert pack["value_capture"]["POST_FILL_VALUE_CAPTURE_EVIDENCE"] == "NOT_ESTABLISHED"
    assert pack["flags"]["NEW_REPLAY"] is False
    assert pack["flags"]["REGIME_FILTER_PROPOSED"] is False
    assert pack["closed_lineage_guard"]["REVIVE_E4"] is False
    assert pack["closed_lineage_guard"]["REVIVE_C4"] is False


def test_l2_r2_constants_and_l3_no_g1g2():
    pack = decide()
    r2 = pack["l2"]["R2_X1_Z3"]
    assert r2["pnl"] == 341340.0
    assert r2["PF"] == 1.4202607699979068
    assert r2["positive_days"] == 2
    assert r2["negative_days"] == 8
    assert r2["EX_BEST"] == -197910.0
    assert pack["l2"]["ALL_CAUSAL_EX_TOP1_NEGATIVE"] is True
    assert pack["l3"]["G1_G2_N"] == 0
    assert pack["l3"]["coverage_n"] == 3
    e4 = pack["l1"]["e4_causal"]
    assert e4["285A_EXCLUDED_BEFORE_SIGNAL_GENERATION"] is True
    assert e4["CAUSAL_EX_285A_PNL"] == -380140.0
    assert e4["CAUSAL_EX_285A_PF"] == 0.5576115164845395


def test_c1_g1g2_not_counted_as_independent_of_st():
    sources = load_sources()
    c1 = sources["by_id"]["C1_MULTI_TIMEFRAME_ENTRY_EXIT_FULL_STRATEGY_V2"]["report"]
    n = sum(1 for r in ranking(c1) if g1_g2(r))
    assert n >= 1
    maps = {r["ARCHITECTURE_ID"]: r["LINEAGE_ID"] for r in sources["architecture_to_lineage"]}
    assert maps["C1_MULTI_TIMEFRAME_ENTRY_EXIT_FULL_STRATEGY_V2"] == "L1_TECHNICAL_PRICE_STATE"
    assert maps["SYSTEMATIC_STATE_TRANSITION_FULL_STRATEGY_V1"] == "L1_TECHNICAL_PRICE_STATE"
    assert maps["C4_PORTFOLIO_CROWDING_FULL_STRATEGY_V2"] == "O1_PORTFOLIO_CROWDING_OVERLAY"
