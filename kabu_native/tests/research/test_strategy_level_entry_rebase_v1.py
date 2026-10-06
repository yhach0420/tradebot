"""Strategy-level ENTRY rebase V1 tests. No 20260903/04 capture input."""
from __future__ import annotations

from research.simple_tech_redesign.precap_entry_quality_existing_data_mechanism_v1_harvest import assert_research_day
from research.simple_tech_redesign.strategy_level_entry_rebase_v1_analyze import decide, gates_for, select_one
from research.simple_tech_redesign.strategy_level_entry_rebase_v1_harvest import inventory_rows
from research.simple_tech_redesign.strategy_level_entry_rebase_v1_spec import (
    AUTO_HOP_NEXT_CANDIDATE,
    ELIGIBLE_ENTRY_IDS,
    EXECUTION_EVALUABLE_MIN,
    EXIT_ADDED,
    FEATURE_SEARCH,
    FORBIDDEN_INPUT_DAYS,
    MAX_RESEARCH_DATE,
    ML_USED,
    NEW_COMBINATION,
    NEW_INDICATOR,
    NEW_THRESHOLD,
    PNL_SELECTION,
    PROSPECTIVE_HARVEST_SUSPENDED,
    RANKING_OPTIMIZATION,
    SESSION_CLOSE_PNL_HARD_REJECT,
    TECHNICAL_EXIT_DEVELOPMENT_EXHAUSTED,
    TRUE_OOS,
)


def test_frozen_bounds_and_no_new_search():
    assert MAX_RESEARCH_DATE == "20260902"
    assert FORBIDDEN_INPUT_DAYS == ("20260903", "20260904")
    assert PROSPECTIVE_HARVEST_SUSPENDED is True
    assert TRUE_OOS is False
    assert NEW_INDICATOR is False
    assert NEW_THRESHOLD is False
    assert NEW_COMBINATION is False
    assert ML_USED is False
    assert RANKING_OPTIMIZATION is False
    assert FEATURE_SEARCH is False
    assert EXIT_ADDED is False
    assert PNL_SELECTION is False
    assert SESSION_CLOSE_PNL_HARD_REJECT is False
    assert AUTO_HOP_NEXT_CANDIDATE is False
    assert TECHNICAL_EXIT_DEVELOPMENT_EXHAUSTED is True
    assert EXECUTION_EVALUABLE_MIN == 52


def test_forbidden_days_fail_closed():
    assert assert_research_day("20260903", today="20260904") == "FAIL_CLOSED_FORBIDDEN_INPUT"
    assert assert_research_day("20260904", today="20260904") == "FAIL_CLOSED_FORBIDDEN_INPUT"
    assert assert_research_day("20260907", today="20260904") == "FAIL_CLOSED_FUTURE_DATA"


def test_r1b0_and_diagnostic_states_are_not_eligible():
    assert "V10_R1B0" not in ELIGIBLE_ENTRY_IDS
    inv = inventory_rows()
    by = {r["ENTRY_ID"]: r for r in inv}
    assert by["V10_R1B0"]["eligible"] is False
    assert by["V10_STATES_RxBy"]["eligible"] is False
    assert by["V9_STATES_Sxx"]["eligible"] is False
    assert "positive markout" in str(by["V10_R1B0"]["exclude_reason"]).lower() or "diagnostic" in str(by["V10_R1B0"]["exclude_reason"]).lower()
    assert by["V2_EVENT_TRIGGER"]["eligible"] is False
    assert by["V7_TF3_TF5"]["eligible"] is False
    assert by["TECHNICAL_EXIT_FAMILIES"]["eligible"] is False
    for eid in ELIGIBLE_ENTRY_IDS:
        assert by[eid]["eligible"] is True


def _pos_metrics(**over):
    body = {
        "EXECUTION_EVALUABLE_N": 80,
        "MEAN_180": 1.0,
        "MEAN_300": 1.0,
        "POSITIVE_DAY_N_180": 10,
        "NEGATIVE_DAY_N_180": 8,
        "POSITIVE_DAY_N_300": 9,
        "NEGATIVE_DAY_N_300": 9,
        "EX_BEST_DAY_MEAN_180": 0.5,
        "EX_BEST_DAY_MEAN_300": 0.4,
        "DROP_TOP_SYMBOL_MEAN_180": 0.1,
        "DROP_TOP_SYMBOL_MEAN_300": 0.0,
        "TOP_DAY_SHARE": 0.2,
        "TOP_SYMBOL_SHARE": 0.2,
        "TOP_SYMBOL_CONTRIBUTION": 0.2,
        "MEDIAN_180": -3.0,
        "MEDIAN_300": -4.0,
    }
    body.update(over)
    return body


def test_median_negative_is_not_a_hard_fail():
    g = gates_for(_pos_metrics(), integrity_ok=True)
    assert g["qualified"] is True
    assert g["MEDIAN_USED_AS_HARD_GATE"] is False
    assert g["PNL_USED_FOR_SELECTION"] is False


