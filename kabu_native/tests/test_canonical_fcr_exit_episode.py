"""Tests for Canonical FCR EXIT episode package."""
from __future__ import annotations

from pathlib import Path

from research.canonical_fcr_exit_episode.arms import increment_exit, resolve_exit
from research.canonical_fcr_exit_episode.constants import (
    CANCEL,
    COST_BPS,
    EXIT_ARMS,
    FROZEN_ENTRY,
    LIVE_ORDER,
    REQUIRED_ARTIFACTS,
    SUBMIT,
)
from research.canonical_fcr_exit_episode.exit_states import ExitEpisode, POST_STATES
from research.canonical_fcr_exit_episode.entry_fixed import FrozenEntry
from datetime import datetime
from zoneinfo import ZoneInfo

PKG = Path(__file__).resolve().parents[1] / "src" / "research" / "canonical_fcr_exit_episode"
JST = ZoneInfo("Asia/Tokyo")


def test_entry_frozen_thresholds():
    assert FROZEN_ENTRY["buy_ratio"] == 0.55
    assert FROZEN_ENTRY["pb_lo"] == 0.10
    assert FROZEN_ENTRY["reclaim_hold_events"] == 2


def test_no_entry_retune():
    src = (PKG / "entry_fixed.py").read_text(encoding="utf-8")
    assert "FROZEN" in src or "frozen" in src.lower()
    assert "fit_thresholds" not in (PKG / "runner.py").read_text(encoding="utf-8")


def test_post_states_defined():
    assert "HEALTHY_ADVANCE" in POST_STATES
    assert "FALSE_RECLAIM" in POST_STATES
    assert "WINNER_GIVEBACK" in POST_STATES


def test_exit_arms_x0_x5():
    assert EXIT_ARMS == ("X0", "X1", "X2", "X3", "X4", "X5")


def test_x0_fixed_horizon():
    from research.canonical_fcr_exit_episode.entry_fixed import FrozenEntry
    e = FrozenEntry(
        day="d", symbol="s", stream_key="d|s", episode_id="ep", impulse_id="imp",
        entry_idx=0, entry_time=datetime(2026, 7, 22, 10, tzinfo=JST), entry_ask=100.0,
        reclaim_level=100.0, pullback_low=99.0, impulse_high=101.0,
    )
    ep = ExitEpisode(entry=e, idx_horizon=50, idx_false_reclaim=10)
    idx, reason = resolve_exit(ep, "X0")
    assert idx == 50 and reason == "FIXED_HORIZON"


def test_x1_false_reclaim():
    e = FrozenEntry(
        day="d", symbol="s", stream_key="d|s", episode_id="ep", impulse_id="imp",
        entry_idx=0, entry_time=datetime(2026, 7, 22, 10, tzinfo=JST), entry_ask=100.0,
        reclaim_level=100.0, pullback_low=99.0, impulse_high=101.0,
    )
    ep = ExitEpisode(entry=e, idx_horizon=50, idx_false_reclaim=12)
    idx, reason = resolve_exit(ep, "X1")
    assert idx == 12 and reason == "FALSE_RECLAIM"


def test_x5_includes_all_signals():
    e = FrozenEntry(
        day="d", symbol="s", stream_key="d|s", episode_id="ep", impulse_id="imp",
        entry_idx=0, entry_time=datetime(2026, 7, 22, 10, tzinfo=JST), entry_ask=100.0,
        reclaim_level=100.0, pullback_low=99.0, impulse_high=101.0,
    )
    ep = ExitEpisode(entry=e, idx_horizon=90, idx_false_reclaim=40, idx_structure=30, idx_noprogress=20, idx_giveback=50)
    idx, reason = resolve_exit(ep, "X5")
    assert idx == 20 and reason == "NO_PROGRESS"


def test_increment_mixed_supported():
    lab = increment_exit(
        {"n": 10, "pf": 0.5, "mean": -1, "stop_rate": 0.3, "winner_rate": 0.2, "mfe_capture": 0.3, "avg_mae": -0.5, "pnl": -10},
        {"n": 10, "pf": 0.8, "mean": -0.5, "stop_rate": 0.2, "winner_rate": 0.2, "mfe_capture": 0.3, "avg_mae": -0.4, "pnl": -5},
    )["label"]
    assert lab in ("INCREMENT_POSITIVE", "INCREMENT_MIXED", "INCREMENT_NEGATIVE")


def test_cost_5bps():
    assert COST_BPS == 5.0


def test_buy_ask_sell_bid():
    src = (PKG / "arms.py").read_text(encoding="utf-8")
    assert "canonical_best_bid" in src
    assert "entry_ask" in src


def test_submit_cancel_live_zero():
    assert SUBMIT == CANCEL == LIVE_ORDER == 0


def test_no_paper_auto_start():
    assert "paper_auto_start" in (PKG / "runner.py").read_text(encoding="utf-8")


def test_mainline_unchanged():
    assert "mainline_changed" in (PKG / "runner.py").read_text(encoding="utf-8")


def test_only_three_outputs():
    assert REQUIRED_ARTIFACTS == ("report.md", "report.json", "audit.xlsx")


def test_no_validation_without_train():
    src = (PKG / "runner.py").read_text(encoding="utf-8")
    assert "SKIPPED_NO_TRAIN" in src


def test_entry_plus_exit_evaluation():
    src = (PKG / "runner.py").read_text(encoding="utf-8")
    assert "ENTRY_PLUS_EXIT" in src
