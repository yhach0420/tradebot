"""Phase452: Board mid+high ENTRY + Weak Shape Reject runtime tests."""

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
    REJECT_ENTRY_SCORE_V2_BELOW,
    REJECT_MOMENTUM_LOW_REQUIRED,
    REJECT_WEAK_SHAPE,
    ExposureGate,
    ExposureGateConfig,
)
from small_paper.config import load_pilot_config
from small_paper.discord_message_builder import format_research_shadow_daily_summary_lines
from small_paper.entry_expectancy_score_shadow import (
    ENTRY_SCORE_V2_GATE_MIN,
    active_score_tokens_v2,
    board_mid_or_high_required_for_v2,
    compute_entry_expectancy_score_fields,
)
from small_paper.pilot_runner import (  # noqa: E402
    _board_entry_summary_fields,
    _weak_shape_reject_guard_summary_fields,
)
from small_paper.weak_shape_reject_entry_guard import (
    REJECT_WEAK_SHAPE as GUARD_REJECT,
    WeakShapeRejectGuardConfig,
    WeakShapeRejectGuardState,
    classify_intraday_weak_shape,
    would_block_weak_shape_reject,
)

REPO_ROOT = REPO / "kabu_native"
PROFILE = "momentum_volume_v13_combined"
PILOT_YAML = (
    REPO_ROOT
    / "configs"
    / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
)
V2_MIN = ENTRY_SCORE_V2_GATE_MIN


def _base_trade(**overrides: object) -> dict:
    trade = {
        "profile": PROFILE,
        "symbol": "9984.T",
        "entry_time": "2026-06-18T09:30:00+09:00",
        "exit_time": "2026-06-18T10:00:00+09:00",
        "trade_date": "2026-06-18",
        "continuation_quality_score": 0.72,
        "momentum_continuation_score": 0.20,
        "entry_order_book_imbalance": 0.50,
        "universe_slot": "core",
        "day_high_minutes_from_open": 90.0,
        "day_high_distance_pct": 0.5,
        "minutes_since_day_high_update": 5.0,
        "entry_rise_15min_pct": 0.5,
        "entry_rise_10min_pct": 0.3,
    }
    trade.update(overrides)
    return trade


def _gate(*, weak_shape_enabled: bool = False) -> ExposureGate:
    ws = None
    if weak_shape_enabled:
        ws = WeakShapeRejectGuardState(config=WeakShapeRejectGuardConfig(enabled=True))
    return ExposureGate(
        ExposureGateConfig(
            profile=PROFILE,
            reject_below_quality=False,
            entry_score_v2_min=V2_MIN,
            max_concurrent_positions=5,
        ),
        weak_shape_reject_guard=ws,
    )


