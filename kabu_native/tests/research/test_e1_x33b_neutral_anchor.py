"""E1_X33B neutral anchor tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

NATIVE = Path(__file__).resolve().parents[2]
OUT = NATIVE / "results" / "research" / "e1_x33b_neutral_anchor"


@pytest.fixture(scope="module")
def interim():
    p = OUT / "_interim.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    r = OUT / "report.json"
    if r.exists():
        return json.loads(r.read_text(encoding="utf-8"))
    pytest.skip("no interim")


@pytest.fixture(scope="module")
def report():
    r = OUT / "report.json"
    if not r.exists():
        pytest.skip("no report")
    return json.loads(r.read_text(encoding="utf-8"))


def test_x33_source_identity_resolution(report):
    from research.e1_x33b_neutral_anchor import CANONICAL_X33_RUN, REPORTED_RUN_ID_USER_TEXT
    xid = report.get("x33_identity") or {}
    assert xid.get("canonical_run_id") == CANONICAL_X33_RUN
    assert xid.get("reported_run_id") == REPORTED_RUN_ID_USER_TEXT
    assert xid.get("artifact_run_id") == CANONICAL_X33_RUN


def test_exact_fixed_clock_semantics(report):
    sem = report.get("neutral_anchor_exact_semantics") or {}
    assert sem.get("clock_grid_definition")
    assert "parent_fixed_clock" in str(sem.get("source_functions"))
    assert sem.get("no_performance_search") is True


def test_future_free_dependency(report):
    assert report.get("future_dependency") is False
    dep = report.get("dependency_manifest") or {}
    assert dep.get("uses_future_information") is False


def test_prefix_invariance(report):
    assert (report.get("prefix_invariance") or {}).get("status") == "PASS"


def test_same_symbol_pool(report):
    assert report.get("population_n") == 22491


def test_same_execution_contract(report):
    assert (report.get("neutral") or {}).get("episodes", 0) > 0
    assert (report.get("parent") or {}).get("episodes", 0) > 0


def test_symbol_session_balancing(report):
    assert "symbol_session_balanced_delta300" in report
    assert "ret300_balanced" in (report.get("neutral") or {})


def test_matched_parent_comparison(report):
    m = report.get("matched") or {}
    assert "delta300" in m and "delta600" in m


def test_day_level_neutrality(report):
    assert (report.get("day_level") or {}).get("days")


def test_coverage(report):
    assert "coverage_share" in (report.get("coverage") or {})


def test_time_of_day_coverage(report):
    assert (report.get("time_of_day_coverage") or {}).get("buckets")


def test_lodo(report):
    assert "lodo" in report


def test_loso(report):
    assert "loso" in report


def test_old_cluster_reference_only(report):
    assert report.get("old_cluster_reference_only") is True


def test_no_anchor_performance_search(interim):
    assert interim.get("no_anchor_performance_search") is True


def test_no_runtime_change(interim):
    assert interim.get("no_runtime_change") is True


def test_no_entry_search(interim):
    assert interim.get("no_entry_search") is True


def test_no_exit(interim):
    assert interim.get("no_exit") is True


def test_no_short(interim):
    assert interim.get("no_short") is True


def test_20260810_not_opened(interim):
    assert interim.get("opened_20260810") is False


def test_submit_cancel_live_zero(report):
    assert report.get("safety", {}).get("submit_cancel_live") == "0/0/0"


def test_ab_determinism(report):
    assert report.get("ab_determinism", {}).get("ok") is True
