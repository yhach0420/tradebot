"""Phase525: Re-entry after stop_hit RSI guard runtime tests."""

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
    REJECT_REENTRY_RSI_GUARD_BELOW60,
)
from small_paper.classic_late_chase_rsi_guard import compute_rsi14_at_entry  # noqa: E402
from small_paper.config import load_pilot_config  # noqa: E402
from small_paper.live_pipeline_preflight import (  # noqa: E402
    PHASE525_RUNTIME_VERDICT,
    build_normal_preflight_price_ring,
    run_reentry_rsi_guard_preflight,
)
from small_paper.reentry_rsi_guard import (  # noqa: E402
    REJECT_REENTRY_RSI_GUARD_BELOW60 as GUARD_REJECT,
    ReentryRsiGuardConfig,
    ReentryRsiGuardState,
    compute_reentry_rsi_guard_fields,
    would_block_reentry_rsi_guard,
)


def _base_trade(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "profile": "momentum_volume_v13_combined",
        "symbol": "5074.T",
        "entry_time": "2026-06-24T10:15:00+09:00",
        "exit_time": "2026-06-24T10:30:00+09:00",
        "trade_date": "2026-06-24",
        "continuation_quality_score": 0.72,
        "momentum_continuation_score": 0.22,
        "entry_expectancy_score_v2": 5,
        "entry_order_book_imbalance": 0.5,
        "pnl_pct": 0.0,
        "rsi14": 55.0,
    }
    base.update(overrides)
    return base


