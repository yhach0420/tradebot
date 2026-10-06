"""Branch P VWAP Close EXIT V1 tests. No 20260903/04 capture input."""
from __future__ import annotations

from research.simple_tech_redesign.branch_p_vwap_close_exit_v1_analyze import decide
from research.simple_tech_redesign.branch_p_vwap_close_exit_v1_harvest import recover_lifecycle_vwap_predicate
from research.simple_tech_redesign.branch_p_vwap_close_exit_v1_spec import (
    AUTO_HOP_P_BB,
    AUTO_HOP_P_RCI,
    AUTO_HOP_P_TREND,
    CANDIDATE_ID,
    CLOSED_DO_NOT_REENTER,
    FORBIDDEN_INPUT_DAYS,
    LIFECYCLE_PRIMITIVE,
    LIFECYCLE_VWAP_DAY_AGREE,
    LIFECYCLE_VWAP_DAY_DISAGREE,
    LIFECYCLE_VWAP_FAIL_HIT_RATE,
    LIFECYCLE_VWAP_KEEP_HIT_RATE,
    MAX_RESEARCH_DATE,
    PROSPECTIVE_HARVEST_SUSPENDED,
    SWING_AND_OR_VWAP,
    TRUE_OOS,
)
from research.simple_tech_redesign.exit_lifecycle_spec import EXIT_POLICY_CREATED
from research.simple_tech_redesign.precap_entry_quality_existing_data_mechanism_v1_harvest import assert_research_day


def test_frozen_bounds_and_closed_families():
    assert MAX_RESEARCH_DATE == "20260902"
    assert FORBIDDEN_INPUT_DAYS == ("20260903", "20260904")
    assert PROSPECTIVE_HARVEST_SUSPENDED is True
    assert TRUE_OOS is False
    assert AUTO_HOP_P_BB is False
    assert AUTO_HOP_P_RCI is False
    assert AUTO_HOP_P_TREND is False
    assert SWING_AND_OR_VWAP is False
    assert CANDIDATE_ID == "POST_BE_VWAP_CLOSE_LOSS_V1"
    assert LIFECYCLE_PRIMITIVE == "P_VWAP_CLOSE_LOSS_1M"
    assert EXIT_POLICY_CREATED is False
    assert "POST_BE_SWING_FLOOR" in CLOSED_DO_NOT_REENTER
    assert "BRANCH_U_BB" in CLOSED_DO_NOT_REENTER
    assert "BRANCH_P_SECOND_BE_CROSSING" in CLOSED_DO_NOT_REENTER
    assert abs(LIFECYCLE_VWAP_FAIL_HIT_RATE - 0.5526315789473685) < 1e-12
    assert abs(LIFECYCLE_VWAP_KEEP_HIT_RATE - 0.47115384615384615) < 1e-12
    assert LIFECYCLE_VWAP_DAY_AGREE == 7
    assert LIFECYCLE_VWAP_DAY_DISAGREE == 1


def test_forbidden_days_fail_closed():
    assert assert_research_day("20260903", today="20260904") == "FAIL_CLOSED_FORBIDDEN_INPUT"
    assert assert_research_day("20260904", today="20260904") == "FAIL_CLOSED_FORBIDDEN_INPUT"
    assert assert_research_day("20260907", today="20260904") == "FAIL_CLOSED_FUTURE_DATA"


def test_exact_pred_vwap_recovered_from_lifecycle_source():
    pack = recover_lifecycle_vwap_predicate()
    assert pack["ok"] is True
    assert pack["SOURCE_FUNCTION"] == "eval_lifecycle.pred_vwap"
    assert "bool(cl < vw)" in pack["EXACT_PREDICATE_TEXT"]
    assert '_arr_at(ind, "close", i)' in pack["EXACT_PREDICATE_TEXT"]
    assert '_arr_at(ind, "vwap", i)' in pack["EXACT_PREDICATE_TEXT"]
    pred = pack["pred"]
    class _Arr(dict):
        pass
    close = __import__("numpy").asarray([100.0, 99.0])
    vwap = __import__("numpy").asarray([99.5, 99.5])
    ind = {"close": close, "vwap": vwap}
    assert pred(ind, 0) is False
    assert pred(ind, 1) is True


def _base(**over):
    body = {
        "identity": {
            "ok": True,
            "control_sot_ok": True,
            "leftover_ok": True,
            "be_flag_mismatch_n": 0,
            "tf1_fail_n": 0,
            "predicate_drift_n": 0,
        },
        "decomp_ok": True,
        "PTF_DIRECT_DELTA": 1000.0,
        "PROVEN_FAILURE_DIRECT_DELTA": 1500.0,
        "PROTECTED_GOOD_DIP_DIRECT_DELTA": 10.0,
        "TOTAL_CAUSAL_DELTA": 2000.0,
        "control": {"PF": 1.2, "max_drawdown": -20000.0, "fill_n": 84},
        "treatment": {"PF": 1.5, "max_drawdown": -15000.0, "fill_n": 90},
        "concentration": {"single_day_contribution_gt_50pct": False, "single_symbol_contribution_gt_50pct": False},
    }
    body.update(over)
    return body


def test_integrity_failed_is_case_e():
    d = decide(_base(), _base(), leak_ok=False, harvested_ok=True, pred_ok=True)
    assert d["CASE"] == "E"
    assert d["VERDICT"] == "SIMPLE_TECH_BRANCH_P_VWAP_INTEGRITY_FAILED"


def test_pred_restore_fail_is_case_e():
    d = decide(_base(), _base(), leak_ok=True, harvested_ok=True, pred_ok=False)
    assert d["CASE"] == "E"


def test_ptf_not_improved_is_case_d():
    d = decide(_base(PTF_DIRECT_DELTA=0.0, PROVEN_FAILURE_DIRECT_DELTA=100.0), _base(), leak_ok=True, harvested_ok=True, pred_ok=True)
    assert d["CASE"] == "D"


def test_winner_harm_is_case_b():
    d = decide(_base(PROTECTED_GOOD_DIP_DIRECT_DELTA=-1.0), _base(), leak_ok=True, harvested_ok=True, pred_ok=True)
    assert d["CASE"] == "B"
    assert d["FAMILY_CLOSED"] is True


def test_portfolio_fail_is_case_c():
    d = decide(_base(TOTAL_CAUSAL_DELTA=-1.0), _base(), leak_ok=True, harvested_ok=True, pred_ok=True)
    assert d["CASE"] == "C"


def test_case_a_and_burned_reverse_is_c():
    d = decide(
        _base(),
        _base(PTF_DIRECT_DELTA=10.0, PROVEN_FAILURE_DIRECT_DELTA=10.0, TOTAL_CAUSAL_DELTA=1.0, PROTECTED_GOOD_DIP_DIRECT_DELTA=1.0),
        leak_ok=True,
        harvested_ok=True,
        pred_ok=True,
    )
    assert d["CASE"] == "A"
    d2 = decide(
        _base(),
        _base(PTF_DIRECT_DELTA=-10.0, PROVEN_FAILURE_DIRECT_DELTA=-10.0, TOTAL_CAUSAL_DELTA=1.0, PROTECTED_GOOD_DIP_DIRECT_DELTA=1.0),
        leak_ok=True,
        harvested_ok=True,
        pred_ok=True,
    )
    assert d2["CASE"] == "C"
