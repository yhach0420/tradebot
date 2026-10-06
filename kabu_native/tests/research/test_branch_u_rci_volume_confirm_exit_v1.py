"""Branch U RCI-then-down-volume-expansion EXIT V1 tests. No 20260903/04 capture input."""
from __future__ import annotations

import numpy as np

from research.simple_tech_redesign.branch_u_rci_volume_confirm_exit_v1_analyze import decide, decide_phase_a
from research.simple_tech_redesign.branch_u_rci_volume_confirm_exit_v1_harvest import (
    _first_bar_flag,
    duplicate_architecture_audit,
    eval_sequence,
    phase_a_gates,
    pred_down_volume_expansion,
    pred_rci_os,
    recover_sequence_predicates,
    unconstrained_class_counts,
)
from research.simple_tech_redesign.branch_u_rci_volume_confirm_exit_v1_spec import (
    AUTO_HOP_NEXT_PAIR,
    BB_USED_AS_CONFIRM,
    CANDIDATE_ID,
    EMA_USED_AS_CONFIRM,
    FORBIDDEN_INPUT_DAYS,
    MAX_RESEARCH_DATE,
    PAIR_ENUMERATION,
    PROSPECTIVE_HARVEST_SUSPENDED,
    REVERSE_SEQUENCE,
    STATIC_AND,
    THIS_ORIGIN,
    THRESHOLD_SEARCH,
    TRUE_OOS,
    V29_ORIGIN_FROZEN,
    VWAP_USED_AS_CONFIRM,
)
from research.simple_tech_redesign.precap_entry_quality_existing_data_mechanism_v1_harvest import assert_research_day
from research.simple_tech_redesign.v29_spec import ORIGIN as V29_ORIGIN


def test_frozen_bounds_and_not_v29():
    assert MAX_RESEARCH_DATE == "20260902"
    assert FORBIDDEN_INPUT_DAYS == ("20260903", "20260904")
    assert PROSPECTIVE_HARVEST_SUSPENDED is True
    assert TRUE_OOS is False
    assert AUTO_HOP_NEXT_PAIR is False
    assert REVERSE_SEQUENCE is False
    assert PAIR_ENUMERATION is False
    assert STATIC_AND is False
    assert THRESHOLD_SEARCH is False
    assert VWAP_USED_AS_CONFIRM is False
    assert EMA_USED_AS_CONFIRM is False
    assert BB_USED_AS_CONFIRM is False
    assert CANDIDATE_ID == "UNPROVEN_RCI_THEN_DOWN_VOLUME_EXPANSION_V1"
    assert THIS_ORIGIN == "FILL_IN_UNPROVEN_STATE"
    assert V29_ORIGIN_FROZEN == V29_ORIGIN
    assert V29_ORIGIN_FROZEN != THIS_ORIGIN


def test_forbidden_days_fail_closed():
    assert assert_research_day("20260903", today="20260904") == "FAIL_CLOSED_FORBIDDEN_INPUT"
    assert assert_research_day("20260904", today="20260904") == "FAIL_CLOSED_FORBIDDEN_INPUT"
    assert assert_research_day("20260907", today="20260904") == "FAIL_CLOSED_FUTURE_DATA"


def test_exact_predicates_recovered_and_not_duplicate():
    pack = recover_sequence_predicates()
    assert pack["ok"] is True, pack
    assert pack["RCI"]["SOURCE_LINE"]
    assert "rci9[i] <= -80" in pack["RCI"]["RCI_EXACT_PREDICATE"]
    assert "Close[i] < Close[i-1]" in pack["VOLUME"]["VOLUME_EXACT_PREDICATE"]
    assert "Volume[i] > Volume[i-1]" in pack["VOLUME"]["VOLUME_EXACT_PREDICATE"]
    assert pack["VOLUME"]["FIXED_THRESHOLD"] is False
    assert pack["VWAP_USED_AS_CONFIRM"] is False
    assert pack["EMA_USED_AS_CONFIRM"] is False
    dup = duplicate_architecture_audit()
    assert dup["ok"] is True
    assert dup["DUPLICATE_ARCHITECTURE"] is False