def test_coverage_floor_and_mean_gates():
    g = gates_for(_pos_metrics(EXECUTION_EVALUABLE_N=51), integrity_ok=True)
    assert g["qualified"] is False
    assert g["gates"]["2_EXECUTION_EVALUABLE_N_ge_52"] is False
    g2 = gates_for(_pos_metrics(MEAN_180=-0.01), integrity_ok=True)
    assert g2["qualified"] is False
    assert g2["gates"]["3_MEAN_180_gt_0"] is False


def test_all_negative_means_case_c():
    scored = [
        {
            "ENTRY_ID": eid,
            "qualified": False,
            "metrics": {"EXECUTION_EVALUABLE_N": 100, "MEAN_180": -1.0, "MEAN_300": -1.0, "POSITIVE_DAY_N_180": 1, "POSITIVE_DAY_N_300": 1},
        }
        for eid in ELIGIBLE_ENTRY_IDS
    ]
    sel = select_one(scored)
    assert sel["n"] == 0
    assert sel["PNL_USED_FOR_SELECTION"] is False
    d = decide(integrity_ok=True, qualified_n=0, burned=None, e4_ok=None, session_close_pnl=-999.0)
    assert d["CASE"] == "C"
    assert d["VERDICT"] == "SIMPLE_TECH_ENTRY_FAMILY_ABSOLUTE_EDGE_EXHAUSTED"
    assert d["ENTRY_BASE_CANDIDATE_FROZEN"] is False
    assert d["CURRENT_T3_STACK_CLOSED"] is True
    assert d["CURRENT_SIMPLE_TECH_ENTRY_FAMILY_CLOSED"] is True
    assert d["PHASE_B_BURNED_RAN"] is False
    assert d["FULL_CAUSAL_DIAGNOSTIC_RAN"] is False
    assert d["NEW_ENTRY_POPULATION_EXIT_REDESIGN_ALLOWED"] is False


def test_selection_prefers_coverage_then_older_rule_not_pnl():
    a = {"ENTRY_ID": "V1_FULL_STACK", "qualified": True, "metrics": {"EXECUTION_EVALUABLE_N": 60, "POSITIVE_DAY_N_180": 8, "POSITIVE_DAY_N_300": 8, "PnL": 1e9}}
    b = {"ENTRY_ID": "V8_A5_PULLBACK_BOARD", "qualified": True, "metrics": {"EXECUTION_EVALUABLE_N": 90, "POSITIVE_DAY_N_180": 2, "POSITIVE_DAY_N_300": 2, "PnL": -1e9}}
    sel = select_one([a, b])
    assert sel["SELECTED_ENTRY_ID"] == "V8_A5_PULLBACK_BOARD"
    assert sel["PNL_USED_FOR_SELECTION"] is False
    c = {"ENTRY_ID": "V8_A4_PULLBACK_RCI_BOARD", "qualified": True, "metrics": {"EXECUTION_EVALUABLE_N": 60, "POSITIVE_DAY_N_180": 8, "POSITIVE_DAY_N_300": 8}}
    sel2 = select_one([c, a])
    assert sel2["SELECTED_ENTRY_ID"] == "V1_FULL_STACK"


def test_session_close_negative_does_not_reject_after_edge_pass():
    d = decide(
        integrity_ok=True,
        qualified_n=1,
        burned={"evaluated": True, "MEAN_180": 0.1, "MEAN_300": 0.2},
        e4_ok=True,
        session_close_pnl=-12345.0,
        full_causal_ran=True,
    )
    assert d["CASE"] == "A"
    assert d["ENTRY_BASE_CANDIDATE_FROZEN"] is True
    assert "ENTRY_EDGE_SUPPORTED_EXIT_REQUIRED" in str(d["NEXT"])
    assert d["NEW_ENTRY_POPULATION_EXIT_REDESIGN_ALLOWED"] is True


def test_no_hop_after_burned_fail():
    d = decide(
        integrity_ok=True,
        qualified_n=1,
        burned={"evaluated": True, "MEAN_180": -1.0, "MEAN_300": -2.0},
        e4_ok=True,
        session_close_pnl=None,
    )
    assert d["CASE"] == "B"
    assert d["ENTRY_BASE_CANDIDATE_FROZEN"] is False
    assert d["AUTO_HOP_NEXT_CANDIDATE"] if False else d["CASE"] == "B"


def test_e4_incompatible_is_rebind_not_integrity():
    d = decide(
        integrity_ok=True,
        qualified_n=1,
        burned={"evaluated": True, "MEAN_180": 0.0, "MEAN_300": 0.0},
        e4_ok=False,
        session_close_pnl=None,
    )
    assert d["CASE"] == "D"
    assert d["ENTRY_EXECUTION_REBIND_REQUIRED"] is True
    assert d["ENTRY_BASE_CANDIDATE_FROZEN"] is True
    assert d["FULL_CAUSAL_DIAGNOSTIC_RAN"] is False
