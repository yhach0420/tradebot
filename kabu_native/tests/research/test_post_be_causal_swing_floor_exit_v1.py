"""POST_BE_CONFIRMED_SWING_FLOOR_V1 unit tests. No 20260903/04 capture input."""
from __future__ import annotations

from research.simple_tech_redesign.post_be_causal_swing_floor_exit_v1_analyze import decide
from research.simple_tech_redesign.post_be_causal_swing_floor_exit_v1_harvest import (
    is_confirmed_swing_low,
    walk_post_be_swing_floor,
)
from research.simple_tech_redesign.post_be_causal_swing_floor_exit_v1_spec import (
    CANDIDATE_ID,
    CLOSED_DO_NOT_REENTER,
    FORBIDDEN_INPUT_DAYS,
    MAX_RESEARCH_DATE,
    NEW_ENTRY_FILTER,
    NEW_EXIT_RULE,
    P2_TOUCH_AGE_THIS_RUN,
    PIVOT_BARS,
    PROSPECTIVE_HARVEST_SUSPENDED,
    THRESHOLD_SEARCH,
    TF_SEARCH,
    TIMEFRAME,
    TRUE_OOS,
)
from research.simple_tech_redesign.precap_entry_quality_existing_data_mechanism_v1_harvest import assert_research_day
from research.simple_tech_redesign.v29_spec import FAMILY_B_SWING_AVAILABLE


def test_frozen_bounds_and_closed_families():
    assert MAX_RESEARCH_DATE == "20260902"
    assert FORBIDDEN_INPUT_DAYS == ("20260903", "20260904")
    assert PROSPECTIVE_HARVEST_SUSPENDED is True
    assert TRUE_OOS is False
    assert NEW_EXIT_RULE is False
    assert NEW_ENTRY_FILTER is False
    assert THRESHOLD_SEARCH is False
    assert TF_SEARCH is False
    assert TIMEFRAME == "1m"
    assert int(PIVOT_BARS) == 3
    assert P2_TOUCH_AGE_THIS_RUN is False
    assert CANDIDATE_ID == "POST_BE_CONFIRMED_SWING_FLOOR_V1"
    assert "V29_TERMINAL_SEQUENCE" in CLOSED_DO_NOT_REENTER
    assert "ENTRY_ANCHORED_FLOOR_BREAK" in CLOSED_DO_NOT_REENTER
    assert "P2_TOUCH_AGE" in CLOSED_DO_NOT_REENTER
    assert "BRANCH_P_SECOND_BE_CROSSING" in CLOSED_DO_NOT_REENTER
    assert FAMILY_B_SWING_AVAILABLE is False


def test_forbidden_days_fail_closed():
    assert assert_research_day("20260903", today="20260904") == "FAIL_CLOSED_FORBIDDEN_INPUT"
    assert assert_research_day("20260904", today="20260904") == "FAIL_CLOSED_FORBIDDEN_INPUT"
    assert assert_research_day("20260907", today="20260904") == "FAIL_CLOSED_FUTURE_DATA"


def test_equal_low_is_not_pivot():
    assert is_confirmed_swing_low(10.0, 9.0, 9.0) is False
    assert is_confirmed_swing_low(9.0, 9.0, 10.0) is False
    assert is_confirmed_swing_low(10.0, 9.0, 10.0) is True


def test_confirmation_is_j_plus_one_finalize_not_backdated():
    # bars 0,1,2: lows 10, 8, 9 → pivot at j=1 confirmed when bar 2 completes at t=300
    walked = walk_post_be_swing_floor(
        low=[10.0, 8.0, 9.0, 9.5],
        close=[10.0, 9.0, 9.2, 7.0],
        minute_epoch=[100.0, 160.0, 220.0, 280.0],
        finalize_t=[160.0, 220.0, 280.0, 340.0],
        first_be_t=50.0,
        sess_end=1000.0,
    )
    assert walked["floor_formed"] is True
    assert walked["swing_floor"] == 8.0
    assert walked["first_floor_t"] == 280.0
    assert walked["pivot_backdate_n"] == 0
    assert walked["triggered"] is True
    assert walked["trigger_t"] == 340.0
    assert walked["trigger_close"] == 7.0


def test_pre_be_center_excluded():
    walked = walk_post_be_swing_floor(
        low=[10.0, 8.0, 9.0],
        close=[10.0, 9.0, 7.0],
        minute_epoch=[100.0, 160.0, 220.0],
        finalize_t=[160.0, 220.0, 280.0],
        first_be_t=200.0,
        sess_end=1000.0,
    )
    assert walked["floor_formed"] is False
    assert walked["triggered"] is False
    assert walked["post_be_pivot_n"] == 0


