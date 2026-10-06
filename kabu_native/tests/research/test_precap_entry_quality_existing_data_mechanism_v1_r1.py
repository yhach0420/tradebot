"""R1 daily-Spearman methodology tests. No 20260903/04 capture input."""
from __future__ import annotations

from research.am_entry_profit_improvement import ELIGIBLE_DAYS
from research.simple_tech_redesign.branch_u_holdout_harvest import LOCKED_SERIES_DAYS
from research.simple_tech_redesign.precap_entry_quality_existing_data_mechanism_v1_harvest import assert_research_day
from research.simple_tech_redesign.precap_entry_quality_existing_data_mechanism_v1_r1_analyze import (
    aggregate_daily,
    capture_reporting_state,
    daily_spearman_table,
    decide,
)
from research.simple_tech_redesign.precap_entry_quality_existing_data_mechanism_v1_r1_harvest import classify_outcome
from research.simple_tech_redesign.precap_entry_quality_existing_data_mechanism_v1_r1_spec import (
    BLOCK_A_DISCOVERY,
    BLOCK_B_INTERNAL_STABILITY,
    BLOCK_C_BURNED_STRESS,
    FEATURES,
    FORBIDDEN_INPUT_DAYS,
    MAX_RESEARCH_DATE,
    MEDIAN_SPLIT_PRIMARY,
    MIN_DAY_N,
    PRIMARY_METHOD,
    PRIOR_ANALYSIS_ID,
    PROSPECTIVE_HARVEST_SUSPENDED,
    SUPERSEDED_FOR_DECISION,
    SUPERSEDED_REASON,
    TRUE_OOS,
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
    assert PRIMARY_METHOD == "CONTINUOUS_DAILY_SPEARMAN"
    assert MEDIAN_SPLIT_PRIMARY is False
    assert MIN_DAY_N == 5
    assert SUPERSEDED_FOR_DECISION is True
    assert SUPERSEDED_REASON == "PRECOMMITTED_CONTINUOUS_DAILY_SPEARMAN_METHOD_NOT_IMPLEMENTED"
    assert PRIOR_ANALYSIS_ID == "SIMPLE_TECH_PRECAP_ENTRY_QUALITY_EXISTING_DATA_MECHANISM_V1"


def test_forbidden_and_future_days_fail_closed():
    assert assert_research_day("20260903", today="20260904") == "FAIL_CLOSED_FORBIDDEN_INPUT"
    assert assert_research_day("20260904", today="20260904") == "FAIL_CLOSED_FORBIDDEN_INPUT"
    assert assert_research_day("20260907", today="20260904") == "FAIL_CLOSED_FUTURE_DATA"


def test_small_n_is_unevaluable_not_rho_zero():
    rows = [{"date": "20260803", "session_close_pnl": float(i), "volume_percentile_60s": float(i)} for i in range(4)]
    tab = daily_spearman_table(rows, "volume_percentile_60s")
    assert len(tab) == 1
    assert tab[0]["evaluable"] is False
    assert tab[0]["rho"] is None
    assert tab[0]["status"] == "UNEVALUABLE_SMALL_N"
    agg = aggregate_daily(tab)
    assert agg["evaluable_day_n"] == 0
    assert agg["median_daily_rho"] is None
    assert agg["direction"] == "NO_DIRECTION"
    assert agg["unevaluable"] is True


def test_daily_spearman_positive_when_feature_tracks_pnl():
    rows = []
    for d in range(5):
        day = f"2026080{d+1}"
        for i in range(6):
            rows.append({"date": day, "session_close_pnl": float(i), "volume_percentile_60s": float(i)})
    tab = daily_spearman_table(rows, "volume_percentile_60s")
    assert all(r["evaluable"] and r["rho"] is not None and r["rho"] > 0.99 for r in tab)
    agg = aggregate_daily(tab)
    assert agg["median_daily_rho"] > 0.99
    assert agg["direction"] == "higher_is_better"


def test_capture_state_null_is_inactive_expected_not_alive():
    st = capture_reporting_state({"CAPTURE_PID": None}, {"CAPTURE_PID": None})
    assert st["CAPTURE_STATE"] == "INACTIVE_EXPECTED"
    assert st["CAPTURE_PID_UNCHANGED"] is True
    assert st["CAPTURE_ALIVE_INFERRED_FROM_NULL_UNCHANGED"] is False
    assert st["null_equals_null_means_alive"] is False
    st2 = capture_reporting_state({"CAPTURE_PID": 11}, {"CAPTURE_PID": 11})
    assert st2["CAPTURE_STATE"] == "ACTIVE_SAME_PID"
    st3 = capture_reporting_state({"CAPTURE_PID": 11}, {"CAPTURE_PID": None})
    assert st3["CAPTURE_STATE"] == "UNKNOWN"


def test_attrition_no_fill_from_occupancy_trace():
    cand = {"session_close_pnl": None, "actual_filled": False, "fill_price": None, "fill_t": None}
    occ = {"executable_signal": True, "actual_filled": False, "fill_price": None, "fill_t": None, "control_exit": {}}
    pack = classify_outcome(cand, occ)
    assert pack["reason"] == "NO_FILL"
    assert pack["explained"] is True
    unknown = classify_outcome(cand, None)
    assert unknown["reason"] == "UNKNOWN_NO_OCCUPANCY_ROW"
    assert unknown["explained"] is False
    filled = classify_outcome({"session_close_pnl": -100.0}, occ)
    assert filled["reason"] == "HAS_OUTCOME"


def test_not_one_sided_requires_both_evaluable_in_decide_integrity():
    d = decide(
        {
            "identity_ok": True,
            "methodology_ok": True,
            "pool_identity": {"pool_identity_ok": True},
            "attrition_unexplained_n": 0,
            "concentration_identical_across_features": False,
            "capture_restreamed": False,
            "features": [
                {
                    "feature": FEATURES[0],
                    "feature_id": "F1",
                    "qualify": False,
                    "has_a_direction": True,
                    "confounded": True,
                    "not_one_sided": False,
                    "PRIMARY_METHOD": PRIMARY_METHOD,
                    "median_split_used_for_qualify": False,
                }
            ]
            + [
                {
                    "feature": n,
                    "qualify": False,
                    "has_a_direction": False,
                    "confounded": False,
                    "PRIMARY_METHOD": PRIMARY_METHOD,
                    "median_split_used_for_qualify": False,
                }
                for n in FEATURES[1:]
            ],
        },
        leak_ok=True,
    )
    assert d["CASE"] == "C"
    assert d["candidate_evaluated_this_run"] is False
    assert d["NEW_ENTRY_FILTER"] is False


def test_unexplained_attrition_is_case_d():
    d = decide(
        {
            "identity_ok": True,
            "methodology_ok": True,
            "pool_identity": {"pool_identity_ok": True},
            "attrition_unexplained_n": 1,
            "concentration_identical_across_features": False,
            "features": [],
        },
        leak_ok=True,
    )
    assert d["CASE"] == "D"
    assert "ATTRITION_UNEXPLAINED" in d["integrity_reasons"]


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
                "PRIMARY_METHOD": PRIMARY_METHOD,
                "median_split_used_for_qualify": False,
            }
        )
    d = decide(
        {
            "identity_ok": True,
            "methodology_ok": True,
            "pool_identity": {"pool_identity_ok": True},
            "attrition_unexplained_n": 0,
            "concentration_identical_across_features": False,
            "features": feats,
        },
        leak_ok=True,
    )
    assert d["CASE"] == "A"
    assert d["selected_feature"] == "distance_from_vwap_bps"
    assert d["PRIMARY_NEXT_MECHANISM"] == "PRECAP_ENTRY_QUALITY_SINGLE_FEATURE_V1"
    assert d["candidate_evaluated_this_run"] is False
