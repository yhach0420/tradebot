"""Phase659 rise5 mainline readiness tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[1]
for p in (NATIVE / "src", NATIVE.parent):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase659_rise5_mainline_readiness import (  # noqa: E402
    PHASE659_VERDICT,
    _apply_rise5,
    _final_verdict,
    daily_breakdown,
    filter_pbv2_trades,
    run_phase659,
)


class Phase659Tests(unittest.TestCase):
    def test_apply_rise5_partition(self) -> None:
        trades = [
            {"day": "2026-06-20", "entry_pool": "PBV2", "entry_rise_5min_pct": 2.5, "pnl_yen_100": -100.0},
            {"day": "2026-06-20", "entry_pool": "PBV2", "entry_rise_5min_pct": 0.5, "pnl_yen_100": 50.0},
        ]
        kept, blocked = _apply_rise5(trades)
        self.assertEqual(len(kept), 1)
        self.assertEqual(len(blocked), 1)

    def test_daily_breakdown_delta(self) -> None:
        trades = filter_pbv2_trades(
            [
                {"day": "2026-06-20", "entry_pool": "PBV2", "entry_rise_5min_pct": 2.5, "pnl_yen_100": -100.0, "minutes_from_open": 60},
                {"day": "2026-06-20", "entry_pool": "PBV2", "entry_rise_5min_pct": 0.2, "pnl_yen_100": 40.0, "minutes_from_open": 60},
            ]
        )
        rows = daily_breakdown(trades)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["delta_pnl_yen"], 100.0)

    def test_final_verdict_reject_negative(self) -> None:
        v, _ = _final_verdict({"delta_pnl_yen": -1, "entry_reduction_pct": 1, "blocked_winners": 1}, [], [], {"recent_delta_pnl_yen": 0}, {})
        self.assertEqual(v, "REJECT")

    def test_run_on_repo_when_data_present(self) -> None:
        if not (NATIVE / "results" / "small_paper").is_dir():
            self.skipTest("no session data")
        result = run_phase659(repo_root=NATIVE)
        self.assertEqual(result["verdict"], PHASE659_VERDICT)
        m = result["mandatory_answers"]
        self.assertIn("8_final_verdict", m)
        self.assertIn(m["8_final_verdict"], ("ADOPT", "HOLD", "REJECT"))


if __name__ == "__main__":
    unittest.main()
