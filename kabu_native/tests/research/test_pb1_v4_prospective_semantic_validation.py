"""Tests for prospective start. Does not require 20260924 bars."""
from __future__ import annotations

from research.pb1_v4_clarified_machine_correction_v4.definitions import machine_sha256 as v4_sha
from research.pb1_v4_prospective_semantic_validation import EXPECTED_PRECOMMIT_SHA256, SESSION0
from research.pb1_v4_prospective_semantic_validation.isolation import OUT, write_overlap_n
from research.pb1_v4_prospective_semantic_validation_preflight import EXPECTED_MACHINE_SHA256
from research.pb1_v4_prospective_semantic_validation_preflight.startgate import start_day_gate


def test_v4_frozen_and_overlap():
    assert v4_sha() == EXPECTED_MACHINE_SHA256
    assert write_overlap_n("", "") == 0
    assert "pb1_v4_prospective_semantic_validation" in str(OUT).replace("\\", "/")
    assert SESSION0 == "20260924"


def test_future_session_is_not_yet_occurred_not_ineligible():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from research.pb1_v4_prospective_semantic_validation.states import (
        STATE_NOT_YET,
        STATE_WAITING,
        apply_quality,
        classify_clock,
    )

    jst = ZoneInfo("Asia/Tokyo")
    clock = classify_clock(session="20260924", today_jst="20260921", now=datetime(2026, 9, 21, 0, 5, tzinfo=jst))
    assert clock["SESSION_STATE"] == STATE_NOT_YET
    assert clock["may_search_bars"] is False
    op = apply_quality(clock=clock, data_complete=False)
    assert op["SESSION_STATE"] == STATE_NOT_YET
    assert op["INELIGIBLE_SESSION"] is False
    assert op["SESSION_ACCEPTED_FOR_PROSPECTIVE"] is False
    assert op["counts_as_session"] is False
    assert op["PROSPECTIVE_DATA_OPENED"] is False
    wait = classify_clock(session="20260924", today_jst="20260924", now=datetime(2026, 9, 24, 10, 0, tzinfo=jst))
    assert wait["SESSION_STATE"] == STATE_WAITING
    wait_op = apply_quality(clock=wait, data_complete=False)
    assert wait_op["INELIGIBLE_SESSION"] is False
    ready = classify_clock(session="20260924", today_jst="20260924", now=datetime(2026, 9, 24, 11, 20, tzinfo=jst))
    inelig = apply_quality(clock=ready, data_complete=False)
    assert inelig["INELIGIBLE_SESSION"] is True
    elig = apply_quality(clock=ready, data_complete=True)
    assert elig["SESSION_STATE"] == "ELIGIBLE"


def test_hash_mismatch_is_do_not_start():
    g = start_day_gate(precommit_sha="nope", expected_precommit_sha=EXPECTED_PRECOMMIT_SHA256)
    assert g["ok"] is False
    assert g["DO_NOT_START_PROSPECTIVE"] is True
