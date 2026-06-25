"""Phase506: Live PUSH pipeline preflight tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.exposure_gate import REJECT_CLASSIC_LATE_CHASE_RSI_OVER80  # noqa: E402
from small_paper.live_pipeline_preflight import (  # noqa: E402
    PREFLIGHT_VERDICT,
    build_float_epoch_price_ring,
    build_live_mock_push_payload,
    build_normal_preflight_price_ring,
    default_config_path,
    run_live_pipeline_case,
    run_live_pipeline_preflight,
)
from small_paper.config import load_pilot_config  # noqa: E402


class TestPhase506LivePipelinePreflight(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cfg_path = default_config_path(REPO)
        cls.config = load_pilot_config(cls.cfg_path)

    def test_mock_ring_uses_float_epoch_seconds(self) -> None:
        from small_paper.live_pipeline_preflight import _now_epoch

        ts = _now_epoch()
        ring = build_float_epoch_price_ring(
            entry_ts=ts, minutes=10, base_price=1000.0, rise_pct_per_min=0.1
        )
        self.assertTrue(ring)
        self.assertIsInstance(ring[0][0], float)

    def test_full_preflight_report_ready(self) -> None:
        report = run_live_pipeline_preflight(config_path=self.cfg_path, repo_root=REPO)
        self.assertTrue(report.ready, report.errors)
        self.assertEqual(report.verdict, PREFLIGHT_VERDICT)
        by_id = {c.case_id: c for c in report.cases}
        self.assertTrue(by_id["normal_candidate"].full_exposure_gate_reached)
        self.assertIsNotNone(by_id["normal_candidate"].rsi14)
        self.assertFalse(by_id["normal_candidate"].late_chase_flag)
        self.assertNotEqual(
            by_id["normal_candidate"].decision_reason,
            REJECT_CLASSIC_LATE_CHASE_RSI_OVER80,
        )
        self.assertEqual(
            by_id["late_chase_rsi_block"].decision_reason,
            REJECT_CLASSIC_LATE_CHASE_RSI_OVER80,
        )
        self.assertNotEqual(
            by_id["late_chase_guard_disabled"].decision_reason,
            REJECT_CLASSIC_LATE_CHASE_RSI_OVER80,
        )

    def test_late_chase_case_rejects_when_guard_enabled(self) -> None:
        from small_paper.live_pipeline_preflight import _now_epoch

        ts = _now_epoch()
        ring = build_float_epoch_price_ring(
            entry_ts=ts, minutes=25, base_price=2600.0, rise_pct_per_min=0.22
        )
        payload = build_live_mock_push_payload(
            symbol="6976.T", price=ring[-1][1], entry_ts=ts
        )
        result = run_live_pipeline_case(
            case_id="late_chase_rsi_block",
            config=self.config,
            price_ring=ring,
            payload=payload,
            classic_guard_enabled=True,
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.tick_ts_type, "float")
        self.assertEqual(result.decision_reason, REJECT_CLASSIC_LATE_CHASE_RSI_OVER80)
        self.assertGreaterEqual(float(result.rsi14 or 0), 80.0)

    def test_normal_case_no_total_seconds_error(self) -> None:
        from small_paper.live_pipeline_preflight import _now_epoch

        ts = _now_epoch()
        ring = build_normal_preflight_price_ring(entry_ts=ts, base_price=2800.0)
        payload = build_live_mock_push_payload(
            symbol="6976.T", price=ring[-1][1], entry_ts=ts
        )
        result = run_live_pipeline_case(
            case_id="normal_candidate",
            config=self.config,
            price_ring=ring,
            payload=payload,
            classic_guard_enabled=True,
        )
        self.assertTrue(result.ok)
        self.assertNotIn("total_seconds", result.error)
        self.assertTrue(result.full_exposure_gate_reached)


if __name__ == "__main__":
    unittest.main()