def _tf1(*, rci, close, volume, fin):
    n = len(fin)
    arr = lambda xs: np.asarray(xs, dtype=float)
    hi = arr(close) + 1.0
    lo = arr(close) - 1.0
    return {
        "finalize_t": arr(fin),
        "rci9": arr(rci),
        "close": arr(close),
        "vwap": arr(close),
        "ema9": arr(close),
        "ema21": arr(close) - 1.0,
        "high": hi,
        "low": lo,
        "volume": arr(volume),
        "bb_lower": arr(close) - 5.0,
        "bb_mid": arr(close),
        "bb_upper": arr(close) + 5.0,
        "minute_epoch": arr(fin),
    }


def _board(t, bid):
    tt = np.asarray(t, dtype=float)
    bb = np.asarray(bid, dtype=float)
    return {
        "t": tt,
        "bid": bb,
        "ask": bb + 1.0,
        "bid_qty": np.full(len(t), 100.0),
        "ask_qty": np.full(len(t), 100.0),
        "executable": np.ones(len(t), dtype=bool),
        "special": np.zeros(len(t), dtype=bool),
        "fresh_sec": np.zeros(len(t), dtype=float),
    }, {"clock": tt.copy()}


def test_same_bar_rci_and_volume_does_not_confirm():
    tf1 = _tf1(rci=[-90.0, -10.0, -10.0], close=[100.0, 99.0, 98.0], volume=[10.0, 20.0, 30.0], fin=[10.0, 20.0, 30.0])
    hit, ht = _first_bar_flag(tf1, start_t=5.0, end_t=50.0, pred=pred_rci_os)
    assert hit is True and ht == 10.0
    hit2, ht2 = _first_bar_flag(tf1, start_t=10.0, end_t=50.0, pred=pred_down_volume_expansion)
    assert hit2 is True and ht2 == 20.0
    board, meta = _board([6.0, 10.0, 20.0, 50.0], [99.0, 99.0, 99.0, 100.0])
    leak = {"PREDICATE_DRIFT_N": 0, "TF1_MISSING_N": 0, "STATIC_AND_SAME_BAR_EXIT_N": 0, "POST_BE_U_TRIGGER_N": 0, "PRE_FILL_EXIT_N": 0, "VOLUME_ARRAY_MISSING_N": 0}
    out = eval_sequence(
        tf1, {}, board, meta,
        t0=4.0, fill_t=5.0, fill_px=100.0, sess_end=80.0,
        control_exit={"reason": "SESSION_CLOSE", "exit_t": 80.0, "exit_bid": 100.0, "pnl_yen_100": 0.0, "miss": False},
        leak=leak, emit_exit=True,
    )
    assert out["rci_armed"] is True
    assert out["volume_confirm"] is True
    assert out["vol_t"] == 20.0
    assert leak["STATIC_AND_SAME_BAR_EXIT_N"] == 0


def test_rci_bar_itself_cannot_confirm_even_if_down_volume():
    tf1 = _tf1(rci=[-90.0, -10.0], close=[99.0, 101.0], volume=[20.0, 5.0], fin=[10.0, 20.0])
    hit, ht = _first_bar_flag(tf1, start_t=10.0, end_t=50.0, pred=pred_down_volume_expansion)
    assert hit is False and ht is None
    board, meta = _board([6.0, 10.0, 50.0], [99.0, 99.0, 100.0])
    leak = {"PREDICATE_DRIFT_N": 0, "TF1_MISSING_N": 0, "STATIC_AND_SAME_BAR_EXIT_N": 0, "POST_BE_U_TRIGGER_N": 0, "PRE_FILL_EXIT_N": 0, "VOLUME_ARRAY_MISSING_N": 0}
    out = eval_sequence(
        tf1, {}, board, meta,
        t0=4.0, fill_t=5.0, fill_px=100.0, sess_end=80.0,
        control_exit={"reason": "SESSION_CLOSE", "exit_t": 80.0, "exit_bid": 100.0, "pnl_yen_100": 0.0, "miss": False},
        leak=leak, emit_exit=True,
    )
    assert out["rci_armed"] is True
    assert out["volume_confirm"] is False
    assert out["triggered"] is False


