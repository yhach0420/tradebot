"""Phase375: dynamic40 rank quality shadow tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase375_dynamic40_rank_quality_shadow import (  # noqa: E402
    SymbolState,
    compute_penalty,
    shadow_rank_day,
)


class TestPhase375Dynamic40RankQualityShadow(unittest.TestCase):
    def test_harmful_penalty_demotes_symbol(self) -> None:
        history = {
            "BAD.T": SymbolState(
                entry_count=4,
                monitored_day_count=2,
                pnl_yens=[-100.0, -200.0, -50.0, -80.0],
                stop_hit_count=3,
                mfe_pcts=[0.1, 0.1, 0.2, 0.1],
            )
        }
        pool = {
            "BAD.T": {"symbol": "BAD.T", "base_vol_liq": 200.0, "baseline_dynamic_rank": 1},
            "GOOD.T": {"symbol": "GOOD.T", "base_vol_liq": 150.0, "baseline_dynamic_rank": 2},
        }
        shadow = shadow_rank_day(variant="B_harmful_penalty", pool=pool, history=history)
        self.assertEqual(shadow["GOOD.T"]["shadow_rank"], 1)
        self.assertEqual(shadow["BAD.T"]["shadow_rank"], 2)

    def test_walk_forward_no_future_leak_in_penalty(self) -> None:
        history: dict[str, SymbolState] = {}
        penalty_day1 = compute_penalty("B_harmful_penalty", "X.T", history, {})
        self.assertEqual(penalty_day1, 0.0)

    def test_baseline_preserves_original_order(self) -> None:
        pool = {
            "A.T": {"symbol": "A.T", "base_vol_liq": 50.0, "baseline_dynamic_rank": 2},
            "B.T": {"symbol": "B.T", "base_vol_liq": 100.0, "baseline_dynamic_rank": 1},
        }
        shadow = shadow_rank_day(variant="A_baseline", pool=pool, history={})
        self.assertEqual(shadow["B.T"]["shadow_rank"], 1)
        self.assertEqual(shadow["A.T"]["shadow_rank"], 2)


if __name__ == "__main__":
    unittest.main()
