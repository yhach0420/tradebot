"""E4 fallback no-adverse-price V1 tests. No 20260903/04 capture input."""
from __future__ import annotations

from research.simple_tech_redesign.e4_fallback_no_adverse_price_v1_analyze import decide
from research.simple_tech_redesign.e4_fallback_no_adverse_price_v1_harvest import apply_treatment, recover_e4_fallback_source
from research.simple_tech_redesign.e4_fallback_no_adverse_price_v1_spec import (
    AND_OR_CLOSED_EXIT,
    AUTO_HOP_P_RCI,
    AUTO_HOP_P_TREND,
    CANDIDATE_ID,
    CLOSED_DO_NOT_REENTER,
    CONTROL_EXECUTION,
    FORBIDDEN_INPUT_DAYS,
    MAX_RESEARCH_DATE,
    NARRATIVE_CORRECTION,
    P_PRIMITIVE_CLOSEOUT,
    PROSPECTIVE_HARVEST_SUSPENDED,
    RCI_FAIL_HIT_RATE,
    RCI_KEEP_HIT_RATE,
    TICK_TOLERANCE,
    TREATMENT_EXECUTION,
    TREND_FAIL_HIT_RATE,
    TREND_KEEP_HIT_RATE,
    TRUE_OOS,
)
from research.simple_tech_redesign.precap_entry_quality_existing_data_mechanism_v1_harvest import assert_research_day


def test_frozen_bounds_and_closeout():
    assert MAX_RESEARCH_DATE == "20260902"
    assert FORBIDDEN_INPUT_DAYS == ("20260903", "20260904")
    assert PROSPECTIVE_HARVEST_SUSPENDED is True
    assert TRUE_OOS is False
    assert AUTO_HOP_P_TREND is False
    assert AUTO_HOP_P_RCI is False
    assert AND_OR_CLOSED_EXIT is False
    assert TICK_TOLERANCE == 0
    assert CANDIDATE_ID == "E4_THEN_ASK_CROSS_W5_NO_ADVERSE_PRICE_V1"
    assert CONTROL_EXECUTION == "E4_THEN_ASK_CROSS_W5"
    assert TREATMENT_EXECUTION == "E4_THEN_ASK_CROSS_W5_NO_ADVERSE_PRICE_V1"
    assert P_PRIMITIVE_CLOSEOUT["P_VWAP_CLOSE_LOSS_1M"] == "CLOSED_ACTUAL_CAUSAL_FAILED"
    assert P_PRIMITIVE_CLOSEOUT["P_BB_STRUCTURE_LOSS_3M"] == "CLOSED_ACTUAL_CAUSAL_FAILED"
    assert P_PRIMITIVE_CLOSEOUT["P_TREND_LOST_1M"] == "NOT_WORTH_ACTUAL_CAUSAL_TEST"
    assert P_PRIMITIVE_CLOSEOUT["P_RCI_ROLLOVER_3M"] == "NOT_WORTH_ACTUAL_CAUSAL_TEST"
    assert "BRANCH_P_VWAP" in CLOSED_DO_NOT_REENTER
    assert "BRANCH_P_BB" in CLOSED_DO_NOT_REENTER
    assert "PTF_DIRECT_DELTA was positive" in NARRATIVE_CORRECTION
    assert abs(TREND_FAIL_HIT_RATE - 0.868421052631579) < 1e-12
    assert abs(TREND_KEEP_HIT_RATE - 0.7980769230769231) < 1e-12
    assert abs(RCI_FAIL_HIT_RATE - 1.0) < 1e-12
    assert abs(RCI_KEEP_HIT_RATE - 0.9711538461538461) < 1e-12


def test_forbidden_days_fail_closed():
    assert assert_research_day("20260903", today="20260904") == "FAIL_CLOSED_FORBIDDEN_INPUT"
    assert assert_research_day("20260904", today="20260904") == "FAIL_CLOSED_FORBIDDEN_INPUT"
    assert assert_research_day("20260907", today="20260904") == "FAIL_CLOSED_FUTURE_DATA"


def test_exact_control_source_recovered():
    pack = recover_e4_fallback_source()
    assert pack["ok"] is True, pack
    assert pack["SOURCE_FUNCTION"] == "simulate_e4_only / simulate_ask_fallback"
    assert "improve_1tick_limit" in pack["EXACT_E4_LIMIT_PRICE"]
    assert "inside_lim" in pack["EXACT_E4_LIMIT_PRICE"]
    assert "CANONICAL_FRESHNESS_SEC" in pack["EXACT_ASK1_FRESHNESS"]
    assert "t_fb" in pack["EXACT_5S_ELIGIBILITY"]
    assert pack["TICK_TOLERANCE"] == 0
    assert pack["E4_WAIT_BUDGET_SEC"] == 5.0


