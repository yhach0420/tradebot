"""Tests for PB1 causal-path failure RCA V1. No PnL. Eligibility unchanged."""
from __future__ import annotations

import inspect

from research.pb1_causal_path_failure_rca_v1 import (
    CASE_NONE,
    CASE_TRIGGER,
    EXPECTED_SETUP_N,
    NEXT_REDESIGN,
    NEXT_STOP,
    PARENT_PLAYBOOK_MACHINE_SHA256,
    PARENT_VERDICT,
    SAMPLE_N,
)
from research.pb1_causal_path_failure_rca_v1.analyze import decide, hierarchy
from research.pb1_causal_path_failure_rca_v1.classify import label_from_visible
from research.pb1_causal_path_failure_rca_v1.isolation import OUT, PARENT_OUT, write_overlap_n
from research.pb1_causal_path_failure_rca_v1.metrics import first_passage, risk_invalid_why
from research.pb1_causal_path_failure_rca_v1.publish import SHEET_ORDER
from research.pb1_causal_path_failure_rca_v1.spec import source_sha256
from research.pb1_opening_range_continuation_face_valid_v2.definitions import machine_sha256 as v2_machine_sha256


def test_parent_frozen_eligibility_not_retuned():
    assert PARENT_VERDICT == "PB1_CAUSAL_PATH_UNSTABLE_V1"
    assert PARENT_PLAYBOOK_MACHINE_SHA256 == "3ebe0220fd9cb1e5749e39833fa1bab0146759cce5dc9daaacf0e3b3d3daf3fd"
    assert v2_machine_sha256() == PARENT_PLAYBOOK_MACHINE_SHA256
    assert EXPECTED_SETUP_N == 465
    assert SAMPLE_N == 96
    assert source_sha256()
    assert write_overlap_n("", "") == 0
    assert "pb1_causal_path_failure_rca_v1" in str(OUT).replace("\\", "/")
    assert "pb1_opening_range_causal_path_test_v1" in str(PARENT_OUT).replace("\\", "/")
    assert SHEET_ORDER[0] == "Binding"
    ana = inspect.getsource(decide)
    assert "profit_factor" not in ana.lower()
    assert "occupancy" not in ana.lower()


def test_risk_invalid_and_first_passage_and_labels_ignore_future():
    assert risk_invalid_why(1, 100.0, 101.0, 100.5, 101.0, 100.4, ["FAILED_PUSH_THEN_CLOSE_BACK"]) == "entry_already_beyond_retest_extreme"
    assert risk_invalid_why(1, 101.0, 101.0, None, 101.0, 100.9, ["FAILED_PUSH_THEN_CLOSE_BACK"]) == "retest_extreme_not_representable"
    src = inspect.getsource(label_from_visible)
    assert "r10_bps" not in src
    assert "MFE_bps" not in src
    rec = {
        "session_idx": list(range(10)),
        "t": [f"09:{i:02d}" for i in range(10)],
        "h": [101.2] * 10,
        "l": [100.8] * 10,
        "c": [101.1] * 10,
        "o": [101.0] * 10,
    }
    fp = first_passage(rec, entry_pos=0, sign=1, entry=101.0, r_px=0.2, or_high=101.0, or_low=100.0, retest_high=101.3, retest_low=100.7)
    assert fp["risk_defined"] is True
    assert "plus_1_0R_before_fail" in fp


def test_no_edge_stops_otherwise_redesign():
    none = decide(
        True,
        True,
        {
            "rank": {"NO TRUE EDGE": "PRIMARY_CAUSE", "TRIGGER SEMANTICS MIXED": "NOT_SUPPORTED_CAUSE"},
            "flags": {"no_underlying_edge": True, "fp_beats_controls": False, "consumed": False, "mixed_triggers": False, "regime_after_reweight": False, "market_block_diff": False},
        },
    )
    assert none["VERDICT"] == CASE_NONE
    assert none["NEXT"] == NEXT_STOP
    mix = decide(
        True,
        True,
        {
            "rank": {"TRIGGER SEMANTICS MIXED": "PRIMARY_CAUSE"},
            "flags": {"no_underlying_edge": False, "fp_beats_controls": True, "consumed": False, "mixed_triggers": True, "regime_after_reweight": False, "market_block_diff": False},
        },
    )
    assert mix["VERDICT"] == CASE_TRIGGER
    assert mix["NEXT"] == NEXT_REDESIGN
    assert mix["STOP_PB1"] is False
    _ = hierarchy
