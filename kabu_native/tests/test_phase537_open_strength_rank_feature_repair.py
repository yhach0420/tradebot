"""Phase537: open strength rank feature repair tests."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase382_capital_constrained_backtest import _position_key  # noqa: E402
from research.phase537_open_strength_rank_feature_repair import (  # noqa: E402
    PHASE537_VERDICT,
    _vwap_distance,
    enrich_open_strength_features,
)


class TestPhase537RankRepair(unittest.TestCase):
    def test_vwap_distance_prefers_pct(self) -> None:
        feats = {"vwap_distance_pct": 1.5, "price_vs_vwap": -1.0}
        self.assertEqual(_vwap_distance(feats), 1.5)

    def test_vwap_distance_fallback_price_vs_vwap(self) -> None:
        feats = {"price_vs_vwap": 2.0}
        self.assertEqual(_vwap_distance(feats), 2.0)

    def test_enrich_assigns_rank_fields(self) -> None:
        base = datetime(2026, 6, 2, 9, 0)
        ent = base + timedelta(minutes=30)
        row = {
            "symbol": "7203.T",
            "day": "20260602",
            "entry_time": ent.isoformat(),
            "exit_time": (ent + timedelta(minutes=60)).isoformat(),
            "entry_price": 1000.0,
            "exit_price": 1100.0,
            "pnl_yen_100": 100.0,
            "exit_reason": "test",
        }
        row["position_key"] = _position_key(row)
        enriched = enrich_open_strength_features(
            [row],
            universe_id="U_TEST",
            strategy_id="OR_ONLY",
            price_idx={("7203.T", "20260602"): [(ent, 1000.0), (ent + timedelta(hours=6), 1100.0)]},
            bar_cache={},
            micro_lookup={},
            universe_by_day={"20260602": {"7203"}},
            executed_keys={row["position_key"]},
        )
        self.assertEqual(len(enriched), 1)
        self.assertIsNotNone(enriched[0].get("day_return_rank"))

    def test_verdict(self) -> None:
        self.assertEqual(PHASE537_VERDICT, "phase537_open_strength_rank_feature_repair_done")


if __name__ == "__main__":
    unittest.main()
