"""NEW_FULL_STRATEGY_ARCHITECTURE_PRECOMMIT_V1. Freeze one architecture. No PnL."""
from __future__ import annotations

from research.new_full_strategy_architecture_precommit_v1 import (
    ANALYSIS_ID,
    BOARD_PRIMARY_ALPHA,
    CASE_A,
    CASE_B,
    FORBIDDEN_NEXT_RUNS,
    NEXT_IF_A,
    NEXT_IF_B,
    OLD_ST_RCA_CONTINUED,
    Q1_NEW_LOGIC_COMPLETION_DIRECT,
    Q2_BLOCKING_WITHOUT_THIS_RUN,
    Q3_OLD_RCA_AS_PURPOSE,
    VWAP_ENTRY_USED,
    VWAP_EXIT_USED,
    VWAP_FILTER_USED,
)
from research.new_full_strategy_architecture_precommit_v1.analyze import PNL_KEYS, build_answers, decide
from research.new_full_strategy_architecture_precommit_v1.closed import revival_flags
from research.new_full_strategy_architecture_precommit_v1.eligibility import GATES
from research.new_full_strategy_architecture_precommit_v1.publish import SHEET_ORDER
from research.new_full_strategy_architecture_precommit_v1.spec import canonical_spec, dumps_sha256


def _walk_pnl(obj) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            assert str(k) not in PNL_KEYS
            _walk_pnl(v)
    elif isinstance(obj, list):
        for v in obj:
            _walk_pnl(v)


def test_priority_gate_and_old_rca_stop():
    spec = canonical_spec()
    assert spec["ANALYSIS_ID"] == ANALYSIS_ID == "NEW_FULL_STRATEGY_ARCHITECTURE_PRECOMMIT_V1"
    assert Q1_NEW_LOGIC_COMPLETION_DIRECT is True
    assert Q2_BLOCKING_WITHOUT_THIS_RUN is True
    assert Q3_OLD_RCA_AS_PURPOSE is False
    assert OLD_ST_RCA_CONTINUED is False
    assert spec["OLD_ST_RCA_CONTINUED"] is False
    assert "SELECTION_SURFACE_EVIDENCE_GAP_V1" in spec["FORBIDDEN_NEXT_RUNS"]
    assert NEXT_IF_A not in FORBIDDEN_NEXT_RUNS
    assert NEXT_IF_B not in FORBIDDEN_NEXT_RUNS
    assert VWAP_ENTRY_USED is False
    assert VWAP_EXIT_USED is False
    assert VWAP_FILTER_USED is False
    assert BOARD_PRIMARY_ALPHA is False
    assert spec["NEW_ARCHITECTURE_ECONOMICS_RUN"] is False
    assert spec["NEW_REPLAY"] is False
    rev = revival_flags()
    assert all(v is False for v in rev.values())
    assert set(rev) == {
        "REVIVE_SIMPLE_FULL",
        "REVIVE_E4",
        "REVIVE_ST",
        "REVIVE_C1",
        "REVIVE_C4",
        "REVIVE_RECOVERY",
        "REVIVE_PARTICIPATION",
        "REVIVE_PFQ",
        "REVIVE_OR",
        "REVIVE_DYNAMIC_ANCHOR",
        "REVIVE_X9",
    }
    assert SHEET_ORDER == (
        "answers",
        "objective_alignment",
        "old_rca_stop",
        "closed_lineages",
        "design_constraints",
        "architecture_proposals",
        "novelty_audit",
        "eligibility",
        "selection",
        "frozen_strategy",
        "data_access",
        "decision",
        "safety",
    )


