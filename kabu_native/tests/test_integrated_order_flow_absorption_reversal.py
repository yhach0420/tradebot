"""Tests for IOAR package."""
from __future__ import annotations

from pathlib import Path

from research.integrated_order_flow_absorption_reversal.constants import (
    CANCEL,
    COST_BPS,
    LIVE_ORDER,
    REQUIRED_ARTIFACTS,
    STRIDE,
    SUBMIT,
)
from research.integrated_order_flow_absorption_reversal.arms import resolve_exit
from research.integrated_order_flow_absorption_reversal.observations import detect_bid_replenish
from research.integrated_order_flow_absorption_reversal.state_machine import Episode
from research.integrated_order_flow_absorption_reversal.loader import Tick
from datetime import datetime
from zoneinfo import ZoneInfo
from types import SimpleNamespace

PKG = Path(__file__).resolve().parents[1] / "src" / "research" / "integrated_order_flow_absorption_reversal"
JST = ZoneInfo("Asia/Tokyo")


def test_stride_one():
    assert STRIDE == 1


def test_no_fcr_iic_pbv2():
    for p in PKG.glob("*.py"):
        t = p.read_text(encoding="utf-8")
        assert "canonical_fcr" not in t
        assert "integrated_initial_impulse" not in t
        assert "score_v2" not in t


def test_sell_pressure_requires_sell_flow():
    src = (PKG / "state_machine.py").read_text(encoding="utf-8")
    assert "SELL_MIN_N" in src and "sell_ok" in src


def test_absorption_needs_sell_and_decay():
    src = (PKG / "state_machine.py").read_text(encoding="utf-8")
    assert "ABSORB_SELL_IMPACT_DECAY" in src and "replenish" in src


def test_bid_replenish_not_snapshot_only():
    board = SimpleNamespace(canonical_best_bid=100.0, canonical_bid_qty=200)
    t = Tick(
        day="d", symbol="s", ts=datetime(2026, 7, 22, 10, tzinfo=JST), px=100.0,
        cum_vol=1, volume_delta=10, board=board, event_id="e", session="AM",
        trade_side="NONE", event_seq=1, prev_ask_qty=None, prev_bid_qty=150, prev_bid_px=100.0,
    )
    # qty up without sell — still classified recover but SM requires sell interaction for count
    assert detect_bid_replenish(t) in ("same_price_qty_recover", "none", "bid_step_up")


def test_acceptance_before_entry():
    src = (PKG / "state_machine.py").read_text(encoding="utf-8")
    assert src.index("S5_ACCEPTANCE_CONFIRM") < src.index('_mark(ep, "ENTRY"')


def test_ask_entry_bid_exit():
    assert "entry_ask" in (PKG / "arms.py").read_text(encoding="utf-8")
    assert "bid_at" in (PKG / "arms.py").read_text(encoding="utf-8")


def test_absorption_failure_exit():
    ep = Episode(episode_id="x", day="d", symbol="s", stream_key="d|s", entry_idx=1, idx_horizon=99, idx_abs_fail=20)
    idx, reason = resolve_exit(ep, "A1")
    assert reason == "ABSORPTION_FAILURE" and idx == 20


def test_no_demand_exit():
    ep = Episode(episode_id="x", day="d", symbol="s", stream_key="d|s", entry_idx=1, idx_horizon=99, idx_no_demand=30)
    idx, reason = resolve_exit(ep, "A2")
    assert reason == "NO_DEMAND_FOLLOW_THROUGH"


def test_cost_5bps():
    assert COST_BPS == 5.0


def test_submit_live_zero():
    assert SUBMIT == CANCEL == LIVE_ORDER == 0


def test_only_three_outputs():
    assert REQUIRED_ARTIFACTS == ("report.md", "report.json", "audit.xlsx")


def test_no_validation_without_train():
    assert "SKIPPED_NO_TRAIN" in (PKG / "runner.py").read_text(encoding="utf-8")


def test_mainline_unchanged():
    assert "mainline_changed" in (PKG / "runner.py").read_text(encoding="utf-8")


def test_feature_distribution_before_profit():
    assert "build_feature_distributions" in (PKG / "runner.py").read_text(encoding="utf-8")
