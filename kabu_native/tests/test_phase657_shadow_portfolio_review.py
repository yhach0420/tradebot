"""Phase657 shadow portfolio review tests."""

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

from research.phase652_shadow_registry import ShadowDef  # noqa: E402
from research.phase657_shadow_portfolio_review import (  # noqa: E402
    PHASE657_VERDICT,
    MERGE_INTO,
    _decide,
    _score_shadow,
    run_phase657,
)


class Phase657PortfolioTests(unittest.TestCase):
    def test_merge_decision(self) -> None:
        sd = ShadowDef(
            shadow_id="loss_acceleration_exit",
            phase="337",
            name="loss accel",
            category="exit_runtime",
            runtime_or_research="runtime",
            entry_or_exit="exit",
        )
        decision, _ = _decide(sd, {}, _score_shadow(sd, {}))
        self.assertEqual(decision, "MERGE")
        self.assertEqual(MERGE_INTO["loss_acceleration_exit"], "realtime_board_exit_shadow")

    def test_adopted_mainline(self) -> None:
        sd = ShadowDef(
            shadow_id="board_dynamic_trailing_shadow",
            phase="332",
            name="bdt",
            category="exit_runtime",
            runtime_or_research="runtime",
            entry_or_exit="exit",
            adopted_mainline=True,
        )
        decision, _ = _decide(sd, {"net_effect_yen": 1000}, _score_shadow(sd, {"net_effect_yen": 1000}))
        self.assertEqual(decision, "ADOPT")

    def test_score_positive_net(self) -> None:
        sd = ShadowDef(
            shadow_id="pbv2_flat_band_shadow",
            phase="650",
            name="fb",
            category="entry_runtime",
            runtime_or_research="runtime",
            entry_or_exit="entry",
            mainline_effect="logging_only",
        )
        scores = _score_shadow(sd, {"net_effect_yen": 100000, "session_count": 10, "per_day_effects": [1, 2, 3]})
        self.assertGreater(scores["total_score"], 50)

    def test_run_on_repo_when_data_present(self) -> None:
        if not (NATIVE / "results" / "small_paper").is_dir():
            self.skipTest("session data not present")
        result = run_phase657(repo_root=REPO)
        self.assertEqual(result["verdict"], PHASE657_VERDICT)
        m = result["mandatory_answers"]
        self.assertIn("1_adopt_top10", m)
        self.assertIn("12_shadow_development_can_pause", m)


if __name__ == "__main__":
    unittest.main()
