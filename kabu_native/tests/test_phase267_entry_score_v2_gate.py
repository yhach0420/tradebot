"""Phase267/314: entry_score_v2 gate (min=3 Momentum+Board since Phase314)."""

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from research.exposure_gate import (
    REJECT_ENTRY_SCORE_V2_BELOW,
    REJECT_LOW_QUALITY,
    REJECT_MOMENTUM_LOW_REQUIRED,
    ExposureGate,
    ExposureGateConfig,
)
from small_paper.config import load_pilot_config
from small_paper.entry_expectancy_score_shadow import (
    ENTRY_SCORE_V2_GATE_MIN,
    compute_entry_expectancy_score_fields,
)

JST = ZoneInfo("Asia/Tokyo")
PROFILE = "momentum_volume_v13_combined"
V2_MIN = ENTRY_SCORE_V2_GATE_MIN


def _pass_v2_trade() -> dict:
    return {
        "profile": PROFILE,
        "symbol": "9984.T",
        "entry_time": datetime(2026, 5, 21, 9, 30, tzinfo=JST).isoformat(),
        "exit_time": datetime(2026, 5, 21, 10, 0, tzinfo=JST).isoformat(),
        "trade_date": "2026-05-21",
        "momentum_continuation_score": 0.20,
        "entry_order_book_imbalance": 0.50,
        "continuation_quality_score": 0.45,
    }


def _momentum_only_trade() -> dict:
    return {
        "profile": PROFILE,
        "symbol": "9984.T",
        "entry_time": datetime(2026, 5, 21, 9, 30, tzinfo=JST).isoformat(),
        "exit_time": datetime(2026, 5, 21, 10, 0, tzinfo=JST).isoformat(),
        "trade_date": "2026-05-21",
        "momentum_continuation_score": 0.20,
        "entry_order_book_imbalance": 0.40,
        "continuation_quality_score": 0.45,
    }


def _no_momentum_trade() -> dict:
    return {
        "profile": PROFILE,
        "symbol": "9984.T",
        "entry_time": datetime(2026, 5, 21, 9, 30, tzinfo=JST).isoformat(),
        "exit_time": datetime(2026, 5, 21, 10, 0, tzinfo=JST).isoformat(),
        "trade_date": "2026-05-21",
        "momentum_continuation_score": 0.35,
        "entry_order_book_imbalance": 0.50,
        "continuation_quality_score": 0.85,
    }


def _gate() -> ExposureGate:
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

    def test_score2_rejects_at_min3(self) -> None:
        trade = dict(_momentum_only_trade())
        fields = compute_entry_expectancy_score_fields(trade=trade)
        self.assertEqual(fields["entry_expectancy_score_v2"], 2)
        gate = _gate()
        decision = gate.evaluate_entry(trade)
        self.assertFalse(decision.accept)
        self.assertEqual(decision.reason, REJECT_ENTRY_SCORE_V2_BELOW)
        self.assertFalse(decision.entry_score_v2_gate_pass)
        self.assertEqual(decision.entry_score_v2_threshold, V2_MIN)

    def test_score3_passes_at_min3(self) -> None:
        trade = dict(_pass_v2_trade())
        fields = compute_entry_expectancy_score_fields(trade=trade)
        self.assertEqual(fields["entry_expectancy_score_v2"], 3)
        gate = _gate()
        decision = gate.evaluate_entry(trade)
        self.assertTrue(decision.accept)
        self.assertTrue(decision.entry_score_v2_gate_pass)
        self.assertEqual(decision.entry_score_v2_threshold, V2_MIN)
        self.assertEqual(decision.entry_expectancy_score_v2, 3)

    def test_no_momentum_rejects_momentum_low_required(self) -> None:
        trade = dict(_no_momentum_trade())
        gate = _gate()
        decision = gate.evaluate_entry(trade)
        self.assertFalse(decision.accept)
        self.assertEqual(decision.reason, REJECT_MOMENTUM_LOW_REQUIRED)

    def test_momentum_without_board_rejects_score_below(self) -> None:
        trade = _momentum_only_trade()
        gate = _gate()
        decision = gate.evaluate_entry(trade)
        self.assertFalse(decision.accept)
        self.assertEqual(decision.reason, REJECT_ENTRY_SCORE_V2_BELOW)

    def test_low_quality_pass_v2_passes(self) -> None:
        trade = _pass_v2_trade()
        gate = _gate()
        decision = gate.evaluate_entry(trade)
        self.assertTrue(decision.accept)
        self.assertTrue(decision.entry_score_v2_gate_pass)
        self.assertNotEqual(decision.reason, REJECT_LOW_QUALITY)

    def test_high_quality_low_v2_rejects(self) -> None:
        trade = _no_momentum_trade()
        fields = compute_entry_expectancy_score_fields(trade=trade)
        self.assertLess(int(fields["entry_expectancy_score_v2"]), V2_MIN)
        gate = _gate()
        decision = gate.evaluate_entry(trade)
        self.assertFalse(decision.accept)
        self.assertIn(decision.reason, (REJECT_ENTRY_SCORE_V2_BELOW, REJECT_MOMENTUM_LOW_REQUIRED))

    def test_quality_reject_disabled_when_v2_gate_on(self) -> None:
        trade = dict(_no_momentum_trade())
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
