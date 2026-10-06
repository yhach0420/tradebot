"""Tests for complete-strategy conversion. No Confirmation. No Frozen Validation. No detector retune."""
from __future__ import annotations

import inspect

from research.support_resistance_first_interaction_matched_causal_test_v1.freeze import detector_sha256, state_machine_sha256
from research.support_resistance_mechanism_to_complete_strategy_v1 import (
    CAP,
    CASE_A,
    CASE_BOTH,
    CASE_COMBINED,
    CASE_NONE,
    DETECTOR_RETUNED,
    EXPECTED_DETECTOR_SHA256,
    EXPECTED_STATE_MACHINE_SHA256,
    FROZEN_VALIDATION_OPENED,
    MATCHABILITY_USED_AS_ENTRY_FILTER,
    OLD_CONFIRMATION_OPENED,
    PARENT_VERDICT,
    PNL_BASED_RULE_CHANGE,
    SHARES,
    STRATEGY_A2,
    STRATEGY_C1,
    STRATEGY_COMBINED,
    STRATEGY_IDS,
)
from research.support_resistance_mechanism_to_complete_strategy_v1.analyze import decide
from research.support_resistance_mechanism_to_complete_strategy_v1.exits import simulate_path
from research.support_resistance_mechanism_to_complete_strategy_v1.isolation import OUT, PARENT_OUT, write_overlap_n
from research.support_resistance_mechanism_to_complete_strategy_v1.portfolio import replay
from research.support_resistance_mechanism_to_complete_strategy_v1.publish import SHEET_ORDER
from research.support_resistance_mechanism_to_complete_strategy_v1.spec import source_sha256
from research.support_resistance_mechanism_to_complete_strategy_v1.targets import structural_target
from research.support_resistance_mechanism_to_complete_strategy_v1.walk import walk_complete


def _rec(bars: list[tuple[str, float, float, float, float]]) -> dict:
    t, o, h, l, c = zip(*bars)
    idx = {str(x): i for i, x in enumerate(t)}
    return {"n": len(t), "t": list(t), "o": list(o), "h": list(h), "l": list(l), "c": list(c), "idx": idx}


def test_hashes_frozen():
    assert detector_sha256() == EXPECTED_DETECTOR_SHA256
    assert state_machine_sha256() == EXPECTED_STATE_MACHINE_SHA256
    assert DETECTOR_RETUNED is False
    assert source_sha256()


def test_closed_partitions_and_safety_constants():
    assert OLD_CONFIRMATION_OPENED is False
    assert FROZEN_VALIDATION_OPENED is False
    assert MATCHABILITY_USED_AS_ENTRY_FILTER is False
    assert PNL_BASED_RULE_CHANGE is False
    assert PARENT_VERDICT == "SR_A_AND_C_EXECUTABLE_MECHANISMS_SUPPORTED_V1"
    assert STRATEGY_IDS == (STRATEGY_A2, STRATEGY_C1, STRATEGY_COMBINED)
    assert CAP == 3
    assert SHARES == 100


def test_no_matchability_filter_in_walk():
    src = inspect.getsource(walk_complete)
    assert "matched" not in src
    assert "propensity" not in src
    assert "tod_min" not in src
    assert "vol_rel" not in src
    assert "find_control" not in src


def test_no_extra_exits():
    src = inspect.getsource(simulate_path)
    assert "EMA" not in src
    assert "VWAP" not in src
    assert "ATR" not in src
    assert "time_stop" not in src


def test_invalidation_wins_over_target():
    rec = _rec(
        [
            ("10:00", 100, 101, 99, 100.5),
            ("10:01", 100.5, 101, 100, 100.8),
            ("10:02", 101, 106, 97, 97),
            ("10:03", 97.2, 98, 97, 97.5),
        ]
    )
    path = simulate_path(rec, entry_i=1, side="LONG", inv_lo=98.0, inv_hi=102.0, target_price=105.0)
    assert path["ok"]
    assert path["exit_reason"] == "invalidation"
    assert path["target_filled"] is False
    assert path["target_invalidation_ambiguous"] is True
    assert path["exit_t"] == "10:03"
    assert path["exit_price"] == 97.2


def test_target_not_on_entry_bar():
    rec = _rec(
        [
            ("10:00", 100, 101, 99, 100.5),
            ("10:01", 100.5, 110, 100, 109),
            ("10:02", 109, 109.5, 108, 109),
            ("15:20", 109, 109.2, 108.8, 109),
        ]
    )
    path = simulate_path(rec, entry_i=1, side="LONG", inv_lo=90.0, inv_hi=120.0, target_price=108.0)
    assert path["exit_reason"] == "structural_target"
    assert path["exit_t"] == "10:02"
    assert path["exit_price"] == 108.0


def test_same_bar_entry_impossible_by_next_entry():
    rec = _rec(
        [
            ("10:00", 100, 101, 99, 100),
            ("10:01", 100.2, 101, 100, 100.5),
        ]
    )
    from research.support_resistance_matched_separation_not_a_strategy_v1.outcomes import next_entry_i

    j = next_entry_i(rec, 0)
    assert j == 1
    assert rec["t"][j] != rec["t"][0]


