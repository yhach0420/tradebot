"""Phase531: O_R003_OR missed winner filter study unit tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase531_o_r003_or_missed_winner_filter_study import (  # noqa: E402
    FILTER_IDS,
    PHASE531_VERDICT,
    _filter_predicate,
    _is_noise_candidate,
    _is_winner_candidate,
    _passes_g9,
)


class TestPhase531FilterStudy(unittest.TestCase):
    def test_filter_ids_count(self) -> None:
        self.assertEqual(len(FILTER_IDS), 10)

    def test_winner_candidate_rules(self) -> None:
        self.assertTrue(_is_winner_candidate({"mfe_pct": 1.5, "pnl_yen_100": -1}, rank=30))
        self.assertTrue(_is_winner_candidate({"mfe_pct": 0.2, "pnl_yen_100": 100}, rank=30))
        self.assertTrue(_is_winner_candidate({"mfe_pct": 0.2, "pnl_yen_100": -1}, rank=5))

    def test_noise_candidate_rules(self) -> None:
        self.assertTrue(_is_noise_candidate({"mfe_pct": 0.3, "pnl_yen_100": -50}, rank=25))
        self.assertFalse(_is_noise_candidate({"mfe_pct": 0.3, "pnl_yen_100": -50}, rank=10))

    def test_f7_matches_g9(self) -> None:
        feats = {"spread_bps": 40, "update_count": 3}
        self.assertTrue(_filter_predicate("F7_spread50_update5")(feats))
        g9_feats = {"spread": 40, "update_count_before_entry": 3}
        self.assertTrue(_passes_g9(g9_feats))

    def test_f3_volume_filter(self) -> None:
        pred = _filter_predicate("F3_volpct80")
        self.assertTrue(pred({"volume_percentile": 85}))
        self.assertFalse(pred({"volume_percentile": 70}))

    def test_verdict_constant(self) -> None:
        self.assertEqual(PHASE531_VERDICT, "phase531_o_r003_or_missed_winner_filter_study_done")


if __name__ == "__main__":
    unittest.main()
