"""Tests for Complete Strategy Economic Confirmation 1. Does not load OC economic bars."""
from __future__ import annotations

from research.pb1_v4_complete_strategy_build_and_economic_validation.freeze import complete_strategy_sha256
from research.pb1_v4_complete_strategy_economic_confirmation1 import (
    CASE_FAIL,
    CASE_INVALID,
    CASE_PASS,
    EXPECTED_COMPLETE_STRATEGY_SHA256,
    EXPECTED_MACHINE_SHA256,
    EXPECTED_SOURCE_INVENTORY_SHA256,
    NEXT_CONF2,
    NEXT_DECOMP,
    PRECOMMIT_ID,
)
from research.pb1_v4_complete_strategy_economic_confirmation1.decide import decide
from research.pb1_v4_complete_strategy_economic_confirmation1.gate import identity_gate
from research.pb1_v4_complete_strategy_economic_confirmation1.isolation import OUT, write_overlap_n
from research.pb1_v4_complete_strategy_economic_confirmation1.metrics import concentration, economic_metrics
from research.pb1_v4_complete_strategy_economic_confirmation1.precommit import conf1_precommit
from research.pb1_v4_complete_strategy_economic_confirmation1.publish import SHEET_ORDER


def test_frozen_identity_matches_complete_strategy_sha():
    g = identity_gate()
    assert g["ok"] is True
    assert g["DO_NOT_OPEN_ECONOMIC_CONFIRMATION1"] is False
    assert g["COMPLETE_STRATEGY_SHA256"] == EXPECTED_COMPLETE_STRATEGY_SHA256
    live = complete_strategy_sha256(
        machine_sha=EXPECTED_MACHINE_SHA256,
        source_inventory_sha=EXPECTED_SOURCE_INVENTORY_SHA256,
    )
    assert live == EXPECTED_COMPLETE_STRATEGY_SHA256
    assert write_overlap_n("", "") == 0
    assert "Concentration" in SHEET_ORDER
    assert "pb1_v4_complete_strategy_economic_confirmation1" in str(OUT).replace("\\", "/")


def test_precommit_stable_before_pnl():
    kwargs = dict(
        machine_sha=EXPECTED_MACHINE_SHA256,
        source_inventory_sha=EXPECTED_SOURCE_INVENTORY_SHA256,
        complete_strategy_sha256=EXPECTED_COMPLETE_STRATEGY_SHA256,
        confirmation_n=97,
        confirmation_first="20251127",
        confirmation_last="20260421",
        lookback_n=80,
        symbol_n=105,
    )
    a = conf1_precommit(**kwargs)
    b = conf1_precommit(**kwargs)
    assert a["PRECOMMIT_SHA256"] == b["PRECOMMIT_SHA256"]
    assert a["precommit_id"] == PRECOMMIT_ID
    assert a["FROZEN_VALIDATION_ECONOMIC_OPENED"] is False


def test_concentration_and_gates():
    trades = [
        {"date": "20251201", "symbol": "1111", "net_pnl_yen": 100.0, "gross_pnl_yen": 110.0, "execution_cost_yen": 10.0, "holding_min": 10, "entry_type": "E0", "side": "bull", "exit_reason": "PB1_V4_THESIS_LOST_NEXT_OPEN", "reentry_n": 0},
        {"date": "20251201", "symbol": "2222", "net_pnl_yen": -20.0, "gross_pnl_yen": -10.0, "execution_cost_yen": 10.0, "holding_min": 10, "entry_type": "E1", "side": "bear", "exit_reason": "SESSION_FLAT_1520", "reentry_n": 0},
        {"date": "20251202", "symbol": "1111", "net_pnl_yen": -30.0, "gross_pnl_yen": -20.0, "execution_cost_yen": 10.0, "holding_min": 10, "entry_type": "E1", "side": "bull", "exit_reason": "SESSION_FLAT_1520", "reentry_n": 1},
    ]
    c = concentration(trades)
    assert c["net_pnl_ex_top1_trade"] == -50.0
    replay = {
        "ok": True,
        "trades": trades,
        "blocked_rows": [],
        "signal_n": 3,
        "fill_n": 3,
        "max_concurrent": 2,
        "cap_blocked_n": 0,
        "same_symbol_blocked_n": 0,
        "e0_n": 47,
        "e1_n": 103,
        "same_bar_entry_n": 0,
        "fill_px_mismatch_n": 0,
        "skip": {},
        "same_symbol_overlap_violation_n": 0,
        "cap_violation_n": 0,
        "FROZEN_VALIDATION_ECONOMIC_OPENED": False,
        "PROSPECTIVE_DATA_OPENED": False,
    }
    m = economic_metrics(replay=replay)
    assert m["gross_minus_cost_equals_net"] is True
    assert m["net_pnl_yen"] == 50.0
    d = decide(invariants={"ok": True, "counts": {}}, metrics=m)
    assert d["VERDICT"] == CASE_PASS
    assert d["NEXT"] == NEXT_CONF2
    fail_m = dict(m)
    fail_m["primary_gate"] = {
        "net_pnl_yen_gt_0": False,
        "profit_factor_gt_1": True,
        "mean_net_pnl_per_trade_gt_0": True,
    }
    d2 = decide(invariants={"ok": True}, metrics=fail_m)
    assert d2["VERDICT"] == CASE_FAIL
    assert d2["NEXT"] == NEXT_DECOMP
    d3 = decide(invariants={"ok": False, "reason": "x"}, metrics=m)
    assert d3["VERDICT"] == CASE_INVALID
    assert d3["economic_verdict_issued"] is False
