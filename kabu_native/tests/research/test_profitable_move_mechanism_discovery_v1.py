"""PROFITABLE_MOVE_MECHANISM_DISCOVERY_V1. Labels only. No strategy PnL. No Full Strategy freeze."""
from __future__ import annotations

import numpy as np
import pytest

from research.profitable_move_mechanism_discovery_v1 import (
    CASE_A,
    CASE_B,
    CASE_C,
    DEVELOPMENT_DAYS,
    FEATURE_LOOKAHEAD_N,
    FIXED_HORIZONS,
    FULL_CAUSAL_PORTFOLIO_RUN,
    FULL_STRATEGY_FROZEN,
    HORIZON_SEARCH,
    INFORMATION_FAMILY_INVENTORY_AS_PRIMARY_NEXT,
    NEW_NUMERIC_THRESHOLD_SEARCH,
    NEXT_A,
    NEXT_B,
    NEXT_C,
    PRIMARY_DISCOVERY_HORIZON,
    Q_VALUE_H5_MAX,
    STRATEGY_EXIT_USED_FOR_DISCOVERY,
    V4_RCA_RUN,
    V4_RETUNE,
)
from research.profitable_move_mechanism_discovery_v1.analyze import (
    PNL_CLAIM_KEYS,
    apply_gates,
    build_answers,
    rank_key,
)
from research.profitable_move_mechanism_discovery_v1.harvest import assert_dev_day, resolve_entry, resolve_exit_h
from research.profitable_move_mechanism_discovery_v1.library import candidate_library, evaluate_row
from research.profitable_move_mechanism_discovery_v1.predicates import CUTPOINT_KIND, FAMILY, assert_st_bindings
from research.profitable_move_mechanism_discovery_v1.prior_use import classify_library
from research.profitable_move_mechanism_discovery_v1.publish import SHEET_ORDER
from research.profitable_move_mechanism_discovery_v1.spec import pin_parent_v3
from research.profitable_move_mechanism_discovery_v1.stats import bh_correct, sign_flip_p_one_sided


def test_parent_pin_and_workflow_flags():
    pin = pin_parent_v3()
    assert pin["ok"] is True
    assert pin["observed_verdict"] == "NEW_FULL_STRATEGY_NO_JUSTIFIED_NEW_ARCHITECTURE_V3"
    assert pin["ELIGIBLE_PROPOSAL_N"] == 0
    assert pin["FULL_STRATEGY_SPEC_SHA256_V5"] is None
    assert INFORMATION_FAMILY_INVENTORY_AS_PRIMARY_NEXT is False
    assert V4_RCA_RUN is False
    assert V4_RETUNE is False
    assert FULL_STRATEGY_FROZEN is False
    assert FULL_CAUSAL_PORTFOLIO_RUN is False
    assert HORIZON_SEARCH is False
    assert NEW_NUMERIC_THRESHOLD_SEARCH is False
    assert STRATEGY_EXIT_USED_FOR_DISCOVERY is False
    assert FEATURE_LOOKAHEAD_N == 0
    assert tuple(FIXED_HORIZONS) == (3, 5, 10)
    assert PRIMARY_DISCOVERY_HORIZON == 5
    assert abs(float(Q_VALUE_H5_MAX) - 0.05) < 1e-12
    assert NEXT_C != "NEW_INFORMATION_FAMILY_INVENTORY_V1"
    assert CASE_A != CASE_B != CASE_C
    assert NEXT_A != NEXT_B != NEXT_C
    assert len(DEVELOPMENT_DAYS) == 10
    assert DEVELOPMENT_DAYS[-1] == "20260807"


