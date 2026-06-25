"""Phase533: OR profit source audit unit tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase533_or_profit_source_audit import (  # noqa: E402
    PHASE533_VERDICT,
    _assign_cluster,
    _exclusion_rows,
    _feature_separation_rows,
)


class TestPhase533OrProfitSource(unittest.TestCase):
    def test_trade_exclusion_top10(self) -> None:
        trades = [
            {"symbol": "A", "entry_time": f"2026-06-01T09:0{i}:00+09:00", "pnl_yen_100": 100 - i * 10, "day": "20260601"}
            for i in range(20)
        ]
        rows = _exclusion_rows(
            trades,
            audit_type="trade",
            group="trade",
            top_ns=(10,),
            key_fn=lambda t: t["position_key"],
            fields=["remaining_max_dd_yen_100"],
        )
        top10 = next(r for r in rows if r["exclusion_type"] == "top10_trades")
        self.assertEqual(top10["excluded_count"], 10)

    def test_feature_separation(self) -> None:
        winners = [{"rsi14": 70, "update_count": 5} for _ in range(10)]
        losers = [{"rsi14": 50, "update_count": 2} for _ in range(10)]
        rows = _feature_separation_rows(winners, losers)
        rsi = next(r for r in rows if r["feature_id"] == "rsi14")
        self.assertIsNotNone(rsi.get("effect_size"))

    def test_cluster_assignment(self) -> None:
        cid, label = _assign_cluster({"minutes_from_open": 30, "update_count": 2, "breakout_type": "true_breakout"})
        self.assertEqual(cid, "A")
        self.assertEqual(label, "early_breakout")

    def test_verdict(self) -> None:
        self.assertEqual(PHASE533_VERDICT, "phase533_or_profit_source_audit_done")


if __name__ == "__main__":
    unittest.main()
