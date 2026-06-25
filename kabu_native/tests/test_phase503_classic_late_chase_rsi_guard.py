"""Phase503: Classic late_chase AND RSI14>=80 runtime ENTRY guard tests."""

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
    REJECT_CLASSIC_LATE_CHASE_RSI_OVER80,
)
from small_paper.classic_late_chase_rsi_guard import (  # noqa: E402
    ClassicLateChaseRsiGuardConfig,
    ClassicLateChaseRsiGuardState,
    REJECT_CLASSIC_LATE_CHASE_RSI_OVER80 as GUARD_REJECT,
    compute_classic_late_chase_rsi_guard_fields,
    compute_late_chase_flag,
    compute_rsi14_at_entry,
    would_block_classic_late_chase_rsi_guard,
)
from small_paper.config import load_pilot_config  # noqa: E402


def _late_chase_trade(**overrides: object) -> dict[str, object]:
    base = {
        "entry_rise_5min_pct": 0.2,
        "entry_rise_10min_pct": 1.5,
        "entry_rise_15min_pct": 1.0,
        "entry_rise_30min_pct": 1.8,
        "entry_vwap_dev_pct": 0.5,
        "late_chase_flag": True,
        "rsi14": 82.0,
    }
    base.update(overrides)
    return base


class TestClassicLateChaseRsiGuard(unittest.TestCase):
    def test_late_chase_flag_matches_phase493_cluster(self) -> None:
        trade = {
            "entry_rise_5min_pct": 0.2,
            "entry_rise_10min_pct": 1.5,
            "entry_rise_15min_pct": 1.0,
            "entry_rise_30min_pct": 1.8,
            "entry_vwap_dev_pct": 0.5,
        }
        self.assertTrue(compute_late_chase_flag(trade))

    def test_blocks_when_enabled_late_chase_and_rsi_over80(self) -> None:
        trade = _late_chase_trade()
        self.assertTrue(would_block_classic_late_chase_rsi_guard(trade))
        guard = ClassicLateChaseRsiGuardState(
            config=ClassicLateChaseRsiGuardConfig(enabled=True, rsi_threshold=80.0)
        )
        self.assertTrue(guard.check(trade).blocked)

    def test_passes_when_rsi_below_threshold(self) -> None:
        trade = _late_chase_trade(rsi14=79.9)
        guard = ClassicLateChaseRsiGuardState(
            config=ClassicLateChaseRsiGuardConfig(enabled=True, rsi_threshold=80.0)
        )
        self.assertFalse(guard.check(trade).blocked)

    def test_passes_when_not_late_chase_even_if_rsi_high(self) -> None:
        trade = _late_chase_trade(late_chase_flag=False, rsi14=90.0)
        guard = ClassicLateChaseRsiGuardState(
            config=ClassicLateChaseRsiGuardConfig(enabled=True, rsi_threshold=80.0)
        )
        self.assertFalse(guard.check(trade).blocked)

    def test_passes_when_guard_disabled(self) -> None:
        trade = _late_chase_trade()
        guard = ClassicLateChaseRsiGuardState(
            config=ClassicLateChaseRsiGuardConfig(enabled=False, rsi_threshold=80.0)
        )
        self.assertFalse(guard.check(trade).blocked)

    def test_rsi14_from_live_float_price_ring(self) -> None:
        """Live symbol_price_ring uses epoch float seconds (extended_entry_shadow)."""
        origin = 1_750_650_000.0
        ring = [(origin + i * 60.0, 100.0 + i * 0.5) for i in range(20)]
        entry_ts = origin + 19 * 60.0
        rsi = compute_rsi14_at_entry(ring, entry_ts=entry_ts)
        self.assertIsNotNone(rsi)
        fields = compute_classic_late_chase_rsi_guard_fields(
            {"entry_rise_10min_pct": 1.5, "entry_vwap_dev_pct": 0.5},
            price_ring=ring,
            entry_ts=entry_ts,
            enabled=True,
        )
        self.assertIsNotNone(fields.get("rsi14"))

    def test_exposure_gate_rejects_with_reason(self) -> None:
        trade = {
            "profile": "momentum_volume_v13_combined",
            "symbol": "6976.T",
            "entry_time": "2026-06-18T09:25:41+09:00",
            "exit_time": "2026-06-18T09:30:00+09:00",
            "trade_date": "2026-06-18",
            "continuation_quality_score": 0.72,
            "momentum_continuation_score": 0.2,
            "entry_expectancy_score_v2": 5,
            "entry_order_book_imbalance": 0.5,
            "pnl_pct": 0.0,
            "late_chase_flag": True,
            "rsi14": 85.0,
        }
        guard = ClassicLateChaseRsiGuardState(
            config=ClassicLateChaseRsiGuardConfig(enabled=True, rsi_threshold=80.0)
        )
        gate = ExposureGate(
            ExposureGateConfig(
                profile="momentum_volume_v13_combined",
                reject_below_quality=False,
                max_concurrent_positions=10,
                entry_score_v2_min=3,
            ),
            classic_late_chase_rsi_guard=guard,
        )
        decision = gate.evaluate_entry(trade)
        self.assertFalse(decision.accept)
        self.assertEqual(decision.reason, REJECT_CLASSIC_LATE_CHASE_RSI_OVER80)
        self.assertEqual(GUARD_REJECT, "classic_late_chase_rsi_over80")

    def test_preflight_yaml_flags(self) -> None:
        cfg_path = (
            REPO
            / "kabu_native"
            / "configs"
            / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
        )
        cfg = load_pilot_config(cfg_path)
        self.assertTrue(cfg.classic_late_chase_rsi_guard_enabled)
        self.assertEqual(cfg.classic_late_chase_rsi_threshold, 80.0)
        self.assertFalse(cfg.order_enabled)
        self.assertTrue(cfg.paper_only)
        self.assertEqual(cfg.max_concurrent_positions, 5)

    def test_make_exposure_gate_wires_guard(self) -> None:
        cfg_path = (
            REPO
            / "kabu_native"
            / "configs"
            / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
        )
        cfg = load_pilot_config(cfg_path)
        gate = cfg.make_exposure_gate()
        self.assertIsNotNone(getattr(gate, "classic_late_chase_rsi_guard", None))


if __name__ == "__main__":
    unittest.main()
