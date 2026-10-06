"""Branch U EMA Structure EXIT V1 tests. No 20260903/04 capture input."""
from __future__ import annotations

import numpy as np

from research.simple_tech_redesign.branch_u_ema_structure_exit_v1_analyze import decide
from research.simple_tech_redesign.branch_u_ema_structure_exit_v1_harvest import (
    _race,
    recover_lifecycle_u_ema_predicate,
)
from research.simple_tech_redesign.branch_u_ema_structure_exit_v1_spec import (
    AND_OR_CLOSED_EXIT,
    AUTO_HOP_U_HH_HL,
    AUTO_HOP_U_PULLBACK,
    AUTO_HOP_U_RCI,
    AUTO_HOP_U_TREND,
    AUTO_HOP_U_VWAP,
    CANDIDATE_ID,
    CLOSED_DO_NOT_REENTER,
    FORBIDDEN_INPUT_DAYS,
    LIFECYCLE_PRIMITIVE,
    LIFECYCLE_U_EMA_DAY_AGREE,
    LIFECYCLE_U_EMA_DAY_DISAGREE,
    LIFECYCLE_U_EMA_FAIL_HIT_RATE,
    LIFECYCLE_U_EMA_KEEP_HIT_RATE,
    MAX_RESEARCH_DATE,
    PERSISTENCE_SEARCH,
    PROSPECTIVE_HARVEST_SUSPENDED,
    TRUE_OOS,
    V26_PRIMITIVE_ID,
)
from research.simple_tech_redesign.exit_lifecycle_harvest import _first_bar_flag
from research.simple_tech_redesign.exit_lifecycle_spec import EXIT_POLICY_CREATED
from research.simple_tech_redesign.precap_entry_quality_existing_data_mechanism_v1_harvest import assert_research_day
from research.simple_tech_redesign.v27_harvest import primitive_known


def test_frozen_bounds_and_closed_families():
    assert MAX_RESEARCH_DATE == "20260902"
    assert FORBIDDEN_INPUT_DAYS == ("20260903", "20260904")
    assert PROSPECTIVE_HARVEST_SUSPENDED is True
    assert TRUE_OOS is False
    assert AUTO_HOP_U_TREND is False
    assert AUTO_HOP_U_PULLBACK is False
    assert AUTO_HOP_U_RCI is False
    assert AUTO_HOP_U_VWAP is False
    assert AUTO_HOP_U_HH_HL is False
    assert AND_OR_CLOSED_EXIT is False
    assert PERSISTENCE_SEARCH is False
    assert CANDIDATE_ID == "UNPROVEN_EMA_STRUCTURE_LOSS_V1"
    assert LIFECYCLE_PRIMITIVE == "U_EMA_STRUCTURE_LOSS"
    assert V26_PRIMITIVE_ID == "A_EMA_STRUCTURE_LOSS"
    assert EXIT_POLICY_CREATED is False
    assert "V27_EMA_PERSISTENCE" in CLOSED_DO_NOT_REENTER
    assert "BRANCH_U_BB" in CLOSED_DO_NOT_REENTER
    assert "E4_FALLBACK_NO_ADVERSE_PRICE" in CLOSED_DO_NOT_REENTER
    assert abs(LIFECYCLE_U_EMA_FAIL_HIT_RATE - 0.4444444444444444) < 1e-12
    assert abs(LIFECYCLE_U_EMA_KEEP_HIT_RATE - 0.015151515151515152) < 1e-12
    assert LIFECYCLE_U_EMA_DAY_AGREE == 13
    assert LIFECYCLE_U_EMA_DAY_DISAGREE == 0


def test_forbidden_days_fail_closed():
    assert assert_research_day("20260903", today="20260904") == "FAIL_CLOSED_FORBIDDEN_INPUT"
    assert assert_research_day("20260904", today="20260904") == "FAIL_CLOSED_FORBIDDEN_INPUT"
    assert assert_research_day("20260907", today="20260904") == "FAIL_CLOSED_FUTURE_DATA"


def test_exact_u_ema_recovered_from_lifecycle_source():
    pack = recover_lifecycle_u_ema_predicate()
    assert pack["ok"] is True, pack
    assert pack["EMA_PERIODS"] == (9, 21)
    assert "primitive_known(\"A_EMA_STRUCTURE_LOSS\"" in pack["EXACT_U_EMA_PREDICATE_TEXT"]
    assert "float(ema9) <= float(ema21)" in pack["EXACT_U_EMA_PREDICATE_TEXT"]
    assert "ft + 1e-12 >= float(end_t)" in pack["EXACT_U_EMA_PREDICATE_TEXT"]
    pred = pack["pred"]
    ema9 = np.asarray([10.0, 9.0])
    ema21 = np.asarray([9.5, 9.5])
    nan = np.asarray([np.nan, np.nan])
    ind = {"close": ema9, "ema9": ema9, "ema21": ema21, "bb_mid": nan, "rci9": nan, "volume": nan}
    assert pred(ind, 0) is False
    assert pred(ind, 1) is True
    missing = {"close": ema9, "ema9": nan, "ema21": nan}
    assert primitive_known("A_EMA_STRUCTURE_LOSS", missing, 0) is None


