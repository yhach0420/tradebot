"""FLEX MBO feasibility. No purchase. No strategy. No Holdout/future market data."""
from __future__ import annotations

import inspect

import pytest

from research.new_information_acquisition_feasibility_design_v1 import (
    CASE_C,
    COLLECTOR_IMPLEMENTED,
    HISTORICAL_DATA_DOWNLOADED,
    MARKET_DATA_PURCHASED,
    MICROTIMING_RESEARCH_ALLOWED,
    NETWORK_LATENCY_ALPHA_ALLOWED,
    NEXT_C,
    REQUIRED_PARENT_NEXT,
    REQUIRED_PARENT_VERDICT,
    REQUIRED_POLICY_SHA256,
    REQUIRED_SELECTED_SOURCE,
    RESEARCH_METHOD_FROZEN,
)
from research.new_information_acquisition_feasibility_design_v1.analyze import build_answers, build_report_body
from research.new_information_acquisition_feasibility_design_v1.date_exposure import build_ledger
from research.new_information_acquisition_feasibility_design_v1.protocol import event_order, protocol_messages
from research.new_information_acquisition_feasibility_design_v1.publish import SHEET_ORDER
from research.new_information_acquisition_feasibility_design_v1.spec import PARENT_REPORT, pin_parent
from research.simple_full_strategy_discovery_v1 import BURNED_HOLDOUT_DAYS, STRESS_DAYS


def test_parent_pin():
    parent = pin_parent()
    assert parent["ok"] is True
    assert parent["VERDICT"] == REQUIRED_PARENT_VERDICT
    assert parent["NEXT"] == REQUIRED_PARENT_NEXT
    assert parent["SELECTED_SOURCE_FAMILY_ID"] == REQUIRED_SELECTED_SOURCE
    assert parent["EXTERNAL_INFORMATION_SOURCE_POLICY_SHA256"] == REQUIRED_POLICY_SHA256
    assert MARKET_DATA_PURCHASED is False
    assert HISTORICAL_DATA_DOWNLOADED is False
    assert COLLECTOR_IMPLEMENTED is False
    assert RESEARCH_METHOD_FROZEN is False
    assert NETWORK_LATENCY_ALPHA_ALLOWED is False
    assert MICROTIMING_RESEARCH_ALLOWED is False


def test_protocol_gaps_case_c():
    proto = protocol_messages()
    ev = event_order()
    assert proto["MESSAGE_TYPES_EXACTLY_DOCUMENTED"] is False
    assert proto["CONNECTION_SPEC_PUBLICLY_OBTAINABLE"] is False
    assert ev["EVENT_ORDER_REPLAYABLE"] is False
    parent = pin_parent()
    body = build_report_body(parent=parent)
    d = body["decision"]
    assert d["CASE"] == "C"
    assert d["VERDICT"] == CASE_C
    assert d["NEXT"] == NEXT_C
    assert d["RESTART_ALLOWED"] is False
    a = build_answers(body)
    assert a["9_exact_MBO_message_types_documented"] is False
    assert a["16_causal_event_order_replayable"] is False
    assert a["26_historical_live_pair_feasible"] is False
    assert a["29_exposed_date_reuse_with_new_MBO_allowed"] is False
    assert a["32_network_latency_alpha_allowed"] is False
    assert a["35_replay_minimum_reaction_latency"] == "UNASSIGNED"
    assert a["65_all_feasibility_gates_pass"] is False
    assert a["66_market_data_purchased"] is False
    assert a["76_NEW_DEV_dates_assigned"] is False
    assert a["87_VERDICT"] == CASE_C


def test_date_ledger_no_prospective():
    led = build_ledger()
    assert led["EXPOSED_DATE_N"] >= 24
    assert "20260722" in led["EXPOSED_DATE_LIST"]
    assert "20260903" in led["EXPOSED_DATE_LIST"]
    assert all(d < "20260907" for d in led["EXPOSED_DATE_LIST"])
    assert led["EXPOSED_DAY_REUSE_WITH_NEW_SOURCE_ALLOWED"] is False
    assert led["CLEAN_HISTORICAL_PERIOD_EXISTS"] is True


def test_no_pnl_or_harvest():
    import research.new_information_acquisition_feasibility_design_v1.analyze as a
    import research.new_information_acquisition_feasibility_design_v1.__main__ as m

    src = inspect.getsource(a) + inspect.getsource(m)
    assert "harvest_development" not in src
    assert "evaluate_strategy" not in src
    assert "compute_pnl_yen_100" not in src


def test_sheet_order_and_sealed_paths():
    assert len(SHEET_ORDER) == 22
    assert SHEET_ORDER[0] == "answers"
    assert SHEET_ORDER[4] == "protocol_messages"
    assert SHEET_ORDER[-1] == "safety"
    s = str(PARENT_REPORT).replace("\\", "/")
    for d in BURNED_HOLDOUT_DAYS:
        assert d not in s
    for d in STRESS_DAYS:
        assert d not in s
    assert "20260907" not in s
    _ = pytest
