"""Phase542: Guard v2 threshold tuning unit tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase542_guard_v2_threshold_tuning import (  # noqa: E402
    G13_GUARD_ID,
    GUARD_SPECS,
    PHASE542_VERDICT,
    _guard_allows,
    build_guard_specs,
)


class TestPhase542GuardV2ThresholdTuning(unittest.TestCase):
    def test_verdict(self) -> None:
        self.assertEqual(PHASE542_VERDICT, "phase542_guard_v2_threshold_tuning_done")

    def test_guard_count(self) -> None:
        specs = build_guard_specs()
        self.assertEqual(len(specs), 33)
        self.assertEqual(specs[0]["guard_id"], "A_baseline")
        self.assertEqual(G13_GUARD_ID, "ADX30_FIVE33")

    def test_baseline_allows(self) -> None:
        self.assertTrue(_guard_allows({}, {"guard_id": "A_baseline", "group": "baseline"}))

    def test_adx_only(self) -> None:
        spec = {"guard_id": "ADX35", "adx_max": 35.0}
        self.assertTrue(_guard_allows({"adx14": 30.0}, spec))
        self.assertFalse(_guard_allows({"adx14": 40.0}, spec))

    def test_g13_combo(self) -> None:
        spec = next(s for s in GUARD_SPECS if s["guard_id"] == G13_GUARD_ID)
        ok = {"adx14": 25.0, "five_min_position": 20.0}
        self.assertTrue(_guard_allows(ok, spec))
        self.assertFalse(_guard_allows({**ok, "five_min_position": 50.0}, spec))

    def test_triple_guard(self) -> None:
        spec = next(s for s in GUARD_SPECS if s["guard_id"] == "ADX35_FIVE50_MA025")
        ok = {"adx14": 30.0, "five_min_position": 40.0, "moving_average_position": 0.1}
        self.assertTrue(_guard_allows(ok, spec))
        self.assertFalse(_guard_allows({**ok, "moving_average_position": 0.4}, spec))


if __name__ == "__main__":
    unittest.main()
