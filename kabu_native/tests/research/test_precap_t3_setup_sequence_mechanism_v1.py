"""T3 P2 touch-age sequence tests. No 20260903/04 capture input."""
from __future__ import annotations

from research.simple_tech_entry_family import PULLBACK_LOOKBACK
from research.simple_tech_redesign.precap_entry_quality_existing_data_mechanism_v1_harvest import assert_research_day
from research.simple_tech_redesign.precap_t3_setup_sequence_mechanism_v1_analyze import decide
from research.simple_tech_redesign.precap_t3_setup_sequence_mechanism_v1_harvest import touch_age_from_window
from research.simple_tech_redesign.precap_t3_setup_sequence_mechanism_v1_spec import (
    ABSOLUTE_FEATURE_FAMILY_CLOSED,
    ALLOWED_AGES,
    CLOSED_ABSOLUTE_FEATURES,
    FLIP_DIRECTION_FROM_RESULTS,
    FORBIDDEN_INPUT_DAYS,
    MAX_RESEARCH_DATE,
    NEXT_CANDIDATE_ACCEPT_AGES,
    NEXT_CANDIDATE_ID,
    NEXT_CANDIDATE_REJECT_AGE,
    PRIMARY_FIELD,
    PRIMARY_HYPOTHESIS,
    PROSPECTIVE_HARVEST_SUSPENDED,
    TRUE_OOS,
    all_research_days,
)


def test_frozen_bounds_and_closed_family():
    assert MAX_RESEARCH_DATE == "20260902"
    assert FORBIDDEN_INPUT_DAYS == ("20260903", "20260904")
    assert "20260907" not in all_research_days()
    assert PROSPECTIVE_HARVEST_SUSPENDED is True
    assert TRUE_OOS is False
    assert ABSOLUTE_FEATURE_FAMILY_CLOSED is True
    assert CLOSED_ABSOLUTE_FEATURES[0] == "volume_percentile_60s"
    assert PRIMARY_FIELD == "P2_TOUCH_AGE_BARS"
    assert PRIMARY_HYPOTHESIS == "OLDER_PULLBACK_IS_WORSE"
    assert FLIP_DIRECTION_FROM_RESULTS is False
    assert int(PULLBACK_LOOKBACK) == 3
    assert NEXT_CANDIDATE_ID == "PRECAP_T3_PULLBACK_FRESHNESS_V1"
    assert NEXT_CANDIDATE_REJECT_AGE == 2
    assert NEXT_CANDIDATE_ACCEPT_AGES == (0, 1)


def test_forbidden_days_fail_closed():
    assert assert_research_day("20260903", today="20260904") == "FAIL_CLOSED_FORBIDDEN_INPUT"
    assert assert_research_day("20260904", today="20260904") == "FAIL_CLOSED_FORBIDDEN_INPUT"
    assert assert_research_day("20260907", today="20260904") == "FAIL_CLOSED_FUTURE_DATA"


def test_touch_age_signal_bar_is_zero():
    pack = touch_age_from_window([10.0, 11.0, 9.0], [12.0, 8.0, 10.0])
    assert pack["ok"] is True
    assert pack["P2_TOUCH_AGE_BARS"] == 0
    assert pack["P2_TOUCH_COUNT"] == 2
    assert pack["SIGNAL_BAR_TOUCH"] is True
    assert pack["P2_TOUCH_AGE_BARS"] in ALLOWED_AGES


def test_touch_age_oldest_only_is_two():
    pack = touch_age_from_window([9.0, 12.0, 12.0], [10.0, 8.0, 8.0])
    assert pack["ok"] is True
    assert pack["P2_TOUCH_AGE_BARS"] == 2
    assert pack["P2_TOUCH_COUNT"] == 1
    assert pack["SIGNAL_BAR_TOUCH"] is False


def test_touch_age_most_recent_wins():
    pack = touch_age_from_window([9.0, 9.0, 12.0], [10.0, 10.0, 8.0])
    assert pack["P2_TOUCH_AGE_BARS"] == 1
    assert pack["P2_TOUCH_COUNT"] == 2


def test_no_touch_is_integrity_fail():
    pack = touch_age_from_window([12.0, 12.0, 12.0], [10.0, 10.0, 10.0])
    assert pack["ok"] is False
    assert pack["blocker"] == "NO_TOUCH_BAR"


def test_integrity_failed_is_case_d():
    d = decide({"identity_ok": False, "sequence_integrity": {"recovered_all": False, "unknown_n": 1}}, leak_ok=False)
    assert d["CASE"] == "D"
    assert d["candidate_evaluated_this_run"] is False
    assert d["NEXT_CANDIDATE"]["reject_age"] == 2


def test_not_supported_is_case_b():
    d = decide(
        {
            "identity_ok": True,
            "sequence_integrity": {"recovered_all": True, "unknown_n": 0, "future_bar_use_n": 0},
            "qualify": False,
            "hypothesis_supported_A": False,
            "confound": {"admitted_blocked_opposite": False, "arrival": False, "added_reversed": False},
            "closed_absolute_features_reopened": False,
        },
        leak_ok=True,
    )
    assert d["CASE"] == "B"
    assert d["PRIMARY_NEXT_MECHANISM"] == "CLOSE_T3_PULLBACK_FRESHNESS_ARCHITECTURE"


def test_confounded_is_case_c():
    d = decide(
        {
            "identity_ok": True,
            "sequence_integrity": {"recovered_all": True, "unknown_n": 0, "future_bar_use_n": 0},
            "qualify": False,
            "hypothesis_supported_A": True,
            "confound": {"admitted_blocked_opposite": True, "arrival": False, "added_reversed": False},
            "closed_absolute_features_reopened": False,
        },
        leak_ok=True,
    )
    assert d["CASE"] == "C"


def test_qualify_is_case_a_frozen_candidate_not_evaluated():
    d = decide(
        {
            "identity_ok": True,
            "sequence_integrity": {"recovered_all": True, "unknown_n": 0, "future_bar_use_n": 0},
            "qualify": True,
            "hypothesis_supported_A": True,
            "confound": {"admitted_blocked_opposite": False, "arrival": False, "added_reversed": False},
            "closed_absolute_features_reopened": False,
        },
        leak_ok=True,
    )
    assert d["CASE"] == "A"
    assert d["PRIMARY_NEXT_MECHANISM"] == "P2_TOUCH_AGE_BARS"
    assert d["NEXT_CANDIDATE"]["candidate_id"] == "PRECAP_T3_PULLBACK_FRESHNESS_V1"
    assert d["NEXT_CANDIDATE"]["reject_age"] == 2
    assert d["candidate_evaluated_this_run"] is False
    assert d["FLIP_DIRECTION_FROM_RESULTS"] is False
