"""Phase407A lookahead audit tests."""

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
    no_progress_matches,
)
from research.phase407a_no_progress_lookahead_audit import (  # noqa: E402
    _find_exit_state,
    _recompute_peak_to_ts,
    run_phase407a_audit,
)


class TestPhase407ALookahead(unittest.TestCase):
    def test_peak_mfe_cumulative(self) -> None:
        entry_ts = 1_000_000.0
        states = build_tick_states(
            [
                (entry_ts + 100, 1010.0),
                (entry_ts + 500, 1008.0),
                (entry_ts + 1000, 1005.0),
            ],
            entry_ts=entry_ts,
            entry_price=1000.0,
            session_end_ts=entry_ts + 5000,
            entry_vwap_dev_pct=None,
        )
        self.assertEqual(states[0]["peak_mfe"], states[0]["pnl"])
        self.assertGreater(states[1]["peak_mfe"], states[1]["pnl"])
        peak_at_1000 = _recompute_peak_to_ts(states, entry_ts + 1000)
        self.assertEqual(peak_at_1000, states[2]["peak_mfe"])

    def test_find_exit_state_closest_tick(self) -> None:
        states = [
            {"ts": 100.0, "px": 100.0, "pnl": 0.0, "peak_mfe": 0.0},
            {"ts": 100.3, "px": 101.0, "pnl": 1.0, "peak_mfe": 1.0},
            {"ts": 100.8, "px": 102.0, "pnl": 2.0, "peak_mfe": 2.0},
        ]
        found = _find_exit_state(states, 100.0)
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found["px"], 100.0)

    def test_no_progress_requires_elapsed(self) -> None:
        policy = NoProgressPolicySpec(900.0, 0.8, 0.2, "none", "none")
        early = {"elapsed": 100.0, "peak_mfe": 0.1, "pnl": -0.3, "high_updates": 0, "vwap_dev": None}
        self.assertFalse(no_progress_matches(early, policy))

    def test_run_audit(self) -> None:
        src = REPO / "results" / "reports" / "phase399_historical_position_cap_backfill_trades.csv"
        if not src.is_file():
            self.skipTest("phase399 trades missing")
        result = run_phase407a_audit(repo_root=REPO, trades_path=src, output_dir=REPO / "results" / "reports")
        checks = result["summary"]["checks"]
        self.assertIn(result["summary"]["verdict"], ("PASS", "WARN"))
        self.assertEqual(checks["1_mfe_is_so_far_at_judgment"]["status"], "PASS")
        self.assertEqual(checks["2_current_pnl_at_judgment_price"]["status"], "PASS")
        self.assertEqual(checks["3_exit_price_exists_at_judgment"]["status"], "PASS")
        self.assertEqual(checks["5_single_exit_judgment"]["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
