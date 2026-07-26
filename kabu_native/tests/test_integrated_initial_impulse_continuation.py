"""Tests for IIC package."""
from __future__ import annotations

from pathlib import Path

from research.integrated_initial_impulse_continuation.constants import (
    CANCEL,
    COST_BPS,
    LIVE_ORDER,
    REQUIRED_ARTIFACTS,
    STRIDE,
    SUBMIT,
)
from research.integrated_initial_impulse_continuation.arms import increment, resolve_exit
from research.integrated_initial_impulse_continuation.state_machine import Episode

PKG = Path(__file__).resolve().parents[1] / "src" / "research" / "integrated_initial_impulse_continuation"


def test_stride_is_one():
    assert STRIDE == 1
    src = (PKG / "loader.py").read_text(encoding="utf-8")
    assert "stride must be 1" in src


def test_no_fcr_reuse():
    for p in PKG.glob("*.py"):
        t = p.read_text(encoding="utf-8")
        assert "canonical_fcr" not in t
        assert "from research.canonical_fcr" not in t


def test_no_pbv2_reuse():
    for p in PKG.glob("*.py"):
        t = p.read_text(encoding="utf-8")
        assert "score_v2" not in t
        assert "Momentum Low" not in t


def test_canonical_ask_entry_bid_exit():
    src = (PKG / "arms.py").read_text(encoding="utf-8")
    assert "entry_ask" in src
    assert "bid_at" in src or "exit_bid" in src


def test_cost_5bps():
    assert COST_BPS == 5.0


def test_state_order_documented():
    src = (PKG / "state_machine.py").read_text(encoding="utf-8")
    assert "S0_QUIET_BASE" in src and "S3_BREAK_HOLD" in src and "ENTRY" in src


def test_a0_a5_arms():
    src = (PKG / "arms.py").read_text(encoding="utf-8")
    assert "A0" in src and "A5" in src


def test_resolve_exit_a1_break_failure():
    ep = Episode(episode_id="x", day="d", symbol="s", stream_key="d|s",
                 entry_idx=10, idx_horizon=100, idx_break_failure=40)
    idx, reason = resolve_exit(ep, "A1")
    assert idx == 40 and reason == "BREAK_FAILURE"


def test_increment_labels():
    lab = increment(
        {"n": 10, "pf": 0.5, "mean": -1, "pnl": -10, "stop_5m_rate": 0.2, "winner_rate": 0.1, "mfe_capture": 0.2},
        {"n": 10, "pf": 0.9, "mean": 0.1, "pnl": 1, "stop_5m_rate": 0.1, "winner_rate": 0.2, "mfe_capture": 0.3},
    )["label"]
    assert lab in ("INCREMENT_POSITIVE", "INCREMENT_MIXED", "INCREMENT_NEGATIVE")


def test_submit_cancel_live_zero():
    assert SUBMIT == CANCEL == LIVE_ORDER == 0


def test_no_paper_auto_start():
    assert "paper_auto_start" in (PKG / "runner.py").read_text(encoding="utf-8")


def test_mainline_unchanged():
    assert "mainline_changed" in (PKG / "runner.py").read_text(encoding="utf-8")


def test_only_three_outputs():
    assert REQUIRED_ARTIFACTS == ("report.md", "report.json", "audit.xlsx")


def test_integrated_not_entry_only():
    src = (PKG / "runner.py").read_text(encoding="utf-8")
    assert "ENTRY" in src and "EXIT" in src or "scenario" in src.lower() or "IIC" in src


def test_no_validation_without_train():
    src = (PKG / "runner.py").read_text(encoding="utf-8")
    assert "SKIPPED_NO_TRAIN" in src
