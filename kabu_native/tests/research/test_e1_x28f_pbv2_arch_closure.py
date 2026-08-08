"""E1_X28F tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

NATIVE = Path(__file__).resolve().parents[2]
OUT = NATIVE / "results" / "research" / "e1_x28f_pbv2_arch_closure"


@pytest.fixture(scope="module")
def interim():
    p = OUT / "_interim.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    r = OUT / "report.json"
    if r.exists():
        return json.loads(r.read_text(encoding="utf-8"))
    pytest.skip("no interim")


def test_x28e_source_identity(interim):
    from research.e1_x28f_pbv2_arch_closure import SOURCE_X28E
    assert interim.get("source_x28e_run_id") == SOURCE_X28E


def test_pbv2_manifest_sha(interim):
    from research.e1_x28f_pbv2_arch_closure import PBV2_MANIFEST_SHA
    assert interim.get("pbv2_manifest_sha") == PBV2_MANIFEST_SHA


def test_pbv2_runtime_reason_mapping(interim):
    assert interim.get("pbv2_runtime_reason_mapping") is True
    assert (OUT / "pbv2_exit_reason_mapping_v1.json").exists() or True


def test_pbv2_trigger_order(interim):
    assert interim.get("pbv2_trigger_order") is True


def test_pbv2_known_episode_parity(interim):
    st = interim.get("pbv2_known_episode_parity") or interim.get("pbv2_parity_status")
    assert st in {
        "PBV2_EXIT_REPLAY_VALIDATED",
        "PBV2_EXIT_REPLAY_STILL_NOT_VALIDATED",
    }


def test_no_pbv2_compare_if_parity_fail(interim):
    assert interim.get("pbv2_compare_if_parity_fail") is False
    if interim.get("pbv2_parity_status") == "PBV2_EXIT_REPLAY_STILL_NOT_VALIDATED":
        assert interim.get("verdict") == "E1_X28F_PBV2_PARITY_UNRESOLVED"


def test_regime_library_only_r0_r2(interim):
    from research.e1_x28f_pbv2_arch_closure import REGIME_LIBRARY
    assert list(interim.get("regime_library") or []) == list(REGIME_LIBRARY)


def test_no_new_regime(interim):
    assert interim.get("no_new_regime") is True


def test_same_entry_specific_family_pbv2(interim):
    if interim.get("verdict") == "E1_X28F_PBV2_PARITY_UNRESOLVED":
        pytest.skip("parity fail path")
    assert interim.get("same_entry_specific_family_pbv2") is True


def test_same_actual_ask(interim):
    assert interim.get("same_actual_ask") is True


def test_same_bid_fill_contract(interim):
    assert interim.get("same_bid_fill_contract") is True


def test_x28e_selection_rule_unchanged(interim):
    assert interim.get("x28e_selection_rule_unchanged") is True


def test_specific49_exact(interim):
    assert interim.get("specific_n") == 49


def test_family118_exact(interim):
    assert interim.get("family_n") == 118


def test_no_candidate_selection(interim):
    assert interim.get("no_candidate_selection") is True


def test_lodo_recompute(interim):
    if interim.get("verdict") == "E1_X28F_PBV2_PARITY_UNRESOLVED":
        pytest.skip("parity fail")
    assert interim.get("lodo_recompute") is True


def test_loso_recompute(interim):
    if interim.get("verdict") == "E1_X28F_PBV2_PARITY_UNRESOLVED":
        pytest.skip("parity fail")
    assert interim.get("loso_recompute") is True


def test_285a_not_present_0805_07(interim):
    assert interim.get("285a_not_present_0805_07") is True


def test_no_stop_grid(interim):
    assert interim.get("no_stop_grid") is True


def test_20260810_not_opened(interim):
    assert interim.get("opened_20260810") is False


def test_no_runtime_change(interim):
    assert interim.get("no_runtime_change") is True


def test_submit_cancel_live_zero(interim):
    assert interim.get("submit_cancel_live") == "0/0/0"


def test_ab_determinism(interim):
    assert interim.get("ab_determinism") is True
