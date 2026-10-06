"""Tests for economic failure decomposition. Does not load Frozen Validation bars."""
from __future__ import annotations

from research.pb1_v4_complete_strategy_economic_confirmation1 import EXPECTED_COMPLETE_STRATEGY_SHA256
from research.pb1_v4_complete_strategy_economic_failure_decomposition import (
    BASELINE,
    EXPAND_BPS,
    SMALL_EDGE_BPS,
)
from research.pb1_v4_complete_strategy_economic_failure_decomposition.classify import classify_entry_path
from research.pb1_v4_complete_strategy_economic_failure_decomposition.isolation import OUT, write_overlap_n
from research.pb1_v4_complete_strategy_economic_failure_decomposition.publish import SHEET_ORDER
from research.pb1_v4_complete_strategy_economic_failure_decomposition.waterfall import primary_attr, waterfall


def test_v1_fail_baseline_and_sheets():
    assert BASELINE["signal_n"] == 150
    assert BASELINE["trade_n"] == 111
    assert BASELINE["net_pnl_yen"] == -235671.0
    assert SMALL_EDGE_BPS == 8.0
    assert EXPAND_BPS == 16.0
    assert EXPECTED_COMPLETE_STRATEGY_SHA256 == "556542319d22dff40cc1758b24d44d2ecb1985b8595927ca8f80618126961bf8"
    assert write_overlap_n("", "") == 0
    assert SHEET_ORDER[0] == "Manifest"
    assert "Loss_Waterfall" in SHEET_ORDER
    assert "pb1_v4_complete_strategy_economic_failure_decomposition" in str(OUT).replace("\\", "/")


def test_a_priori_classes_and_single_attribution():
    a = classify_entry_path({"MFE_bps": 3.0, "MAE_bps": -20.0, "time_to_MAE": 1, "realized_gross_bps": -15.0, "MFE_capture_ratio": None})
    assert a == "A_IMMEDIATE_WRONG_DIRECTION"
    c = classify_entry_path({"MFE_bps": 40.0, "MAE_bps": -5.0, "time_to_MAE": 10, "realized_gross_bps": -8.0, "MFE_capture_ratio": None})
    assert c == "C_FAVORABLE_THEN_FULL_GIVEBACK"
    d = classify_entry_path({"MFE_bps": 40.0, "MAE_bps": -5.0, "time_to_MAE": 10, "realized_gross_bps": 30.0, "MFE_capture_ratio": 0.75})
    assert d == "D_FAVORABLE_AND_EXIT_CAPTURED"
    row = {
        "net_pnl_yen": -100.0,
        "entry_class": "A_IMMEDIATE_WRONG_DIRECTION",
        "gross_pnl_yen": -80.0,
        "THESIS_LOST_REASON": "ACCEPTED_STRUCTURAL_FAILURE",
        "MFE_bps": 40.0,
        "minutes_MFE_to_THESIS_LOST": 20,
        "ops_flatten": False,
        "entry_px": 30000.0,
    }
    assert primary_attr(row) == "ENTRY_WRONG_FROM_START"
    wf = waterfall([row], blocked=[{"net_pnl_yen": 50.0}])
    assigned = sum(int(x["trade_n"]) for x in wf["primary"])
    assert assigned == 1
    assert wf["double_counted"] is False
    assert wf["PORTFOLIO_BLOCKING"]["COUNTERFACTUAL_NOT_STRATEGY_RESULT"] is True