class TestReentryRsiGuard(unittest.TestCase):
    def test_first_entry_not_blocked(self) -> None:
        guard = ReentryRsiGuardState(
            config=ReentryRsiGuardConfig(enabled=True, rsi_threshold=60.0)
        )
        trade = _base_trade(rsi14=40.0)
        self.assertFalse(guard.check(trade).blocked)

    def test_reentry_after_stop_blocks_low_rsi(self) -> None:
        guard = ReentryRsiGuardState(
            config=ReentryRsiGuardConfig(enabled=True, rsi_threshold=60.0)
        )
        guard.record_exit(
            {
                "symbol": "5074.T",
                "exit_reason": "stop_hit",
                "structural_exit_reason": "stop_hit",
                "stop_hit": True,
            }
        )
        trade = _base_trade(rsi14=59.9)
        result = guard.check(trade)
        self.assertTrue(result.blocked)
        self.assertEqual(result.reject_reason, GUARD_REJECT)

    def test_reentry_after_stop_passes_high_rsi(self) -> None:
        guard = ReentryRsiGuardState(
            config=ReentryRsiGuardConfig(enabled=True, rsi_threshold=60.0)
        )
        guard.record_exit(
            {
                "symbol": "5074.T",
                "exit_reason": "stop_hit",
                "structural_exit_reason": "stop_hit",
            }
        )
        trade = _base_trade(rsi14=61.0)
        self.assertFalse(guard.check(trade).blocked)

    def test_reentry_after_non_stop_not_blocked(self) -> None:
        guard = ReentryRsiGuardState(
            config=ReentryRsiGuardConfig(enabled=True, rsi_threshold=60.0)
        )
        guard.record_exit(
            {
                "symbol": "5074.T",
                "exit_reason": "trailing_mfe_exit",
                "structural_exit_reason": "trailing_mfe_exit",
            }
        )
        trade = _base_trade(rsi14=40.0)
        self.assertFalse(guard.check(trade).blocked)

    def test_overlap_structural_stop_counts_as_stop(self) -> None:
        guard = ReentryRsiGuardState(
            config=ReentryRsiGuardConfig(enabled=True, rsi_threshold=60.0)
        )
        guard.record_exit(
            {
                "symbol": "5074.T",
                "exit_reason": "overlap_replaced_review",
                "structural_exit_reason": "stop_hit",
                "stop_hit": True,
            }
        )
        self.assertTrue(guard.is_reentry_after_stop("5074.T"))
        self.assertTrue(guard.check(_base_trade(rsi14=50.0)).blocked)

    def test_guard_disabled_passes(self) -> None:
        guard = ReentryRsiGuardState(
            config=ReentryRsiGuardConfig(enabled=False, rsi_threshold=60.0)
        )
        guard.record_exit({"symbol": "5074.T", "exit_reason": "stop_hit"})
        self.assertFalse(guard.check(_base_trade(rsi14=30.0)).blocked)

    def test_rsi14_from_live_float_price_ring(self) -> None:
        origin = 1_750_650_000.0
        ring = [(origin + i * 60.0, 100.0 - i * 0.3) for i in range(20)]
        entry_ts = origin + 19 * 60.0
        rsi = compute_rsi14_at_entry(ring, entry_ts=entry_ts)
        self.assertIsNotNone(rsi)
        fields = compute_reentry_rsi_guard_fields(
            {},
            price_ring=ring,
            entry_ts=entry_ts,
            threshold=60.0,
            enabled=True,
            reentry_after_stop=True,
        )
        self.assertIsNotNone(fields.get("rsi14"))
        self.assertTrue(
            would_block_reentry_rsi_guard(
                {"rsi14": fields["rsi14"]},
                threshold=60.0,
                reentry_after_stop=True,
            )
        )

    def test_exposure_gate_rejects_with_reason(self) -> None:
        guard = ReentryRsiGuardState(
            config=ReentryRsiGuardConfig(enabled=True, rsi_threshold=60.0)
        )
        guard.record_exit({"symbol": "5074.T", "exit_reason": "stop_hit", "stop_hit": True})
        gate = ExposureGate(
            ExposureGateConfig(
                profile="momentum_volume_v13_combined",
                min_continuation_quality=0.55,
                reject_below_quality=False,
                max_concurrent_positions=10,
                entry_score_v2_min=3,
            ),
            reentry_rsi_guard=guard,
        )
        decision = gate.evaluate_entry(_base_trade(rsi14=45.0))
        self.assertFalse(decision.accept)
        self.assertEqual(decision.reason, REJECT_REENTRY_RSI_GUARD_BELOW60)
        self.assertTrue(decision.reentry_rsi_after_stop)

    def test_pilot_config_loads_reentry_guard(self) -> None:
        cfg_path = (
            REPO
            / "kabu_native"
            / "configs"
            / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
        )
        config = load_pilot_config(cfg_path)
        self.assertTrue(config.reentry_rsi_guard_enabled)
        self.assertEqual(config.reentry_rsi_guard_threshold, 60.0)
        gate = config.make_exposure_gate()
        self.assertIsNotNone(getattr(gate, "reentry_rsi_guard", None))

    def test_phase525_preflight_ready(self) -> None:
        cfg_path = (
            REPO
            / "kabu_native"
            / "configs"
            / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
        )
        report = run_reentry_rsi_guard_preflight(config_path=cfg_path, repo_root=REPO)
        self.assertTrue(report.ready, report.errors)
        self.assertEqual(report.verdict, PHASE525_RUNTIME_VERDICT)
        by_id = {c.case_id: c for c in report.cases}
        self.assertTrue(by_id["reentry_stop_rsi_low"].uses_float_epoch_timestamps)
        self.assertEqual(
            by_id["reentry_stop_rsi_low"].decision_reason,
            REJECT_REENTRY_RSI_GUARD_BELOW60,
        )
        self.assertNotEqual(
            by_id["first_entry_rsi_low"].decision_reason,
            REJECT_REENTRY_RSI_GUARD_BELOW60,
        )

    def test_normal_ring_float_timestamps(self) -> None:
        origin = 1_750_650_000.0
        ring = build_normal_preflight_price_ring(entry_ts=origin, base_price=2800.0)
        self.assertIsInstance(ring[0][0], float)


if __name__ == "__main__":
    unittest.main()
