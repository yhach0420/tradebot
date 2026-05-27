"""Phase 72: combined_structural_exit_v2_price_mom policy."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "kabu_native" / "src"))

from research.structural_exit_policies import (
    POLICY_COMBINED_STRUCTURAL_EXIT_V2_PRICE_MOM,
    simulate_structural_policy,
)


@dataclass
class _Cfg:
    hard_stop_pct: float = 1.2
    take_quality_drop: float = 0.08
    momentum_weaken_ratio: float = 0.85
    favorable_fade_ratio: float = 0.85
    price_momentum_fade_ratio: float = 0.80
    structural_exit_policy: str = POLICY_COMBINED_STRUCTURAL_EXIT_V2_PRICE_MOM


def test_v2_price_momentum_fade_exit_fires() -> None:
    ticks = [
        {
            "price": 100.0,
            "pnl_pct": 0.1,
            "quality": 0.8,
            "momentum": 0.5,
            "favorable": 0.9,
            "pure_price_momentum": 0.01,
        },
        {
            "price": 100.5,
            "pnl_pct": 0.15,
            "quality": 0.79,
            "momentum": 0.5,
            "favorable": 0.9,
            "pure_price_momentum": 0.004,
        },
    ]
    result = simulate_structural_policy(
        ticks,
        100.0,
        POLICY_COMBINED_STRUCTURAL_EXIT_V2_PRICE_MOM,
        _Cfg(),
        allow_session_end=False,
    )
    assert result is not None
    pnl, reason = result
    assert reason == "price_momentum_fade_exit"
    assert pnl == 0.15


def test_v2_quality_decay_before_price_fade() -> None:
    ticks = [
        {
            "price": 100.0,
            "pnl_pct": 0.0,
            "quality": 0.9,
            "momentum": 0.5,
            "favorable": 0.9,
            "pure_price_momentum": 0.01,
        },
        {
            "price": 99.0,
            "pnl_pct": -1.0,
            "quality": 0.75,
            "momentum": 0.1,
            "favorable": 0.9,
            "pure_price_momentum": 0.001,
        },
    ]
    result = simulate_structural_policy(
        ticks,
        100.0,
        POLICY_COMBINED_STRUCTURAL_EXIT_V2_PRICE_MOM,
        _Cfg(),
        allow_session_end=False,
    )
    assert result == (-1.0, "quality_decay_exit")
