"""Source policy. No acquisition. No strategy. No Holdout/Stress/future market data."""
from __future__ import annotations

import inspect

import pytest

from research.new_information_source_policy_decision_v1 import (
    CASE_A,
    CONSOLIDATED_FEED_ID,
    EXTERNAL_MARKET_DATA_DOWNLOADED,
    GAP_CATEGORIES,
    NEW_STRATEGY_CREATED,
    NEXT_A,
    QUEUE_POSITION_ALPHA_POLICY_ELIGIBLE,
    REQUIRED_PARENT_NEXT,
    REQUIRED_PARENT_VERDICT,
    REQUIRED_PAUSE_PROTOCOL_SHA256,
)
from research.new_information_source_policy_decision_v1.analyze import build_answers, build_report_body, decide
from research.new_information_source_policy_decision_v1.documentation import source_family_rows, unique_policy_eligible
from research.new_information_source_policy_decision_v1.publish import SHEET_ORDER
from research.new_information_source_policy_decision_v1.spec import PARENT_REPORT, pin_parent
from research.simple_full_strategy_discovery_v1 import BURNED_HOLDOUT_DAYS, STRESS_DAYS


def test_parent_pin_and_no_acquisition():
    parent = pin_parent()
    assert parent["ok"] is True
    assert parent["VERDICT"] == REQUIRED_PARENT_VERDICT
    assert parent["NEXT"] == REQUIRED_PARENT_NEXT
    assert parent["PAUSE_PROTOCOL_SPEC_SHA256"] == REQUIRED_PAUSE_PROTOCOL_SHA256
    assert parent["RESTART_ALLOWED"] is False
    assert parent["STRATEGY_RESEARCH_STATUS"] == "PAUSED"
    assert NEW_STRATEGY_CREATED is False
    assert EXTERNAL_MARKET_DATA_DOWNLOADED is False
    assert QUEUE_POSITION_ALPHA_POLICY_ELIGIBLE is False
    assert len(GAP_CATEGORIES) == 6


def test_s1_s2_consolidated_case_a():
    rows = source_family_rows()
    ids = [r["SOURCE_FAMILY_ID"] for r in rows]
    assert ids == [
        "ORDER_LEVEL_ADD_CANCEL_EXECUTE",
        "EXCHANGE_NATIVE_EXECUTION_TAPE",
        "BROAD_MARKET_FUTURES_CONTEXT",
    ]
    assert rows[1]["INDEPENDENT_PRODUCT"] is False
    assert rows[1]["GATES"]["POLICY_ELIGIBLE"] is False
    eligible = unique_policy_eligible(rows)
    assert CONSOLIDATED_FEED_ID in eligible
    assert "BROAD_MARKET_FUTURES_CONTEXT" in eligible
    d = decide(rows)
    assert d["CASE"] == "A"
    assert d["VERDICT"] == CASE_A
    assert d["NEXT"] == NEXT_A
    assert d["SELECTED_SOURCE_FAMILY_ID"] == CONSOLIDATED_FEED_ID
    assert d["RESTART_ALLOWED"] is False
    assert d["DERIVABLE_FROM_CURRENT_CAPTURE"] is False
    assert d["EXTERNAL_INFORMATION_SOURCE_POLICY_SHA256"]
    body = build_report_body(parent=pin_parent())
    a = build_answers(body)
    assert a["8_NATIVE_AGGRESSOR_TRADE_SIDE_policy_eligible"] is False
    assert a["9_AUCTION_BEYOND_CAPTURED_FIELDS_policy_eligible"] is False
    assert a["10_QUEUE_POSITION_strategy_alpha_policy_eligible"] is False
    assert a["38_selected_information_derivable_from_current_Capture"] is False
    assert a["42_external_market_data_downloaded"] is False
    assert a["54_NEW_DEV_dates_assigned"] is False
    assert a["56_RESTART_ALLOWED"] is False
    assert a["66_VERDICT"] == CASE_A
    assert a["22_native_aggressor_side"]["ORDER_LEVEL_ADD_CANCEL_EXECUTE"] is None


def test_no_pnl_or_harvest_in_source():
    import research.new_information_source_policy_decision_v1.analyze as a
    import research.new_information_source_policy_decision_v1.__main__ as m
    import research.new_information_source_policy_decision_v1.documentation as d

    src = inspect.getsource(a) + inspect.getsource(m) + inspect.getsource(d)
    assert "harvest_development" not in src
    assert "evaluate_strategy" not in src
    assert "compute_pnl_yen_100" not in src
    assert "TOTAL_PNL" not in src


def test_sheet_order():
    assert SHEET_ORDER[0] == "answers"
    assert SHEET_ORDER[3] == "candidate_sources"
    assert SHEET_ORDER[4] == "official_documentation"
    assert SHEET_ORDER[-2] == "decision"
    assert SHEET_ORDER[-1] == "safety"
    assert len(SHEET_ORDER) == 15


def test_holdout_stress_future_not_in_read_paths():
    s = str(PARENT_REPORT).replace("\\", "/")
    for d in BURNED_HOLDOUT_DAYS:
        assert d not in s
    for d in STRESS_DAYS:
        assert d not in s
    assert "20260907" not in s
    _ = pytest
