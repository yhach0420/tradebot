"""Phase402 time-decay exit shadow tests."""

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

from research.phase402_time_decay_exit_shadow import (  # noqa: E402
    POLICY_COMBINED_20M,
    PolicySpec,
    iter_policy_grid,
    simulate_time_decay_exit,
    run_phase402_shadow,
)


class TestPhase402Shadow(unittest.TestCase):
    def test_policy_grid_count(self) -> None:
        specs = iter_policy_grid()
        # baseline + A(12) + B(12) + C(16) + D(16) = 57
        self.assertEqual(len(specs), 57)

    def test_mfe_decay_exits_earlier(self) -> None:
        entry = 1000.0
        entry_ts = 1_000_000.0
        series = [
            (entry_ts + 60, 1005.0),
            (entry_ts + 600, 1004.0),
            (entry_ts + 1300, 1002.5),
            (entry_ts + 1400, 1001.0),
        ]
        base = simulate_time_decay_exit(
            series,
            entry_ts=entry_ts,
            entry_price=entry,
            session_end_ts=entry_ts + 5000,
            imb_pct=10.0,
            policy=PolicySpec("baseline", None, None, None, False, False),
        )
        decay = simulate_time_decay_exit(
            series,
            entry_ts=entry_ts,
            entry_price=entry,
            session_end_ts=entry_ts + 5000,
            imb_pct=10.0,
            policy=PolicySpec(POLICY_COMBINED_20M, 1200.0, 0.2, -0.6, True, True),
        )
        self.assertIn(base["shadow_exit_reason"], ("session_close", "trailing_mfe_exit"))
        self.assertIn(decay["shadow_exit_reason"], ("trailing_mfe_exit", "stop_hit", "session_close"))

    def test_stop_tighten_after_threshold(self) -> None:
        entry = 1000.0
        entry_ts = 1_000_000.0
        series = [
            (entry_ts + 100, 999.0),
            (entry_ts + 1300, 994.0),
        ]
        decay = simulate_time_decay_exit(
            series,
            entry_ts=entry_ts,
            entry_price=entry,
            session_end_ts=entry_ts + 5000,
            imb_pct=10.0,
            policy=PolicySpec(POLICY_COMBINED_20M, 1200.0, 0.3, -0.4, True, True),
        )
        self.assertEqual(decay["shadow_exit_reason"], "stop_hit")
        self.assertGreater(decay["shadow_pnl_pct"], -1.2)

    def test_run_shadow(self) -> None:
        src = REPO / "results" / "reports" / "phase399_historical_position_cap_backfill_trades.csv"
        if not src.is_file():
            self.skipTest("phase399 trades missing")
        out = REPO / "results" / "reports"
        result = run_phase402_shadow(repo_root=REPO, trades_path=src, output_dir=out)
        self.assertEqual(result["summary"]["position_cap_accepted_trade_count"], 755)
        self.assertTrue((out / "phase402_time_decay_exit_grid.csv").is_file())
        self.assertTrue((out / "phase402_time_decay_exit_trades.csv").is_file())
        self.assertTrue((out / "phase402_time_decay_exit_summary.json").is_file())


if __name__ == "__main__":
    unittest.main()
