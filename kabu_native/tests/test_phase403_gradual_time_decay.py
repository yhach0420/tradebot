"""Phase403 gradual time-decay MFE shadow tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PARENT = REPO.parent
for p in (REPO / "src", PARENT):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase403_gradual_time_decay_shadow import (  # noqa: E402
    POLICY_EXP,
    POLICY_LINEAR,
    GradualPolicySpec,
    activation_mfe_at_elapsed,
    iter_policy_grid,
    run_phase403_shadow,
    simulate_gradual_decay_exit,
)


class TestPhase403GradualDecay(unittest.TestCase):
    def test_activation_linear_schedule(self) -> None:
        policy = GradualPolicySpec(
            POLICY_LINEAR,
            decay_start_sec=600.0,
            initial_mfe_pct=0.8,
            floor_mfe_pct=0.2,
            linear_decay_per_min=0.02,
        )
        self.assertAlmostEqual(
            activation_mfe_at_elapsed(600.0, policy=policy, board_activate=0.6),
            0.8,
        )
        self.assertAlmostEqual(
            activation_mfe_at_elapsed(900.0, policy=policy, board_activate=0.6),
            0.7,
        )
        self.assertAlmostEqual(
            activation_mfe_at_elapsed(1200.0, policy=policy, board_activate=0.6),
            0.6,
        )

    def test_activation_exponential_floor(self) -> None:
        policy = GradualPolicySpec(
            POLICY_EXP,
            decay_start_sec=600.0,
            initial_mfe_pct=1.0,
            floor_mfe_pct=0.2,
            exp_decay_lambda=0.02,
        )
        late = activation_mfe_at_elapsed(12000.0, policy=policy, board_activate=0.6)
        self.assertGreaterEqual(late, 0.2)
        self.assertLess(late, 0.23)

    def test_policy_grid_count(self) -> None:
        specs = iter_policy_grid()
        # linear 3*3*3*3=81, slow 27, fast 27, exp 81 => 216
        self.assertEqual(len(specs), 216)

    def test_gradual_exits_on_decay(self) -> None:
        entry = 1000.0
        entry_ts = 1_000_000.0
        series = [
            (entry_ts + 60, 1006.0),
            (entry_ts + 700, 1005.0),
            (entry_ts + 1300, 1003.0),
            (entry_ts + 1500, 1001.5),
        ]
        policy = GradualPolicySpec(
            POLICY_LINEAR,
            decay_start_sec=600.0,
            initial_mfe_pct=0.8,
            floor_mfe_pct=0.2,
            linear_decay_per_min=0.05,
        )
        result = simulate_gradual_decay_exit(
            series,
            entry_ts=entry_ts,
            entry_price=entry,
            session_end_ts=entry_ts + 8000,
            imb_pct=10.0,
            policy=policy,
        )
        self.assertIn(
            result["shadow_exit_reason"],
            ("trailing_mfe_exit", "session_close", "stop_hit"),
        )

    def test_run_shadow(self) -> None:
        src = REPO / "results" / "reports" / "phase399_historical_position_cap_backfill_trades.csv"
        if not src.is_file():
            self.skipTest("phase399 trades missing")
        out = REPO / "results" / "reports"
        result = run_phase403_shadow(repo_root=REPO, trades_path=src, output_dir=out)
        self.assertEqual(result["summary"]["position_cap_accepted_trade_count"], 755)
        self.assertTrue((out / "phase403_gradual_time_decay_grid.csv").is_file())
        self.assertIn("mandatory_answers", result["summary"])


if __name__ == "__main__":
    unittest.main()
