"""Phase439: High Drift pullback guard runtime tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.exposure_gate import (  # noqa: E402
    ExposureGate,
    ExposureGateConfig,
    REJECT_HIGH_DRIFT_PULLBACK,
)
from small_paper.config import load_pilot_config  # noqa: E402
from small_paper.high_drift_pullback_entry_guard import (  # noqa: E402
    HighDriftPullbackGuardConfig,
    HighDriftPullbackGuardState,
    REJECT_HIGH_DRIFT_PULLBACK as GUARD_REJECT,
    would_block_high_drift_pullback_guard,
)
from small_paper.pullback_misread_dynamic40_entry_guard import (  # noqa: E402
    PullbackMisreadDynamic40GuardConfig,
    PullbackMisreadDynamic40GuardState,
)


class TestHighDriftPullbackGuard(unittest.TestCase):
    def test_pattern_a_blocks_bounce_from_high(self) -> None:
        trade = {
            "universe_slot": "dynamic",
            "universe_bucket": "dynamic40",
            "entry_rise_5min_pct": -0.5,
            "entry_rise_10min_pct": -1.0,
            "entry_near_day_high_pct": 2.0,
        }
        self.assertTrue(would_block_high_drift_pullback_guard(trade))

    def test_pattern_b_blocks_sustained_decline(self) -> None:
        trade = {
            "universe_slot": "dynamic",
            "entry_rise_5min_pct": -0.8,
            "entry_rise_10min_pct": -0.3,
            "entry_rise_15min_pct": -1.2,
            "entry_near_day_high_pct": 3.5,
        }
        self.assertTrue(would_block_high_drift_pullback_guard(trade))

    def test_core10_never_blocked(self) -> None:
        trade = {
            "universe_slot": "core",
            "entry_rise_5min_pct": -1.0,
            "entry_rise_10min_pct": -2.0,
            "entry_near_day_high_pct": 5.0,
        }
        self.assertFalse(would_block_high_drift_pullback_guard(trade))

    def test_exposure_gate_rejects_with_reason(self) -> None:
        trade = {
            "profile": "momentum_volume_v13_combined",
            "symbol": "6976.T",
            "entry_time": "2026-06-18T09:25:41+09:00",
            "exit_time": "2026-06-18T09:30:00+09:00",
            "trade_date": "2026-06-18",
            "universe_slot": "dynamic",
            "universe_bucket": "dynamic40",
            "entry_rise_5min_pct": -0.68,
            "entry_rise_10min_pct": -1.51,
            "entry_near_day_high_pct": 3.0,
            "continuation_quality_score": 0.72,
            "momentum_continuation_score": 0.2,
            "pnl_pct": 0.0,
        }
        guard = HighDriftPullbackGuardState(config=HighDriftPullbackGuardConfig(enabled=True))
        gate = ExposureGate(
            ExposureGateConfig(
                profile="momentum_volume_v13_combined",
                reject_below_quality=False,
                max_concurrent_positions=10,
            ),
            high_drift_pullback_guard=guard,
        )
        decision = gate.evaluate_entry(trade)
        self.assertFalse(decision.accept)
        self.assertEqual(decision.reason, REJECT_HIGH_DRIFT_PULLBACK)
        self.assertEqual(GUARD_REJECT, "high_drift_pullback")

    def test_guard_disabled_passes(self) -> None:
        trade = {
            "universe_slot": "dynamic",
            "entry_rise_5min_pct": -1.0,
            "entry_rise_10min_pct": -2.0,
            "entry_near_day_high_pct": 4.0,
        }
        guard = HighDriftPullbackGuardState(config=HighDriftPullbackGuardConfig(enabled=False))
        self.assertFalse(guard.check(trade).blocked)

    def test_preflight_yaml_flags(self) -> None:
        cfg_path = (
            REPO
            / "kabu_native"
            / "configs"
            / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
        )
        cfg = load_pilot_config(cfg_path)
        self.assertTrue(cfg.high_drift_guard_enabled)
        self.assertFalse(cfg.enable_pullback_misread_dynamic40_guard)
        self.assertFalse(cfg.order_enabled)
        self.assertTrue(cfg.paper_only)
        self.assertEqual(cfg.max_concurrent_positions, 5)
        self.assertEqual(cfg.same_symbol_open_policy, "no_overlap_replace")

    def test_vwap_guard_disabled_when_legacy_false(self) -> None:
        cfg_path = (
            REPO
            / "kabu_native"
            / "configs"
            / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
        )
        cfg = load_pilot_config(cfg_path)
        gate = cfg.make_exposure_gate()
        self.assertIsNone(getattr(gate, "pullback_misread_dynamic40_guard", None))
        self.assertIsNotNone(getattr(gate, "high_drift_pullback_guard", None))

    def test_vwap_guard_still_available_when_enabled(self) -> None:
        guard = PullbackMisreadDynamic40GuardState(
            config=PullbackMisreadDynamic40GuardConfig(enabled=True)
        )
        trade = {
            "universe_slot": "dynamic",
            "entry_rise_5min_pct": -0.5,
            "entry_vwap_dev_pct": -0.2,
        }
        self.assertTrue(guard.check(trade).blocked)


if __name__ == "__main__":
    unittest.main()