def test_down_without_volume_expansion_does_not_confirm():
    tf1 = _tf1(rci=[-90.0, -10.0, -10.0], close=[100.0, 99.0, 98.0], volume=[10.0, 10.0, 9.0], fin=[10.0, 20.0, 30.0])
    assert pred_down_volume_expansion(tf1, 1) is False
    board, meta = _board([6.0, 10.0, 20.0, 50.0], [99.0, 99.0, 99.0, 100.0])
    leak = {"PREDICATE_DRIFT_N": 0, "TF1_MISSING_N": 0, "STATIC_AND_SAME_BAR_EXIT_N": 0, "POST_BE_U_TRIGGER_N": 0, "PRE_FILL_EXIT_N": 0, "VOLUME_ARRAY_MISSING_N": 0}
    out = eval_sequence(
        tf1, {}, board, meta,
        t0=4.0, fill_t=5.0, fill_px=100.0, sess_end=80.0,
        control_exit={"reason": "SESSION_CLOSE", "exit_t": 80.0, "exit_bid": 100.0, "pnl_yen_100": 0.0, "miss": False},
        leak=leak, emit_exit=True,
    )
    assert out["rci_armed"] is True
    assert out["volume_confirm"] is False


def test_volume_expansion_without_down_close_does_not_confirm():
    tf1 = _tf1(rci=[-90.0, -10.0, -10.0], close=[100.0, 101.0, 102.0], volume=[10.0, 20.0, 30.0], fin=[10.0, 20.0, 30.0])
    assert pred_down_volume_expansion(tf1, 1) is False
    board, meta = _board([6.0, 10.0, 20.0, 50.0], [99.0, 99.0, 99.0, 100.0])
    leak = {"PREDICATE_DRIFT_N": 0, "TF1_MISSING_N": 0, "STATIC_AND_SAME_BAR_EXIT_N": 0, "POST_BE_U_TRIGGER_N": 0, "PRE_FILL_EXIT_N": 0, "VOLUME_ARRAY_MISSING_N": 0}
    out = eval_sequence(
        tf1, {}, board, meta,
        t0=4.0, fill_t=5.0, fill_px=100.0, sess_end=80.0,
        control_exit={"reason": "SESSION_CLOSE", "exit_t": 80.0, "exit_bid": 100.0, "pnl_yen_100": 0.0, "miss": False},
        leak=leak, emit_exit=True,
    )
    assert out["volume_confirm"] is False


def test_equal_volume_does_not_confirm():
    tf1 = _tf1(rci=[-90.0, -10.0], close=[100.0, 99.0], volume=[10.0, 10.0], fin=[10.0, 20.0])
    assert pred_down_volume_expansion(tf1, 1) is False


def test_be_first_does_not_arm():
    tf1 = _tf1(rci=[-90.0, -10.0], close=[99.0, 98.0], volume=[10.0, 20.0], fin=[20.0, 30.0])
    board, meta = _board([6.0, 10.0, 20.0], [100.0, 100.0, 99.0])
    leak = {"PREDICATE_DRIFT_N": 0, "TF1_MISSING_N": 0, "STATIC_AND_SAME_BAR_EXIT_N": 0, "POST_BE_U_TRIGGER_N": 0, "PRE_FILL_EXIT_N": 0, "VOLUME_ARRAY_MISSING_N": 0}
    out = eval_sequence(
        tf1, {}, board, meta,
        t0=4.0, fill_t=5.0, fill_px=100.0, sess_end=80.0,
        control_exit={"reason": "SESSION_CLOSE", "exit_t": 80.0, "exit_bid": 100.0, "pnl_yen_100": 0.0, "miss": False},
        leak=leak, emit_exit=True,
    )
    assert out["be_reached"] is True
    assert out["rci_armed"] is False
    assert out["triggered"] is False
    assert out["state_end"] == "PROVEN_NO_U_EXIT"


def test_phase_a_p_early_confirm_blocks_and_exhausts():
    rows = []
    for i in range(6):
        rows.append(
            {
                "date": f"2026080{i+1}",
                "symbol": f"{4060+i}",
                "residual_class": "U_EARLY_NEVER_BE",
                "candidate_path": {"rci_armed": True, "volume_confirm": True, "volume_array_ok": True, "tf1_ok": True},
            }
        )
    rows.append(
        {
            "date": "20260810",
            "symbol": "7203",
            "residual_class": "P_EARLY_AFTER_BE",
            "candidate_path": {"rci_armed": True, "volume_confirm": True, "volume_array_ok": True, "tf1_ok": True},
        }
    )
    audit = unconstrained_class_counts(rows)
    pa = phase_a_gates(audit, identity_ok=True, leak_ok=True, pred_ok=True, dup_ok=True)
    assert pa["gates"]["C_P_EARLY_CONFIRM_N_eq_0"] is False
    assert pa["passed"] is False
    d = decide_phase_a(pa)
    assert d["VERDICT"] == "SIMPLE_TECH_BRANCH_U_PARTICIPATION_STATE_NOT_ACTIONABLE"
    assert d["CURRENT_SIMPLE_TECH_TECHNICAL_EXIT_DEVELOPMENT_EXHAUSTED"] is True
    assert d["PHASE_B_RAN"] is False
    assert d["CANDIDATE_FROZEN"] is False