def test_library_finite_natural_or_frozen_cutpoints():
    assert_st_bindings()
    lib = candidate_library()
    ids = [r["MECHANISM_ID"] for r in lib]
    assert len(ids) == len(set(ids))
    assert len(lib) >= 40
    for r in lib:
        assert r["PRIMITIVE_N"] <= 2
        assert r["TEMPLATE"] in {
            "A_SINGLE_STATE",
            "B_ONSET",
            "C_CROSS_FAMILY_PAIR",
            "D_ONE_STEP_SEQUENCE",
            "E_ONSET_WITH_CONTEXT",
        }
        if r["TEMPLATE"] in ("C_CROSS_FAMILY_PAIR", "D_ONE_STEP_SEQUENCE", "E_ONSET_WITH_CONTEXT"):
            assert r["FAMILIES"][0] != r["FAMILIES"][1]
    for pid, kind in CUTPOINT_KIND.items():
        assert kind in ("FROZEN_HISTORICAL", "NATURAL_BOUNDARY")
        assert pid in FAMILY
    prior = classify_library(lib)
    by = {r["MECHANISM_ID"]: r for r in prior}
    assert by["B_ONSET_S_CLOSE_ABOVE_VWAP"]["EXACT_PRIOR_TEST_MATCH"] is True
    assert by["B_ONSET_S_CLOSE_ABOVE_VWAP"]["EXACT_CLOSED_LINEAGE"] == "E4_VWAP_RECLAIM"
    assert by["E_ONSET_S_MA_TREND_UP__CTX_S_BREADTH_EXPANDING"]["EXACT_CLOSED_LINEAGE"] == "CSB_MA_ONSET"
    assert by["E_ONSET_S_MA_TREND_UP__CTX_S_BREADTH_EXPANDING"]["ELIGIBLE_AS_NEW_ARCHITECTURE"] is False
    assert by["A_S_MA_TREND_UP"]["ELIGIBLE_AS_NEW_ARCHITECTURE"] is True


def test_evaluate_templates():
    now = {"P": True, "Q": True}
    prev = {"P": False, "Q": True}
    assert evaluate_row({"TEMPLATE": "A_SINGLE_STATE", "PRIMITIVES": ["P"]}, now, prev) is True
    assert evaluate_row({"TEMPLATE": "B_ONSET", "PRIMITIVES": ["P"]}, now, prev) is True
    assert evaluate_row({"TEMPLATE": "B_ONSET", "PRIMITIVES": ["Q"]}, now, prev) is False
    assert evaluate_row({"TEMPLATE": "C_CROSS_FAMILY_PAIR", "PRIMITIVES": ["P", "Q"]}, now, prev) is True
    assert evaluate_row({"TEMPLATE": "D_ONE_STEP_SEQUENCE", "PRIMITIVES": ["Q", "P"]}, now, prev) is True
    assert evaluate_row({"TEMPLATE": "E_ONSET_WITH_CONTEXT", "PRIMITIVES": ["P", "Q"]}, now, prev) is True


def test_markout_search_windows():
    t = np.array([100.0, 160.0, 200.0], dtype=float)
    px = np.array([10.0, 11.0, 12.0], dtype=float)
    standing_ok = {"ask_ok": True, "ask": 9.5}
    et, ep = resolve_entry(standing_ok, t, px, 150.0)
    assert et == 150.0 and abs(ep - 9.5) < 1e-12
    standing_no = {"ask_ok": False, "ask": float("nan")}
    et, ep = resolve_entry(standing_no, t, px, 150.0)
    assert et == 160.0 and abs(ep - 11.0) < 1e-12
    bid_t = np.array([179.0, 180.0, 200.0], dtype=float)
    bid_px = np.array([1.0, 2.0, 3.0], dtype=float)
    xt, xp = resolve_exit_h(None, bid_t, bid_px, 150.0, 0)
    # H=0 window [150, 210) first bid at 179
    assert xt == 179.0 and abs(xp - 1.0) < 1e-12
    stand_bid = {"bid_ok": True, "bid": 8.0}
    xt, xp = resolve_exit_h(stand_bid, bid_t, bid_px, 100.0, 1)
    assert abs(xt - 160.0) < 1e-12 and abs(xp - 8.0) < 1e-12
    miss_t = np.array([10.0], dtype=float)
    miss_px = np.array([1.0], dtype=float)
    xt, xp = resolve_exit_h(None, miss_t, miss_px, 150.0, 5)
    assert xt is None and xp is None


def test_holdout_stress_future_blocked():
    with pytest.raises(RuntimeError):
        assert_dev_day("20260810")
    with pytest.raises(RuntimeError):
        assert_dev_day("20260828")
    with pytest.raises(RuntimeError):
        assert_dev_day("20260903")
    with pytest.raises(RuntimeError):
        assert_dev_day("20260808")
    assert_dev_day("20260807")


