"""Method audit. No new ENTRY/EXIT. No economics. No Holdout."""
from __future__ import annotations

import inspect

import pytest

from research.existing_data_strategy_research_stop_reassessment_v1 import (
    FDG_RESCUE,
    NEW_ECONOMIC_RUN,
    NEW_ENTRY_CREATED,
    NEW_EXIT_CREATED,
    NEW_STRATEGY_CREATED,
    THRESHOLD_SEARCH,
)
from research.existing_data_strategy_research_stop_reassessment_v1.analyze import bounded_data_driven_eligibility, decide, run_eligibility
from research.existing_data_strategy_research_stop_reassessment_v1.inventory import architecture_inventory, method_taxonomy
from research.existing_data_strategy_research_stop_reassessment_v1.publish import SHEET_ORDER
from research.existing_data_strategy_research_stop_reassessment_v1.spec import pin_fdg, pin_object
from research.simple_full_strategy_discovery_v1 import BURNED_HOLDOUT_DAYS, STRESS_DAYS


def test_pins_and_no_strategy_design():
    fdg = pin_fdg()
    obj = pin_object()
    assert fdg["ok"] is True
    assert obj["ok"] is True
    assert obj["RAW_OBJECT_N"] == 13
    assert obj["UNDEREXPLORED_CAUSAL_OBJECT_N"] == 1
    assert obj["selected"] == "FULL_DEPTH_GEOMETRY"
    assert FDG_RESCUE is False
    assert NEW_ECONOMIC_RUN is False
    assert NEW_STRATEGY_CREATED is False
    assert NEW_ENTRY_CREATED is False
    assert NEW_EXIT_CREATED is False
    assert THRESHOLD_SEARCH is False


def test_architecture_families_closed_and_no_reopen_list():
    rows = architecture_inventory()
    ids = {r["ARCHITECTURE_ID"] for r in rows}
    for need in (
        "SIMPLE_FULL",
        "SIMPLE_TECH",
        "SYSTEMATIC_STATE_TRANSITION",
        "RECOVERY_SEQUENCE",
        "PARTICIPATION_ONSET",
        "C1_MULTI_TIMEFRAME",
        "C4_PORTFOLIO_CROWDING",
        "CSB",
        "V4_RESISTANCE_EPISODE",
        "PULLBACK_FAMILY",
        "PFQ",
        "OR",
        "DYNAMIC_ANCHOR",
        "BREAKOUT",
        "FAILED_BREAKDOWN_RECLAIM",
        "VWAP_RECLAIM_REJECTION",
        "IOAR",
        "UEIA",
        "RPFE",
        "E1_X14",
        "FDG_STATIC_SPAN",
        "FDG_RELATIVE_MIGRATION",
    ):
        assert need in ids
        assert all(r["CURRENTLY_CLOSED"] is True for r in rows)


def test_methods_m1_m10_and_bounded_not_auto_ml():
    ms = method_taxonomy()
    assert [m["METHOD_ID"] for m in ms] == [f"M{i}" for i in range(1, 11)]
    cand = bounded_data_driven_eligibility()
    assert cand["ALREADY_EXECUTED_EQUIVALENT"] is False
    assert cand["ELIGIBLE"] is False
    assert "R9_capacity_bounded_before_outcomes" in cand["FAILED_GATES"]
    elig = run_eligibility()
    assert elig["ELIGIBLE_RESEARCH_METHOD_N"] == 0
    d = decide(elig)
    assert d["CASE"] == "B"
    assert d["JUSTIFIED_RESEARCH_METHOD_SPACE_EXHAUSTED"] is True


def test_no_pnl_in_analyze_source():
    import research.existing_data_strategy_research_stop_reassessment_v1.analyze as a
    import research.existing_data_strategy_research_stop_reassessment_v1.__main__ as m

    src = inspect.getsource(a) + inspect.getsource(m)
    assert "harvest_development" not in src
    assert "evaluate_strategy" not in src
    assert "compute_pnl_yen_100" not in src


def test_sheet_order():
    assert SHEET_ORDER[0] == "answers"
    assert SHEET_ORDER[2] == "current_closure"
    assert SHEET_ORDER[-1] == "safety"
    assert len(SHEET_ORDER) == 12


def test_holdout_stress_not_in_read_list():
    from research.existing_data_strategy_research_stop_reassessment_v1.spec import FDG_REPORT, OBJECT_REPORT

    for p in (FDG_REPORT, OBJECT_REPORT):
        s = str(p).replace("\\", "/")
        for d in BURNED_HOLDOUT_DAYS:
            assert d not in s
        for d in STRESS_DAYS:
            assert d not in s
        assert "20260907" not in s
    _ = pytest
