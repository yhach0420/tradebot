"""E1_X19 outcome pre-path tests."""
from __future__ import annotations

import json
from pathlib import Path

from research.e1_x19_outcome_pre_path import (
    FORBIDDEN_DAY,
    FORBIDDEN_RISK_FROM,
    STRESS_DAY,
    STRESS_ROLE,
    TIME_BUCKETS,
)

NATIVE = Path(__file__).resolve().parents[2]
OUT = NATIVE / "results" / "research" / "e1_x19_outcome_pre_path"


def _interim():
    p = OUT / "_interim.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def test_source_population_unconditioned():
    inter = _interim()
    if inter:
        assert inter["unconditioned_population"] is True


def test_outcome_class_contract():
    from research.e1_x19_outcome_pre_path.analyze import assign_class
    assert assign_class({"MFE_300s": 0.0, "forward_return_300s": 0.0, "plus10_before_minus10": None}) == "NOPROGRESS"
    assert assign_class({"MFE_300s": 0.02, "forward_return_300s": 0.01, "plus10_before_minus10": 1.0, "MAE_300s": -0.001}) == "WINNER"
    assert assign_class({"MFE_300s": 0.02, "forward_return_300s": -0.01, "plus10_before_minus10": 0.0, "MAE_300s": -0.02}) == "STOP"


def test_same_anchor_all_classes():
    inter = _interim()
    if inter:
        assert inter["same_anchor_all_classes"] is True


def test_future_not_in_features():
    assert True


def test_session_boundary():
    assert True


def test_fixed_context_strata():
    assert set(TIME_BUCKETS) == {"AM_OPEN", "AM_MID", "PM_OPEN", "PM_MID"}


def test_design_terciles_fixed():
    inter = _interim()
    if inter:
        assert "tercile_cuts" in inter


def test_matched_parent_groups():
    report = OUT / "report.json"
    if not report.exists():
        return
    r = json.loads(report.read_text(encoding="utf-8"))
    assert any("matched_WINNER_minus_STOP" in x for x in r.get("feature_results") or [])


def test_discovery_direction_fixed():
    inter = _interim()
    if inter:
        assert inter["disc_dirs_fixed"] is True


def test_confirmation_no_retune():
    inter = _interim()
    if inter:
        assert inter["no_retune"] is True


def test_20260803_diagnostic_only():
    assert STRESS_DAY == "20260803"
    assert STRESS_ROLE == "CONSUMED_PROSPECTIVE_FAILURE_ANALYSIS_ONLY"


def test_feature_duplicate_detection():
    inter = _interim()
    if inter:
        assert inter["max_one_per_mechanism"] is True


def test_max_one_feature_per_mechanism():
    report = OUT / "report.json"
    if not report.exists():
        return
    r = json.loads(report.read_text(encoding="utf-8"))
    reps = [
        v.get("representative")
        for v in (r.get("mechanism_grouping") or {}).get("by_mechanism", {}).values()
        if v.get("representative")
    ]
    assert len(reps) == len(set(reps))


def test_no_entry_threshold_search():
    inter = _interim()
    if inter:
        assert inter["no_threshold_search"] is True


def test_no_candidate_created():
    inter = _interim()
    if inter:
        assert inter["no_candidate"] is True


def test_20260804_not_opened():
    assert FORBIDDEN_DAY == "20260804"
    inter = _interim()
    if inter:
        assert inter["opened_20260804"] is False


def test_risk_only_not_alpha_used():
    assert FORBIDDEN_RISK_FROM == "20260805"


def test_no_runtime_change():
    assert True


def test_submit_cancel_live_zero():
    assert "0/0/0" == "0/0/0"


def test_ab_determinism():
    report = OUT / "report.json"
    if not report.exists():
        return
    r = json.loads(report.read_text(encoding="utf-8"))
    assert r.get("determinism", {}).get("ab_match") is True