def test_permutation_and_bh_and_gates():
    pos = sign_flip_p_one_sided(np.array([1.0, 2.0, 1.5, 0.5, 3.0, 1.0, 2.0, 1.2, 0.8, 1.1]), iters=1999, seed=1)
    assert pos["p"] < 0.05
    neg = sign_flip_p_one_sided(np.array([-1.0, -2.0, -0.5, -1.5, -0.2, -0.8, -1.1, -0.4, -0.9, -1.3]), iters=1999, seed=1)
    assert neg["p"] > 0.5
    q = bh_correct([0.001, 0.02, 0.5, 0.8])
    assert q[0] <= q[1] <= 1.0
    row = {
        "EVENT_N": 40,
        "EVENT_DAY_N": 8,
        "h3": {"mean_markout_yen100": 1.0, "mean_excess_yen100": 0.1},
        "h5": {"mean_markout_yen100": 2.0, "mean_excess_yen100": 0.2},
        "h10": {"mean_markout_yen100": 0.5, "mean_excess_yen100": 0.05},
        "H5_POSITIVE_BLOCK_N": 4,
        "H5_EX_BEST_DAY_TOTAL_EXCESS": 1.0,
    }
    g = apply_gates(row, 0.04)
    assert g["PASS_D1_D11"] is True
    g2 = apply_gates(row, 0.06)
    assert g2["D11_BH_Q_H5"] is False
    assert g2["PASS_D1_D11"] is False


def test_rank_prefers_min_excess_then_simpler():
    a = {
        "h3": {"mean_excess_yen100": 1.0},
        "h5": {"mean_excess_yen100": 2.0},
        "h10": {"mean_excess_yen100": 0.5},
        "H5_POSITIVE_BLOCK_N": 5,
        "H5_EX_BEST_DAY_TOTAL_EXCESS": 10.0,
        "PRIMITIVE_N": 2,
        "TEMPLATE_RANK": 4,
        "MECHANISM_ID": "Z",
    }
    b = dict(a)
    b["h10"] = {"mean_excess_yen100": 0.8}
    b["PRIMITIVE_N"] = 1
    b["TEMPLATE_RANK"] = 0
    b["MECHANISM_ID"] = "A"
    assert rank_key(b) < rank_key(a)


def test_sheet_order_and_answer_keys():
    assert SHEET_ORDER == (
        "answers",
        "objective_alignment",
        "data",
        "outcome_semantics",
        "predicate_library",
        "prior_use",
        "candidate_library",
        "coverage",
        "markout_h3",
        "markout_h5",
        "markout_h10",
        "daily",
        "blocks",
        "statistics",
        "fdr",
        "discovery_gates",
        "closed_lineage",
        "selection",
        "decision",
        "safety",
    )
    pack = {
        "decision": {
            "VERDICT": CASE_C,
            "NEXT": NEXT_C,
            "PASS_D1_D11_N": 0,
            "PASSING_IDS": [],
            "SELECTED_MECHANISM_ID": None,
        },
        "selected": None,
        "data": {"HOLDOUT_READ_N": 0, "STRESS_READ_N": 0, "FUTURE_DATE_READ_N": 0},
        "RAW_PREDICATE_N": 12,
        "CANDIDATE_MECHANISM_N": 70,
        "EXACT_PRIOR_MATCH_CANDIDATE_N": 2,
        "COVERAGE_QUALIFIED_N": 0,
    }
    a = build_answers(pack)
    for i in range(1, 37):
        assert any(k.startswith(f"{i}_") for k in a)
    assert a["2_information_family_inventory_as_primary_next"] is False
    assert a["4_Holdout_read"] is False
    assert a["12_horizon_search"] is False
    assert a["15_new_threshold_search"] is False
    assert a["30_Full_Strategy_frozen"] is False
    assert a["32_strategy_PnL_claimed"] is False
    assert a["34_submit_cancel_live"] == "0/0/0"
    for k in PNL_CLAIM_KEYS:
        assert k not in a