def test_ratchet_up_only():
    walked = walk_post_be_swing_floor(
        low=[12.0, 8.0, 9.0, 9.5, 10.5, 9.6, 11.0],
        close=[12.0, 9.0, 9.1, 9.6, 10.2, 9.8, 10.8],
        minute_epoch=[100.0, 160.0, 220.0, 280.0, 340.0, 400.0, 460.0],
        finalize_t=[160.0, 220.0, 280.0, 340.0, 400.0, 460.0, 520.0],
        first_be_t=50.0,
        sess_end=1000.0,
    )
    assert walked["swing_floor"] == 9.6
    assert walked["ratchet_n"] == 1
    assert walked["triggered"] is False


def test_low_touch_does_not_trigger():
    walked = walk_post_be_swing_floor(
        low=[10.0, 8.0, 9.0, 7.0],
        close=[10.0, 9.0, 9.1, 8.0],
        minute_epoch=[100.0, 160.0, 220.0, 280.0],
        finalize_t=[160.0, 220.0, 280.0, 340.0],
        first_be_t=50.0,
        sess_end=1000.0,
    )
    assert walked["swing_floor"] == 8.0
    assert walked["triggered"] is False


def test_close_equal_floor_does_not_trigger():
    walked = walk_post_be_swing_floor(
        low=[10.0, 8.0, 9.0, 7.5],
        close=[10.0, 9.0, 9.1, 8.0],
        minute_epoch=[100.0, 160.0, 220.0, 280.0],
        finalize_t=[160.0, 220.0, 280.0, 340.0],
        first_be_t=50.0,
        sess_end=1000.0,
    )
    assert walked["triggered"] is False


def _base_pack(**over):
    body = {
        "identity": {"ok": True, "control_sot_ok": True, "leftover_ok": True, "be_flag_mismatch_n": 0, "tf1_fail_n": 0},
        "decomp_ok": True,
        "FAILURE_DIRECT_DELTA": 1000.0,
        "PROTECTED_GOOD_DIP_DIRECT_DELTA": 10.0,
        "TOTAL_CAUSAL_DELTA": 2000.0,
        "control": {"PF": 1.2, "max_drawdown": -20000.0, "fill_n": 84},
        "treatment": {"PF": 1.5, "max_drawdown": -15000.0, "fill_n": 90},
        "concentration": {"single_day_contribution_gt_50pct": False, "single_symbol_contribution_gt_50pct": False},
    }
    body.update(over)
    return body


def test_integrity_failed_is_case_e():
    d = decide(_base_pack(), _base_pack(FAILURE_DIRECT_DELTA=10.0, TOTAL_CAUSAL_DELTA=1.0), leak_ok=False, harvested_ok=True)
    assert d["CASE"] == "E"
    assert d["VERDICT"] == "SIMPLE_TECH_POST_BE_SWING_FLOOR_INTEGRITY_FAILED"
    assert d["CANDIDATE_FROZEN"] is False


def test_winner_harm_is_case_b():
    d = decide(
        _base_pack(PROTECTED_GOOD_DIP_DIRECT_DELTA=-100.0),
        _base_pack(FAILURE_DIRECT_DELTA=50.0, TOTAL_CAUSAL_DELTA=1.0),
        leak_ok=True,
        harvested_ok=True,
    )
    assert d["CASE"] == "B"
    assert d["FAMILY_CLOSED"] is True


def test_no_failure_separation_is_case_d():
    d = decide(_base_pack(FAILURE_DIRECT_DELTA=0.0), _base_pack(), leak_ok=True, harvested_ok=True)
    assert d["CASE"] == "D"


def test_portfolio_fail_is_case_c():
    d = decide(_base_pack(TOTAL_CAUSAL_DELTA=-1.0), _base_pack(), leak_ok=True, harvested_ok=True)
    assert d["CASE"] == "C"


def test_case_a_requires_burned_clear():
    d = decide(_base_pack(), _base_pack(FAILURE_DIRECT_DELTA=50.0, TOTAL_CAUSAL_DELTA=10.0), leak_ok=True, harvested_ok=True)
    assert d["CASE"] == "A"
    assert d["CANDIDATE_FROZEN"] is True
    d2 = decide(_base_pack(), _base_pack(FAILURE_DIRECT_DELTA=-10.0, TOTAL_CAUSAL_DELTA=10.0), leak_ok=True, harvested_ok=True)
    assert d2["CASE"] == "C"
    d3 = decide(_base_pack(), _base_pack(FAILURE_DIRECT_DELTA=50.0, TOTAL_CAUSAL_DELTA=-1.0), leak_ok=True, harvested_ok=True)
    assert d3["CASE"] == "C"
