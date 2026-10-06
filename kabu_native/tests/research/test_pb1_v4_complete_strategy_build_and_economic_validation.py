"""Tests for PB1 V4 Complete Strategy binding. Does not open Confirmation/FV economic outcomes."""
from __future__ import annotations

from research.pb1_v4_clarified_machine_correction_v4.definitions import machine_sha256 as v4_sha
from research.pb1_v4_complete_strategy_build_and_economic_validation import (
    CAP,
    CASE_READY,
    EXPECTED_MACHINE_SHA256,
    EXPECTED_SOURCE_INVENTORY_SHA256,
    FROZEN_ENTRY_IDENTITY,
    NEXT_CONF1,
    SHARES,
)
from research.pb1_v4_complete_strategy_build_and_economic_validation.analyze import freeze_decision
from research.pb1_v4_complete_strategy_build_and_economic_validation.clocks import flatten_open, next_open_after
from research.pb1_v4_complete_strategy_build_and_economic_validation.exits import resolve_exit
from research.pb1_v4_complete_strategy_build_and_economic_validation.freeze import complete_strategy_sha256
from research.pb1_v4_complete_strategy_build_and_economic_validation.gate import identity_gate
from research.pb1_v4_complete_strategy_build_and_economic_validation.isolation import OUT, write_overlap_n
from research.pb1_v4_complete_strategy_build_and_economic_validation.portfolio import replay_occupancy
from research.pb1_v4_complete_strategy_build_and_economic_validation.publish import SHEET_ORDER


def _rec() -> dict:
    times = [f"09:{m:02d}" for m in range(30, 60)] + ["11:19", "11:20", "12:30", "15:19", "15:20"]
    return {"t": times, "o": [100.0 + i for i in range(len(times))], "n": len(times)}


def test_identity_and_sheets():
    g = identity_gate()
    assert v4_sha() == EXPECTED_MACHINE_SHA256
    assert g["ok"] is True
    assert g["source_inventory_sha"] == EXPECTED_SOURCE_INVENTORY_SHA256
    assert FROZEN_ENTRY_IDENTITY == "PB1_V4_CLARIFIED_MACHINE_CORRECTION_V4_FROZEN_V1"
    assert write_overlap_n("", "") == 0
    assert CAP == 5 and SHARES == 100
    assert SHEET_ORDER[0] == "Manifest"
    assert "Economic_Confirmation" in SHEET_ORDER
    assert "pb1_v4_complete_strategy_build_and_economic_validation" in str(OUT).replace("\\", "/")
    a = complete_strategy_sha256(machine_sha=EXPECTED_MACHINE_SHA256, source_inventory_sha=EXPECTED_SOURCE_INVENTORY_SHA256)
    b = complete_strategy_sha256(machine_sha=EXPECTED_MACHINE_SHA256, source_inventory_sha=EXPECTED_SOURCE_INVENTORY_SHA256)
    assert a == b


def test_next_open_skips_lunch_allows_pm():
    rec = _rec()
    nxt = next_open_after(rec, after_t="11:19", allow_pm=True)
    assert nxt and nxt["t"] == "11:20" and nxt["same_bar"] is False
    nxt = next_open_after(rec, after_t="11:20", allow_pm=True)
    assert nxt and nxt["t"] == "12:30"
    nxt = next_open_after(rec, after_t="11:19", allow_pm=False)
    assert nxt and nxt["t"] == "11:20"
    flat = flatten_open(rec, fill_t="09:35")
    assert flat and flat["t"] == "15:20"


def test_technical_exit_and_flatten():
    rec = _rec()
    lost = resolve_exit(rec, fill_t="09:35", thesis_lost=True, thesis_lost_at="09:44")
    assert lost["ok"] is True
    assert lost["thesis_death"] is True
    assert lost["exit_t"] > "09:44"
    assert lost["same_bar_exit"] is False
    flat = resolve_exit(rec, fill_t="09:35", thesis_lost=False, thesis_lost_at=None)
    assert flat["ok"] is True
    assert flat["ops_flatten"] is True
    assert flat["exit_t"] == "15:20"
    dead = resolve_exit(rec, fill_t="09:50", thesis_lost=True, thesis_lost_at="09:40")
    assert dead["ok"] is False


def test_cap_and_same_symbol_and_reentry():
    cands = [
        {
            "date": "20241001",
            "symbol": "1111",
            "signal_t": "09:34",
            "entry_t": "09:35",
            "exit_t": "09:50",
            "net_pnl_yen": 1,
        },
        {
            "date": "20241001",
            "symbol": "1111",
            "signal_t": "09:36",
            "entry_t": "09:37",
            "exit_t": "10:00",
            "net_pnl_yen": 1,
        },
        {
            "date": "20241001",
            "symbol": "2222",
            "signal_t": "09:34",
            "entry_t": "09:35",
            "exit_t": "15:20",
            "net_pnl_yen": 1,
        },
    ]
    occ = replay_occupancy(cands, cap=1)
    assert occ["cap"] == 1
    assert occ["same_symbol_blocked_n"] >= 1 or occ["cap_blocked_n"] >= 1
    assert occ["same_symbol_overlap_violation_n"] == 0
    assert occ["cap_violation_n"] == 0
    later = [
        {
            "date": "20241001",
            "symbol": "1111",
            "signal_t": "09:34",
            "entry_t": "09:35",
            "exit_t": "09:40",
            "net_pnl_yen": -10,
        },
        {
            "date": "20241001",
            "symbol": "1111",
            "signal_t": "09:41",
            "entry_t": "09:42",
            "exit_t": "10:00",
            "net_pnl_yen": 10,
        },
    ]
    occ2 = replay_occupancy(later, cap=5)
    assert occ2["trade_n"] == 2
    assert occ2["same_symbol_blocked_n"] == 0


def test_freeze_ignores_development_pnl():
    identity = {
        "ok": True,
        "machine_sha": EXPECTED_MACHINE_SHA256,
        "source_inventory_sha": EXPECTED_SOURCE_INVENTORY_SHA256,
    }
    roles = {
        "ok": True,
        "economic_confirmation_1": {"ECONOMIC_OUTCOME_UNOPENED": True, "opened_this_task": False},
        "economic_confirmation_2": {"ECONOMIC_OUTCOME_UNOPENED": True, "opened_this_task": False},
    }
    walked = {"ok": True, "same_bar_entry_n": 0}
    trade = {
        "date": "20241001",
        "symbol": "1111",
        "signal_t": "09:34",
        "entry_t": "09:35",
        "exit_t": "15:20",
        "same_bar_entry": False,
        "used_mid": False,
        "net_pnl_yen": -999999.0,
    }
    replay = {
        "ok": True,
        "trades": [trade],
        "skip": {},
        "fill_px_mismatch_n": 0,
        "cap_violation_n": 0,
        "same_symbol_overlap_violation_n": 0,
        "max_concurrent": 1,
        "candidate_n": 1,
    }
    exec_inv = {"historical_research_approximation": {"RESEARCH_EXECUTION_APPROXIMATION": True}}
    exit_inv = {"selected_technical_exit": {"id": "PB1_V4_THESIS_LOST_NEXT_OPEN"}}
    d = freeze_decision(
        identity=identity,
        roles=roles,
        walked=walked,
        replay=replay,
        exec_inv=exec_inv,
        exit_inv=exit_inv,
    )
    assert d["ok"] is True
    assert d["VERDICT"] == CASE_READY
    assert d["NEXT"] == NEXT_CONF1
    assert d["development_pnl_did_not_gate_freeze"] is True
    assert d["COMPLETE_STRATEGY_SHA256"]
