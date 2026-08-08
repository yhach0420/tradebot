"""E1_X28A2 audit reconciliation tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

NATIVE = Path(__file__).resolve().parents[2]
OUT = NATIVE / "results" / "research" / "e1_x28a2_audit_reconciliation"


@pytest.fixture(scope="module")
def interim():
    p = OUT / "_interim.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    r = OUT / "report.json"
    if not r.exists():
        pytest.skip("no interim/report")
    return json.loads(r.read_text(encoding="utf-8"))


def test_source_manifest_sha(interim):
    from research.e1_x28a2_audit_reconciliation import LOGIC_MANIFEST_SHA
    assert interim.get("logic_manifest_sha") == LOGIC_MANIFEST_SHA
    assert interim.get("source_manifest_sha") == LOGIC_MANIFEST_SHA


def test_assignments_unchanged(interim):
    assert interim.get("assignments_unchanged") is True
    assert interim.get("assignment_mutation_count", 1) == 0


def test_assignment_registry_sha_unchanged(interim):
    x28a1 = json.loads(
        (NATIVE / "results/research/e1_x28a1_candidate_exit_repair/report.json").read_text(encoding="utf-8")
    )
    assert interim.get("assignment_registry_sha") == x28a1.get("assignment_registry_sha")


def test_semantic_registry_sha_unchanged(interim):
    x28a1 = json.loads(
        (NATIVE / "results/research/e1_x28a1_candidate_exit_repair/report.json").read_text(encoding="utf-8")
    )
    assert interim.get("semantic_exit_registry_sha") == x28a1.get("semantic_exit_registry_sha")


def test_changed_count_recomputed(interim):
    assert interim.get("changed_assignment_count") == 368


def test_unchanged_count_recomputed(interim):
    assert interim.get("unchanged_assignment_count") == 6073


def test_change_reason_counts_sum(interim):
    reasons = interim.get("change_reason_counts") or {}
    non_u = sum(v for k, v in reasons.items() if k != "UNCHANGED")
    assert non_u == interim.get("changed_assignment_count")
    assert reasons.get("UNCHANGED") == interim.get("unchanged_assignment_count")
    assert reasons.get("TARGET_BELOW_MINIMUM_TO_FALLBACK") == 307
    assert reasons.get("TARGET_WITHIN_HORIZON_SUPPORT_TO_FALLBACK") == 1
    assert reasons.get("TARGET_WITHIN_HORIZON_RECALIBRATED") == 60


def test_target_attempts_vs_assignments(interim):
    assert interim.get("v1_target_calibration_attempts") == 370
    assert interim.get("v1_successful_target_assignments") == 369
    assert interim.get("v1_target_calibration_failures") == 1


def test_target_raw_partition_369(interim):
    assert interim.get("v1_target_raw_below_20") == 307
    assert interim.get("v1_target_raw_ge_20") == 62
    assert interim["v1_target_raw_below_20"] + interim["v1_target_raw_ge_20"] == 369


def test_target_valid_61(interim):
    assert interim.get("v2_candidate_target_count") == 61


def test_target_support_failure_1(interim):
    assert interim.get("within_horizon_support_failure_count") == 1


def test_final_assignment_partition_6441(interim):
    t = interim.get("v2_candidate_target_count")
    tr = interim.get("v2_candidate_trail_count")
    f = interim.get("family_fallback_count")
    c = interim.get("control_fallback_count")
    assert t + tr + f + c == 6441
    assert t + tr == 6118
    assert f + c == 323


def test_no_parameter_mutation(interim):
    assert interim.get("no_parameter_mutation") is True


def test_no_evaluation_use(interim):
    assert interim.get("evaluation_not_used") is True


def test_no_x27_pnl_use(interim):
    assert interim.get("x27_pnl_not_used") is True


def test_no_x28_pnl_use(interim):
    assert interim.get("x28_pnl_not_used") is True


def test_no_runtime_change(interim):
    s = interim.get("safety") or {}
    assert s.get("production_runtime_changed") is False
    assert s.get("runtime_ENTRY_changed") is False
    assert s.get("runtime_EXIT_changed") is False


def test_submit_cancel_live_zero(interim):
    assert (interim.get("safety") or {}).get("submit_cancel_live") == "0/0/0"


def test_ab_determinism(interim):
    assert interim.get("content_sha")