def test_no_future_target_zone():
    hit = structural_target(
        side="LONG",
        entry_px=100.0,
        entry_zone_id="z0",
        session_date="20240917",
        resistance_active=[
            {"zone_id": "future", "lo": 110.0, "hi": 112.0, "center": 111.0, "ZONE_ACTIVATED_AT": "20240918"},
            {"zone_id": "known", "lo": 120.0, "hi": 122.0, "center": 121.0, "ZONE_ACTIVATED_AT": "20240916"},
        ],
        support_active=[],
    )
    assert hit["target_zone_id"] == "known"
    assert hit["target_price"] == 120.0
    assert hit["target_future_leakage"] is False


def test_cap_no_delayed_entry():
    def sig(i, t, sym="7203"):
        return {
            "ok": True,
            "signal_id": f"A2|{sym}|20240917|z{i}|{t}",
            "episode_id": f"{sym}|20240917|z{i}",
            "mechanism": "A2",
            "symbol": sym,
            "date": "20240917",
            "entry_t": t,
            "slot_release_t": "15:20",
            "exit_t": "15:20",
            "gross_yen": 0.0,
            "stress_yen": 0.0,
            "same_bar_entry": False,
            "target_future_leakage": False,
            "exit_retroactive": False,
            "target_invalidation_ambiguous": False,
            "entry_price": 100.0,
            "side": "LONG",
            "block": "D2",
            "hold_min": 1,
            "mfe_bps": 0,
            "mae_bps": 0,
            "mfe_realized_giveback_bps": 0,
            "exit_reason": "session_close",
            "attribution": "session_close",
        }

    out = replay(
        [
            sig(0, "10:00", "7203"),
            sig(1, "10:01", "6758"),
            sig(2, "10:02", "9984"),
            sig(3, "10:03", "8306"),
        ],
        strategy_id=STRATEGY_A2,
    )
    assert out["filled_n"] == 3
    assert out["cap_blocked_n"] == 1
    assert out["delayed_cap_entry_n"] == 0
    assert out["same_bar_entry_n"] == 0


def test_a2_before_c1_on_tie():
    def s(mech, sym):
        return {
            "ok": True,
            "signal_id": f"{mech}|{sym}|20240917|z|{mech}",
            "episode_id": f"{sym}|20240917|z{mech}",
            "mechanism": mech,
            "symbol": sym,
            "date": "20240917",
            "entry_t": "10:00",
            "slot_release_t": "15:20",
            "exit_t": "15:20",
            "gross_yen": 0.0,
            "stress_yen": 0.0,
            "same_bar_entry": False,
            "target_future_leakage": False,
            "exit_retroactive": False,
            "target_invalidation_ambiguous": False,
            "entry_price": 100.0,
            "side": "LONG",
            "block": "D2",
            "hold_min": 1,
            "mfe_bps": 0,
            "mae_bps": 0,
            "mfe_realized_giveback_bps": 0,
            "exit_reason": "session_close",
            "attribution": "session_close",
        }

    out = replay([s("C1", "7203"), s("A2", "7203")], strategy_id=STRATEGY_COMBINED, cap=1)
    assert out["filled_n"] == 1
    assert out["trades"][0]["mechanism"] == "A2"


def test_decide_does_not_pick_highest_pnl_or_rescue():
    def pack(stress, pf, coherent, **extra):
        e = {
            "coherent": coherent,
            "stress_yen": stress,
            "pf_stress": pf,
            "gross_yen": stress + 1000,
            **extra,
        }
        return {"economics": e, "tie": {"CAP_TIE_ORDER_FRAGILE": False}}

    a2 = pack(1000, 1.5, True)
    c1 = pack(-5000, 0.4, False)
    comb = pack(8000, 2.0, True)
    d = decide(a2, c1, comb)
    assert d["VERDICT"] == CASE_A
    assert d["combined_hides_failed_component"] is True
    assert d["highest_pnl_selected"] is False
    both = decide(pack(1000, 1.5, True), pack(800, 1.2, True), pack(50, 1.1, True))
    assert both["VERDICT"] == CASE_BOTH
    none = decide(pack(-1, 0.9, False), pack(-2, 0.8, False), pack(9, 1.4, True))
    assert none["VERDICT"] == CASE_NONE


def test_write_overlap_zero_and_sheets():
    assert write_overlap_n("", "") == 0
    assert "Safety" in SHEET_ORDER
    assert "Freeze_Candidate" in SHEET_ORDER
    assert PARENT_OUT.name == "support_resistance_matched_separation_not_a_strategy_v1"
    assert OUT.name == "support_resistance_mechanism_to_complete_strategy_v1"


def test_parent_not_overwritten_constant():
    assert CASE_COMBINED != CASE_NONE
    assert CASE_BOTH.startswith("SR_A2_AND_C1")
