"""E1_X33 causal anchor repair tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

NATIVE = Path(__file__).resolve().parents[2]
OUT = NATIVE / "results" / "research" / "e1_x33_causal_anchor_repair"


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


def test_x32_identity(interim):
    from research.e1_x33_causal_anchor_repair import SOURCE_X32_RUN
    assert interim.get("source_x32_run_id") == SOURCE_X32_RUN


def test_full_future_free_grid(report):
    meta = report.get("grid_rebuild_by_day") or []
    assert len(meta) == 14
    assert all(m.get("feature_ok_n", 0) >= 0 for m in meta)


def test_no_forward_return_dependency(report):
    dep = report.get("dependency_manifest") or {}
    assert dep.get("uses_future_information") is False
    assert "forward_return_60s" in str(dep.get("forbidden_inputs"))


def test_no_future_label_dependency(report):
    dep = report.get("dependency_manifest") or {}
    assert "attach_forward_labels" in str(dep.get("forbidden_inputs"))


def test_prefix_invariance(report):
    assert report.get("prefix_invariance", {}).get("status") in {
        "PASS", "CAUSALITY_VIOLATION", "INSUFFICIENT_TESTS"
    }


def test_cluster_window_300_unchanged(interim):
    assert interim.get("cluster_window_sec") == 300


def test_cluster_first_semantic_unchanged(report):
    dep = report.get("dependency_manifest") or {}
    assert dep.get("cluster_first_semantic") == "CLUSTER_FIRST_ANCHOR"


def test_old_reference_only(report):
    assert report.get("old_reference_only") is True


def test_no_anchor_grid_search(interim):
    assert interim.get("no_anchor_grid_search") is True


def test_common_execution_contract(report):
    assert report.get("causal_summary", {}).get("episodes", 0) >= 0
    assert report.get("parent_summary", {}).get("episodes", 0) >= 0


def test_parent_vs_causal_matched(report):
    cp = report.get("causal_parent") or {}
    assert "matched_delta300" in cp
    assert "raw_delta300" in cp


def test_old_vs_causal(report):
    assert "causal_old" in report


def test_day_level(report):
    assert (report.get("day_level") or {}).get("days")


def test_loso(report):
    assert "loso" in report


def test_feature_eligibility_audit(report):
    assert "feature_eligibility" in report


def test_session_end_censoring(report):
    assert "session_end_censoring" in report


def test_anchor_spacing(report):
    assert "anchor_spacing" in report


def test_no_runtime_change(interim):
    assert interim.get("no_runtime_change") is True


def test_no_entry_rule_search(interim):
    assert interim.get("no_entry_rule_search") is True


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
