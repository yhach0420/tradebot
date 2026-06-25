"""Phase527: entry quality guard research unit tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase527_entry_quality_guard import (  # noqa: E402
    GUARD_IDS,
    PHASE527_VERDICT,
    _guard_allows_entry,
    _is_mfe0,
)


class TestPhase527EntryQualityGuard(unittest.TestCase):
    def test_guard_ids_include_baseline_and_g10(self) -> None:
        self.assertEqual(GUARD_IDS[0], "A_baseline")
        self.assertIn("G10_adx30_spread50_update5", GUARD_IDS)
        self.assertEqual(len(GUARD_IDS), 11)

    def test_g1_blocks_high_adx(self) -> None:
        self.assertTrue(_guard_allows_entry("G1_adx_le25", {"adx14": 20.0}))
        self.assertFalse(_guard_allows_entry("G1_adx_le25", {"adx14": 33.0}))

    def test_g7_requires_both_adx_and_spread(self) -> None:
        feats = {"adx14": 28.0, "spread": 45.0, "update_count_before_entry": 2}
        self.assertTrue(_guard_allows_entry("G7_adx30_spread50", feats))
        self.assertFalse(
            _guard_allows_entry("G7_adx30_spread50", {**feats, "spread": 55.0})
        )

    def test_baseline_allows_all(self) -> None:
        self.assertTrue(
            _guard_allows_entry(
                "A_baseline",
                {"adx14": 99.0, "spread": 99.0, "update_count_before_entry": 99},
            )
        )

    def test_mfe0_definition(self) -> None:
        self.assertTrue(_is_mfe0({"mfe_pct": 0.0}))
        self.assertFalse(_is_mfe0({"mfe_pct": 0.1}))

    def test_verdict_constant(self) -> None:
        self.assertEqual(PHASE527_VERDICT, "phase527_entry_quality_guard_research_done")


if __name__ == "__main__":
    unittest.main()
