"""Phase 52: allowed trading windows (operational safety only)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from research.exposure_gate import REJECT_OUTSIDE_ALLOWED_TRADING_WINDOW, ExposureGate, ExposureGateConfig
from small_paper.allowed_trading_windows import (
    TradingWindow,
    is_in_allowed_trading_window,
    parse_allowed_trading_windows,
)
from small_paper.session_schedule import parse_hhmm

JST = ZoneInfo("Asia/Tokyo")
WINDOWS = parse_allowed_trading_windows(
    [
        {"start": "09:05", "end": "11:23"},
        {"start": "12:33", "end": "15:20"},
    ]
)


@pytest.mark.parametrize(
    "hhmm,expected",
    [
        ("09:04", False),
        ("09:05", True),
        ("11:23", True),
        ("11:24", False),
        ("12:32", False),
        ("12:33", True),
        ("15:20", True),
        ("15:21", False),
    ],
)
def test_is_in_allowed_trading_window(hhmm: str, expected: bool) -> None:
    dt = datetime.fromisoformat(f"2026-05-18T{hhmm}:00+09:00")
    assert is_in_allowed_trading_window(dt, WINDOWS) is expected


def test_gate_rejects_outside_window_before_quality() -> None:
    cfg = ExposureGateConfig(
        profile="momentum_volume_v13_combined",
        min_continuation_quality=0.99,
    )
    gate = ExposureGate(cfg, allowed_windows=WINDOWS)
    trade = {
        "profile": "momentum_volume_v13_combined",
        "symbol": "7203.T",
        "entry_time": "2026-05-18T11:30:00+09:00",
        "exit_time": "2026-05-18T11:35:00+09:00",
        "trade_date": "2026-05-18",
        "pnl_pct": 0.0,
    }
    decision = gate.evaluate_entry(trade)
    assert not decision.accept
    assert decision.reason == REJECT_OUTSIDE_ALLOWED_TRADING_WINDOW


def test_gate_accepts_inside_window() -> None:
    cfg = ExposureGateConfig(
        profile="momentum_volume_v13_combined",
        min_continuation_quality=0.0,
        reject_below_quality=False,
        max_concurrent_positions=10,
    )
    gate = ExposureGate(cfg, allowed_windows=WINDOWS)
    trade = {
        "profile": "momentum_volume_v13_combined",
        "symbol": "7203.T",
        "entry_time": "2026-05-18T10:00:00+09:00",
        "exit_time": "2026-05-18T10:05:00+09:00",
        "trade_date": "2026-05-18",
        "pnl_pct": 0.0,
        "momentum_continuation_score": 0.8,
        "favorable_continuation": True,
        "max_favorable_excursion_pct": 0.5,
        "max_adverse_excursion_pct": -0.1,
        "max_continuation_duration": 120,
    }
    decision = gate.evaluate_entry(trade)
    assert decision.accept
