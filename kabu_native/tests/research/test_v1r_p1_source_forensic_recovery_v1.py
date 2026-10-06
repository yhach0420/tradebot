"""P1 source forensic recovery tests. No 20260903/04. No main-tree restore."""
from __future__ import annotations

from research.v1r_p1_source_forensic_recovery_v1.analyze import decide
from research.v1r_p1_source_forensic_recovery_v1.harvest import match_want, summarize_searches
from research.v1r_p1_source_forensic_recovery_v1.spec import (
    EXPECTED_DUAL_SHA,
    EXPECTED_NATIVE_SHA,
    FORBIDDEN_INPUT_DAYS,
    MAX_RESEARCH_DATE,
    P1_FULL14_DAYS,
    PRIOR_CASE,
    PRIOR_VERDICT,
    PROSPECTIVE_HARVEST_SUSPENDED,
)


def test_bounds_and_expected_shas():
    assert MAX_RESEARCH_DATE == "20260902"
    assert FORBIDDEN_INPUT_DAYS == ("20260903", "20260904")
    assert PROSPECTIVE_HARVEST_SUSPENDED is True
    assert len(P1_FULL14_DAYS) == 14
    assert EXPECTED_NATIVE_SHA.startswith("e25285")
    assert EXPECTED_DUAL_SHA.startswith("810719")
    assert PRIOR_VERDICT == "V1R_FROZEN_REPLAY_INTEGRITY_FAILED"
    assert PRIOR_CASE == "E"


def test_match_want_empty():
    assert match_want(b"not the p1 source") is None


def test_summarize_requires_both_hits():
    parts = {
        "a": {"candidate_n": 3, "hash_match_n": 0, "rows": []},
        "b": {"candidate_n": 1, "hash_match_n": 0, "hits": []},
    }
    s = summarize_searches(parts)
    assert s["BYTE_EXACT_RECOVERED"] is False
    assert s["exact_sha_hit_n"] == 0


def test_case_a_byte_exact():
    d = decide(exact_native=True, exact_dual=True, material_n=9, calib={"ran": False}, leak_ok=True)
    assert d["CASE"] == "A"
    assert d["VERDICT"] == "V1R_P1_BYTE_EXACT_SOURCE_RECOVERED"
    assert d["extension_replay_allowed"] is True
    assert d["NEW_ENTRY_FAMILY_DESIGN_ALLOWED"] is False
    assert d["SIZING_RESEARCH_ALLOWED"] is False


def test_case_b_requires_zero_material_and_full14():
    calib = {"ran": True, "BEHAVIORAL_EQUIVALENCE": True}
    d = decide(exact_native=False, exact_dual=False, material_n=0, calib=calib, leak_ok=True)
    assert d["CASE"] == "B"
    assert d["BYTE_IDENTICAL"] is False
    assert d["BEHAVIORALLY_IDENTICAL_ON_P1_FULL14"] is True
    d2 = decide(exact_native=False, exact_dual=False, material_n=1, calib=calib, leak_ok=True)
    assert d2["CASE"] == "C"


def test_case_c_full14_fail():
    d = decide(
        exact_native=False,
        exact_dual=False,
        material_n=0,
        calib={"ran": True, "BEHAVIORAL_EQUIVALENCE": False},
        leak_ok=True,
    )
    assert d["CASE"] == "C"
    assert d["VERDICT"] == "V1R_P1_IMPLEMENTATION_RECOVERY_EXHAUSTED"
    assert d["NEW_ENTRY_FAMILY_DESIGN_ALLOWED"] is True
    assert d["extension_replay_allowed"] is False
    assert d["SIZING_RESEARCH_ALLOWED"] is False


def test_case_e_leak():
    d = decide(exact_native=True, exact_dual=True, material_n=0, calib={"ran": False}, leak_ok=False)
    assert d["CASE"] == "E"
    assert d["VERDICT"] == "FORENSIC_RECOVERY_INTEGRITY_FAILED"
