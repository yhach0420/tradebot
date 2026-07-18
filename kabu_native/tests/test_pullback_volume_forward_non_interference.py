"""Phase687W57 — logger must not change GateDecision / ENTRY / PullbackMisread."""

from __future__ import annotations

import copy

from small_paper.pullback_misread_entry_guard_shadow import (
    would_block_pullback_dynamic40_shadow,
    would_block_pullback_misread_guard,
)
from small_paper.pullback_volume_forward_logger import (
    PullbackVolumeForwardState,
    build_entry_row,
    format_discord_lines,
    logger_enabled,
)


def test_logger_disabled_by_default(monkeypatch):
    monkeypatch.delenv("PULLBACK_VOLUME_FORWARD", raising=False)
    monkeypatch.delenv("KABU_PAPER_RUNTIME", raising=False)
    assert logger_enabled() is False


def test_pullback_predicate_unchanged_by_logger_import():
    fields = {
        "entry_rise_5min_pct": -0.2,
        "entry_vwap_dev_pct": -0.1,
        "universe_slot": "dynamic",
    }
    assert would_block_pullback_misread_guard(fields) is True
    assert would_block_pullback_dynamic40_shadow(fields) is True
    fields2 = {
        "entry_rise_5min_pct": 0.2,
        "entry_vwap_dev_pct": -0.1,
        "universe_slot": "dynamic",
    }
    assert would_block_pullback_dynamic40_shadow(fields2) is False


def test_logger_does_not_mutate_gate_fields():
    trade = {
        "symbol": "7203.T",
        "entry_time": "2026-07-18T10:00:00+09:00",
        "entry_rise_5min_pct": -0.2,
        "entry_vwap_dev_pct": -0.1,
        "universe_slot": "dynamic",
        "entry_price": 1000,
        "gate_accept": True,
        "final_reject_reason": "",
    }
    before = copy.deepcopy(trade)
    st = PullbackVolumeForwardState(enabled=True, trading_date="20260718")
    build_entry_row(st, trade, official_entry=True, official_reject=False)
    assert trade["gate_accept"] == before["gate_accept"]
    assert trade["final_reject_reason"] == before["final_reject_reason"]
    assert trade["entry_rise_5min_pct"] == before["entry_rise_5min_pct"]


def test_logger_returns_no_decision_object():
    st = PullbackVolumeForwardState(enabled=True, trading_date="20260718")
    row = build_entry_row(
        st,
        {
            "symbol": "7203.T",
            "entry_time": "2026-07-18T10:00:00+09:00",
            "entry_rise_5min_pct": -0.2,
            "entry_vwap_dev_pct": -0.1,
            "universe_slot": "dynamic",
            "entry_price": 1000,
        },
        official_entry=True,
        official_reject=False,
    )
    assert row is not None
    assert "reject" not in row or row.get("official_reject") is False
    assert "block_entry" not in row
    assert row.get("pullback_misread_shadow_hit") is True


def test_discord_lines_collecting_only_no_recommend():
    lines = format_discord_lines(
        {
            "pullback_volume_forward": {
                "enabled": True,
                "hits": 3,
                "volume_high_n": 1,
                "volume_low_n": 1,
                "volume_high": {"healthy_rate": 0.8},
                "volume_low": {"collapse_rate": 0.6},
                "board_volume": {"board_down_vol_low": {"n": 1}},
            }
        }
    )
    text = "\n".join(lines)
    assert "status: collecting" in text
    assert "Reject" not in text
    assert "Permit" not in text
    assert "採用" not in text
    assert "[Pullback Volume Forward]" in text


def test_on_off_shadow_predicate_identical(monkeypatch):
    """Simulated ON/OFF: PullbackMisread hit set identical whether logger runs."""
    monkeypatch.setenv("PULLBACK_VOLUME_FORWARD", "1")
    trades = [
        {
            "symbol": "A.T",
            "entry_time": "t1",
            "entry_rise_5min_pct": -0.1,
            "entry_vwap_dev_pct": -0.1,
            "universe_slot": "dynamic",
        },
        {
            "symbol": "B.T",
            "entry_time": "t2",
            "entry_rise_5min_pct": 0.1,
            "entry_vwap_dev_pct": -0.1,
            "universe_slot": "dynamic",
        },
    ]
    hits_off = [would_block_pullback_dynamic40_shadow(t) for t in trades]
    st = PullbackVolumeForwardState(enabled=True, trading_date="20260718")
    for t in trades:
        build_entry_row(
            st,
            {**t, "entry_time": f"2026-07-18T09:00:00+09:00-{t['symbol']}", "entry_price": 1},
            official_entry=True,
            official_reject=False,
        )
    hits_on = [would_block_pullback_dynamic40_shadow(t) for t in trades]
    assert hits_off == hits_on
