"""Phase380 board quality tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase380_board_quality_entry_signal_review import (  # noqa: E402
    _board_tier,
    build_bucket_rows,
)


class TestPhase380BoardQuality(unittest.TestCase):
    def test_board_buckets(self) -> None:
        trades = [
            {
                "pnl_yen_100": -100.0,
                "exit_reason_canonical": "stop_hit",
                "peak_mfe_pct": 0.1,
                "board_dynamic_tier": "board_low",
                "entry_imbalance_percentile": 10.0,
                "universe_group": "dynamic40",
                "session_kind": "am",
            },
            {
                "pnl_yen_100": 200.0,
                "exit_reason_canonical": "trailing_mfe_exit",
                "peak_mfe_pct": 1.0,
                "board_dynamic_tier": "board_high",
                "entry_imbalance_percentile": 80.0,
                "universe_group": "dynamic40",
                "session_kind": "am",
            },
        ]
        rows = build_bucket_rows(trades)
        self.assertEqual(_board_tier(trades[0]), "board_low")
        self.assertTrue(any(r["bucket"] == "board_high" for r in rows))


if __name__ == "__main__":
    unittest.main()
