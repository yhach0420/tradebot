"""Tests for calendar reason erratum + prospective harness preflight."""
from __future__ import annotations

from research.pb1_v4_clarified_machine_correction_v4.definitions import machine_sha256 as v4_sha
from research.pb1_v4_prospective_semantic_validation_preflight import (
    CALENDAR_CORRECTED_PRECOMMIT_SHA256,
    CALENDAR_REASON,
    EXPECTED_MACHINE_SHA256,
    FIRST_ELIGIBLE_JP_CASH_SESSION,
)
from research.pb1_v4_prospective_semantic_validation_preflight.completeness import check_1m_complete, synthetic_1m
from research.pb1_v4_prospective_semantic_validation_preflight.eligibility import resolve_jp_cash_session
from research.pb1_v4_prospective_semantic_validation_preflight.erratum import apply_reason_erratum, eligibility_unchanged
from research.pb1_v4_prospective_semantic_validation_preflight.isolation import OUT, write_overlap_n
from research.pb1_v4_prospective_semantic_validation_preflight.startgate import start_day_gate


def test_v4_and_overlap():
    assert v4_sha() == EXPECTED_MACHINE_SHA256
    assert write_overlap_n("", "") == 0
    assert "pb1_v4_prospective_semantic_validation_preflight" in str(OUT).replace("\\", "/")


def test_reason_erratum_keeps_eligibility_and_new_sha():
    parent = {
        "data_eligibility": {
            "rule": "eligible session = first actual TSE cash trading session",
            "first_eligible_session": FIRST_ELIGIBLE_JP_CASH_SESSION,
            "calendar_plus_one_is_not_a_session": True,
            "tse_cash_only": True,
            "derivative_holiday_sessions_not_eligible": True,
            "20260921_COUNTS_AS_SESSION": False,
            "20260922_COUNTS_AS_SESSION": False,
            "20260923_COUNTS_AS_SESSION": False,
            "non_cash_dates_explicit": ["20260921", "20260922", "20260923"],
        },
        "observation_period_stopping_rule": {
            "close_when": "WHY_THIS_STOCK candidate-days >= 21 OR 40 actual eligible JP cash sessions, whichever first",
            "min_candidate_days_WHY_THIS_STOCK": 21,
            "max_sessions": 40,
        },
        "calendar_correction": {"reason": "wrong weekend text", "new_first_eligible_session": FIRST_ELIGIBLE_JP_CASH_SESSION},
        "OLD_PRECOMMIT_SHA256": "c6bf03f61c9bf936e0440a0994d51ee0c5df15bd91421c5bb2416d596540a50c",
        "PRECOMMIT_SHA256": CALENDAR_CORRECTED_PRECOMMIT_SHA256,
    }
    out = apply_reason_erratum(parent)
    assert out["PRECOMMIT_SHA256"] != CALENDAR_CORRECTED_PRECOMMIT_SHA256
    assert out["calendar_correction"]["reason"] == CALENDAR_REASON
    par = eligibility_unchanged(parent, out)
    assert par["ok"] is True
    assert par["FIRST_ELIGIBLE_JP_CASH_SESSION"] == FIRST_ELIGIBLE_JP_CASH_SESSION


def test_resolver_holidays_and_no_20260924_open():
    for d in ("20260921", "20260922", "20260923"):
        r = resolve_jp_cash_session(d)
        assert r["is_tse_cash_session"] is False
        assert r["counts_as_session"] is False
    r24 = resolve_jp_cash_session("20260924", allow_open_prospective=False)
    assert r24["is_tse_cash_session"] is True
    assert r24["eligible"] is False
    assert "PROSPECTIVE_DATA_NOT_OPENED_THIS_TASK" in r24["reasons"]
    assert check_1m_complete(synthetic_1m(drop=("11:19",))).get("ok") is False


def test_start_day_gate_fail_closed():
    g = start_day_gate(precommit_sha="deadbeef", expected_precommit_sha="cafebabe")
    assert g["ok"] is False
    assert g["DO_NOT_START_PROSPECTIVE"] is True
    assert g["verdict"] == "DO_NOT_START_PROSPECTIVE"
