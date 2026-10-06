"""Existing-data mechanism protocol tests. No 20260903/04 capture input."""
from __future__ import annotations

from research.am_entry_profit_improvement import ELIGIBLE_DAYS
from research.simple_tech_redesign.branch_u_holdout_harvest import LOCKED_SERIES_DAYS
from research.simple_tech_redesign.precap_entry_quality_existing_data_mechanism_v1_analyze import decide, next_threshold
from research.simple_tech_redesign.precap_entry_quality_existing_data_mechanism_v1_harvest import assert_research_day
from research.simple_tech_redesign.precap_entry_quality_existing_data_mechanism_v1_spec import (
    BLOCK_A_DISCOVERY,
    BLOCK_B_INTERNAL_STABILITY,
    BLOCK_C_BURNED_STRESS,
    FEATURES,
    FORBIDDEN_INPUT_DAYS,
    MAX_RESEARCH_DATE,
    MISSING_POLICY_FROZEN,
    NEXT_CANDIDATE_ID_IF_CASE_A,
    NEXT_CANDIDATE_THRESHOLD_SEARCH_ALLOWED,
    PROSPECTIVE_HARVEST_SUSPENDED,
    THRESHOLD_POLICY,
    TRUE_OOS,
    WORST_REJECT_PCT,
    all_research_days,
)


def test_blocks_and_future_stop():
    assert MAX_RESEARCH_DATE == "20260902"
    assert FORBIDDEN_INPUT_DAYS == ("20260903", "20260904")
    assert list(BLOCK_A_DISCOVERY) + list(BLOCK_B_INTERNAL_STABILITY) == list(ELIGIBLE_DAYS)
    assert list(BLOCK_C_BURNED_STRESS) == list(LOCKED_SERIES_DAYS)
    assert all(d <= MAX_RESEARCH_DATE for d in all_research_days())
    assert "20260907" not in all_research_days()
    assert PROSPECTIVE_HARVEST_SUSPENDED is True
    assert TRUE_OOS is False


def test_forbidden_and_future_days_fail_closed():
    assert assert_research_day("20260903", today="20260904") == "FAIL_CLOSED_FORBIDDEN_INPUT"
    assert assert_research_day("20260904", today="20260904") == "FAIL_CLOSED_FORBIDDEN_INPUT"
    assert assert_research_day("20260907", today="20260904") == "FAIL_CLOSED_FUTURE_DATA"


def test_threshold_and_missing_policy_frozen_before_results():
    assert THRESHOLD_POLICY == "FIXED_WORST_30PCT_REJECTION_FROM_BLOCK_A_ONLY"
    assert WORST_REJECT_PCT == 0.30
    assert MISSING_POLICY_FROZEN == "FAIL_OPEN_TO_BASELINE"
    assert NEXT_CANDIDATE_ID_IF_CASE_A == "PRECAP_ENTRY_QUALITY_SINGLE_FEATURE_V1"
    assert NEXT_CANDIDATE_THRESHOLD_SEARCH_ALLOWED is False
    assert FEATURES[0] == "volume_percentile_60s"
    thr = next_threshold({"higher_is_better": True, "block_a_q30": 0.2, "block_a_q70": 0.8})
    assert thr["threshold"] == 0.2
    assert thr["q20_tried"] is False
    thr2 = next_threshold({"higher_is_better": False, "block_a_q30": 0.2, "block_a_q70": 0.8})
    assert thr2["threshold"] == 0.8


def test_integrity_failed_is_case_d():
    d = decide({"identity_ok": False, "features": []}, leak_ok=False)
    assert d["CASE"] == "D"
    assert d["candidate_evaluated_this_run"] is False
    assert d["PROSPECTIVE_HARVEST_SUSPENDED"] is True


def test_no_robust_mechanism_is_case_b():
    feats = [{"feature": n, "qualify": False, "has_a_direction": False, "confounded": False} for n in FEATURES]
    d = decide({"identity_ok": True, "features": feats}, leak_ok=True)
    assert d["CASE"] == "B"
    assert d["selected_feature"] is None


def test_confounded_is_case_c():
    feats = [
        {"feature": FEATURES[0], "qualify": False, "has_a_direction": True, "confounded": True},
        {"feature": FEATURES[1], "qualify": False, "has_a_direction": False, "confounded": False},
        {"feature": FEATURES[2], "qualify": False, "has_a_direction": False, "confounded": False},
        {"feature": FEATURES[3], "qualify": False, "has_a_direction": False, "confounded": False},
    ]
    d = decide({"identity_ok": True, "features": feats}, leak_ok=True)
    assert d["CASE"] == "C"


def test_tie_break_selects_first_qualified_only():
    feats = []
    for i, name in enumerate(FEATURES):
        feats.append(
            {
                "feature": name,
                "feature_id": f"F{i+1}",
                "qualify": i in (1, 2),
                "has_a_direction": True,
                "confounded": False,
                "higher_is_better": True,
                "block_a_q30": float(i),
                "block_a_q70": float(i) + 1,
            }
        )
    d = decide({"identity_ok": True, "features": feats}, leak_ok=True)
    assert d["CASE"] == "A"
    assert d["selected_feature"] == "distance_from_vwap_bps"
    assert d["NEXT_CANDIDATE_ID_IF_CASE_A"] == "PRECAP_ENTRY_QUALITY_SINGLE_FEATURE_V1"
    assert d["candidate_evaluated_this_run"] is False
