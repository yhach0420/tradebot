"""Phase255-SectorHeat-Forward-Shadow-Logger tests."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.market_sector_heat_forward_shadow_logger import (  # noqa: E402
    FORWARD_PATTERNS,
    MarketSectorHeatForwardShadowLogger,
    _upsert_rows,
    backfill_from_phase253,
    compute_forward_summary,
    run_forward_shadow_logger,
)


class TestMarketSectorHeatForwardShadowLogger(unittest.TestCase):
    def test_upsert_rows(self) -> None:
        existing = [{"day": "20260520", "pattern": "actual", "x": 1}]
        new = [{"day": "20260520", "pattern": "actual", "x": 2}, {"day": "20260521", "pattern": "actual", "x": 3}]
        out = _upsert_rows(existing, new, key_fields=("day", "pattern"))
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["x"], 2)

    def test_compute_forward_summary_adopt_blocked(self) -> None:
        trade_rows = [
            {
                "day": "20260520",
                "pattern": "actual",
                "entry_count": 10,
                "total_pnl_yen_100": -100.0,
                "delta_vs_actual": 0,
            },
            {
                "day": "20260520",
                "pattern": "bottom5_exclude",
                "entry_count": 5,
                "total_pnl_yen_100": 50.0,
                "delta_vs_actual": 150.0,
                "removed_loser_avoidance_yen_100": 100.0,
                "added_winner_contribution_yen_100": 50.0,
            },
        ]
        summary = compute_forward_summary(trade_rows, universe_rows=[])
        self.assertTrue(summary["adopt_not_allowed_global"])
        self.assertEqual(summary["trade_overlap_day_count"], 1)

    def test_backfill_from_phase253(self) -> None:
        reports = REPO / "kabu_native" / "results" / "reports"
        if not (reports / "phase253_universe_diff_by_day.csv").is_file():
            self.skipTest("phase253 outputs missing")
        u, t = backfill_from_phase253(
            reports_dir=reports,
            universe_path=reports / "phase253_universe_diff_by_day.csv",
            trade_path=reports / "phase253_trade_validation_by_pattern.csv",
            day_level_path=reports / "phase253_day_level_delta.csv",
        )
        self.assertGreater(len(u), 0)
        self.assertGreater(len(t), 0)
        patterns = {str(r.get("pattern")) for r in t}
        self.assertTrue(patterns.issubset(set(FORWARD_PATTERNS)))

    def test_run_backfill_on_repo(self) -> None:
        reports = REPO / "kabu_native" / "results" / "reports"
        if not (reports / "phase253_sector_heat_negative_filter_summary.json").is_file():
            self.skipTest("phase253 summary missing")
        result = run_forward_shadow_logger(
            repo_root=REPO,
            reports_dir=reports,
            log_universe=False,
            log_trades=False,
            update_summary=True,
            backfill_phase253=True,
        )
        self.assertEqual(result["phase"], "255-SectorHeat-Forward-Shadow-Logger")
        summary = result.get("forward_summary") or {}
        self.assertGreaterEqual(summary.get("trade_overlap_day_count") or 0, 4)

    def test_write_outputs(self) -> None:
        reports = REPO / "kabu_native" / "results" / "reports"
        if not (reports / "phase253_sector_heat_negative_filter_summary.json").is_file():
            self.skipTest("phase253 summary missing")
        job = MarketSectorHeatForwardShadowLogger(repo_root=REPO, reports_dir=reports)
        result = job.run(backfill_phase253=True, log_universe=False, log_trades=False)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            job_out = MarketSectorHeatForwardShadowLogger(repo_root=REPO, reports_dir=out)
            paths = job_out.write_outputs(result)
            for key in ("universe_log", "trade_log", "summary", "report"):
                self.assertTrue(paths[key].is_file(), key)


if __name__ == "__main__":
    unittest.main()
