"""Phase404 no progress exit shadow tests."""

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

from research.phase404_no_progress_exit_shadow import (  # noqa: E402
    NoProgressPolicySpec,
    build_tick_states,
    iter_policy_grid,
    no_progress_matches,
    run_phase404_shadow,
    simulate_no_progress_exit,
)


class TestPhase404NoProgress(unittest.TestCase):
    def test_grid_count(self) -> None:
        self.assertEqual(len(iter_policy_grid()), 576)

    def test_no_progress_requires_time_and_stagnation(self) -> None:
        policy = NoProgressPolicySpec(
            hold_sec=900.0,
            max_mfe_pct=0.5,
            current_pnl_pct=0.0,
            high_update_mode="zero",
            vwap_dev_mode="none",
        )
        early = {
            "elapsed": 600.0,
            "peak_mfe": 0.1,
            "pnl": -0.3,
            "high_updates": 0,
            "vwap_dev": None,
        }
        late_flat = {
            "elapsed": 1000.0,
            "peak_mfe": 0.1,
            "pnl": -0.3,
            "high_updates": 0,
            "vwap_dev": None,
        }
        late_progress = {
            "elapsed": 1000.0,
            "peak_mfe": 0.6,
            "pnl": 0.2,
            "high_updates": 2,
            "vwap_dev": None,
        }
        self.assertFalse(no_progress_matches(early, policy))
        self.assertTrue(no_progress_matches(late_flat, policy))
        self.assertFalse(no_progress_matches(late_progress, policy))

    def test_high_update_filter(self) -> None:
        policy = NoProgressPolicySpec(
            hold_sec=900.0,
            max_mfe_pct=0.8,
            current_pnl_pct=0.2,
            high_update_mode="lte1",
            vwap_dev_mode="none",
        )
        state = {
            "elapsed": 1200.0,
            "peak_mfe": 0.3,
            "pnl": -0.1,
            "high_updates": 1,
            "vwap_dev": None,
        }
        self.assertTrue(no_progress_matches(state, policy))
        state["high_updates"] = 2
        self.assertFalse(no_progress_matches(state, policy))

    def test_simulate_no_progress_exit(self) -> None:
        entry = 1000.0
        entry_ts = 1_000_000.0
        states = build_tick_states(
            [
                (entry_ts + 100, 999.0),
                (entry_ts + 1000, 998.5),
                (entry_ts + 2000, 997.0),
            ],
            entry_ts=entry_ts,
            entry_price=entry,
            session_end_ts=entry_ts + 5000,
            entry_vwap_dev_pct=-0.1,
        )
        policy = NoProgressPolicySpec(
            hold_sec=900.0,
            max_mfe_pct=0.5,
            current_pnl_pct=0.0,
            high_update_mode="zero",
            vwap_dev_mode="lt0",
        )
        result = simulate_no_progress_exit(
            states,
            entry_price=entry,
            entry_ts=entry_ts,
            session_end_ts=entry_ts + 5000,
            imb_pct=10.0,
            policy=policy,
        )
        self.assertEqual(result["shadow_exit_reason"], "no_progress_exit")

    def test_run_shadow(self) -> None:
        src = REPO / "results" / "reports" / "phase399_historical_position_cap_backfill_trades.csv"
        if not src.is_file():
            self.skipTest("phase399 trades missing")
        out = REPO / "results" / "reports"
        result = run_phase404_shadow(repo_root=REPO, trades_path=src, output_dir=out)
        self.assertEqual(result["summary"]["position_cap_accepted_trade_count"], 755)
        self.assertEqual(result["summary"]["policy_variant_count"], 576)
        self.assertTrue((out / "phase404_no_progress_exit_grid.csv").is_file())


if __name__ == "__main__":
    unittest.main()
