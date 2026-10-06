"""FLEX MBO connection-spec resolution. No account. No market data. No strategy."""
from __future__ import annotations

import inspect

import pytest

from research.flex_mbo_connection_spec_resolution_v1 import (
    ACCOUNT_CREATED,
    CASE_C,
    COLLECTOR_IMPLEMENTED,
    MARKET_DATA_PURCHASED,
    MICROTIMING_RESEARCH_ALLOWED,
    NETWORK_LATENCY_ALPHA_ALLOWED,
    NEXT_C,
    PROVIDER_CONTACTED,
    PUBLIC_WEB_PROTOCOL_RESOLUTION_EXPECTED,
    REQUIRED_FEASIBILITY_SPEC_SHA256,
    REQUIRED_PARENT_NEXT,
    REQUIRED_PARENT_VERDICT,
    REQUIRED_POLICY_SHA256,
    REQUIRED_SELECTED_SOURCE,
    RESEARCH_METHOD_FROZEN,
)
from research.flex_mbo_connection_spec_resolution_v1.analyze import build_answers, build_report_body, decide
from research.flex_mbo_connection_spec_resolution_v1.publish import SHEET_ORDER
from research.flex_mbo_connection_spec_resolution_v1.search import is_official_spec_filename
from research.flex_mbo_connection_spec_resolution_v1.spec import PARENT_REPORT, pin_parent
from research.simple_full_strategy_discovery_v1 import BURNED_HOLDOUT_DAYS, STRESS_DAYS


def test_parent_pin():
    parent = pin_parent()
    assert parent["ok"] is True
    assert parent["VERDICT"] == REQUIRED_PARENT_VERDICT
    assert parent["NEXT"] == REQUIRED_PARENT_NEXT
    assert parent["SELECTED_SOURCE_FAMILY_ID"] == REQUIRED_SELECTED_SOURCE
    assert parent["EXTERNAL_INFORMATION_SOURCE_POLICY_SHA256"] == REQUIRED_POLICY_SHA256
    assert parent["FEASIBILITY_SPEC_SHA256"] == REQUIRED_FEASIBILITY_SPEC_SHA256
    assert parent["PARENT_F8"] is False
    assert parent["PARENT_F9"] is False
    assert parent["PARENT_F12"] is False
    assert parent["PARENT_F13"] is False
    assert parent["RESTART_ALLOWED"] is False
    assert ACCOUNT_CREATED is False
    assert PROVIDER_CONTACTED is False
    assert MARKET_DATA_PURCHASED is False
    assert COLLECTOR_IMPLEMENTED is False
    assert RESEARCH_METHOD_FROZEN is False
    assert NETWORK_LATENCY_ALPHA_ALLOWED is False
    assert MICROTIMING_RESEARCH_ALLOWED is False
    assert PUBLIC_WEB_PROTOCOL_RESOLUTION_EXPECTED is False


def test_filename_classifier():
    assert is_official_spec_filename("FLEX Market by Order Specifications.pdf") is True
    assert is_official_spec_filename("FLEXConnectionSpecification.pdf") is True
    assert is_official_spec_filename("FLEX_MBO_Specifications.pdf") is True
    assert is_official_spec_filename("run_phase288_symbolspec_subscript_crash_fix.py") is False
    assert is_official_spec_filename("kabu_station_system_design.pdf") is False
    assert is_official_spec_filename("DESIGN.pdf") is False
    assert is_official_spec_filename("FLEX_Standard_10level.pdf") is False


def test_case_c_when_no_local_specs():
    parent = pin_parent()
    empty = {
        "LOCAL_CANDIDATE_DOC_N": 0,
        "ELIGIBLE_OFFICIAL_SPEC_N": 0,
        "DOCUMENT_IDS": [],
        "DOCUMENT_VERSIONS": [],
        "DOCUMENT_DATES": [],
        "DOCUMENT_HASHES": [],
        "CANDIDATES": [],
        "ELIGIBLE": [],
        "PUBLIC_WEB_SEARCHED": False,
    }
    d0 = decide(eligible_n=0)
    assert d0["CASE"] == "C"
    assert d0["VERDICT"] == CASE_C
    assert d0["NEXT"] == NEXT_C
    body = build_report_body(parent=parent, search=empty)
    d = body["decision"]
    assert d["CASE"] == "C"
    assert d["VERDICT"] == CASE_C
    assert d["NEXT"] == NEXT_C
    assert d["CONNECTION_SPEC_AVAILABLE"] is False
    assert d["PROTOCOL_RESOLUTION_POSSIBLE"] is False
    assert d["RESTART_ALLOWED"] is False
    a = build_answers(body)
    assert a["4_official_FLEX_specs_publicly_accessible"] is False
    assert a["5_authorized_official_spec_found_locally"] is False
    assert a["6_eligible_official_spec_N"] == 0
    assert a["10_connection_spec_available"] is False
    assert a["11_protocol_resolution_possible"] is False
    assert a["12_account_created"] is False
    assert a["13_provider_contacted"] is False
    assert a["30_EVENT_ORDER_REPLAYABLE"] is False
    assert a["34_F8"] is False
    assert a["35_F9"] is False
    assert a["37_network_latency_alpha_allowed"] is False
    assert a["38_microtiming_research_allowed"] is False
    assert a["42_market_data_downloaded"] is False
    assert a["46_NEW_DEV_dates_assigned"] is False
    assert a["48_20260907_plus_read"] is False
    assert a["50_RESTART_ALLOWED"] is False
    assert a["57_VERDICT"] == CASE_C
    assert a["58_NEXT"] == NEXT_C


def test_no_pnl_or_harvest():
    import research.flex_mbo_connection_spec_resolution_v1.analyze as a
    import research.flex_mbo_connection_spec_resolution_v1.__main__ as m
    import research.flex_mbo_connection_spec_resolution_v1.search as s

    src = inspect.getsource(a) + inspect.getsource(m) + inspect.getsource(s)
    assert "harvest_development" not in src
    assert "evaluate_strategy" not in src
    assert "compute_pnl_yen_100" not in src
    assert "create_account" not in src
    assert "mailto:" not in src


def test_sheet_order_and_sealed_paths():
    assert len(SHEET_ORDER) == 19
    assert SHEET_ORDER[0] == "answers"
    assert SHEET_ORDER[4] == "local_spec_search"
    assert SHEET_ORDER[15] == "F8_F9"
    assert SHEET_ORDER[-1] == "safety"
    s = str(PARENT_REPORT).replace("\\", "/")
    for d in BURNED_HOLDOUT_DAYS:
        assert d not in s
    for d in STRESS_DAYS:
        assert d not in s
    assert "20260907" not in s
    _ = pytest