def test_treatment_zero_tick_no_chase():
    leak: dict = {}
    base = {
        "e4_filled": False,
        "fill_role": "ADDED",
        "actual_filled": True,
        "e4": {"limit_price": 1000.0, "collapsed_to_bid": False},
        "ask_fallback": {"eligible": True, "filled": True, "ask": 1000.0},
        "control_exit": {"exit_t": 1.0, "pnl_yen_100": 10.0},
        "fill_t": 1.0,
        "fill_price": 1000.0,
    }
    acc = apply_treatment(base, leak)
    assert acc["exec_class"] == "W5_ACCEPTED_NO_ADVERSE"
    assert acc["actual_filled"] is True
    rej = apply_treatment({**base, "ask_fallback": {"eligible": True, "filled": True, "ask": 1001.0}}, leak)
    assert rej["exec_class"] == "W5_REJECTED_ADVERSE_PRICE"
    assert rej["actual_filled"] is False
    assert rej["control_exit"] is None
    one_tick = apply_treatment({**base, "ask_fallback": {"eligible": True, "filled": True, "ask": 1000.0 + 1.0}}, leak)
    assert one_tick["exec_class"] == "W5_REJECTED_ADVERSE_PRICE"


def _base(**over):
    body = {
        "identity": {"ok": True, "control_sot_ok": True, "treatment_sot_ok": True, "leftover_ok": True},
        "control": {"total_pnl": 1000.0, "PF": 1.2, "max_drawdown": -20000.0, "fill_n": 84},
        "treatment": {"total_pnl": 2000.0, "PF": 1.5, "max_drawdown": -15000.0, "fill_n": 50},
        "CORE_ONLY_CAUSAL_FILL_N": 40,
        "TREATMENT_FILL_N": 50,
        "TREATMENT_ADDED_N": 10,
        "COVERAGE_COLLAPSED": False,
        "TOTAL_CAUSAL_DELTA": 1000.0,
        "concentration": {"single_day_contribution_gt_50pct": False, "single_symbol_contribution_gt_50pct": False},
    }
    body.update(over)
    return body


def test_integrity_failed_is_case_e():
    d = decide(_base(), _base(), leak_ok=False, harvested_ok=True, pred_ok=True)
    assert d["CASE"] == "E"
    assert d["VERDICT"] == "SIMPLE_TECH_E4_FALLBACK_NO_ADVERSE_PRICE_INTEGRITY_FAILED"
    assert d["CANDIDATE_FROZEN"] is False


def test_coverage_collapsed_with_econ_is_case_c():
    d = decide(
        _base(COVERAGE_COLLAPSED=True, TREATMENT_ADDED_N=0, TREATMENT_FILL_N=40, CORE_ONLY_CAUSAL_FILL_N=40),
        _base(TOTAL_CAUSAL_DELTA=10.0),
        leak_ok=True,
        harvested_ok=True,
        pred_ok=True,
    )
    assert d["CASE"] == "C"
    assert d["VERDICT"] == "SIMPLE_TECH_E4_FALLBACK_NO_ADVERSE_PRICE_COVERAGE_COLLAPSED"
    assert d["CANDIDATE_FROZEN"] is False


def test_economics_failed_is_case_b():
    d = decide(_base(treatment={"total_pnl": 100.0, "PF": 0.8, "max_drawdown": -25000.0, "fill_n": 50}), _base(), leak_ok=True, harvested_ok=True, pred_ok=True)
    assert d["CASE"] == "B"
    assert d["VERDICT"] == "SIMPLE_TECH_E4_FALLBACK_NO_ADVERSE_PRICE_ECONOMICS_FAILED"


def test_case_a_and_burned_reverse_is_d():
    d = decide(_base(), _base(TOTAL_CAUSAL_DELTA=10.0, treatment={"total_pnl": 2010.0, "PF": 1.5, "max_drawdown": -14000.0}), leak_ok=True, harvested_ok=True, pred_ok=True)
    assert d["CASE"] == "A"
    assert d["CANDIDATE_FROZEN"] is True
    d2 = decide(_base(), _base(TOTAL_CAUSAL_DELTA=-10.0, treatment={"total_pnl": 990.0, "PF": 1.1, "max_drawdown": -14000.0}), leak_ok=True, harvested_ok=True, pred_ok=True)
    assert d2["CASE"] == "D"
    assert d2["VERDICT"] == "SIMPLE_TECH_E4_FALLBACK_NO_ADVERSE_PRICE_BURNED_FAILED"
    assert d2["CANDIDATE_FROZEN"] is False


def test_coverage_collapsed_precommit_definition():
    collapsed_zero_added = _base(TREATMENT_ADDED_N=0, TREATMENT_FILL_N=50, CORE_ONLY_CAUSAL_FILL_N=40, COVERAGE_COLLAPSED=True)
    assert collapsed_zero_added["COVERAGE_COLLAPSED"] is True
    collapsed_le_core = _base(TREATMENT_ADDED_N=2, TREATMENT_FILL_N=40, CORE_ONLY_CAUSAL_FILL_N=40, COVERAGE_COLLAPSED=True)
    assert collapsed_le_core["COVERAGE_COLLAPSED"] is True
