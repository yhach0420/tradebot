"""Behavior group × sequence tests. Frozen Validation closed. No paid data. No 5-minute grid."""
from __future__ import annotations

import inspect

from research.behavior_group_sequence_mechanism_v1 import (
    CASE_BIND,
    CASE_DESCRIPTIVE,
    CASE_EXECUTABLE,
    CASE_IMPROVES,
    FIVE_MINUTE_GRID,
    FROZEN_VALIDATION_OPENED,
    FULL_DISCOVERY_GROUP_LEAKAGE,
    HM1_TUNED,
    KABU_50_APPLIED,
    MATERIAL_BPS,
    NEW_PAID_DATA,
    PARENT_VERDICT,
    PURCHASE_REQUESTED,
    X1_TAX_BPS,
    ZERO_TRADE_CAUSE,
)
from research.behavior_group_sequence_mechanism_v1.analyze import decide
from research.behavior_group_sequence_mechanism_v1.isolation import ATLAS_OUT, OUT, write_overlap_n
from research.behavior_group_sequence_mechanism_v1.prequential import aggregate_profiles, assign_groups, maps_for_blocks
from research.behavior_group_sequence_mechanism_v1.publish import SHEET_ORDER
from research.behavior_group_sequence_mechanism_v1.replay import SPECS, _select
from research.one_minute_native_playbook_discovery_v1.episodes import classify
from research.one_minute_native_playbook_discovery_v1.states import families_at


def test_constants_and_sheets():
    assert PARENT_VERDICT == "ONE_MINUTE_BEHAVIOR_ATLAS_READY_NO_COMPLETE_PLAYBOOK_V1"
    assert FROZEN_VALIDATION_OPENED is False
    assert KABU_50_APPLIED is False
    assert PURCHASE_REQUESTED is False
    assert HM1_TUNED is False
    assert NEW_PAID_DATA is False
    assert FIVE_MINUTE_GRID is False
    assert FULL_DISCOVERY_GROUP_LEAKAGE is False
    assert X1_TAX_BPS == 8.0
    assert MATERIAL_BPS == 2.0
    assert OUT.name == "behavior_group_sequence_mechanism_v1"
    assert ATLAS_OUT.name == "one_minute_native_playbook_discovery_v1"
    assert write_overlap_n("", "") == 0
    assert SHEET_ORDER[0] == "Binding"
    assert SHEET_ORDER[-1] == "Safety"
    assert "Prequential_Group_Map" in SHEET_ORDER
    assert "Global_vs_Group" in SHEET_ORDER
    assert "BreakEven_Cost" in SHEET_ORDER
    assert "HM1_Comparison" in SHEET_ORDER
    assert tuple(s["candidate_id"] for s in SPECS[:2]) == ("GM_CATCHUP_RS_TURN", "BG_CATCHUP_RS_TURN")


def test_decide_and_zero_trade_cause():
    assert decide(bind_ok=False, material_n=1, promoted_n=1)["VERDICT"] == CASE_BIND
    assert decide(bind_ok=True, material_n=0, promoted_n=1)["VERDICT"] == CASE_EXECUTABLE
    assert decide(bind_ok=True, material_n=1, promoted_n=0)["VERDICT"] == CASE_IMPROVES
    assert decide(bind_ok=True, material_n=0, promoted_n=0)["VERDICT"] == CASE_DESCRIPTIVE
    assert "impulse_up" in ZERO_TRADE_CAUSE
    fams = families_at({"impulse_up": True}, recent_impulse_up=False, lagging=True, leader_impulse_recent=False)
    assert "LAG_CATCHUP" not in fams
    fams2 = families_at({"impulse_up": True}, recent_impulse_up=False, lagging=True, leader_impulse_recent=True)
    assert "LAG_CATCHUP" in fams2
    assert classify(["VOL_EXPAND", "IMPULSE_UP", "LAG_CATCHUP"]) != "LAG_THEN_CATCHUP"


def test_prequential_no_future_leakage():
    daily = []
    for i, day in enumerate(["20240917", "20240918", "20240919", "20240920"]):
        daily.append(
            {
                "date": day,
                "symbol": "6758",
                "sector": "Electric Appliances",
                "present_n": 100,
                "leading_n": 5 if i < 2 else 80,
                "lagging_n": 40 if i < 2 else 5,
                "impulse_n": 30,
                "continuation_n": 10 if i < 2 else 25,
                "reversal_n": 15 if i < 2 else 2,
                "favor_first_n": 20,
                "opening_n": 1,
                "opening_hold_n": 1,
                "gap_up_n": 1,
                "gap_up_hold_n": 1,
            }
        )
        daily.append(
            {
                "date": day,
                "symbol": "9983",
                "sector": "Retail Trade",
                "present_n": 100,
                "leading_n": 1,
                "lagging_n": 50,
                "impulse_n": 25,
                "continuation_n": 2,
                "reversal_n": 20,
                "favor_first_n": 5,
                "opening_n": 1,
                "opening_hold_n": 0,
                "gap_up_n": 0,
                "gap_up_hold_n": 0,
            }
        )
    early = aggregate_profiles(daily, end_date="20240918")
    late = aggregate_profiles(daily, end_date="20240920")
    assert early[0]["train_day_n"] == 2
    assert late[0]["train_day_n"] == 4
    blocks = [
        {"block_id": "D1", "last": "20240918", "dates": ["20240917", "20240918"]},
        {"block_id": "D2", "last": "20240919", "dates": ["20240919"]},
        {"block_id": "D3", "last": "20240920", "dates": ["20240920"]},
    ]
    packed = maps_for_blocks(daily=daily, blocks=blocks)
    assert packed["full_discovery_group_leakage"] is False
    assert packed["d1_characterization_only"] is True
    d2_end = next(d["profile_training_end"] for d in packed["definitions"] if d["eval_block"] == "D2")
    assert d2_end == "20240918"
    d3_end = next(d["profile_training_end"] for d in packed["definitions"] if d["eval_block"] == "D3")
    assert d3_end == "20240919"
    groups = assign_groups(late)
    assert groups["future_block_agree_used"] is False
    events = [
        {"sequence": "CATCHUP_RS_TURN", "x0_entry_open": 100.0, "block": "D1", "preq_group": None, "date": "20240917", "event_time": "10:00", "symbol": "6758"},
        {"sequence": "CATCHUP_RS_TURN", "x0_entry_open": 100.0, "block": "D2", "preq_group": "SECTOR_LAGGARD_CATCHUP", "date": "20240919", "event_time": "10:00", "symbol": "6758"},
    ]
    spec = {"sequence": "CATCHUP_RS_TURN", "group_filter": "SECTOR_LAGGARD_CATCHUP", "window": "D2_D4"}
    got = _select(events, spec)
    assert len(got) == 1
    assert got[0]["block"] == "D2"


def test_no_grids_or_paid_imports():
    import research.behavior_group_sequence_mechanism_v1.analyze as a
    import research.behavior_group_sequence_mechanism_v1.engine as e

    src_a = inspect.getsource(a)
    src_e = inspect.getsource(e)
    assert "import yfinance" not in src_a.lower()
    assert "import yfinance" not in src_e.lower()
    assert "grid_clocks(" not in src_a
    assert "grid_clocks(" not in src_e
    assert FROZEN_VALIDATION_OPENED is False
    assert FIVE_MINUTE_GRID is False
    assert "LAG_CATCHUP" not in src_e
