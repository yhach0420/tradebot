"""Harvester readiness tests. Does not open 20260903/20260904 capture as research input."""
from __future__ import annotations

import pytest

from research.simple_tech_redesign.precap_marginal_quality_prospective_harvester import (
    FROZEN_SOURCE_SHA256,
    FROZEN_SPEC_SHA256,
    STATUS_DAY_MUTATED,
    STATUS_FORBIDDEN,
    STATUS_NOT_SEALED,
    STRATEGY_CANDIDATE_ARMED,
    assert_day_immutable,
    day_identity_payload,
    digest_sha,
    frozen_identity,
    prove_sealed_am_session,
    questions_display,
    runtime_arming_status,
    save_digest,
)
from research.simple_tech_redesign.precap_marginal_quality_prospective_v1_spec import (
    ANALYSIS_ID,
    CANDIDATE_FROZEN,
    FIRST_ELIGIBLE_DATE,
    FORBIDDEN_INPUT_DAYS,
    INTRINSIC_MECHANISM_CONFIRMED,
    OBSERVED_ECONOMIC_BOTTLENECK,
    PRIMARY_MECHANISM_FROZEN,
    PROSPECTIVE_OBSERVATION_PROTOCOL_FROZEN,
    source_sha256_prospective,
    spec_sha256_prospective,
)


def test_frozen_protocol_identity():
    ident = frozen_identity()
    assert ident["ok"] is True
    assert ANALYSIS_ID == "SIMPLE_TECH_PRECAP_MARGINAL_QUALITY_PROSPECTIVE_V1"
    assert spec_sha256_prospective() == FROZEN_SPEC_SHA256
    assert source_sha256_prospective() == FROZEN_SOURCE_SHA256
    assert PROSPECTIVE_OBSERVATION_PROTOCOL_FROZEN is True
    assert CANDIDATE_FROZEN is False
    assert INTRINSIC_MECHANISM_CONFIRMED is False
    assert OBSERVED_ECONOMIC_BOTTLENECK == "MARGINAL_ENTRY_QUALITY"
    assert PRIMARY_MECHANISM_FROZEN == "OUTLIER_DOMINATED"


def test_observation_vs_strategy_arming():
    st = runtime_arming_status()
    assert st["OBSERVATION_PROTOCOL_ARMED"] is True
    assert st["STRATEGY_CANDIDATE_ARMED"] is False
    assert STRATEGY_CANDIDATE_ARMED is False
    assert st["observation_day1_is_floor_break_day1"] is False
    assert st["observation_day1_is_strategy_validation_day1"] is False
    assert st["first_eligible_date"] == FIRST_ELIGIBLE_DATE == "20260907"


def test_forbidden_dates_fail_closed_without_active_capture():
    for day in FORBIDDEN_INPUT_DAYS:
        rec = prove_sealed_am_session(day, today="20260904", active_capture_path="C:/tmp/session_ing_active")
        assert rec["ok"] is False
        assert rec["status"] == STATUS_FORBIDDEN
        assert rec["capture_path"] == ""


def test_future_or_today_not_sealed():
    rec = prove_sealed_am_session("20260907", today="20260904", active_capture_path="")
    assert rec["ok"] is False
    assert rec["status"] == STATUS_NOT_SEALED
    rec2 = prove_sealed_am_session("20260904", today="20260904", active_capture_path="")
    assert rec2["status"] == STATUS_FORBIDDEN


def test_questions_not_evaluable_below_min_n():
    disp = questions_display(
        blocked_n=0,
        questions={
            "Q1_future_blocked_weaker": False,
            "Q2_weakness_on_multiple_days": False,
            "Q3_single_day_explains": False,
            "Q4_single_symbol_explains": False,
            "Q5_one_role_only": False,
            "Q6_late_arrival_only": False,
            "Q7_285A_type_outlier_reproduced": False,
        },
    )
    assert disp["questions_evaluable"] is False
    assert disp["status_class"] == "NOT_EVALUABLE_INSUFFICIENT"
    assert set(disp["questions_status"].values()) == {"NOT_EVALUABLE"}
    disp2 = questions_display(blocked_n=15, questions={"Q1_future_blocked_weaker": True})
    assert disp2["questions_evaluable"] is True
    assert disp2["questions_status"]["Q1_future_blocked_weaker"] == "TRUE"


def test_day_digest_mutation_fail_closed(tmp_path, monkeypatch):
    from research.simple_tech_redesign import precap_marginal_quality_prospective_harvester as h

    monkeypatch.setattr(h, "DIGEST_PATH", tmp_path / "day_digests.json")
    monkeypatch.setattr(h, "DAY_CACHE", tmp_path)
    body = {
        "date": "20260907",
        "spec_sha": FROZEN_SPEC_SHA256,
        "executable_n": 2,
        "admitted_n": 1,
        "hyp_n": 1,
        "cap_inventory": {"cap_only_n": 1},
        "candidates": [
            {
                "trade_id": "20260907|AAA|1",
                "control_admitted": True,
                "cap_only_blocked": False,
                "hypothetical_fill": False,
                "session_close_pnl": 1.0,
            },
            {
                "trade_id": "20260907|BBB|2",
                "control_admitted": False,
                "cap_only_blocked": True,
                "hypothetical_fill": True,
                "session_close_pnl": -2.0,
            },
        ],
    }
    payload = day_identity_payload(body, capture_path="/data/market_capture/20260907/session_ing_x")
    save_digest("20260907", payload, {"date": "20260907"})
    assert_day_immutable("20260907", payload)
    mutated = dict(payload)
    mutated["hyp_n"] = 99
    with pytest.raises(RuntimeError, match=STATUS_DAY_MUTATED):
        assert_day_immutable("20260907", mutated)
    assert digest_sha(payload) != digest_sha(mutated)
