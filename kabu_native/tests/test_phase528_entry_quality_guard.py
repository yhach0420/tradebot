"""Phase528: Entry quality guard (G9) runtime tests."""

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
    REJECT_ENTRY_QUALITY_GUARD_SPREAD,
    REJECT_ENTRY_QUALITY_GUARD_UPDATE_COUNT,
)
from small_paper.config import load_pilot_config  # noqa: E402
from small_paper.entry_quality_guard import (  # noqa: E402
    EntryQualityGuardConfig,
    EntryQualityGuardState,
    REJECT_ENTRY_QUALITY_GUARD_SPREAD,
    REJECT_ENTRY_QUALITY_GUARD_UPDATE_COUNT,
    compute_entry_quality_guard_fields,
    compute_update_count_before_entry,
)
from small_paper.live_pipeline_preflight import (  # noqa: E402
    PHASE528_RUNTIME_VERDICT as PREFLIGHT_VERDICT,
    build_high_update_count_price_ring,
    build_wide_spread_push_payload,
    run_entry_quality_guard_preflight,
)


def _trade(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "profile": "momentum_volume_v13_combined",
        "symbol": "6976.T",
        "entry_time": "2026-06-24T10:15:00+09:00",
        "exit_time": "2026-06-24T10:30:00+09:00",
        "trade_date": "2026-06-24",
        "continuation_quality_score": 0.72,
        "momentum_continuation_score": 0.22,
        "entry_expectancy_score_v2": 5,
        "entry_order_book_imbalance": 0.5,
        "pnl_pct": 0.0,
        "spread_bps": 35.0,
        "update_count_before_entry": 2,
    }
    base.update(overrides)
    return base


class TestEntryQualityGuard(unittest.TestCase):
    def test_blocks_wide_spread_first(self) -> None:
        guard = EntryQualityGuardState(
            config=EntryQualityGuardConfig(enabled=True, max_spread_bps=50.0, max_update_count=5)
        )
        result = guard.check(_trade(spread_bps=55.0, update_count_before_entry=2))
        self.assertTrue(result.blocked)
        self.assertEqual(result.reject_reason, REJECT_ENTRY_QUALITY_GUARD_SPREAD)

    def test_blocks_high_update_count_when_spread_ok(self) -> None:
        guard = EntryQualityGuardState(
            config=EntryQualityGuardConfig(enabled=True, max_spread_bps=50.0, max_update_count=5)
        )
        result = guard.check(_trade(spread_bps=30.0, update_count_before_entry=8))
        self.assertTrue(result.blocked)
        self.assertEqual(result.reject_reason, REJECT_ENTRY_QUALITY_GUARD_UPDATE_COUNT)

    def test_passes_when_both_ok(self) -> None:
        guard = EntryQualityGuardState(
            config=EntryQualityGuardConfig(enabled=True, max_spread_bps=50.0, max_update_count=5)
        )
        self.assertFalse(guard.check(_trade()).blocked)

    def test_update_count_from_float_price_ring(self) -> None:
        origin = 1_750_650_000.0
        ring = build_high_update_count_price_ring(entry_ts=origin + 9 * 60.0, minutes=10)
        uc = compute_update_count_before_entry(ring, entry_ts=origin + 9 * 60.0)
        self.assertGreater(uc, 5)

    def test_spread_from_wide_payload(self) -> None:
        payload = build_wide_spread_push_payload(symbol="6976.T", price=2800.0, spread_bps_target=80.0)
        fields = compute_entry_quality_guard_fields({}, payload=payload, enabled=True)
        self.assertIsNotNone(fields.get("spread_bps"))
        self.assertGreater(float(fields["spread_bps"]), 50.0)

    def test_exposure_gate_rejects_spread(self) -> None:
        guard = EntryQualityGuardState(
            config=EntryQualityGuardConfig(enabled=True, max_spread_bps=50.0, max_update_count=5)
        )
        gate = ExposureGate(
            ExposureGateConfig(
                profile="momentum_volume_v13_combined",
                reject_below_quality=False,
                max_concurrent_positions=10,
                entry_score_v2_min=3,
            ),
            entry_quality_guard=guard,
        )
        decision = gate.evaluate_entry(_trade(spread_bps=70.0))
        self.assertFalse(decision.accept)
        self.assertEqual(decision.reason, REJECT_ENTRY_QUALITY_GUARD_SPREAD)

    def test_pilot_config_loads_guard(self) -> None:
        cfg_path = (
            REPO
            / "kabu_native"
            / "configs"
            / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
        )
        config = load_pilot_config(cfg_path)
        self.assertTrue(config.entry_quality_guard_enabled)
        self.assertEqual(config.entry_quality_max_spread_bps, 50.0)
        self.assertEqual(config.entry_quality_max_update_count, 5)
        gate = config.make_exposure_gate()
        self.assertIsNotNone(getattr(gate, "entry_quality_guard", None))

    def test_phase528_preflight_ready(self) -> None:
        cfg_path = (
            REPO
            / "kabu_native"
            / "configs"
            / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
        )
        report = run_entry_quality_guard_preflight(config_path=cfg_path, repo_root=REPO)
        self.assertTrue(report.ready, report.errors)
        self.assertEqual(report.verdict, PREFLIGHT_VERDICT)
        by_id = {c.case_id: c for c in report.cases}
        self.assertEqual(
            by_id["entry_quality_spread_block"].decision_reason,
            REJECT_ENTRY_QUALITY_GUARD_SPREAD,
        )
        self.assertEqual(
            by_id["entry_quality_update_block"].decision_reason,
            REJECT_ENTRY_QUALITY_GUARD_UPDATE_COUNT,
        )

    def test_verdict_constant(self) -> None:
        self.assertEqual(PREFLIGHT_VERDICT, "phase528_entry_quality_guard_runtime_ready")


if __name__ == "__main__":
    unittest.main()
