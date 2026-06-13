"""Phase355: pullback misread Dynamic40 production ENTRY guard tests."""

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
from small_paper.pullback_misread_dynamic40_entry_guard import (  # noqa: E402
    PullbackMisreadDynamic40GuardConfig,
    PullbackMisreadDynamic40GuardState,
    REJECT_PULLBACK_MISREAD_DYNAMIC40_GUARD,
    attach_universe_fields,
    compute_pullback_misread_guard_fields,
    is_dynamic40_universe,
)
from small_paper.pullback_misread_entry_guard_shadow import (  # noqa: E402
    PullbackMisreadEntryGuardShadowCounters,
    would_block_pullback_dynamic40_shadow,
    would_block_pullback_misread_guard,
)


class TestPullbackMisreadDynamic40Guard(unittest.TestCase):
    def test_dynamic40_only_blocks_on_negative_rise_and_vwap(self) -> None:
        trade = {
            "profile": "momentum_volume_v13_combined",
            "symbol": "6976.T",
            "entry_time": "2026-06-12T09:15:00+09:00",
            "exit_time": "2026-06-12T09:20:00+09:00",
            "trade_date": "2026-06-12",
            "universe_slot": "dynamic",
            "universe_bucket": "dynamic40",
            "entry_rise_5min_pct": -0.42,
            "entry_vwap_dev_pct": -0.18,
            "continuation_quality_score": 0.72,
            "momentum_continuation_score": 0.5,
            "pnl_pct": 0.0,
        }
        guard = PullbackMisreadDynamic40GuardState(
            config=PullbackMisreadDynamic40GuardConfig(enabled=True)
        )
        gate = ExposureGate(
            ExposureGateConfig(profile="momentum_volume_v13_combined", reject_below_quality=False),
            pullback_misread_dynamic40_guard=guard,
        )
        decision = gate.evaluate_entry(trade)
        self.assertFalse(decision.accept)
        self.assertEqual(decision.reason, REJECT_PULLBACK_MISREAD_DYNAMIC40_GUARD)

    def test_core10_never_blocked_even_with_pullback_signal(self) -> None:
        trade = {
            "profile": "momentum_volume_v13_combined",
            "symbol": "3905.T",
            "entry_time": "2026-06-12T09:15:00+09:00",
            "exit_time": "2026-06-12T09:20:00+09:00",
            "trade_date": "2026-06-12",
            "universe_slot": "core",
            "universe_bucket": "core10",
            "entry_rise_5min_pct": -1.0,
            "entry_vwap_dev_pct": -1.0,
            "continuation_quality_score": 0.72,
            "momentum_continuation_score": 0.5,
            "pnl_pct": 0.0,
        }
        guard = PullbackMisreadDynamic40GuardState(
            config=PullbackMisreadDynamic40GuardConfig(enabled=True)
        )
        gate = ExposureGate(
            ExposureGateConfig(
                profile="momentum_volume_v13_combined",
                reject_below_quality=False,
                max_concurrent_positions=10,
            ),
            pullback_misread_dynamic40_guard=guard,
        )
        decision = gate.evaluate_entry(trade)
        self.assertTrue(decision.accept)

    def test_guard_disabled_passes(self) -> None:
        trade = {
            "profile": "momentum_volume_v13_combined",
            "symbol": "6976.T",
            "universe_slot": "dynamic",
            "entry_rise_5min_pct": -1.0,
            "entry_vwap_dev_pct": -1.0,
        }
        guard = PullbackMisreadDynamic40GuardState(
            config=PullbackMisreadDynamic40GuardConfig(enabled=False)
        )
        chk = guard.check(trade)
        self.assertFalse(chk.blocked)

    def test_is_dynamic40_universe(self) -> None:
        self.assertTrue(is_dynamic40_universe({"universe_slot": "dynamic"}))
        self.assertFalse(is_dynamic40_universe({"universe_slot": "core"}))
        self.assertTrue(
            is_dynamic40_universe({"source_bucket": "vol_liq_dynamic40"})
        )

    def test_attach_universe_fields(self) -> None:
        trade: dict = {}
        attach_universe_fields(
            trade,
            {"universe_slot": "dynamic", "source_bucket": "vol_liq_dynamic40"},
        )
        self.assertEqual(trade["universe_slot"], "dynamic")
        self.assertEqual(trade["universe_bucket"], "dynamic40")

    def test_shadow_counters_dynamic40_scope(self) -> None:
        counters = PullbackMisreadEntryGuardShadowCounters()
        dyn_blocked = {
            "universe_slot": "dynamic",
            "entry_rise_5min_pct": -0.5,
            "entry_vwap_dev_pct": -0.2,
        }
        core_blocked = {
            "universe_slot": "core",
            "entry_rise_5min_pct": -0.5,
            "entry_vwap_dev_pct": -0.2,
        }
        self.assertTrue(would_block_pullback_misread_guard(dyn_blocked))
        self.assertTrue(would_block_pullback_dynamic40_shadow(dyn_blocked))
        self.assertFalse(would_block_pullback_dynamic40_shadow(core_blocked))
        counters.record_accept(dyn_blocked)
        counters.record_accept(core_blocked)
        self.assertEqual(counters.pullback_misread_guard_shadow_blocked_count, 1)
        self.assertEqual(counters.pullback_misread_guard_shadow_kept_count, 1)

    def test_compute_fields(self) -> None:
        fields = compute_pullback_misread_guard_fields(
            {
                "universe_slot": "dynamic",
                "entry_rise_5min_pct": -0.1,
                "entry_vwap_dev_pct": -0.1,
            }
        )
        self.assertTrue(fields["pullback_misread_dynamic40_guard_blocked"])


if __name__ == "__main__":
    unittest.main()