def test_phase_a_u_early_short_of_six_fails():
    rows = [
        {
            "date": "20260801",
            "symbol": "4062",
            "residual_class": "U_EARLY_NEVER_BE",
            "candidate_path": {"rci_armed": True, "volume_confirm": True, "volume_array_ok": True, "tf1_ok": True},
        }
    ]
    audit = unconstrained_class_counts(rows)
    pa = phase_a_gates(audit, identity_ok=True, leak_ok=True, pred_ok=True, dup_ok=True)
    assert pa["U_EARLY_CONFIRM_N"] == 1
    assert pa["gates"]["B_U_EARLY_CONFIRM_N_ge_6"] is False
    assert pa["passed"] is False


def test_phase_a_pass_does_not_use_pnl():
    rows = []
    days = ["20260801", "20260803", "20260804", "20260805", "20260806", "20260807"]
    syms = ["4062", "7203", "6758", "9984", "8035", "6857"]
    for i in range(6):
        rows.append(
            {
                "date": days[i],
                "symbol": syms[i],
                "residual_class": "U_EARLY_NEVER_BE",
                "candidate_path": {"rci_armed": True, "volume_confirm": True, "volume_array_ok": True, "tf1_ok": True},
            }
        )
    audit = unconstrained_class_counts(rows)
    assert audit["PNL_USED_FOR_SELECTION"] is False
    pa = phase_a_gates(audit, identity_ok=True, leak_ok=True, pred_ok=True, dup_ok=True)
    assert pa["passed"] is True
    assert pa["PNL_USED_FOR_SELECTION"] is False
    d = decide_phase_a(pa)
    assert d["CURRENT_SIMPLE_TECH_TECHNICAL_EXIT_DEVELOPMENT_EXHAUSTED"] is False
    assert d["CASE"] == "PHASE_A_PASS"


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
        "P_EARLY_DIRECT_DELTA": 0.0,
        "GOOD_DIRECT_DELTA": 0.0,
        "DIP_DIRECT_DELTA": 0.0,
        "PROTECTED_KEEP_DIRECT_DELTA": 0.0,
        "TOTAL_CAUSAL_DELTA": 3000.0,
        "control": {"PF": 1.2, "max_drawdown": -20000.0, "total_pnl": 1000.0, "fill_n": 84},
        "treatment": {"PF": 1.5, "max_drawdown": -15000.0, "total_pnl": 4000.0, "fill_n": 90},
        "concentration": {"single_day_contribution_gt_50pct": False, "single_symbol_contribution_gt_50pct": False},
    }
    body.update(over)
    return body


def test_p_early_harm_is_case_b_and_exhausts():
    d = decide(_base(P_EARLY_DIRECT_DELTA=-1.0), _base(), leak_ok=True, harvested_ok=True, pred_ok=True, identity_232_ok=True)
    assert d["CASE"] == "B"
    assert d["VERDICT"] == "SIMPLE_TECH_BRANCH_U_RCI_VOLUME_RECOVERY_HARM"
    assert d["FAMILY_CLOSED"] is True
    assert d["CURRENT_SIMPLE_TECH_TECHNICAL_EXIT_DEVELOPMENT_EXHAUSTED"] is True


def test_case_a_and_integrity_e():
    d = decide(_base(), _base(), leak_ok=True, harvested_ok=True, pred_ok=True, identity_232_ok=True)
    assert d["CASE"] == "A"
    assert d["VERDICT"] == "SIMPLE_TECH_BRANCH_U_RCI_VOLUME_EXIT_SUPPORTED"
    assert d["CURRENT_SIMPLE_TECH_TECHNICAL_EXIT_DEVELOPMENT_EXHAUSTED"] is False
    d2 = decide(_base(), _base(), leak_ok=False, harvested_ok=True, pred_ok=True, identity_232_ok=True)
    assert d2["CASE"] == "E"
    assert d2["CURRENT_SIMPLE_TECH_TECHNICAL_EXIT_DEVELOPMENT_EXHAUSTED"] is False