class TestPhase452BoardMidHighWeakShape(unittest.TestCase):
    def test_board_mid_passes_v2_gate(self) -> None:
        trade = _base_trade(entry_order_book_imbalance=0.50)
        fields = compute_entry_expectancy_score_fields(trade=trade)
        self.assertEqual(fields["entry_expectancy_score_v2"], 3)
        self.assertIn("Board:mid", active_score_tokens_v2(trade))
        decision = _gate().evaluate_entry(trade)
        self.assertTrue(decision.accept)
        self.assertTrue(decision.entry_score_v2_gate_pass)

    def test_board_high_passes_v2_gate(self) -> None:
        trade = _base_trade(entry_order_book_imbalance=0.55)
        fields = compute_entry_expectancy_score_fields(trade=trade)
        self.assertEqual(fields["entry_expectancy_score_v2"], 3)
        self.assertIn("Board:high", active_score_tokens_v2(trade))
        self.assertTrue(board_mid_or_high_required_for_v2(trade))
        decision = _gate().evaluate_entry(trade)
        self.assertTrue(decision.accept)

    def test_board_low_rejects(self) -> None:
        trade = _base_trade(entry_order_book_imbalance=0.40)
        fields = compute_entry_expectancy_score_fields(trade=trade)
        self.assertEqual(fields["entry_expectancy_score_v2"], 2)
        self.assertFalse(board_mid_or_high_required_for_v2(trade))
        decision = _gate().evaluate_entry(trade)
        self.assertFalse(decision.accept)
        self.assertEqual(decision.reason, REJECT_ENTRY_SCORE_V2_BELOW)

    def test_momentum_mid_and_high_reject(self) -> None:
        for mom in (0.28, 0.35):
            trade = _base_trade(momentum_continuation_score=mom)
            decision = _gate().evaluate_entry(trade)
            self.assertFalse(decision.accept, msg=f"momentum={mom}")
            self.assertEqual(decision.reason, REJECT_MOMENTUM_LOW_REQUIRED)

    def test_weak_shape_reject_blocks_opening_peak(self) -> None:
        trade = _base_trade(
            day_high_minutes_from_open=15.0,
            day_high_distance_pct=2.0,
            minutes_since_day_high_update=30.0,
            entry_rise_15min_pct=-0.5,
            entry_rise_10min_pct=-0.3,
        )
        self.assertEqual(classify_intraday_weak_shape(trade), "opening_peak")
        decision = _gate(weak_shape_enabled=True).evaluate_entry(trade)
        self.assertFalse(decision.accept)
        self.assertEqual(decision.reason, REJECT_WEAK_SHAPE)
        self.assertEqual(GUARD_REJECT, "weak_shape_reject")

    def test_weak_shape_reject_blocks_slow_opening_peak(self) -> None:
        trade = _base_trade(
            day_high_minutes_from_open=45.0,
            day_high_distance_pct=2.5,
            minutes_since_day_high_update=25.0,
            entry_rise_15min_pct=-0.2,
            entry_rise_10min_pct=-0.1,
        )
        self.assertEqual(classify_intraday_weak_shape(trade), "slow_opening_peak")
        self.assertTrue(would_block_weak_shape_reject(trade))

    def test_weak_shape_disabled_preserves_accept(self) -> None:
        trade = _base_trade(
            day_high_minutes_from_open=15.0,
            day_high_distance_pct=2.0,
            minutes_since_day_high_update=30.0,
            entry_rise_15min_pct=-0.5,
        )
        self.assertTrue(_gate(weak_shape_enabled=False).evaluate_entry(trade).accept)

    def test_pilot_runner_logs_weak_shape_reject_reason(self) -> None:
        pilot_src = (REPO_ROOT / "src" / "small_paper" / "pilot_runner.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('decision.reason == "weak_shape_reject"', pilot_src)
        self.assertIn('"weak_shape_reject_count"', pilot_src)
        self.assertIn('"board_mid_entry_count"', pilot_src)
        self.assertIn('"board_high_entry_count"', pilot_src)

    def test_summary_and_discord_counts(self) -> None:
        from dataclasses import dataclass, field

        @dataclass
        class _State:
            weak_shape_reject_count: int = 2
            weak_shape_reject_symbols: set[str] = field(default_factory=lambda: {"6976.T"})
            board_mid_entry_count: int = 5
            board_high_entry_count: int = 3

        gate = _gate(weak_shape_enabled=True)
        state = _State()
        summary = {}
        summary.update(_weak_shape_reject_guard_summary_fields(gate, state))  # type: ignore[arg-type]
        summary.update(_board_entry_summary_fields(state))  # type: ignore[arg-type]
        self.assertEqual(summary["weak_shape_reject_count"], 2)
        self.assertEqual(summary["board_mid_entry_count"], 5)
        self.assertEqual(summary["board_high_entry_count"], 3)

        lines = format_research_shadow_daily_summary_lines(
            {
                "weak_shape_reject_enabled": True,
                "weak_shape_reject_count": 2,
                "board_high_entry_count": 3,
            }
        )
        self.assertTrue(any("WeakShape Reject: count=2" in line for line in lines))
        self.assertTrue(any("BoardHigh ENTRY: count=3" in line for line in lines))

    def test_no_lookahead_in_weak_shape_guard(self) -> None:
        src = (
            REPO_ROOT / "src" / "small_paper" / "weak_shape_reject_entry_guard.py"
        ).read_text(
            encoding="utf-8"
        )
        self.assertNotIn("eod_shape_class", src)
        self.assertNotIn("session_close", src)
        self.assertNotIn("close_price", src)

    def test_preflight_yaml_flags(self) -> None:
        cfg = load_pilot_config(PILOT_YAML)
        self.assertTrue(cfg.paper_only)
        self.assertFalse(cfg.order_enabled)
        self.assertEqual(cfg.max_concurrent_positions, 5)
        self.assertEqual(cfg.same_symbol_open_policy, "no_overlap_replace")
        self.assertTrue(cfg.high_drift_guard_enabled)
        self.assertTrue(cfg.no_progress_exit_enabled)
        self.assertTrue(cfg.weak_shape_reject_enabled)
        self.assertFalse(cfg.enable_pullback_misread_dynamic40_guard)
        gate = cfg.make_exposure_gate()
        self.assertIsNotNone(getattr(gate, "weak_shape_reject_guard", None))


if __name__ == "__main__":
    unittest.main()
