"""Phase660 rise5 recent regression tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[1]
for p in (NATIVE / "src", NATIVE.parent):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase660_rise5_recent_regression import (  # noqa: E402
    PHASE660_VERDICT,
    _split_recent,
    classify_root_cause,
    threshold_sweep_recent,
    run_phase660,
)


class Phase660Tests(unittest.TestCase):
    def test_split_recent(self) -> None:
        trades = [{"day": f"2026-06-{d:02d}", "entry_pool": "PBV2", "pnl_yen_100": 1.0} for d in range(20, 28)]
        days, recent, prior = _split_recent(trades)
        self.assertEqual(len(days), 5)
        self.assertEqual(len(recent), 5)
        self.assertEqual(len(prior), 3)

    def test_threshold_sweep(self) -> None:
        trades = [
            {"entry_pool": "PBV2", "entry_rise_5min_pct": 2.5, "pnl_yen_100": -100.0},
            {"entry_pool": "PBV2", "entry_rise_5min_pct": 0.2, "pnl_yen_100": 50.0},
        ]
        rows = threshold_sweep_recent(trades)
        self.assertEqual(len(rows), 4)
        self.assertGreater(rows[0]["delta_pnl_yen"], 0)

    def test_run_on_repo_when_data_present(self) -> None:
        if not (NATIVE / "results" / "small_paper").is_dir():
            self.skipTest("no session data")
        result = run_phase660(repo_root=NATIVE)
        self.assertEqual(result["verdict"], PHASE660_VERDICT)
        m = result["mandatory_answers"]
        self.assertIn("10_final_verdict", m)
        self.assertIn(m["10_final_verdict"], ("ADOPT", "HOLD", "KEEP", "REJECT"))


if __name__ == "__main__":
    unittest.main()
