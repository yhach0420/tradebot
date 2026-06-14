"""Phase374: dynamic40 universe quality review tests."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase374_dynamic40_universe_quality_review import (  # noqa: E402
    Phase374Dynamic40UniverseQualityReview,
    classify_symbol_quality,
    dynamic40_monitored_from_universe,
    rank_bucket,
    resolve_pnl_yen_100,
)


class TestPhase374Dynamic40UniverseQualityReview(unittest.TestCase):
    def test_rank_bucket_mapping(self) -> None:
        self.assertEqual(rank_bucket(1), "rank_1_10")
        self.assertEqual(rank_bucket(15), "rank_11_20")
        self.assertEqual(rank_bucket(35), "rank_31_40")
        self.assertEqual(rank_bucket(None), "rank_unknown")

    def test_dynamic40_monitored_from_universe(self) -> None:
        universe = {
            "3905.T": {"symbol": "3905.T", "universe_slot": "core", "rank": "6"},
            "6779.T": {"symbol": "6779.T", "universe_slot": "dynamic", "rank": "11"},
            "6656.T": {"symbol": "6656.T", "universe_slot": "dynamic", "rank": "12"},
        }
        out = dynamic40_monitored_from_universe(universe)
        self.assertEqual(out["6779.T"]["dynamic_rank"], 1)
        self.assertEqual(out["6779.T"]["rank_bucket"], "rank_1_10")
        self.assertEqual(out["6656.T"]["dynamic_rank"], 2)

    def test_classify_symbol_quality_dead_watch(self) -> None:
        row = {
            "entry_count": 0,
            "session_count_monitored": 3,
            "profit_factor": None,
            "total_pnl_yen_100": None,
            "stop_hit_rate": None,
            "avg_mfe_pct": None,
            "avg_hold_minutes": None,
        }
        self.assertEqual(classify_symbol_quality(row), "dead_watch")

    def test_classify_symbol_quality_harmful(self) -> None:
        row = {
            "entry_count": 4,
            "session_count_monitored": 2,
            "profit_factor": 0.5,
            "total_pnl_yen_100": -1000.0,
            "stop_hit_rate": 0.75,
            "avg_mfe_pct": 0.1,
            "avg_hold_minutes": 40.0,
        }
        self.assertEqual(classify_symbol_quality(row), "harmful_watch")

    def test_resolve_pnl_yen_100_prefers_direct_then_shadow(self) -> None:
        self.assertEqual(resolve_pnl_yen_100({"pnl_yen_100": "100"}), 100.0)
        self.assertEqual(resolve_pnl_yen_100({"shadow_pnl_yen_100": "200"}), 200.0)
        self.assertEqual(
            resolve_pnl_yen_100({"entry_price": "1000", "exit_price": "1010"}), 1000.0
        )
        self.assertIsNone(resolve_pnl_yen_100({}))

    def test_finalize_outputs_with_rank_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp)
            audit = Phase374Dynamic40UniverseQualityReview(
                reports_dir=reports, repo_root=REPO, day_stamp="20260612"
            )
            audit.ingest_session(
                {
                    "session_meta": {
                        "session_id": "20260612/live_session_080806",
                        "day_key": "20260612",
                    },
                    "dynamic_monitored": {
                        "6976.T": {
                            "symbol": "6976.T",
                            "dynamic_rank": None,
                            "rank_bucket": "rank_unknown",
                        }
                    },
                    "reject_stats": {},
                    "trades": [
                        {
                            "symbol": "6976.T",
                            "universe_group": "dynamic40",
                            "pnl_yen_100": -500.0,
                            "pnl_pct": -0.5,
                            "peak_mfe_pct": 0.1,
                            "mae_pct": -0.8,
                            "hold_sec": 120.0,
                            "exit_reason_canonical": "stop_hit",
                            "rank_bucket": "rank_unknown",
                            "dynamic_rank": None,
                        }
                    ],
                    "production_trades": [],
                }
            )
            paths = audit.finalize_outputs(
                wall_runtime_sec=0.1,
                sessions_discovered=1,
                min_day="20260612",
                max_day="20260612",
            )
            summary = json.loads(paths["summary"].read_text(encoding="utf-8"))
            buckets = {r["rank_bucket"]: r for r in summary["rank_bucket_summary"]}
            self.assertIn("rank_unknown", buckets)
            self.assertEqual(buckets["rank_unknown"]["entry_count"], 1)
            self.assertTrue(paths["recommendation_md"].is_file())
            self.assertTrue(paths["selection_logic_md"].is_file())


if __name__ == "__main__":
    unittest.main()
