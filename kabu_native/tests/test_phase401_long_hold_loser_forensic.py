"""Phase401 long hold loser forensic tests."""

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

from research.phase401_long_hold_loser_forensic import (  # noqa: E402
    _mfe_category,
    _classify_trajectory,
    run_phase401_forensic,
    select_long_hold_losers,
)
from research.phase400_holding_time_audit import enrich_trade, load_phase399_trades


class TestPhase401Forensic(unittest.TestCase):
    def test_mfe_category(self) -> None:
        self.assertEqual(_mfe_category(0.1), "A_mfe_lt_0p2")
        self.assertEqual(_mfe_category(0.3), "B_mfe_0p2_0p5")
        self.assertEqual(_mfe_category(0.8), "C_mfe_0p5_1p0")
        self.assertEqual(_mfe_category(1.2), "D_mfe_gte_1p0")

    def test_trajectory_dead(self) -> None:
        cls = _classify_trajectory(
            max_mfe=0.1,
            mfe_5m=0.05,
            mfe_10m=0.08,
            mfe_20m=0.1,
            price_20m=100.0,
            price_30m=99.5,
            entry_px=100.0,
            exit_px=98.0,
        )
        self.assertEqual(cls, "dead_from_start")

    def test_cohort_count_27(self) -> None:
        src = REPO / "results" / "reports" / "phase399_historical_position_cap_backfill_trades.csv"
        p400 = REPO / "results" / "reports" / "phase400_holding_time_summary.json"
        if not src.is_file() or not p400.is_file():
            self.skipTest("phase399/400 artifacts missing")
        import json

        p90 = json.loads(p400.read_text(encoding="utf-8"))["hold_duration_sec"]["p90_hold_sec"]
        trades = [enrich_trade(r) for r in load_phase399_trades(src)]
        losers = select_long_hold_losers(trades, p90_hold_sec=p90)
        self.assertEqual(len(losers), 27)

    def test_run_forensic(self) -> None:
        src = REPO / "results" / "reports" / "phase399_historical_position_cap_backfill_trades.csv"
        if not src.is_file():
            self.skipTest("phase399 trades missing")
        out = REPO / "results" / "reports"
        result = run_phase401_forensic(repo_root=REPO, trades_path=src, output_dir=out)
        self.assertEqual(result["summary"]["cohort_count"], 27)
        self.assertEqual(result["summary"]["verdict"], "PASS")
        self.assertTrue((out / "phase401_long_hold_loser_forensic.csv").is_file())


if __name__ == "__main__":
    unittest.main()
