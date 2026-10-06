"""Tests for cross-sectional peer propagation. No strategy. No R13/R11/R14 repair."""
from __future__ import annotations

import inspect

import numpy as np

from research.cross_sectional_peer_propagation_discovery_v1 import (
    CASE_CONTROL,
    CASE_FOUND,
    CASE_NONE,
    CASE_PARTIAL,
    CASE_SIM,
    EXPECTED_DETECTOR_SHA256,
    EXPECTED_STATE_MACHINE_SHA256,
    NEXT_RCA,
    NEXT_STOP,
    PARENT_NEXT,
    PARENT_VERDICT,
    PEER_MIN_N,
    PNL_OPTIMIZATION,
    R13_R11_R14_REPAIRED,
    THRESHOLD_RETUNED,
    TV_ELEVATED_PCTL,
)
from research.cross_sectional_peer_propagation_discovery_v1.analyze import build_report_body, decide
from research.cross_sectional_peer_propagation_discovery_v1.features import leave_one_out_median, p_flags, peer_stats, sess_ret
from research.cross_sectional_peer_propagation_discovery_v1.isolation import OUT, write_overlap_n
from research.cross_sectional_peer_propagation_discovery_v1.outcomes import trading_path
from research.cross_sectional_peer_propagation_discovery_v1.publish import SHEET_ORDER
from research.cross_sectional_peer_propagation_discovery_v1.spec import source_sha256
from research.cross_sectional_peer_propagation_discovery_v1.walk import emit_events, harvest_d1_metrics
from research.support_resistance_first_interaction_matched_causal_test_v1.freeze import detector_sha256, state_machine_sha256


def test_hashes_and_closed_paths():
    assert detector_sha256() == EXPECTED_DETECTOR_SHA256
    assert state_machine_sha256() == EXPECTED_STATE_MACHINE_SHA256
    assert PARENT_VERDICT == "DIRECTION_ALIGNED_CONTEXT_PARTIAL_MECHANISM_V1"
    assert PARENT_NEXT == "STOP_NATIVE_DIRECTION_ALIGNED_CONTEXT_STACK_V1"
    assert R13_R11_R14_REPAIRED is False
    assert THRESHOLD_RETUNED is False
    assert PNL_OPTIMIZATION is False
    assert PEER_MIN_N == 3
    assert TV_ELEVATED_PCTL == 0.50
    assert source_sha256()
    assert len(source_sha256()) == 64


def test_target_excluded_from_peer_median():
    vals = np.array([0.01, 0.02, 0.03, 0.40], dtype=float)
    loo = leave_one_out_median(vals)
    assert abs(float(loo[3]) - float(np.median(vals[:3]))) < 1e-12
    st = peer_stats(vals, np.array([0.1, 0.2, 0.9, 0.4]), include_mask=np.ones(4, dtype=bool))
    assert int(st["target_in_peer_n"].sum()) == 0
    assert int(st["peer_n"][0]) == 3


def test_trading_minute_return_skips_index_holes():
    close = np.array([100.0, 101.0, 102.0, 103.0])
    session_idx = [0, 1, 2, 3]
    assert abs(sess_ret(close, session_idx, 3, 3) - 0.03) < 1e-9


def test_same_bar_not_in_path():
    rec = {
        "t": ["10:00", "10:01", "10:02"],
        "h": np.array([1.0, 2.0, 2.0]),
        "l": np.array([1.0, 1.0, 1.0]),
        "c": np.array([1.0, 1.5, 1.2]),
        "n": 3,
    }
    path = trading_path(rec, [0, 1, 2], 0, 1)
    assert path["same_bar_outcome"] is False
    src = inspect.getsource(trading_path)
    assert "range(1," in src


def test_no_displacement_or_indicator_zoo():
    src = inspect.getsource(harvest_d1_metrics) + inspect.getsource(emit_events) + inspect.getsource(build_report_body)
    assert "displacement(" not in src
    assert "profit_factor" not in src.lower()
    assert "RandomForest" not in src
    assert "RSI" not in src
    assert "MACD" not in src
    assert "Bollinger" not in src
    for date in ("20251127", "20260422", "20260911"):
        assert date not in inspect.getsource(emit_events)


def test_p_flags_need_high_lag():
    freeze = {"high": {"peer_ret_3m": 0.01, "peer_breadth_3m": 0.6, "peer_minus_target_3m": 0.005, "peer_tv_breadth": 0.4}}
    row = {"peer_n": 5, "peer_ret_3m": 0.02, "peer_breadth_3m": 0.8, "peer_minus_target_3m": 0.001, "peer_tv_breadth": 0.9}
    f = p_flags(row, freeze)
    assert f["P1"] is False
    assert f["P2"] is False
    assert f["P3"] is False
    row["peer_minus_target_3m"] = 0.01
    f2 = p_flags(row, freeze)
    assert f2["P1"] is True
    assert f2["P2"] is True
    assert f2["P3"] is True


def test_verdicts_and_sheets():
    assert CASE_FOUND == "PEER_PROPAGATION_MECHANISM_FOUND_V1"
    assert CASE_PARTIAL == "PEER_PROPAGATION_PARTIAL_LEAD_V1"
    assert CASE_SIM == "PEER_MOVE_SIMULTANEOUS_NOT_PREDICTIVE_V1"
    assert CASE_NONE == "PEER_PROPAGATION_NO_INCREMENTAL_INFORMATION_V1"
    assert CASE_CONTROL == "PEER_CONTROL_DESIGN_INSUFFICIENT_V1"
    assert NEXT_RCA == "PEER_PROPAGATION_MECHANISM_RCA_V1"
    assert NEXT_STOP == "STOP_CROSS_SECTIONAL_PEER_PROPAGATION_V1"
    assert SHEET_ORDER[0] == "Binding"
    assert "Peer_Exclusion" in SHEET_ORDER
    assert "Offset_Map" in SHEET_ORDER
    assert "Safety" in SHEET_ORDER
    d = decide({"hard_ids": ["P1"], "partial_ids": [], "simultaneous_not_true_lead": False, "control_insufficient": False})
    assert d["VERDICT"] == CASE_FOUND
    assert d["NEXT"] == NEXT_RCA
    d2 = decide({"hard_ids": [], "partial_ids": [], "simultaneous_not_true_lead": False, "control_insufficient": False})
    assert d2["VERDICT"] == CASE_NONE
    assert write_overlap_n("", "") == 0
    assert "cross_sectional_peer_propagation_discovery_v1" in str(OUT).replace("\\", "/")
