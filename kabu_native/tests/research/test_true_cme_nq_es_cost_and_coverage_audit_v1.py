"""True CME NQ/ES cost/coverage audit tests. Frozen Validation closed. No purchase. No Kabu 50."""
from __future__ import annotations

import inspect
from datetime import date

from research.true_cme_nq_es_cost_and_coverage_audit_v1 import (
    ACCOUNT_CREATION_PERFORMED,
    CASE_BLOCKED,
    CASE_EXISTING,
    CASE_HIGH,
    CASE_REVIEW,
    FROZEN_VALIDATION_OPENED,
    HKG_CHI_CLOSE,
    KABU_50_APPLIED,
    NEXT_FREE_IF_NOT_APPROVED,
    PARENT_VERDICT,
    PURCHASE_REQUESTED,
    TECH_FOCUS,
    TIMESERIES_GET_RANGE_CALLED,
)
from research.true_cme_nq_es_cost_and_coverage_audit_v1.analyze import decide
from research.true_cme_nq_es_cost_and_coverage_audit_v1.isolation import HKG_OUT, OUT, write_overlap_n
from research.true_cme_nq_es_cost_and_coverage_audit_v1.publish import SHEET_ORDER
from research.true_cme_nq_es_cost_and_coverage_audit_v1.source import front_contract_on, third_friday


def test_constants():
    assert PARENT_VERDICT == "HKG_CHINA_A50_SIMULTANEOUS_ONLY_V1"
    assert HKG_CHI_CLOSE == "NO_USABLE_CAUSAL_LEAD"
    assert FROZEN_VALIDATION_OPENED is False
    assert KABU_50_APPLIED is False
    assert PURCHASE_REQUESTED is False
    assert ACCOUNT_CREATION_PERFORMED is False
    assert TIMESERIES_GET_RANGE_CALLED is False
    assert NEXT_FREE_IF_NOT_APPROVED == "OIL_COMMODITY_DRIVER_RESPONSE_V1"
    assert OUT.name == "true_cme_nq_es_cost_and_coverage_audit_v1"
    assert HKG_OUT.name == "hkg_china_a50_driver_response_v1"
    assert write_overlap_n("", "") == 0
    assert SHEET_ORDER[0] == "Binding"
    assert SHEET_ORDER[-1] == "Safety"
    assert "Databento_Cost" in SHEET_ORDER
    assert "Contract_Roll_Design" in SHEET_ORDER
    assert "Paid_Data_Decision" in SHEET_ORDER
    assert len(TECH_FOCUS) == 13
    assert "6857" in TECH_FOCUS


def test_decide_and_calendar():
    blocked = decide(
        bind_ok=True,
        local_present=False,
        api_key_present=False,
        combined_usd="UNKNOWN_WITHOUT_DATABENTO_API_KEY",
        nq_usd="UNKNOWN_WITHOUT_DATABENTO_API_KEY",
        es_usd="UNKNOWN_WITHOUT_DATABENTO_API_KEY",
        timeseries_called=False,
        purchase=False,
        account_created=False,
    )
    assert blocked["VERDICT"] == CASE_BLOCKED
    assert blocked["ask_user_for_paid_acquisition_approval"] is False
    review = decide(
        bind_ok=True,
        local_present=False,
        api_key_present=True,
        combined_usd=12.0,
        nq_usd=6.0,
        es_usd=6.0,
        timeseries_called=False,
        purchase=False,
        account_created=False,
    )
    assert review["VERDICT"] == CASE_REVIEW
    high = decide(
        bind_ok=True,
        local_present=False,
        api_key_present=True,
        combined_usd=500.0,
        nq_usd=250.0,
        es_usd=250.0,
        timeseries_called=False,
        purchase=False,
        account_created=False,
    )
    assert high["VERDICT"] == CASE_HIGH
    existing = decide(
        bind_ok=True,
        local_present=True,
        api_key_present=False,
        combined_usd=None,
        nq_usd=None,
        es_usd=None,
        timeseries_called=False,
        purchase=False,
        account_created=False,
    )
    assert existing["VERDICT"] == CASE_EXISTING
    assert third_friday(2024, 9) == date(2024, 9, 20)
    assert front_contract_on("NQ", date(2024, 9, 16)) == "NQZ4"
    assert front_contract_on("ES", date(2025, 11, 26)) == "ESZ5"
    import research.true_cme_nq_es_cost_and_coverage_audit_v1.analyze as a
    import research.true_cme_nq_es_cost_and_coverage_audit_v1.source as s

    src_a = inspect.getsource(a)
    src_s = inspect.getsource(s)
    assert "import yfinance" not in src_a.lower()
    assert "import yfinance" not in src_s.lower()
    assert "get_range(" not in src_s
    assert "get_range(" not in src_a
    assert "grid_clocks(" not in src_a
    assert FROZEN_VALIDATION_OPENED is False
