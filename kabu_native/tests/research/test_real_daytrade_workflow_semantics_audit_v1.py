"""Tests for the daytrade workflow semantics audit. No strategy. No PnL."""
from __future__ import annotations

import inspect

from research.real_daytrade_workflow_semantics_audit_v1 import (
    CASE_MISALIGNED,
    NEXT_FACE_VALID,
    PARENT_NEXT,
    PARENT_VERDICT,
    STANDARD_MA_FAMILY_EXHAUSTED,
    TESTED_MACHINE_FAILED,
)
from research.real_daytrade_workflow_semantics_audit_v1.analyze import build_report_body, decide
from research.real_daytrade_workflow_semantics_audit_v1.convention import convention_audit
from research.real_daytrade_workflow_semantics_audit_v1.isolation import OUT, write_overlap_n
from research.real_daytrade_workflow_semantics_audit_v1.matching import matching_audit
from research.real_daytrade_workflow_semantics_audit_v1.publish import SHEET_ORDER
from research.real_daytrade_workflow_semantics_audit_v1.spec import source_sha256
from research.real_daytrade_workflow_semantics_audit_v1.station import inspect_station
from research.real_daytrade_workflow_semantics_audit_v1.workflow import playbook_candidates


def test_parent_flags_and_no_exhaustion():
    assert PARENT_VERDICT == "MTF_5M_SMA5_25_75_PARTIAL_V1"
    assert PARENT_NEXT == "STOP_STANDARD_SMA5_25_75_PATH_V1"
    assert TESTED_MACHINE_FAILED is True
    assert STANDARD_MA_FAMILY_EXHAUSTED is False
    assert source_sha256()
    assert CASE_MISALIGNED == "REAL_DAYTRADE_WORKFLOW_SEMANTICS_MISALIGNED_V1"
    assert NEXT_FACE_VALID == "FACE_VALID_CANDIDATE_PLAYBOOKS_THEN_TEST_V1"


def test_daily_convention_not_intraday():
    c = convention_audit()
    assert c["daily_5_25_75_is_the_named_convention"] is True
    assert c["confused_daily_convention_with_intraday_bar_periods"] is True
    assert c["five_minute"]["sma75_on_5m_supported_as_daily_convention"] is False
    assert c["one_minute"]["belongs_in_our_common_market_playbook_as_structure"] is False


def test_matching_flags_pullback_returns():
    m = matching_audit()
    assert m["over_conditioned_on_setup_state"] is True
    names = [v["name"] for v in m["variables"]]
    assert any("target_ret_1m" in n for n in names)


def test_three_playbooks_and_sheets():
    pbs = playbook_candidates()
    assert len(pbs) == 3
    assert all(p.get("not_tested_here") for p in pbs)
    assert SHEET_ORDER[0] == "Binding"
    assert "Playbook_Candidates" in SHEET_ORDER
    assert SHEET_ORDER[-1] == "Safety"
    d = decide({"convention": convention_audit(), "sample_summary": {"would_trader_call_intended_pct": 0.0}})
    assert d["VERDICT"] == CASE_MISALIGNED
    assert d["new_strategy_run"] is False
    assert write_overlap_n("", "") == 0
    assert "real_daytrade_workflow_semantics_audit_v1" in str(OUT).replace("\\", "/")


def test_no_pnl_or_period_search_in_decision():
    src = inspect.getsource(decide) + inspect.getsource(build_report_body)
    assert "profit_factor" not in src.lower()
    assert "DecisionTree" not in src
    assert "SMA10" not in src
    st = inspect_station()
    assert "recovered" in st
