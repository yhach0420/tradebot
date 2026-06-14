"""Phase386 third position quality tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase386_third_position_quality_review import (  # noqa: E402
    cohort_metrics,
    enrich_trade_row,
    simulate_cap_acceptance,
)


class TestPhase386ThirdPositionQuality(unittest.TestCase):
    def _trade(
        self,
        *,
        symbol: str = "1234.T",
        entry: str = "2026-06-01T09:05:00+09:00",
        exit_t: str = "2026-06-01T09:30:00+09:00",
        ep: float = 1000.0,
        xp: float = 1010.0,
        reason: str = "trailing_mfe_exit",
        peak_mfe: float = 0.8,
    ) -> dict:
        return {
            "symbol": symbol,
            "entry_time": entry,
            "exit_time": exit_t,
            "entry_price": ep,
            "exit_price": xp,
            "pnl_yen_100": round((xp - ep) * 100.0, 2),
            "pnl_pct": round((xp - ep) / ep * 100.0, 4),
            "exit_reason_canonical": reason,
            "peak_mfe_pct": peak_mfe,
            "peak_mae_pct": 0.2,
            "hold_sec": 1500,
            "entry_vwap_dev_pct": 0.1,
            "entry_rise_5min_pct": 0.2,
            "entry_momentum_score": 0.5,
            "dynamic40_rank_bucket": "rank_21_30",
            "session_kind": "am",
            "day_key": "20260601",
        }

    def test_cap3_additional_subset(self) -> None:
        trades = [
            self._trade(symbol="1111.T", entry="2026-06-01T09:05:00+09:00", exit_t="2026-06-01T09:50:00+09:00"),
            self._trade(symbol="2222.T", entry="2026-06-01T09:06:00+09:00", exit_t="2026-06-01T09:55:00+09:00"),
            self._trade(symbol="3333.T", entry="2026-06-01T09:07:00+09:00", exit_t="2026-06-01T10:00:00+09:00"),
            self._trade(symbol="4444.T", entry="2026-06-01T09:08:00+09:00", exit_t="2026-06-01T10:05:00+09:00"),
        ]
        s2 = simulate_cap_acceptance(trades, cap=2)
        s3 = simulate_cap_acceptance(trades, cap=3)
        additional = s3["accepted_keys"] - s2["accepted_keys"]
        self.assertGreater(len(additional), 0)
        self.assertLess(len(s2["accepted_keys"]), len(s3["accepted_keys"]))

    def test_cohort_metrics(self) -> None:
        trades = [self._trade(), self._trade(symbol="2222.T", xp=990.0, reason="stop_hit", peak_mfe=0.1)]
        m = cohort_metrics(trades)
        self.assertEqual(m["trade_count"], 2)
        self.assertEqual(m["stop_hit_count"], 1)

    def test_enrich_trade_row(self) -> None:
        row = enrich_trade_row(self._trade(), cohort="cap3_additional", cap2_reject_reason="max_concurrent_positions")
        self.assertEqual(row["cohort"], "cap3_additional")
        self.assertEqual(row["exit_reason"], "trailing_mfe_exit")


if __name__ == "__main__":
    unittest.main()
