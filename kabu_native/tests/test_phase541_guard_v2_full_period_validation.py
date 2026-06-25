"""Phase541: Guard v2 full-period validation unit tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase541_guard_v2_full_period_validation import (  # noqa: E402
    ADX_MAX,
    FIVE_MIN_POSITION_MAX,
    GUARD_IDS,
    MOVING_AVERAGE_POSITION_MAX,
    PHASE541_VERDICT,
    _guard_allows,
)


class TestPhase541GuardV2FullPeriod(unittest.TestCase):
    def test_verdict(self) -> None:
        self.assertEqual(PHASE541_VERDICT, "phase541_guard_v2_full_period_validation_done")

    def test_guard_ids(self) -> None:
        self.assertEqual(GUARD_IDS[0], "A_baseline")
        self.assertIn("G14_adx_ma", GUARD_IDS)
        self.assertEqual(len(GUARD_IDS), 6)

    def test_g3_adx(self) -> None:
        self.assertTrue(_guard_allows("G3_adx_le30", {"adx14": 25.0}))
        self.assertFalse(_guard_allows("G3_adx_le30", {"adx14": 35.0}))

    def test_g11_five_min(self) -> None:
        self.assertTrue(_guard_allows("G11_five_min_position", {"five_min_position": 20.0}))
        self.assertFalse(
            _guard_allows("G11_five_min_position", {"five_min_position": FIVE_MIN_POSITION_MAX + 1})
        )

    def test_g12_combo(self) -> None:
        ok = {"five_min_position": 20.0, "moving_average_position": 0.1}
        self.assertTrue(_guard_allows("G12_five_min_ma", ok))
        self.assertFalse(
            _guard_allows(
                "G12_five_min_ma",
                {**ok, "moving_average_position": MOVING_AVERAGE_POSITION_MAX + 0.1},
            )
        )

    def test_g13_g14(self) -> None:
        self.assertTrue(
            _guard_allows(
                "G13_adx_five_min",
                {"adx14": ADX_MAX, "five_min_position": FIVE_MIN_POSITION_MAX},
            )
        )
        self.assertTrue(
            _guard_allows(
                "G14_adx_ma",
                {"adx14": 20.0, "moving_average_position": 0.0},
            )
        )

    def test_baseline(self) -> None:
        self.assertTrue(_guard_allows("A_baseline", {}))


if __name__ == "__main__":
    unittest.main()
