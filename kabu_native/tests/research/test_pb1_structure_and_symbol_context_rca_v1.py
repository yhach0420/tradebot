"""Tests for PB1 structure and symbol-context RCA V1. No PnL. Eligibility unchanged."""
from __future__ import annotations

import inspect

from research.pb1_opening_range_continuation_face_valid_v2.definitions import machine_sha256 as v2_machine_sha256
from research.pb1_structure_and_symbol_context_rca_v1 import (
    CASE_MAPPED,
    EXPECTED_SETUP_N,
    NEXT_REDESIGN,
    PARENT_PLAYBOOK_MACHINE_SHA256,
    PARENT_VERDICT,
    SAMPLE_N,
)
from research.pb1_structure_and_symbol_context_rca_v1.analyze import decide
from research.pb1_structure_and_symbol_context_rca_v1.baseline import commit_day, new_baselines, snapshot
from research.pb1_structure_and_symbol_context_rca_v1.behavior import describe, new_history
from research.pb1_structure_and_symbol_context_rca_v1.classify import label_from_visible
from research.pb1_structure_and_symbol_context_rca_v1.isolation import OUT, PARENT_OUT, write_overlap_n
from research.pb1_structure_and_symbol_context_rca_v1.publish import SHEET_ORDER
from research.pb1_structure_and_symbol_context_rca_v1.spec import source_sha256
from research.pb1_structure_and_symbol_context_rca_v1.structure import classify_structure, level_zone


def _z(name: str, role: str, px: float, atr: float = 10.0) -> dict:
    z = level_zone(name, role, px, atr)
    assert z is not None
    return z


def test_frozen_identity_and_no_retune():
    assert PARENT_VERDICT == "PB1_TRIGGER_SEMANTICS_MIXED_V1"
    assert PARENT_PLAYBOOK_MACHINE_SHA256 == "3ebe0220fd9cb1e5749e39833fa1bab0146759cce5dc9daaacf0e3b3d3daf3fd"
    assert v2_machine_sha256() == PARENT_PLAYBOOK_MACHINE_SHA256
    assert EXPECTED_SETUP_N == 465
    assert SAMPLE_N == 96
    assert source_sha256()
    assert write_overlap_n("", "") == 0
    assert "pb1_structure_and_symbol_context_rca_v1" in str(OUT).replace("\\", "/")
    assert "pb1_causal_path_failure_rca_v1" in str(PARENT_OUT).replace("\\", "/")
    assert SHEET_ORDER[0] == "Binding"
    src = inspect.getsource(decide)
    assert "profit_factor" not in src.lower()
    assert "occupancy" not in src.lower()
    assert NEXT_REDESIGN == "PB1_PLAYBOOK_REDESIGN_V3"


def test_structural_classes_entry_time_only():
    atr = 10.0
    entry = 100.0
    into = classify_structure(
        sign=1,
        entry=entry,
        or_high=99.0,
        or_low=97.0,
        break_close=99.2,
        retest_high=99.4,
        retest_low=98.8,
        trigger_close=99.5,
        held=True,
        atr=atr,
        zones=[_z("PDH", "RESISTANCE", 101.0, atr)],
        r_px=1.0,
        med_1m=0.2,
        med_or15=2.0,
        or_width=2.0,
    )
    assert into["structural_class"] == "BREAK_INTO_RESISTANCE"
    through = classify_structure(
        sign=1,
        entry=102.0,
        or_high=99.0,
        or_low=97.0,
        break_close=101.6,
        retest_high=102.2,
        retest_low=101.4,
        trigger_close=101.8,
        held=True,
        atr=atr,
        zones=[_z("PDH", "RESISTANCE", 100.0, atr)],
        r_px=1.0,
        med_1m=0.2,
        med_or15=2.0,
        or_width=2.0,
    )
    assert through["structural_class"] in ("BREAK_THROUGH_RESISTANCE", "RESISTANCE_TO_SUPPORT_FLIP")
    flip = classify_structure(
        sign=1,
        entry=101.6,
        or_high=99.0,
        or_low=97.0,
        break_close=101.5,
        retest_high=101.8,
        retest_low=100.0,
        trigger_close=101.4,
        held=True,
        atr=atr,
        zones=[_z("PDH", "RESISTANCE", 100.0, atr)],
        r_px=1.0,
        med_1m=0.2,
        med_or15=2.0,
        or_width=2.0,
    )
    assert flip["flip_class"] == "CLEAN_FLIP"
    assert flip["structural_class"] == "RESISTANCE_TO_SUPPORT_FLIP"
    opened = classify_structure(
        sign=1,
        entry=100.0,
        or_high=99.0,
        or_low=97.0,
        break_close=99.2,
        retest_high=99.5,
        retest_low=98.8,
        trigger_close=99.4,
        held=True,
        atr=atr,
        zones=[_z("PDH", "RESISTANCE", 130.0, atr)],
        r_px=1.0,
        med_1m=0.2,
        med_or15=2.0,
        or_width=2.0,
    )
    assert opened["structural_class"] == "OPEN_SPACE_BREAK"
    assert opened["room_class"] == "NO_KNOWN_RESISTANCE_AHEAD" or opened["room_class"] == "HAS_SPACE"
    cong = classify_structure(
        sign=1,
        entry=100.0,
        or_high=99.0,
        or_low=97.0,
        break_close=99.2,
        retest_high=99.5,
        retest_low=98.8,
        trigger_close=99.4,
        held=True,
        atr=atr,
        zones=[_z("PDH", "RESISTANCE", 101.0, atr), _z("D5H", "RESISTANCE", 104.5, atr)],
        r_px=1.0,
        med_1m=0.2,
        med_or15=2.0,
        or_width=2.0,
    )
    assert cong["structural_class"] == "STRUCTURALLY_CONGESTED"
    src = inspect.getsource(label_from_visible)
    assert "r10_bps" not in src
    assert "MFE_bps" not in src


def test_baselines_use_prior_only_and_decide_maps_to_redesign():
    base = new_baselines()
    rec = {
        "t": ["09:00", "09:01"],
        "h": [101.0, 101.2],
        "l": [100.0, 100.1],
        "c": [100.5, 100.8],
        "o": [100.2, 100.5],
    }
    before = snapshot(base, clock="09:01", atr=1.0)
    assert before["median_1m_range_tod"] is None
    commit_day(base, rec=rec, session_idx=[0, 1], or_width=2.0, tv0915=1e8, gap_atr=0.2, atr=1.0)
    after = snapshot(base, clock="09:01", atr=1.0)
    assert after["median_1m_range_tod"] is not None
    hist = new_history()
    desc = describe(hist, atr_pctl=0.5, med_tv=1.0)
    assert desc["archetype"] == "INSUFFICIENT_HISTORY"
    assert desc["return_based"] is False or desc.get("pb1_outcome_used") in (False, None)
    mapped = decide(True, True, {"PRIMARY_CAUSE": "TRIGGER_SEMANTICS_MIXED"})
    assert mapped["VERDICT"] == CASE_MAPPED
    assert mapped["NEXT"] == NEXT_REDESIGN
    assert mapped["eligibility_changed"] is False
    assert mapped["redesign_from_trigger_rca_alone"] is False
    bind_fail = decide(False, True, {})
    assert bind_fail["VERDICT"] != CASE_MAPPED
