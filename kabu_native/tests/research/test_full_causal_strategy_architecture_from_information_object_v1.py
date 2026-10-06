"""FDG price-geometry Full Causal architecture. No imbalance. No 42 retune."""
from __future__ import annotations

import inspect

import pytest

from research.full_causal_strategy_architecture_from_information_object_v1 import (
    CURRENT_42_RETUNE,
    DEPTH_MIGRATION_RULE_USED,
    PREOPEN_EXECUTION_VALID,
    RANK_PRESENCE_AS_PRIMARY_ALPHA,
    REQUIRED_OBJECT_SHA256,
    STRATEGY_ID,
)
from research.full_causal_strategy_architecture_from_information_object_v1.analyze import decide, structural_coverage
from research.full_causal_strategy_architecture_from_information_object_v1.geometry import (
    onset_false_to_true,
    snapshot_from_payload,
    update_last_known,
    valid_full_depth,
)
from research.full_causal_strategy_architecture_from_information_object_v1.harvest import assert_dev_only_day
from research.full_causal_strategy_architecture_from_information_object_v1.publish import SHEET_ORDER
from research.full_causal_strategy_architecture_from_information_object_v1.spec import freeze_strategy_spec, pin_parent
from research.simple_full_strategy_discovery_v1 import BURNED_HOLDOUT_DAYS, STRESS_DAYS


def _book(bid0: float = 100.0, ask0: float = 101.0, step: float = 1.0, qty: float = 1.0) -> dict:
    pay = {}
    for i in range(1, 11):
        pay[f"Buy{i}"] = {"Price": bid0 - (i - 1) * step, "Qty": qty}
        pay[f"Sell{i}"] = {"Price": ask0 + (i - 1) * step, "Qty": qty}
    return pay


def test_parent_pin_and_flags():
    parent = pin_parent()
    assert parent["ok"] is True
    assert parent["SELECTED_OBJECT_ID"] == "FULL_DEPTH_GEOMETRY"
    assert parent["INFORMATION_OBJECT_SPEC_SHA256"] == REQUIRED_OBJECT_SHA256
    assert CURRENT_42_RETUNE is False
    assert RANK_PRESENCE_AS_PRIMARY_ALPHA is False
    assert DEPTH_MIGRATION_RULE_USED is False
    assert PREOPEN_EXECUTION_VALID is False
    spec = freeze_strategy_spec()
    assert spec["STRATEGY_FROZEN_BEFORE_ECONOMICS"] is True
    assert spec["STRATEGY_ID"] == STRATEGY_ID
    assert spec["FULL_STRATEGY_SPEC_SHA256_FDG_V1"] == freeze_strategy_spec()["FULL_STRATEGY_SPEC_SHA256_FDG_V1"]


def test_valid_full_depth_and_unknown():
    snap = snapshot_from_payload(_book())
    assert snap["VALID_FULL_DEPTH"] is True
    assert snap["UNKNOWN"] is False
    assert snap["BID_SPAN"] == 9.0
    assert snap["ASK_SPAN"] == 9.0
    assert snap["LONG_GEOMETRY_STATE"] is False
    wide_ask = _book()
    for i in range(1, 11):
        wide_ask[f"Sell{i}"]["Price"] = 101.0 + (i - 1) * 2.0
    snap2 = snapshot_from_payload(wide_ask)
    assert snap2["ASK_SPAN"] == 18.0
    assert snap2["LONG_GEOMETRY_STATE"] is True
    empty = snapshot_from_payload({})
    assert empty["UNKNOWN"] is True
    assert empty["LONG_GEOMETRY_STATE"] is None
    hole = _book()
    hole["Buy5"]["Qty"] = 0.0
    hole["Buy6"]["Qty"] = 10.0
    h = snapshot_from_payload(hole)
    assert h["bid_internal_zero_then_nonzero"] is True
    assert h["VALID_FULL_DEPTH"] is False
    assert h["UNKNOWN"] is True


def test_onset_initial_true_and_unknown():
    assert onset_false_to_true(None, True) is False
    assert onset_false_to_true(False, True) is True
    assert onset_false_to_true(True, True) is False
    assert onset_false_to_true(False, None) is False
    assert update_last_known(False, None) is False
    assert update_last_known(None, True) is True


def test_no_imbalance_in_geometry_and_harvest():
    import research.full_causal_strategy_architecture_from_information_object_v1.geometry as g
    import research.full_causal_strategy_architecture_from_information_object_v1.analyze as a

    src = inspect.getsource(g)
    assert "canonical_depth_imbalance" not in src
    assert "depth_imb" not in src
    assert "depth_ratio" not in src
    src_a = inspect.getsource(a)
    assert "canonical_depth_imbalance" not in src_a


def test_decide_semantic_and_case_b():
    d = decide(
        semantic_ok=False,
        integrity_pass=True,
        canary_ok=True,
        structural_pass=True,
        coverage_ok=True,
        g15=True,
        g6=True,
        s1=True,
        s2=True,
    )
    assert d["VERDICT"] == "FDG_DEPTH_RANK_SEMANTICS_UNRESOLVED_V1"
    st = structural_coverage([], {"valid_full_depth_n": 10, "unknown_n": 1, "long_state_true_n": 3})
    assert st["SC1"] is False
    d2 = decide(
        semantic_ok=True,
        integrity_pass=True,
        canary_ok=True,
        structural_pass=False,
        coverage_ok=False,
        g15=False,
        g6=None,
        s1=None,
        s2=None,
    )
    assert d2["CASE"] == "B"


def test_sheet_order():
    assert SHEET_ORDER[0] == "answers"
    assert SHEET_ORDER[-1] == "safety"
    assert "depth_rank_semantics" in SHEET_ORDER
    assert "trades" in SHEET_ORDER


def test_holdout_stress_blocked():
    with pytest.raises(RuntimeError, match="HOLDOUT"):
        assert_dev_only_day(BURNED_HOLDOUT_DAYS[0])
    with pytest.raises(RuntimeError, match="STRESS"):
        assert_dev_only_day(STRESS_DAYS[0])
    with pytest.raises(RuntimeError, match="FUTURE"):
        assert_dev_only_day("20260907")


def test_valid_requires_all_ten():
    bid = [10 - i for i in range(10)]
    qty = [1.0] * 10
    ask = [11 + i for i in range(10)]
    assert valid_full_depth(bid, qty, ask, qty) is True
    qty2 = list(qty)
    qty2[9] = 0.0
    assert valid_full_depth(bid, qty2, ask, qty) is False
