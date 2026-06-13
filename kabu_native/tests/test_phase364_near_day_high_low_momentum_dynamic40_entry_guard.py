"""Phase364: near day high + low momentum Dynamic40 production ENTRY guard tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.exposure_gate import ExposureGate, ExposureGateConfig  # noqa: E402
from small_paper.near_day_high_low_momentum_dynamic40_entry_guard import (  # noqa: E402
    NearDayHighLowMomentumDynamic40GuardConfig,
    NearDayHighLowMomentumDynamic40GuardState,
    REJECT_NEAR_DAY_HIGH_LOW_MOMENTUM_DYNAMIC40_GUARD,
    compute_near_day_high_low_momentum_guard_fields,
)
from small_paper.near_day_high_low_mom_entry_guard_shadow import (  # noqa: E402
    would_block_near_day_high_low_mom_guard,
)
from small_paper.pullback_misread_dynamic40_entry_guard import (  # noqa: E402
    PullbackMisreadDynamic40GuardConfig,
    PullbackMisreadDynamic40GuardState,
)


def _base_trade(**overrides: object) -> dict:
    trade = {
        "profile": "momentum_volume_v13_combined",
        "symbol": "6976.T",
        "entry_time": "2026-06-12T09:15:00+09:00",
        "exit_time": "2026-06-12T09:20:00+09:00",
        "trade_date": "2026-06-12",
        "universe_slot": "dynamic",
        "universe_bucket": "dynamic40",
        "continuation_quality_score": 0.72,
        "momentum_continuation_score": 0.5,
        "pnl_pct": 0.0,
    }
    trade.update(overrides)
    return trade


class TestNearDayHighLowMomentumDynamic40Guard(unittest.TestCase):
    def test_dynamic40_blocks_near_high_low_momentum(self) -> None:
        trade = _base_trade(
            day_high_distance_pct=1.0,
            entry_momentum_score=0.20,
        )
        guard = NearDayHighLowMomentumDynamic40GuardState(
            config=NearDayHighLowMomentumDynamic40GuardConfig(enabled=True)
        )
        gate = ExposureGate(
            ExposureGateConfig(
                profile="momentum_volume_v13_combined",
                reject_below_quality=False,
            ),
            near_day_high_low_momentum_dynamic40_guard=guard,
        )
        decision = gate.evaluate_entry(trade)
        self.assertFalse(decision.accept)
        self.assertEqual(
            decision.reason, REJECT_NEAR_DAY_HIGH_LOW_MOMENTUM_DYNAMIC40_GUARD
        )

    def test_core10_never_blocked_even_with_c03_signal(self) -> None:
        trade = _base_trade(
            universe_slot="core",
            universe_bucket="core10",
            day_high_distance_pct=0.5,
            entry_momentum_score=0.10,
        )
        guard = NearDayHighLowMomentumDynamic40GuardState(
            config=NearDayHighLowMomentumDynamic40GuardConfig(enabled=True)
        )
        gate = ExposureGate(
            ExposureGateConfig(
                profile="momentum_volume_v13_combined",
                reject_below_quality=False,
                max_concurrent_positions=10,
            ),
            near_day_high_low_momentum_dynamic40_guard=guard,
        )
        decision = gate.evaluate_entry(trade)
        self.assertTrue(decision.accept)

    def test_missing_fields_not_blocked(self) -> None:
        trade = _base_trade()
        guard = NearDayHighLowMomentumDynamic40GuardState(
            config=NearDayHighLowMomentumDynamic40GuardConfig(enabled=True)
        )
        chk = guard.check(trade)
        self.assertFalse(chk.blocked)

    def test_guard_disabled_passes(self) -> None:
        trade = _base_trade(
            day_high_distance_pct=0.5,
            entry_momentum_score=0.10,
        )
        guard = NearDayHighLowMomentumDynamic40GuardState(
            config=NearDayHighLowMomentumDynamic40GuardConfig(enabled=False)
        )
        chk = guard.check(trade)
        self.assertFalse(chk.blocked)

    def test_entry_near_day_high_pct_fallback(self) -> None:
        trade = _base_trade(
            entry_near_day_high_pct=1.2,
            entry_momentum_continuation_score=0.25,
        )
        guard = NearDayHighLowMomentumDynamic40GuardState(
            config=NearDayHighLowMomentumDynamic40GuardConfig(enabled=True)
        )
        chk = guard.check(trade)
        self.assertTrue(chk.blocked)

    def test_momentum_at_threshold_not_blocked(self) -> None:
        trade = _base_trade(
            day_high_distance_pct=1.0,
            entry_momentum_score=0.30,
        )
        self.assertFalse(would_block_near_day_high_low_mom_guard(trade))
        guard = NearDayHighLowMomentumDynamic40GuardState(
            config=NearDayHighLowMomentumDynamic40GuardConfig(enabled=True)
        )
        self.assertFalse(guard.check(trade).blocked)

    def test_compute_fields(self) -> None:
        fields = compute_near_day_high_low_momentum_guard_fields(
            {
                "universe_slot": "dynamic",
                "day_high_distance_pct": 1.0,
                "entry_momentum_score": 0.20,
            }
        )
        self.assertTrue(fields["near_day_high_low_momentum_dynamic40_guard_blocked"])

    def test_stacks_after_pullback_guard(self) -> None:
        trade = _base_trade(
            entry_rise_5min_pct=1.0,
            entry_vwap_dev_pct=1.0,
            day_high_distance_pct=1.0,
            entry_momentum_score=0.20,
        )
        pb_guard = PullbackMisreadDynamic40GuardState(
            config=PullbackMisreadDynamic40GuardConfig(enabled=True)
        )
        nd_guard = NearDayHighLowMomentumDynamic40GuardState(
            config=NearDayHighLowMomentumDynamic40GuardConfig(enabled=True)
        )
        gate = ExposureGate(
            ExposureGateConfig(
                profile="momentum_volume_v13_combined",
                reject_below_quality=False,
            ),
            pullback_misread_dynamic40_guard=pb_guard,
            near_day_high_low_momentum_dynamic40_guard=nd_guard,
        )
        decision = gate.evaluate_entry(trade)
        self.assertFalse(decision.accept)
        self.assertEqual(
            decision.reason, REJECT_NEAR_DAY_HIGH_LOW_MOMENTUM_DYNAMIC40_GUARD
        )


if __name__ == "__main__":
    unittest.main()
