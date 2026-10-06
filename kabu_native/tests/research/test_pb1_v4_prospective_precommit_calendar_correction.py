"""Tests for precommit calendar correction. No prospective open. V4 immutable."""
from __future__ import annotations

from research.pb1_v4_clarified_machine_correction_v4.definitions import machine_sha256 as v4_sha
from research.pb1_v4_clarified_machine_correction_v4_freeze_and_prospective_prep import EXPECTED_V4_MACHINE_SHA256
from research.pb1_v4_prospective_precommit_calendar_correction import (
    FIRST_ELIGIBLE_JP_CASH_SESSION,
    OLD_PRECOMMIT_SHA256,
)
from research.pb1_v4_prospective_precommit_calendar_correction.correct import correct_precommit, verify_parent_precommit
from research.pb1_v4_prospective_precommit_calendar_correction.isolation import OUT, write_overlap_n


def test_v4_unchanged_and_overlap_zero():
    assert v4_sha() == EXPECTED_V4_MACHINE_SHA256
    assert write_overlap_n("", "") == 0
    assert "pb1_v4_prospective_precommit_calendar_correction" in str(OUT).replace("\\", "/")


def test_new_sha_is_not_old_sha():
    parent = {
        "purpose": "SEMANTIC_CAUSAL_PROSPECTIVE_VALIDATION",
        "data_eligibility": {"first_eligible_session": "20260921", "rule": "calendar plus one"},
        "observation_period_stopping_rule": {
            "close_when": "WHY_THIS_STOCK candidate-days >= 21 OR sessions == 40, whichever first",
            "min_candidate_days_WHY_THIS_STOCK": 21,
            "max_sessions": 40,
        },
        "PRECOMMIT_SHA256": OLD_PRECOMMIT_SHA256,
    }
    out = correct_precommit(parent)
    assert out["PRECOMMIT_SHA256"] != OLD_PRECOMMIT_SHA256
    assert out["OLD_PRECOMMIT_SHA256"] == OLD_PRECOMMIT_SHA256
    assert out["data_eligibility"]["first_eligible_session"] == FIRST_ELIGIBLE_JP_CASH_SESSION
    assert out["data_eligibility"]["20260921_COUNTS_AS_SESSION"] is False
    assert out["data_eligibility"]["20260922_COUNTS_AS_SESSION"] is False
    assert out["data_eligibility"]["20260923_COUNTS_AS_SESSION"] is False
    assert out["observation_period_stopping_rule"]["non_business_days_do_not_count"] is True
    assert "40 actual eligible JP cash sessions" in str(out["observation_period_stopping_rule"]["close_when"])


def test_parent_verify_rejects_wrong_stored_sha():
    v = verify_parent_precommit({"purpose": "x", "PRECOMMIT_SHA256": "deadbeef"})
    assert v["ok"] is False
    assert v["matches_expected_old"] is False
