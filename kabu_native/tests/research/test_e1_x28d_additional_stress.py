"""E1_X28D additional historical stress tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

NATIVE = Path(__file__).resolve().parents[2]
OUT = NATIVE / "results" / "research" / "e1_x28d_additional_stress"
X29 = NATIVE / "results" / "research" / "e1_x29_prospective"


@pytest.fixture(scope="module")
def interim():
    p = OUT / "_interim.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    r = OUT / "report.json"
    if r.exists():
        return json.loads(r.read_text(encoding="utf-8"))
    pytest.skip("no interim/report")


def test_source_identity(interim):
    from research.e1_x28d_additional_stress import (
        ASSIGNMENT_REGISTRY_SHA,
        BOARD_MAPPING_SHA,
        FAMILY_BASELINE_REGISTRY_SHA,
        LOGIC_MANIFEST_SHA,
        SEMANTIC_EXIT_REGISTRY_SHA,
        SOURCE_X28C,
    )
    assert interim.get("source_x28c_run_id") == SOURCE_X28C
    assert interim.get("logic_manifest_sha") == LOGIC_MANIFEST_SHA
    assert interim.get("assignment_registry_sha") == ASSIGNMENT_REGISTRY_SHA
    assert interim.get("semantic_exit_registry_sha") == SEMANTIC_EXIT_REGISTRY_SHA
    assert interim.get("family_baseline_registry_sha") == FAMILY_BASELINE_REGISTRY_SHA
    assert interim.get("board_mapping_sha") == BOARD_MAPPING_SHA


def test_phase0_no_performance_open():
    p0 = OUT / "_phase0.json"
    if not p0.exists():
        pytest.skip("phase0 not written")
    d = json.loads(p0.read_text(encoding="utf-8"))
    assert d.get("performance_blind") is True
    for day, info in (d.get("days") or {}).items():
        assert info.get("performance_metrics_computed") is False
        assert info.get("pnl_computed") is False
        assert info.get("return_computed") is False
        assert "candidate_signal_count" not in info


@pytest.mark.parametrize("day", ["20260805", "20260806", "20260807"])
def test_required_fields_day(day):
    p0 = OUT / "_phase0.json"
    if not p0.exists():
        pytest.skip("phase0 not written")
    d = json.loads(p0.read_text(encoding="utf-8"))
    info = d["days"][day]
    assert info.get("push_jsonl_dir_exists") is True
    assert info.get("board_usable_symbol_n", 0) > 0
    assert info.get("currentprice_symbol_n", 0) > 0


def test_required_fields_20260805():
    test_required_fields_day("20260805")


def test_required_fields_20260806():
    test_required_fields_day("20260806")


def test_required_fields_20260807():
    test_required_fields_day("20260807")


def test_old_x29_not_superseded_if_data_insufficient():
    from research.e1_x28d_additional_stress import OLD_X29_PRECOMMIT_SHA
    p0 = OUT / "_phase0.json"
    report = OUT / "report.json"
    if not p0.exists() or not report.exists():
        pytest.skip("missing artifacts")
    phase0 = json.loads(p0.read_text(encoding="utf-8"))
    rep = json.loads(report.read_text(encoding="utf-8"))
    if not phase0.get("all_days_sufficient"):
        assert rep.get("old_x29_status") == "MAINTAINED_NOT_SUPERSEDED"
        assert rep.get("old_x29_precommit_sha") == OLD_X29_PRECOMMIT_SHA
        assert (X29 / "precommit.json").exists()
        old = json.loads((X29 / "precommit.json").read_text(encoding="utf-8"))
        assert old.get("precommit_sha") == OLD_X29_PRECOMMIT_SHA
    else:
        # sufficient path: superseded marker exists but old file preserved
        assert (X29 / "precommit.json").exists()
        old = json.loads((X29 / "precommit.json").read_text(encoding="utf-8"))
        assert old.get("precommit_sha") == OLD_X29_PRECOMMIT_SHA


def test_specific_49_exact(interim):
    assert interim.get("specific_n") == 49 or (
        (interim.get("specific_cohort") or {}).get("n") == 49
    )


def test_family_118_exact(interim):
    assert interim.get("family_n") == 118 or (
        (interim.get("family_cohort") or {}).get("n") == 118
    )


def test_overlap_zero(interim):
    assert interim.get("overlap") == 0


def test_no_candidate_selection_change(interim):
    assert interim.get("no_candidate_selection_change") is True


def test_no_parameter_retune(interim):
    assert interim.get("no_parameter_retune") is True


def test_x28c_execution_contract_parity(interim):
    qc = interim.get("quote_contract") or {}
    assert qc.get("first_valid_ask") is True
    assert qc.get("first_valid_bid") is True


def test_first_valid_ask(interim):
    assert (interim.get("quote_contract") or {}).get("first_valid_ask") is True


def test_first_valid_bid(interim):
    assert (interim.get("quote_contract") or {}).get("first_valid_bid") is True


def test_qty100(interim):
    assert (interim.get("quote_contract") or {}).get("qty100") is True


def test_freshness(interim):
    assert (interim.get("quote_contract") or {}).get("freshness") is True


def test_special_quote(interim):
    assert (interim.get("quote_contract") or {}).get("special_quote") is True


def test_no_future_best(interim):
    assert (interim.get("quote_contract") or {}).get("no_future_best") is True


def test_same_session(interim):
    assert (interim.get("quote_contract") or {}).get("same_session") is True


def test_actual_hard_stop_count(interim):
    assert interim.get("actual_hard_stop_counted") is True


def test_near_stop_recovery(interim):
    assert interim.get("near_stop_recovery") is True


def test_lodo_3_days(interim):
    assert interim.get("lodo_3_days") is True


def test_loso(interim):
    assert interim.get("loso") is True


def test_candidate_balanced_view(interim):
    assert interim.get("candidate_balanced_view") is True


def test_cluster_balanced_view(interim):
    assert interim.get("cluster_balanced_view") is True


def test_program_decision_rule(interim):
    v = interim.get("program_decision_rule") or interim.get("verdict") or interim.get("program_decision")
    assert v in {
        "E1_X28D_ADDITIONAL_STRESS_SUPPORT_PRESENT",
        "E1_X28D_ADDITIONAL_STRESS_EVIDENCE_MIXED",
        "E1_X28D_CURRENT_LOGIC_SYSTEMIC_STRESS_FAILURE",
        "E1_X28D_SOURCE_DATA_INSUFFICIENT",
    }


def test_no_runtime_change(interim):
    assert interim.get("no_runtime_change") is True


def test_submit_cancel_live_zero(interim):
    assert interim.get("submit_cancel_live") == "0/0/0" or (
        (interim.get("safety") or {}).get("submit_cancel_live") == "0/0/0"
    )


def test_ab_determinism(interim):
    ab = interim.get("ab_determinism")
    assert ab is True or (isinstance(ab, dict) and ab.get("ok") is True)
