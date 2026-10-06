"""Pause protocol. No new ENTRY/EXIT. No economics. No Holdout/Stress/future."""
from __future__ import annotations

import inspect

import pytest

from research.research_pause_and_data_requirements_redesign_v1 import (
    CASE_B,
    ENTRY_ONLY_METHOD_REVIVAL,
    FRESH_DATA_ONLY_RESTART_ALLOWED,
    FUTURE_DATASET_DATES_ASSIGNED,
    FUTURE_USE_REQUIRES_EXPLICIT_POLICY_CHANGE,
    INTERNAL_FOLD_N,
    LEGACY_DEV_DAYS,
    NEW_CANDIDATE_LIBRARY_CREATED,
    NEW_DEV_MIN_CALENDAR_WEEKS,
    NEW_DEV_MIN_VALID_DAYS,
    NEW_ECONOMIC_RUN,
    NEW_ENTRY_CREATED,
    NEW_EXIT_CREATED,
    NEW_EXTERNAL_DATA_ACQUIRED,
    NEW_MODEL_CREATED,
    NEW_STRATEGY_CREATED,
    NEXT_B,
    REQUIRED_CLOSED_ARCHITECTURE_N,
    REQUIRED_ELIGIBLE_METHOD_N,
    REQUIRED_METHOD_SPEC_SHA256,
    REQUIRED_PARENT_NEXT,
    REQUIRED_PARENT_VERDICT,
    TRUE_OOS_MIN_VALID_DAYS,
)
from research.research_pause_and_data_requirements_redesign_v1.analyze import build_answers, build_report_body, decide
from research.research_pause_and_data_requirements_redesign_v1.publish import SHEET_ORDER
from research.research_pause_and_data_requirements_redesign_v1.requirements import missing_information_categories
from research.research_pause_and_data_requirements_redesign_v1.spec import PARENT_REPORT, pin_parent
from research.simple_full_strategy_discovery_v1 import BURNED_HOLDOUT_DAYS, STRESS_DAYS


def test_parent_pin_and_no_strategy():
    parent = pin_parent()
    assert parent["ok"] is True
    assert parent["VERDICT"] == REQUIRED_PARENT_VERDICT
    assert parent["NEXT"] == REQUIRED_PARENT_NEXT
    assert parent["CLOSED_ARCHITECTURE_FAMILY_N"] == REQUIRED_CLOSED_ARCHITECTURE_N
    assert parent["ELIGIBLE_RESEARCH_METHOD_N"] == REQUIRED_ELIGIBLE_METHOD_N
    assert parent["SELECTED_METHOD_ID"] is None
    assert parent["REMAINING_RESEARCH_METHOD_SPEC_SHA256"] == REQUIRED_METHOD_SPEC_SHA256
    assert NEW_STRATEGY_CREATED is False
    assert NEW_ENTRY_CREATED is False
    assert NEW_EXIT_CREATED is False
    assert NEW_MODEL_CREATED is False
    assert NEW_CANDIDATE_LIBRARY_CREATED is False
    assert NEW_ECONOMIC_RUN is False
    assert NEW_EXTERNAL_DATA_ACQUIRED is False
    assert ENTRY_ONLY_METHOD_REVIVAL is False
    assert FRESH_DATA_ONLY_RESTART_ALLOWED is False
    assert FUTURE_DATASET_DATES_ASSIGNED is False
    assert FUTURE_USE_REQUIRES_EXPLICIT_POLICY_CHANGE is True
    assert LEGACY_DEV_DAYS[-1] == "20260807"
    assert int(NEW_DEV_MIN_VALID_DAYS) == 20
    assert int(NEW_DEV_MIN_CALENDAR_WEEKS) == 4
    assert int(INTERNAL_FOLD_N) == 5
    assert int(TRUE_OOS_MIN_VALID_DAYS) == 10


def test_case_b_restart_blocked():
    missing = missing_information_categories()
    d = decide(missing)
    assert d["CASE"] == "B"
    assert d["VERDICT"] == CASE_B
    assert d["NEXT"] == NEXT_B
    assert d["SELECTED_INFORMATION_CATEGORY_ID"] is None
    assert d["RESTART_ALLOWED"] is False
    assert d["RG3_new_causal_information_or_justified_premise"] is False
    assert d["RG4_method_capacity_frozen_before_outcomes"] is False
    assert d["RG7_future_use_policy_explicitly_permits"] is False
    body = build_report_body(parent=pin_parent())
    a = build_answers(body)
    assert a["5_legacy_DEV_usable_for_new_strategy_selection"] is False
    assert a["10_20260907_plus_assigned_to_NEW_DEV"] is False
    assert a["15_fresh_data_alone_sufficient"] is False
    assert a["19_selected_recommended_information_category_ID"] is None
    assert a["32_future_dataset_dates_assigned"] is False
    assert a["41_RESTART_ALLOWED"] is False
    assert a["42_new_strategy_created"] is False
    assert a["58_VERDICT"] == CASE_B
    assert a["59_NEXT"] == NEXT_B
    assert a["17_missing_information_category_N"] == 6


def test_no_pnl_or_harvest_in_source():
    import research.research_pause_and_data_requirements_redesign_v1.analyze as a
    import research.research_pause_and_data_requirements_redesign_v1.__main__ as m
    import research.research_pause_and_data_requirements_redesign_v1.requirements as r

    src = inspect.getsource(a) + inspect.getsource(m) + inspect.getsource(r)
    assert "harvest_development" not in src
    assert "evaluate_strategy" not in src
    assert "compute_pnl_yen_100" not in src
    assert "TOTAL_PNL" not in src


def test_sheet_order():
    assert SHEET_ORDER[0] == "answers"
    assert SHEET_ORDER[1] == "objective_alignment"
    assert SHEET_ORDER[2] == "parent"
    assert SHEET_ORDER[8] == "missing_information"
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
    assert "20260903" not in s
    _ = pytest
