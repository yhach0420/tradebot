"""Phase472: PBv2-3 runtime adoption tests."""

from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.exposure_gate import (  # noqa: E402
    REJECT_LATE_CHASE_GUARD,
    REJECT_MOMENTUM_LOW_REQUIRED,
    ExposureGate,
    ExposureGateConfig,
)
from small_paper.config import load_pilot_config  # noqa: E402
from small_paper.discord_message_builder import format_research_shadow_daily_summary_lines
from small_paper.entry_expectancy_score_shadow import (  # noqa: E402
    MOMENTUM_SCORE_CUTOFF_P33,
    momentum_score_cutoff_pass,
)
from small_paper.late_chase_entry_guard import (  # noqa: E402
    LateChaseGuardConfig,
    LateChaseGuardState,
    REJECT_LATE_CHASE_GUARD as GUARD_REJECT,
    would_block_late_chase_guard,
)
from small_paper.pilot_runner import _late_chase_guard_summary_fields  # noqa: E402

REPO_ROOT = REPO / "kabu_native"
PROFILE = "momentum_volume_v13_combined"
PILOT_YAML = (
    REPO_ROOT
    / "configs"
    / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
)


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
        "entry_rise_10min_pct": 0.50,
        "entry_near_day_high_pct": 2.0,
        "universe_slot": "core",
    }
    trade.update(overrides)
    return trade


def _gate(*, late_chase_enabled: bool = False) -> ExposureGate:
    lc = None
    if late_chase_enabled:
        lc = LateChaseGuardState(config=LateChaseGuardConfig(enabled=True))
    return ExposureGate(
        ExposureGateConfig(
            profile=PROFILE,
            reject_below_quality=False,
            entry_score_v2_min=3,
            momentum_score_cutoff_max=MOMENTUM_SCORE_CUTOFF_P33,
            max_concurrent_positions=5,
        ),
        late_chase_guard=lc,
    )


class TestPhase472PBv2Runtime(unittest.TestCase):
    def test_momentum_score_cutoff_pass_low(self) -> None:
        self.assertTrue(momentum_score_cutoff_pass(_base_trade(momentum_continuation_score=0.20)))
        self.assertTrue(momentum_score_cutoff_pass(_base_trade(momentum_continuation_score=0.2546)))

    def test_momentum_score_cutoff_rejects_mid_high(self) -> None:
        self.assertFalse(momentum_score_cutoff_pass(_base_trade(momentum_continuation_score=0.28)))
        decision = _gate().evaluate_entry(_base_trade(momentum_continuation_score=0.28))
        self.assertFalse(decision.accept)
        self.assertEqual(decision.reason, REJECT_MOMENTUM_LOW_REQUIRED)

    def test_late_chase_blocks_chase_near_high(self) -> None:
        trade = _base_trade(entry_rise_10min_pct=0.20, entry_near_day_high_pct=0.8)
        self.assertTrue(would_block_late_chase_guard(trade))

    def test_late_chase_passes_far_from_high(self) -> None:
        trade = _base_trade(entry_rise_10min_pct=0.20, entry_near_day_high_pct=2.0)
        self.assertFalse(would_block_late_chase_guard(trade))

    def test_late_chase_passes_strong_r10(self) -> None:
        trade = _base_trade(entry_rise_10min_pct=0.50, entry_near_day_high_pct=0.5)
        self.assertFalse(would_block_late_chase_guard(trade))

    def test_exposure_gate_late_chase_reject_reason(self) -> None:
        trade = _base_trade(entry_rise_10min_pct=0.20, entry_near_day_high_pct=0.8)
        decision = _gate(late_chase_enabled=True).evaluate_entry(trade)
        self.assertFalse(decision.accept)
        self.assertEqual(decision.reason, REJECT_LATE_CHASE_GUARD)
        self.assertEqual(GUARD_REJECT, "late_chase_guard")

    def test_late_chase_disabled_allows_chase(self) -> None:
        trade = _base_trade(entry_rise_10min_pct=0.20, entry_near_day_high_pct=0.8)
        self.assertTrue(_gate(late_chase_enabled=False).evaluate_entry(trade).accept)

    def test_pilot_yaml_pbv2_flags(self) -> None:
        cfg = load_pilot_config(PILOT_YAML)
        self.assertTrue(cfg.late_chase_guard_enabled)
        self.assertAlmostEqual(cfg.momentum_score_cutoff_max, 0.2546)
        self.assertTrue(cfg.high_drift_guard_enabled)
        self.assertTrue(cfg.weak_shape_reject_enabled)
        self.assertFalse(cfg.order_enabled)
        self.assertTrue(cfg.paper_only)

    def test_make_exposure_gate_wires_late_chase(self) -> None:
        cfg = load_pilot_config(PILOT_YAML)
        gate = cfg.make_exposure_gate()
        self.assertIsNotNone(getattr(gate, "late_chase_guard", None))

    def test_summary_and_discord_late_chase(self) -> None:
        @dataclass
        class _State:
            late_chase_reject_count: int = 3
            late_chase_reject_symbols: set[str] = field(default_factory=lambda: {"4062.T"})

        gate = _gate(late_chase_enabled=True)
        summary = _late_chase_guard_summary_fields(gate, _State())  # type: ignore[arg-type]
        self.assertTrue(summary["late_chase_guard_enabled"])
        self.assertEqual(summary["late_chase_reject_count"], 3)

        lines = format_research_shadow_daily_summary_lines(
            {"late_chase_guard_enabled": True, "late_chase_reject_count": 3}
        )
        self.assertTrue(any("LateChase Guard: reject=3" in line for line in lines))

    def test_pilot_runner_logs_late_chase_reject(self) -> None:
        src = (REPO_ROOT / "src" / "small_paper" / "pilot_runner.py").read_text(encoding="utf-8")
        self.assertIn('decision.reason == "late_chase_guard"', src)
        self.assertIn('"late_chase_reject_count"', src)


if __name__ == "__main__":
    unittest.main()
