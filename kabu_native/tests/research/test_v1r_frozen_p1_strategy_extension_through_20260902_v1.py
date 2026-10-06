"""Frozen P1 V1R strategy extension through 20260902. No 20260903/04 capture input."""
from __future__ import annotations

from research.v1r_frozen_p1_strategy_extension_through_20260902_v1.analyze import decide, unused_metrics
from research.v1r_frozen_p1_strategy_extension_through_20260902_v1.spec import (
    ENTRY_SHA,
    ENTRY_V1R_SHA,
    EXTENSION_CANDIDATE_DAYS,
    FORBIDDEN_INPUT_DAYS,
    MAX_RESEARCH_DATE,
    MIN_EXTENSION_FULL_DAY_N,
    MIN_EXTENSION_FULL_TRADE_N,
    PROSPECTIVE_HARVEST_SUSPENDED,
    TRUE_OOS,
)


def _pin_ok() -> dict:
    return {"ok": True, "PIN_MODE": "CURRENT_FILE_HASH_EQUAL_TO_P1"}


def _alias_ok() -> dict:
    return {"ok": True}


def _p1_ok() -> dict:
    return {"ok": True}


def _ext(pnl: float, pf: float, trades: int, days: int, pos: int, neg: int) -> dict:
    return {
        "ran": True,
        "metrics": {
            "used": True,
            "trade_n": trades,
            "PnL": pnl,
            "PF": pf,
            "positive_day_n": pos,
            "negative_day_n": neg,
            "day_n": days,
        },
        "robustness": {
            "used": True,
            "EX_TOP1": {"PnL": 100.0, "PF": 1.2},
            "top1_day_contribution": 0.2,
        },
        "stress": {"used": True, "PnL": 10.0, "PF": 1.1, "day_list": ["20260828", "20260902"]},
        "combined": {"used": True, "PnL": 1000.0, "PF": 2.0},
    }


def test_frozen_bounds():
    assert MAX_RESEARCH_DATE == "20260902"
    assert FORBIDDEN_INPUT_DAYS == ("20260903", "20260904")
    assert PROSPECTIVE_HARVEST_SUSPENDED is True
    assert TRUE_OOS is False
    assert EXTENSION_CANDIDATE_DAYS == (
        "20260824",
        "20260825",
        "20260826",
        "20260827",
        "20260828",
        "20260831",
        "20260901",
        "20260902",
    )
    assert MIN_EXTENSION_FULL_DAY_N == 6
    assert MIN_EXTENSION_FULL_TRADE_N == 80
    assert "20260903" not in EXTENSION_CANDIDATE_DAYS
    assert "20260904" not in EXTENSION_CANDIDATE_DAYS


def test_entry_alias_is_not_p1_entry_sha():
    assert ENTRY_SHA != ENTRY_V1R_SHA
    assert ENTRY_SHA.startswith("f288")
    assert ENTRY_V1R_SHA.startswith("dfd311")


def test_case_e_on_source_pin_fail():
    d = decide(
        pin={"ok": False, "PIN_MODE": "P1_SOURCE_UNRECOVERABLE"},
        alias=_alias_ok(),
        p1=_p1_ok(),
        inv={"FULL_N": 8},
        extension={"ran": False, "metrics": unused_metrics(reason="x")},
        spot={"ran": False, "pass": False},
        leak_ok=True,
    )
    assert d["CASE"] == "E"
    assert d["VERDICT"] == "V1R_FROZEN_REPLAY_INTEGRITY_FAILED"
    assert d["SIZING_RESEARCH_ALLOWED"] is False
    assert d["NEW_ENTRY_FAMILY_DESIGN_ALLOWED"] is False
    assert d["V1R_RESEARCH_PRIORITY_MAINTAINED"] is False
    assert d["extension_economics_used"] is False


def test_case_e_on_alias_conflict():
    d = decide(
        pin=_pin_ok(),
        alias={"ok": False},
        p1=_p1_ok(),
        inv={"FULL_N": 8},
        extension=_ext(1, 2, 100, 8, 5, 3),
        spot={"ran": True, "pass": True},
        leak_ok=True,
    )
    assert d["CASE"] == "E"


def test_case_d_insufficient_coverage():
    d = decide(
        pin=_pin_ok(),
        alias=_alias_ok(),
        p1=_p1_ok(),
        inv={"FULL_N": 5},
        extension=_ext(100, 2, 50, 5, 3, 2),
        spot={"ran": True, "pass": True},
        leak_ok=True,
    )
    assert d["CASE"] == "D"
    assert d["NEW_ENTRY_FAMILY_DESIGN_ALLOWED"] is False


def test_case_c_extension_nonpositive():
    d = decide(
        pin=_pin_ok(),
        alias=_alias_ok(),
        p1=_p1_ok(),
        inv={"FULL_N": 8},
        extension=_ext(-100, 0.5, 100, 8, 2, 6),
        spot={"ran": True, "pass": True},
        leak_ok=True,
    )
    assert d["CASE"] == "C"
    assert d["NEW_ENTRY_FAMILY_DESIGN_ALLOWED"] is True
    assert d["SIZING_RESEARCH_ALLOWED"] is False


def test_prior_profit_does_not_rescue_via_combined():
    ext = _ext(-1, 0.9, 100, 8, 4, 4)
    ext["combined"] = {"used": True, "PnL": 2_289_100.0, "PF": 3.5}
    d = decide(
        pin=_pin_ok(),
        alias=_alias_ok(),
        p1=_p1_ok(),
        inv={"FULL_N": 8},
        extension=ext,
        spot={"ran": True, "pass": True},
        leak_ok=True,
    )
    assert d["CASE"] == "C"


def test_case_a_all_strong_gates():
    d = decide(
        pin=_pin_ok(),
        alias=_alias_ok(),
        p1=_p1_ok(),
        inv={"FULL_N": 8},
        extension=_ext(500, 1.4, 100, 8, 5, 3),
        spot={"ran": True, "pass": True},
        leak_ok=True,
    )
    assert d["CASE"] == "A"
    assert d["SIZING_RESEARCH_ALLOWED"] is True
    assert d["NEW_ENTRY_FAMILY_DESIGN_ALLOWED"] is False
    assert d["V1R_RESEARCH_PRIORITY_MAINTAINED"] is True


def test_case_e_spot_fail_stops_before_aggregate():
    d = decide(
        pin=_pin_ok(),
        alias=_alias_ok(),
        p1=_p1_ok(),
        inv={"FULL_N": 8},
        extension=_ext(500, 1.4, 100, 8, 5, 3),
        spot={"ran": True, "pass": False},
        leak_ok=True,
    )
    assert d["CASE"] == "E"
    assert d["extension_economics_used"] is False
