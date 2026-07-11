"""Phase655 no-progress entry quality tests."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
for p in (NATIVE / "src", REPO):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase655_no_progress_entry_quality import (  # noqa: E402
    PHASE655_VERDICT,
    _counterfactual_rows,
    _is_no_progress,
    _is_winner,
    _metrics_until,
    _overlay_pnl,
    _rank_np_vs_winner,
    _trigger_fns,
    run_phase655,
)


class Phase655NoProgressTests(unittest.TestCase):
    def test_labels(self) -> None:
        self.assertTrue(_is_no_progress({"exit_reason": "no_progress_exit"}))
        self.assertTrue(_is_winner({"pnl_yen_100": 100}))
        self.assertFalse(_is_winner({"pnl_yen_100": -100, "exit_reason": "stop_hit"}))

    def test_metrics_until(self) -> None:
        series = [(0.0, 100.0), (30.0, 100.5), (60.0, 99.0)]
        m = _metrics_until(series, entry_ts=0.0, entry_px=100.0, until_ts=30.0, entry_imb=None, imb_snaps=[], entry_vwap_dev=None)
        self.assertAlmostEqual(float(m["mfe_pct"]), 0.5, places=3)

    def test_rank_np_vs_winner(self) -> None:
        np_rows = [{"entry_rise_5min_pct": 0.1 + i * 0.01, "pnl_yen_100": -100} for i in range(5)]
        win_rows = [{"entry_rise_5min_pct": 1.0 + i * 0.05, "pnl_yen_100": 200} for i in range(5)]
        rows = _rank_np_vs_winner(
            np_rows,
            win_rows,
            features=[("entry_rise_5min_pct", "rise5")],
            pool="test",
        )
        self.assertEqual(rows[0]["feature_id"], "entry_rise_5min_pct")
        self.assertGreater(abs(float(rows[0]["cohens_d_np_vs_winner"])), 0.5)

    def test_counterfactual_baseline(self) -> None:
        trades = [
            {
                "day": "2026-07-01",
                "entry_time": "2026-07-01T09:00:00+09:00",
                "pnl_yen_100": -1000.0,
                "mfe_pct_at_30s": 0.05,
                "pnl_yen_100_at_30s": -200.0,
                "entry_price": 1000.0,
                "exit_reason": "no_progress_exit",
            },
            {
                "day": "2026-07-01",
                "entry_time": "2026-07-01T09:05:00+09:00",
                "pnl_yen_100": 500.0,
                "mfe_pct_at_30s": 0.8,
                "pnl_yen_100_at_30s": 300.0,
                "entry_price": 2000.0,
                "exit_reason": "trailing_mfe_exit",
            },
        ]
        rows = _counterfactual_rows(trades, pool="all")
        baseline = next(r for r in rows if r["scenario_id"] == "baseline")
        self.assertEqual(baseline["total_pnl_yen_100"], -500.0)
        mfe30 = next(r for r in rows if r["scenario_id"] == "mfe30_lt_015_exit30")
        self.assertGreater(float(mfe30["delta_pnl_yen_100"]), 0.0)

    def test_overlay_pnl_uses_horizon(self) -> None:
        trade = {
            "pnl_yen_100": -1000.0,
            "mfe_pct_at_30s": 0.05,
            "pnl_yen_100_at_30s": -200.0,
        }
        out = _overlay_pnl(trade, horizon_sec=30, trigger=_trigger_fns()["mfe30_lt_015"])
        self.assertEqual(out, -200.0)

    def test_run_on_repo_when_data_present(self) -> None:
        if not (NATIVE / "results" / "small_paper").is_dir():
            self.skipTest("Phase634 dataset not present")
        result = run_phase655(repo_root=REPO)
        self.assertEqual(result["verdict"], PHASE655_VERDICT)
        mandatory = result["mandatory_answers"]
        self.assertIn("10_final_verdict", mandatory)
        self.assertIn(mandatory["10_final_verdict"], ("ADOPT", "HOLD", "REJECT"))


if __name__ == "__main__":
    unittest.main()