def test_three_complete_proposals_one_frozen_no_economics():
    pack = decide()
    props = list(pack["proposals"])
    elig = list(pack["eligibility"])
    d = pack["decision"]
    assert len(props) == 3
    ids = [p["ARCHITECTURE_ID"] for p in props]
    assert ids == [
        "CSB_MA_ONSET_X1_Z_MA_LOSS",
        "BB_WIDTH_RELEASE_X1_Z3",
        "HTF5_MA_ONLY_X1_Z3",
    ]
    by_e = {e["ARCHITECTURE_ID"]: e for e in elig}
    for p in props:
        e = by_e[p["ARCHITECTURE_ID"]]
        assert e["A1_COMPLETE_FULL_STRATEGY"] is True
        assert e["A2_CAUSAL_IMPLEMENTABLE"] is True
        assert e["A3_DATA_AVAILABLE_ON_DEV"] is True
        assert all(k in e for k in GATES)
        assert p["VWAP_ENTRY_USED"] is False
        assert p["VWAP_EXIT_USED"] is False
        assert p["BOARD_PRIMARY_ALPHA"] is False
        assert p["PLACEHOLDER"] is False
    assert by_e["BB_WIDTH_RELEASE_X1_Z3"]["PROPOSAL_ELIGIBLE"] is False
    assert by_e["BB_WIDTH_RELEASE_X1_Z3"]["A4_STRUCTURALLY_DISTINCT"] is False
    assert by_e["BB_WIDTH_RELEASE_X1_Z3"]["A8_NO_CLOSED_LINEAGE_RESCUE"] is False
    assert by_e["HTF5_MA_ONLY_X1_Z3"]["PROPOSAL_ELIGIBLE"] is False
    assert by_e["HTF5_MA_ONLY_X1_Z3"]["A4_STRUCTURALLY_DISTINCT"] is False
    assert by_e["CSB_MA_ONSET_X1_Z_MA_LOSS"]["PROPOSAL_ELIGIBLE"] is True
    assert d["ELIGIBLE_PROPOSAL_N"] == 1
    assert d["ELIGIBLE_PROPOSAL_IDS"] == ["CSB_MA_ONSET_X1_Z_MA_LOSS"]
    assert d["SELECTED_ARCHITECTURE_ID"] == "CSB_MA_ONSET_X1_Z_MA_LOSS"
    assert d["WINNER_FORCED_DESPITE_NO_ELIGIBLE"] is False
    assert d["DETERMINISTIC_SELECTION_RULE_USED"] is True
    assert d["NEW_ARCHITECTURE_ECONOMICS_RUN"] is False
    assert d["NEW_ARCHITECTURE_PNL_READ_N"] == 0
    assert d["SELECTION_EVIDENCE_GAP_RUN"] is False
    assert d["OLD_ST_RCA_CONTINUED"] is False
    frozen = pack["frozen_strategy"]
    assert frozen["ARCHITECTURE_ID"] == "CSB_MA_ONSET_X1_Z_MA_LOSS"
    assert frozen["ENTRY_STATE_MACHINE"]
    assert frozen["EXECUTION_RULE"]["EXEC_ID"] == "X1_IMMEDIATE_ASK"
    assert frozen["EXIT_STATE_MACHINE"]["EXIT_ID"] == "Z_MA_TREND_LOSS"
    assert frozen["EXIT_STATE_MACHINE"]["JOINT_WITH_ENTRY"] is True
    assert frozen["CAP"] == 5
    assert frozen["SHARES"] == 100
    assert frozen["same_symbol"] is True
    assert frozen["OCCUPANCY_BEHAVIOR"]
    assert frozen["SLOT_RELEASE"]
    assert frozen["REENTRY"] is True
    assert frozen["SESSION_CLOSE"]
    assert frozen["VWAP_ENTRY_USED"] is False
    assert frozen["VWAP_EXIT_USED"] is False
    assert frozen["BOARD_PRIMARY_ALPHA"] is False
    sha = d["FULL_STRATEGY_SPEC_SHA256"]
    assert isinstance(sha, str) and len(sha) == 64 and all(c in "0123456789abcdef" for c in sha)
    assert sha == dumps_sha256(frozen)
    assert d["VERDICT"] == CASE_A
    assert d["NEXT"] == NEXT_IF_A
    assert d["NEXT"] != CASE_B
    assert d["ARCHITECTURE_FROZEN_BEFORE_ECONOMICS"] is True
    _walk_pnl(pack["proposals"])
    _walk_pnl(frozen)
    answers = build_answers(pack)
    assert answers["1_current_priority_new_logic_completion"] is True
    assert answers["2_old_ST_RCA_stopped"] is True
    assert answers["3_selection_evidence_gap_run"] is False
    assert answers["4_proposal_N"] == 3
    assert answers["8_each_structurally_distinct"] is False
    assert answers["10_each_Coverage_plausible"] is False
    assert answers["11_eligible_proposal_N"] == 1
    assert answers["12_PnL_used_in_proposal_creation"] is False
    assert answers["13_PnL_used_in_proposal_selection"] is False
    assert answers["14_new_architecture_economics_run"] is False
    assert answers["15_VWAP_ENTRY_used"] is False
    assert answers["18_selected_architecture_ID"] == "CSB_MA_ONSET_X1_Z_MA_LOSS"
    assert answers["20_winner_forced_despite_no_eligible_architecture"] is False
    assert answers["21_ENTRY_fully_frozen"] is True
    assert answers["23_EXIT_fully_frozen"] is True
    assert answers["30_ENTRY_EXIT_jointly_defined"] is True
    assert answers["32_distinct_from_Simple_Full"] is True
    assert answers["33_distinct_from_ST"] is True
    assert answers["34_distinct_from_C1"] is True
    assert answers["35_distinct_from_C4"] is True
    assert answers["40_new_threshold_search"] is False
    assert answers["41_old_architecture_retune"] is False
    assert answers["42_Holdout_read"] is False
    assert answers["43_Stress_read"] is False
    assert answers["44_future_read"] is False
    assert answers["45_Runtime_changed"] is False
    assert answers["46_submit_cancel_live"] == "0/0/0"
    assert answers["47_TRUE_OOS"] is False
    assert answers["48_CERTIFIED"] is False
    assert answers["49_VERDICT"] == CASE_A
    assert answers["50_NEXT"] == NEXT_IF_A
    assert pack["data_access"]["BURNED_HOLDOUT_READ_N"] == 0
    assert pack["data_access"]["STRESS_READ_N"] == 0
    assert pack["data_access"]["FUTURE_DATA_N"] == 0
    assert pack["revival"]["REVIVE_ST"] is False
    assert pack["revival"]["REVIVE_C1"] is False
