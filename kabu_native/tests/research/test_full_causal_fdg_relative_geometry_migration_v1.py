"""FDG relative-geometry migration. No span retune. No imbalance. No UNKNOWN bridge."""
from __future__ import annotations

import inspect

import numpy as np
import pytest

from research.full_causal_fdg_relative_geometry_migration_v1 import (
    CLOSED_STATIC_STRATEGY_ID,
    DEEP_RANKS_REQUIRED,
    PREOPEN_EXECUTION_VALID,
    REQUIRED_OBJECT_SHA256,
    SESSION_EXIT_FIX_AS_RESCUE,
    STATIC_SPAN_FAMILY_CLOSED,
    STATIC_SPAN_RETUNE,
    STRATEGY_ID,
)
from research.full_causal_fdg_relative_geometry_migration_v1.analyze import decide, structural_coverage
from research.full_causal_fdg_relative_geometry_migration_v1.geometry import (
    ask_geometry_expands,
    bid_geometry_contracts,
    first_baseline_loss_i,
    onset_false_to_true,
    relative_ask,
    relative_bid,
    scan_pairs,
    snapshot_rel,
    thesis_valid,
    translated_same,
)
from research.full_causal_fdg_relative_geometry_migration_v1.harvest import assert_dev_only_day
from research.full_causal_fdg_relative_geometry_migration_v1.publish import SHEET_ORDER
from research.full_causal_fdg_relative_geometry_migration_v1.spec import (
    freeze_strategy_spec,
    pin_object_parent,
    pin_static_parent,
)
from research.simple_full_strategy_discovery_v1 import BURNED_HOLDOUT_DAYS, STRESS_DAYS


def _book(bid0: float = 100.0, ask0: float = 101.0, bid_step: float = 1.0, ask_step: float = 1.0) -> dict:
    pay = {}
    for i in range(1, 11):
        pay[f"Buy{i}"] = {"Price": bid0 - (i - 1) * bid_step, "Qty": 1.0}
        pay[f"Sell{i}"] = {"Price": ask0 + (i - 1) * ask_step, "Qty": 1.0}
    return pay


def test_parent_pins_and_static_closed():
    obj = pin_object_parent()
    st = pin_static_parent()
    assert obj["ok"] is True
    assert obj["SELECTED_OBJECT_ID"] == "FULL_DEPTH_GEOMETRY"
    assert obj["INFORMATION_OBJECT_SPEC_SHA256"] == REQUIRED_OBJECT_SHA256
    assert st["ok"] is True
    assert st["CLOSED_STRATEGY_ID"] == CLOSED_STATIC_STRATEGY_ID
    assert STATIC_SPAN_FAMILY_CLOSED is True
    assert STATIC_SPAN_RETUNE is False
    assert SESSION_EXIT_FIX_AS_RESCUE is False
    assert DEEP_RANKS_REQUIRED is True
    assert PREOPEN_EXECUTION_VALID is False
    spec = freeze_strategy_spec()
    assert spec["STRATEGY_FROZEN_BEFORE_ECONOMICS"] is True
    assert spec["STRATEGY_ID"] == STRATEGY_ID
    assert spec["STRATEGY_ID"] != CLOSED_STATIC_STRATEGY_ID
    assert spec["FULL_STRATEGY_SPEC_SHA256_FDG_MIGRATION_V1"] == freeze_strategy_spec()["FULL_STRATEGY_SPEC_SHA256_FDG_MIGRATION_V1"]
    assert spec["magnitude_threshold"] is False
    assert spec["qty_in_alpha"] is False


def test_relative_geometry_and_translation_invariance():
    bid = [100.0 - i for i in range(10)]
    ask = [101.0 + i for i in range(10)]
    br = relative_bid(bid)
    ar = relative_ask(ask)
    assert br is not None and ar is not None
    assert float(br[0]) == 1.0
    assert float(ar[-1]) == 9.0
    assert translated_same(bid, ask, 12.5) is True
    snap = snapshot_rel(_book())
    assert snap["VALID_FULL_DEPTH"] is True
    empty = snapshot_rel({})
    assert empty["UNKNOWN"] is True
    assert empty["VALID_FULL_DEPTH"] is False


def test_contracts_expands_and_no_partial():
    prev_b = np.array([1.0, 2, 3, 4, 5, 6, 7, 8, 9], dtype=float)
    cur_b = np.array([0.5, 2, 3, 4, 5, 6, 7, 8, 9], dtype=float)
    bad_b = np.array([0.5, 2, 3, 4, 5, 6, 7, 8, 10], dtype=float)
    prev_a = np.array([1.0, 2, 3, 4, 5, 6, 7, 8, 9], dtype=float)
    cur_a = np.array([1.0, 2, 3, 4, 5, 6, 7, 8, 10], dtype=float)
    bad_a = np.array([0.5, 2, 3, 4, 5, 6, 7, 8, 10], dtype=float)
    assert bid_geometry_contracts(prev_b, cur_b) is True
    assert bid_geometry_contracts(prev_b, bad_b) is False
    assert ask_geometry_expands(prev_a, cur_a) is True
    assert ask_geometry_expands(prev_a, bad_a) is False
    assert thesis_valid(cur_b, cur_a, prev_b, prev_a) is True
    assert thesis_valid(bad_b, cur_a, prev_b, prev_a) is False


