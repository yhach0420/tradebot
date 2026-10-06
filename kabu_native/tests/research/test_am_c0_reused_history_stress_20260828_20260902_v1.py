"""AM C0 reused-history stress tests. No 20260903/04. No Runtime write."""
from __future__ import annotations

from research.am_c0_reused_history_stress_20260828_20260902_v1 import (
    CERTIFIED,
    DEVELOPMENT_DAYS,
    EXPECTED_C0_SPEC_SHA256,
    FORBIDDEN_INPUT_DAYS,
    LABEL,
    MAX_RESEARCH_DATE,
    MIN_AUGMENT_TRADE_N,
    PROSPECTIVE_HARVEST_SUSPENDED,
    STRESS_DAYS,
    TRUE_OOS,
)
from research.am_c0_reused_history_stress_20260828_20260902_v1.analyze import (
    CASE_A,
    CASE_B,
    CASE_C,
    CASE_D,
    CASE_E,
    decide,
)
from research.am_c0_reused_history_stress_20260828_20260902_v1.spec import live_c0_spec_sha256
from research.am_entry_architecture_final_reassessment import C0
from research.am_entry_profit_improvement import C14_ID, ELIGIBLE_DAYS, SESSION
from research.am_exit_research_final_decision import BEST_TESTED_EXIT_FOR_C0


def test_bounds_and_identity():
    assert MAX_RESEARCH_DATE == "20260902"
    assert FORBIDDEN_INPUT_DAYS == ("20260903", "20260904")
    assert STRESS_DAYS == ("20260828", "20260831", "20260901", "20260902")
    assert "20260903" not in STRESS_DAYS
    assert "20260907" not in STRESS_DAYS
    assert PROSPECTIVE_HARVEST_SUSPENDED is True
    assert TRUE_OOS is False
    assert CERTIFIED is False
    assert LABEL == "REUSED_HISTORY_STRESS"
    assert SESSION == "AM"
    assert C0 == "C0_B0_PRIMARY_B1_CONFIRM"
    assert BEST_TESTED_EXIT_FOR_C0 == "C14_ORIGINAL"
    assert C14_ID == "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14"
    assert tuple(ELIGIBLE_DAYS) == DEVELOPMENT_DAYS
    assert set(STRESS_DAYS).isdisjoint(set(DEVELOPMENT_DAYS))
    assert len(DEVELOPMENT_DAYS) == 18
    assert int(MIN_AUGMENT_TRADE_N) == 5


def test_c0_spec_sha_live():
    assert live_c0_spec_sha256() == EXPECTED_C0_SPEC_SHA256


def test_case_e_integrity():
    d = decide(integrity_ok=False, preservation_ok=True, gates={}, econ={"AUGMENT_TRADE_N": 10})
    assert d["CASE"] == "E"
    assert d["VERDICT"] == CASE_E
    assert d["NEW_ENTRY_FAMILY_DESIGN_ALLOWED"] is False


def test_case_e_preservation():
    d = decide(integrity_ok=True, preservation_ok=False, gates={}, econ={"AUGMENT_TRADE_N": 10})
    assert d["CASE"] == "E"
    assert d["VERDICT"] == CASE_E


def test_case_d_insufficient():
    d = decide(
        integrity_ok=True,
        preservation_ok=True,
        gates={},
        econ={"AUGMENT_TRADE_N": 4, "AUGMENT_NET": 100.0},
    )
    assert d["CASE"] == "D"
    assert d["VERDICT"] == CASE_D
    assert d["NEW_ENTRY_FAMILY_DESIGN_ALLOWED"] is False


def test_case_a_all_gates():
    gates = {
        "G3_AUGMENT_TRADE_N_GE_5": True,
        "G4_AUGMENT_PNL_GT_0": True,
        "G5_AUGMENT_PF_GT_1": True,
        "G6_OVERLAY_PNL_GT_CURRENT": True,
        "G7_OVERLAY_PF_GE_CURRENT": True,
        "G8_OVERLAY_MAXDD_NOT_WORSE": True,
        "G9_PAIRED_POS_GE_NEG": True,
    }
    d = decide(integrity_ok=True, preservation_ok=True, gates=gates, econ={"AUGMENT_TRADE_N": 6, "AUGMENT_NET": 1.0, "DELTA_NET": 1.0})
    assert d["CASE"] == "A"
    assert d["VERDICT"] == CASE_A
    assert d["C0_RESEARCH_PRIORITY_MAINTAINED"] is True
    assert d["NEW_ENTRY_FAMILY_DESIGN_ALLOWED"] is False


def test_case_c_augment_nonpositive():
    gates = {
        "G3_AUGMENT_TRADE_N_GE_5": True,
        "G4_AUGMENT_PNL_GT_0": False,
        "G5_AUGMENT_PF_GT_1": False,
        "G6_OVERLAY_PNL_GT_CURRENT": False,
        "G7_OVERLAY_PF_GE_CURRENT": False,
        "G8_OVERLAY_MAXDD_NOT_WORSE": False,
        "G9_PAIRED_POS_GE_NEG": False,
    }
    d = decide(
        integrity_ok=True,
        preservation_ok=True,
        gates=gates,
        econ={"AUGMENT_TRADE_N": 8, "AUGMENT_NET": -10.0, "DELTA_NET": -10.0},
    )
    assert d["CASE"] == "C"
    assert d["VERDICT"] == CASE_C
    assert d["NEW_ENTRY_FAMILY_DESIGN_ALLOWED"] is True
    assert d["C0_RESEARCH_PRIORITY_MAINTAINED"] is False


def test_case_b_mixed_positive_delta_pf_fail():
    gates = {
        "G3_AUGMENT_TRADE_N_GE_5": True,
        "G4_AUGMENT_PNL_GT_0": True,
        "G5_AUGMENT_PF_GT_1": True,
        "G6_OVERLAY_PNL_GT_CURRENT": True,
        "G7_OVERLAY_PF_GE_CURRENT": False,
        "G8_OVERLAY_MAXDD_NOT_WORSE": True,
        "G9_PAIRED_POS_GE_NEG": True,
    }
    d = decide(
        integrity_ok=True,
        preservation_ok=True,
        gates=gates,
        econ={"AUGMENT_TRADE_N": 8, "AUGMENT_NET": 50.0, "DELTA_NET": 50.0},
    )
    assert d["CASE"] == "B"
    assert d["VERDICT"] == CASE_B
    assert d["NEW_ENTRY_FAMILY_DESIGN_ALLOWED"] is False
    assert d["C0_RESEARCH_PRIORITY_MAINTAINED"] is True
