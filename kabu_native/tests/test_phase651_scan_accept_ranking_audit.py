"""Phase651 scan accept ranking audit tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
for p in (NATIVE / "src", REPO):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase651_scan_accept_ranking_audit import (  # noqa: E402
    RANKING_RULES,
    _alt_rank_key,
    _compute_rank,
)
from small_paper.entry_scan_controller import EntryFreshnessSnapshot, candidate_rank_score


class Phase651RankingTests(unittest.TestCase):
    def test_rank_score_formula(self) -> None:
        trade = {
            "entry_expectancy_score_v2": 3,
            "continuation_quality_score": 0.5,
            "trading_value": 2e8,
            "entry_order_book_imbalance": 0.6,
            "entry_vwap_dev_pct": 0.1,
            "momentum_continuation_score": 0.2,
        }
        fresh = EntryFreshnessSnapshot("kabu_push", "t", "t", 0.5, 0.5)
        self.assertEqual(_compute_rank({**trade, **{"price_age_sec": 0.5}}), candidate_rank_score(trade, fresh))

    def test_v2_dominates_cq(self) -> None:
        high_v2 = _compute_rank(
            {
                "entry_expectancy_score_v2": 4,
                "continuation_quality_score": 0.1,
                "price_age_sec": 1.0,
            }
        )
        low_v2 = _compute_rank(
            {
                "entry_expectancy_score_v2": 3,
                "continuation_quality_score": 0.9,
                "price_age_sec": 1.0,
            }
        )
        self.assertGreater(high_v2, low_v2)

    def test_alt_v2_only(self) -> None:
        a = {"entry_expectancy_score_v2": 4, "message_index": 10}
        b = {"entry_expectancy_score_v2": 3, "message_index": 1}
        self.assertLess(_alt_rank_key("v2_only", a, 0), _alt_rank_key("v2_only", b, 1))

    def test_rules_present(self) -> None:
        self.assertTrue(any(r["rule_id"] == "primary_sort" for r in RANKING_RULES))


if __name__ == "__main__":
    unittest.main()