def test_onset_initial_true_and_unknown_no_bridge():
    assert onset_false_to_true(None, True) is False
    assert onset_false_to_true(False, True) is True
    t = np.array([1.0, 2.0, 3.0, 4.0], dtype=float)
    valid = np.array([True, False, True, True], dtype=bool)
    bid = np.vstack(
        [
            np.array([2, 3, 4, 5, 6, 7, 8, 9, 10], dtype=float),
            np.full(9, np.nan),
            np.array([1, 2, 3, 4, 5, 6, 7, 8, 9], dtype=float),
            np.array([1, 2, 3, 4, 5, 6, 7, 8, 9], dtype=float),
        ]
    )
    ask = np.vstack(
        [
            np.array([1, 2, 3, 4, 5, 6, 7, 8, 9], dtype=float),
            np.full(9, np.nan),
            np.array([2, 3, 4, 5, 6, 7, 8, 9, 10], dtype=float),
            np.array([2, 3, 4, 5, 6, 7, 8, 9, 10], dtype=float),
        ]
    )
    scanned = scan_pairs(t, valid, bid, ask, flatten_t=99.0)
    assert scanned["FALSE_TO_TRUE_SIGNAL_N"] == 0
    t2 = np.array([1.0, 2.0], dtype=float)
    v2 = np.array([True, True], dtype=bool)
    b_p = np.array([2, 3, 4, 5, 6, 7, 8, 9, 10], dtype=float)
    b_c = np.array([1, 2, 3, 4, 5, 6, 7, 8, 9], dtype=float)
    a_p = np.array([1, 2, 3, 4, 5, 6, 7, 8, 9], dtype=float)
    a_c = np.array([2, 3, 4, 5, 6, 7, 8, 9, 10], dtype=float)
    scanned2 = scan_pairs(t2, v2, np.vstack([b_p, b_c]), np.vstack([a_p, a_c]), flatten_t=99.0)
    assert scanned2["FALSE_TO_TRUE_SIGNAL_N"] == 0
    t3 = np.array([1.0, 2.0, 3.0], dtype=float)
    v3 = np.array([True, True, True], dtype=bool)
    scanned3 = scan_pairs(
        t3,
        v3,
        np.vstack([b_p, b_p, b_c]),
        np.vstack([a_p, a_p, a_c]),
        flatten_t=99.0,
    )
    assert scanned3["FALSE_TO_TRUE_SIGNAL_N"] == 1
    assert scanned3["onsets"][0] == (2, 1)


def test_baseline_from_p_not_c_and_unknown_no_exit():
    t = np.array([10.0, 20.0, 30.0, 40.0], dtype=float)
    valid = np.array([True, True, False, True], dtype=bool)
    base_b = np.array([2, 3, 4, 5, 6, 7, 8, 9, 10], dtype=float)
    base_a = np.array([1, 2, 3, 4, 5, 6, 7, 8, 9], dtype=float)
    hold_b = np.array([1, 2, 3, 4, 5, 6, 7, 8, 9], dtype=float)
    hold_a = np.array([2, 3, 4, 5, 6, 7, 8, 9, 10], dtype=float)
    lose_b = np.array([3, 4, 5, 6, 7, 8, 9, 10, 11], dtype=float)
    bid = np.vstack([base_b, hold_b, np.full(9, np.nan), lose_b])
    ask = np.vstack([base_a, hold_a, np.full(9, np.nan), hold_a])
    i = first_baseline_loss_i(t, valid, bid, ask, fill_t=20.0, flatten_t=99.0, bid_base=base_b, ask_base=base_a)
    assert i == 3
    i2 = first_baseline_loss_i(t, valid, bid, ask, fill_t=20.0, flatten_t=35.0, bid_base=base_b, ask_base=base_a)
    assert i2 is None


def test_no_span_imbalance_threshold_in_geometry():
    import research.full_causal_fdg_relative_geometry_migration_v1.analyze as a
    import research.full_causal_fdg_relative_geometry_migration_v1.geometry as g

    src = inspect.getsource(g)
    assert "canonical_depth_imbalance" not in src
    assert "bid_span(" not in src
    assert "ask_span(" not in src
    assert "long_geometry_state" not in src
    src_a = inspect.getsource(a)
    assert "canonical_depth_imbalance" not in src_a


def test_decide_case_b_and_sheet_order():
    d = decide(
        integrity_pass=True,
        canary_ok=True,
        structural_pass=False,
        coverage_ok=False,
        g15=False,
        g6=None,
        s1=None,
        s2=None,
    )
    assert d["CASE"] == "B"
    assert d["FULL_DEPTH_GEOMETRY_JUSTIFIED_MECHANISM_SPACE_EXHAUSTED"] is True
    st = structural_coverage([], {"valid_full_depth_n": 10, "unknown_pair_n": 1})
    assert st["SC1"] is False
    assert st["SC3"] is False
    assert SHEET_ORDER[0] == "answers"
    assert SHEET_ORDER[3] == "object_boundary"
    assert SHEET_ORDER[5] == "relative_geometry"
    assert SHEET_ORDER[-1] == "safety"
    assert len(SHEET_ORDER) == 20


def test_holdout_stress_blocked():
    with pytest.raises(RuntimeError, match="HOLDOUT"):
        assert_dev_only_day(BURNED_HOLDOUT_DAYS[0])
    with pytest.raises(RuntimeError, match="STRESS"):
        assert_dev_only_day(STRESS_DAYS[0])
    with pytest.raises(RuntimeError, match="FUTURE"):
        assert_dev_only_day("20260907")
