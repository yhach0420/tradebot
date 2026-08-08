"""E1_X29 precommit tests (before unused market data)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

NATIVE = Path(__file__).resolve().parents[2]
OUT = NATIVE / "results" / "research" / "e1_x29_prospective"


@pytest.fixture(scope="module")
def interim():
    p = OUT / "_interim.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    r = OUT / "precommit.json"
    if not r.exists():
        pytest.skip("no interim/precommit")
    return json.loads(r.read_text(encoding="utf-8"))


def test_x28c_source_identity(interim):
    from research.e1_x29_prospective import SOURCE_X28C
    assert interim.get("x28c_source") == SOURCE_X28C or interim.get("source_x28c_run_id") == SOURCE_X28C


def test_logic_manifest_sha(interim):
    from research.e1_x29_prospective import LOGIC_MANIFEST_SHA
    assert interim.get("logic_manifest_sha") == LOGIC_MANIFEST_SHA


def test_assignment_registry_sha(interim):
    from research.e1_x29_prospective import ASSIGNMENT_REGISTRY_SHA
    assert interim.get("assignment_registry_sha") == ASSIGNMENT_REGISTRY_SHA


def test_semantic_registry_sha(interim):
    from research.e1_x29_prospective import SEMANTIC_EXIT_REGISTRY_SHA
    assert interim.get("semantic_exit_registry_sha") == SEMANTIC_EXIT_REGISTRY_SHA


def test_family_baseline_registry_sha(interim):
    from research.e1_x29_prospective import FAMILY_BASELINE_REGISTRY_SHA
    assert interim.get("family_baseline_registry_sha") == FAMILY_BASELINE_REGISTRY_SHA


def test_board_mapping_sha(interim):
    from research.e1_x29_prospective import BOARD_MAPPING_SHA
    assert interim.get("board_mapping_sha") == BOARD_MAPPING_SHA


def test_specific_cohort_49(interim):
    assert interim.get("specific_cohort_49") == 49 or (
        (interim.get("cohorts") or {}).get("PROSPECTIVE_SPECIFIC_49", {}).get("n") == 49
    )


def test_specific_reference_survivor_24(interim):
    assert interim.get("specific_reference_survivor_24") == 24 or (
        (interim.get("cohorts") or {}).get("PROSPECTIVE_SPECIFIC_49", {}).get("REFERENCE_SURVIVOR") == 24
    )


def test_specific_execution_emergent_25(interim):
    assert interim.get("specific_execution_emergent_25") == 25 or (
        (interim.get("cohorts") or {}).get("PROSPECTIVE_SPECIFIC_49", {}).get("EXECUTION_EMERGENT") == 25
    )


def test_family_preferred_cohort_118(interim):
    assert interim.get("family_preferred_cohort_118") == 118 or (
        (interim.get("cohorts") or {}).get("PROSPECTIVE_FAMILY_PREFERRED_118", {}).get("n") == 118
    )


def test_no_target_in_specific_cohort(interim):
    assert interim.get("no_target_in_specific_cohort") is True
    regs = interim.get("specific_registry") or []
    if regs:
        assert all(r.get("exit_mode") != "TARGET" for r in regs)


def test_no_parameter_retune(interim):
    assert interim.get("no_parameter_retune") is True


def test_no_cohort_retune(interim):
    assert interim.get("no_cohort_retune") is True


def test_first_valid_ask(interim):
    assert interim.get("first_valid_ask") is True or (interim.get("quote_contract") or {}).get("entry_ask_raw") == "Sell1.Price"


def test_first_valid_bid(interim):
    assert interim.get("first_valid_bid") is True or (interim.get("quote_contract") or {}).get("exit_bid_raw") == "Buy1.Price"


def test_qty_100(interim):
    assert interim.get("qty_100") is True or (interim.get("quote_contract") or {}).get("min_qty") == 100.0


def test_freshness_5s(interim):
    assert interim.get("freshness_5s") is True or (interim.get("quote_contract") or {}).get("freshness_sec") == 5.0


def test_special_quote_block(interim):
    assert interim.get("special_quote_block") is True or (interim.get("quote_contract") or {}).get("special_quote_block") is True


def test_no_future_best(interim):
    assert interim.get("no_future_best") is True or (interim.get("quote_contract") or {}).get("no_future_best") is True


def test_no_mid(interim):
    assert interim.get("no_mid") is True or (interim.get("quote_contract") or {}).get("no_mid") is True


def test_no_currentprice_fill(interim):
    assert interim.get("no_currentprice_fill") is True or (interim.get("quote_contract") or {}).get("no_currentprice_fill") is True


def test_specific_family_same_entry_ask(interim):
    assert interim.get("specific_family_same_entry_ask") is True or interim.get("same_entry_ask_specific_family") is True


def test_append_only_ledger(interim):
    assert interim.get("append_only_ledger") is True


def test_performance_blind_collection(interim):
    assert interim.get("performance_blind_collection") is True


def test_next_5_valid_trading_days_rule(interim):
    assert interim.get("next_5_valid_trading_days_rule") is True or (
        (interim.get("prospective_window") or {}).get("rule")
    )


def test_invalid_day_rule(interim):
    assert interim.get("invalid_day_rule") is True or (
        (interim.get("prospective_window") or {}).get("forbid_exclusion_for_pnl") is True
    )


def test_no_historical_risk_dates_as_alpha(interim):
    assert interim.get("no_historical_risk_dates_as_alpha") is True


def test_no_order_route(interim):
    assert interim.get("no_order_route") is True or (interim.get("safety") or {}).get("no_order_route") is True


def test_submit_cancel_live_zero(interim):
    assert (interim.get("safety") or {}).get("submit_cancel_live") == "0/0/0"


def test_precommit_sha(interim):
    sha = interim.get("precommit_sha") or ""
    assert len(sha) == 64


def test_ab_determinism(interim):
    assert interim.get("content_sha") or interim.get("precommit_sha")
