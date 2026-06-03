"""Phase267/273: entry_score_v2 gate replaces quality entry reject (min=5 since Phase273)."""

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from research.exposure_gate import (
    REJECT_ENTRY_SCORE_V2_BELOW,
    REJECT_LOW_QUALITY,
    ExposureGate,
    ExposureGateConfig,
)
from small_paper.config import load_pilot_config
from small_paper.entry_expectancy_score_shadow import compute_entry_expectancy_score_fields

JST = ZoneInfo("Asia/Tokyo")
PROFILE = "momentum_volume_v13_combined"
V2_MIN = 5


def _high_v2_trade() -> dict:
    return {
        "profile": PROFILE,
        "symbol": "9984.T",
        "entry_time": datetime(2026, 5, 21, 9, 30, tzinfo=JST).isoformat(),
        "exit_time": datetime(2026, 5, 21, 10, 0, tzinfo=JST).isoformat(),
        "trade_date": "2026-05-21",
        "trading_value": 3e10,
        "rolling_mae_pct": -0.0003,
        "entry_high_break_recent": False,
        "max_continuation_duration": 500.0,
        "momentum_continuation_score": 0.20,
        "entry_order_book_imbalance": 0.50,
        "current_price": 5000.0,
        "continuation_quality_score": 0.45,
    }


def _low_v2_trade() -> dict:
    return {
        "profile": PROFILE,
        "symbol": "9984.T",
        "entry_time": datetime(2026, 5, 21, 9, 30, tzinfo=JST).isoformat(),
        "exit_time": datetime(2026, 5, 21, 10, 0, tzinfo=JST).isoformat(),
        "trade_date": "2026-05-21",
        "trading_value": 1e9,
        "rolling_mae_pct": -0.01,
        "entry_high_break_recent": True,
        "max_continuation_duration": 10.0,
        "momentum_continuation_score": 0.35,
        "entry_order_book_imbalance": 0.40,
        "current_price": 1000.0,
        "continuation_quality_score": 0.85,
    }


def _gate_min5() -> ExposureGate:
    return ExposureGate(
        ExposureGateConfig(
            profile=PROFILE,
            min_continuation_quality=0.70,
            reject_below_quality=False,
            entry_score_v2_min=V2_MIN,
            max_concurrent_positions=3,
        )
    )


class TestPhase267EntryScoreV2Gate(unittest.TestCase):
    def test_v2_gate_config_from_q070_yaml(self) -> None:
        cfg_path = (
            __import__("pathlib").Path(__file__).resolve().parents[1]
            / "configs"
            / "small_paper_pilot_q070_cap3.yaml"
        )
        cfg = load_pilot_config(cfg_path)
        self.assertEqual(cfg.entry_score_v2_min, V2_MIN)
        self.assertFalse(cfg.reject_below_quality)
        eg = cfg.exposure_gate_config()
        self.assertEqual(eg.entry_score_v2_min, V2_MIN)

    def test_score4_rejects_at_min5(self) -> None:
        trade = dict(_high_v2_trade())
        trade["entry_expectancy_score_v2"] = 4
        gate = _gate_min5()
        decision = gate.evaluate_entry(trade)
        self.assertFalse(decision.accept)
        self.assertEqual(decision.reason, REJECT_ENTRY_SCORE_V2_BELOW)
        self.assertFalse(decision.entry_score_v2_gate_pass)
        self.assertEqual(decision.entry_score_v2_threshold, V2_MIN)
        self.assertEqual(decision.entry_expectancy_score_v2, 4)

    def test_score5_passes_at_min5(self) -> None:
        trade = dict(_high_v2_trade())
        trade["entry_expectancy_score_v2"] = 5
        gate = _gate_min5()
        decision = gate.evaluate_entry(trade)
        self.assertTrue(decision.accept)
        self.assertTrue(decision.entry_score_v2_gate_pass)
        self.assertEqual(decision.entry_score_v2_threshold, V2_MIN)
        self.assertEqual(decision.entry_expectancy_score_v2, 5)

    def test_low_quality_high_v2_passes(self) -> None:
        trade = _high_v2_trade()
        fields = compute_entry_expectancy_score_fields(trade=trade)
        self.assertGreaterEqual(int(fields["entry_expectancy_score_v2"]), V2_MIN)
        gate = _gate_min5()
        decision = gate.evaluate_entry(trade)
        self.assertTrue(decision.accept)
        self.assertTrue(decision.entry_score_v2_gate_pass)
        self.assertNotEqual(decision.reason, REJECT_LOW_QUALITY)

    def test_high_quality_low_v2_rejects(self) -> None:
        trade = _low_v2_trade()
        fields = compute_entry_expectancy_score_fields(trade=trade)
        self.assertLess(int(fields["entry_expectancy_score_v2"]), V2_MIN)
        gate = _gate_min5()
        decision = gate.evaluate_entry(trade)
        self.assertFalse(decision.accept)
        self.assertEqual(decision.reason, REJECT_ENTRY_SCORE_V2_BELOW)
        self.assertFalse(decision.entry_score_v2_gate_pass)
        self.assertEqual(decision.entry_score_v2_threshold, V2_MIN)

    def test_quality_reject_disabled_when_v2_gate_on(self) -> None:
        trade = dict(_low_v2_trade())
        trade["continuation_quality_score"] = 0.50
        gate = ExposureGate(
            ExposureGateConfig(
                profile=PROFILE,
                min_continuation_quality=0.70,
                reject_below_quality=True,
                entry_score_v2_min=V2_MIN,
            )
        )
        decision = gate.evaluate_entry(trade)
        self.assertEqual(decision.reason, REJECT_ENTRY_SCORE_V2_BELOW)
        self.assertNotEqual(decision.reason, REJECT_LOW_QUALITY)

    def test_pilot_runner_logs_v2_gate_fields(self) -> None:
        pilot_src = (
            __import__("pathlib").Path(__file__).resolve().parents[1]
            / "src"
            / "small_paper"
            / "pilot_runner.py"
        ).read_text(encoding="utf-8")
        for key in (
            "entry_score_v2_threshold",
            "entry_score_v2_gate_pass",
        ):
            self.assertIn(f'"{key}"', pilot_src)


if __name__ == "__main__":
    unittest.main()