def test_lifecycle_tie_be_wins_same_timestamp():
    fin = np.asarray([10.0, 20.0, 30.0])
    ema9 = np.asarray([8.0, 8.0, 8.0])
    ema21 = np.asarray([9.0, 9.0, 9.0])
    nan = np.asarray([np.nan, np.nan, np.nan])
    tf1 = {
        "finalize_t": fin,
        "ema9": ema9,
        "ema21": ema21,
        "close": ema9,
        "bb_mid": nan,
        "rci9": nan,
        "volume": nan,
    }
    pred = recover_lifecycle_u_ema_predicate()["pred"]
    hit, ht = _first_bar_flag(tf1, start_t=5.0, end_t=20.0, pred=pred)
    assert hit is True
    assert ht == 10.0
    hit2, ht2 = _first_bar_flag(tf1, start_t=5.0, end_t=10.0, pred=pred)
    assert hit2 is False
    assert ht2 is None
    assert _race(10.0, 10.0) == "SAME_TIMESTAMP"
    assert _race(20.0, 10.0) == "EMA_FIRST"
    assert _race(10.0, 20.0) == "BE_FIRST"


def _base(**over):
    body = {
        "identity": {
            "ok": True,
            "control_sot_ok": True,
            "treatment_sot_ok": True,
            "leftover_ok": True,
            "be_flag_mismatch_n": 0,
            "tf1_fail_n": 0,
            "predicate_drift_n": 0,
        },
        "decomp_ok": True,
        "U_EARLY_NEVER_BE_DIRECT_DELTA": 2000.0,
        "PROTECTED_KEEP_DIRECT_DELTA": 10.0,
        "TOTAL_CAUSAL_DELTA": 3000.0,
        "control": {"PF": 1.2, "max_drawdown": -20000.0, "total_pnl": 1000.0, "fill_n": 84},
        "treatment": {"PF": 1.5, "max_drawdown": -15000.0, "total_pnl": 4000.0, "fill_n": 90},
        "concentration": {"single_day_contribution_gt_50pct": False, "single_symbol_contribution_gt_50pct": False},
    }
    body.update(over)
    return body


def test_integrity_failed_is_case_e():
    d = decide(_base(), _base(), leak_ok=False, harvested_ok=True, pred_ok=True)
    assert d["CASE"] == "E"
    assert d["VERDICT"] == "SIMPLE_TECH_BRANCH_U_EMA_INTEGRITY_FAILED"


def test_u_early_not_improved_is_case_d():
    d = decide(_base(U_EARLY_NEVER_BE_DIRECT_DELTA=0.0), _base(), leak_ok=True, harvested_ok=True, pred_ok=True)
    assert d["CASE"] == "D"
    assert d["FAMILY_CLOSED"] is True


def test_winner_harm_is_case_b():
    d = decide(_base(PROTECTED_KEEP_DIRECT_DELTA=-1.0), _base(), leak_ok=True, harvested_ok=True, pred_ok=True)
    assert d["CASE"] == "B"
    assert d["VERDICT"] == "SIMPLE_TECH_BRANCH_U_EMA_WINNER_HARM"
    assert d["FAMILY_CLOSED"] is True


def test_portfolio_fail_is_case_c():
    d = decide(_base(TOTAL_CAUSAL_DELTA=-1.0), _base(), leak_ok=True, harvested_ok=True, pred_ok=True)
    assert d["CASE"] == "C"


def test_case_a_and_burned_reverse_is_c():
    d = decide(
        _base(),
        _base(U_EARLY_NEVER_BE_DIRECT_DELTA=10.0, PROTECTED_KEEP_DIRECT_DELTA=1.0, TOTAL_CAUSAL_DELTA=10.0),
        leak_ok=True,
        harvested_ok=True,
        pred_ok=True,
    )
    assert d["CASE"] == "A"
    d2 = decide(
        _base(),
        _base(U_EARLY_NEVER_BE_DIRECT_DELTA=-10.0, PROTECTED_KEEP_DIRECT_DELTA=1.0, TOTAL_CAUSAL_DELTA=10.0),
        leak_ok=True,
        harvested_ok=True,
        pred_ok=True,
    )
    assert d2["CASE"] == "C"
    assert d2["CANDIDATE_FROZEN"] is False
