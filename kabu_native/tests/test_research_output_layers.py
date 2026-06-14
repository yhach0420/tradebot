"""Tests for shared research dual-layer output standard."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.research_output_layers import (  # noqa: E402
    LIVE_SIM_REQUIRED_FIELDS,
    build_adoption_verdict,
    build_dual_layer_bundle,
    build_live_simulation_layer,
    build_research_layer,
    compute_days_below_50pct,
)


class TestResearchOutputLayers(unittest.TestCase):
    def test_build_research_layer(self) -> None:
        layer = build_research_layer([100.0, -50.0, 200.0])
        self.assertEqual(layer["total_pnl_yen"], 250.0)
        self.assertGreater(layer["profit_factor"], 1.0)
        self.assertEqual(layer["win_rate"], 0.6667)

    def test_build_live_simulation_layer_required_fields(self) -> None:
        layer = build_live_simulation_layer(
            cap=2,
            final_equity=1_600_000.0,
            total_return_pct=6.6667,
            max_drawdown_pct=5.0,
            days_below_50pct=0,
            accepted_count=10,
            rejected_count=5,
        )
        for field in LIVE_SIM_REQUIRED_FIELDS:
            self.assertIn(field, layer)
        self.assertEqual(layer["starting_equity"], 1_500_000.0)
        self.assertEqual(layer["leverage"], 2.0)
        self.assertEqual(layer["shares"], 100)

    def test_adoption_uses_final_equity_not_pf(self) -> None:
        research = build_research_layer([100.0, 200.0, 300.0])
        live = build_live_simulation_layer(
            cap=2,
            final_equity=1_400_000.0,
            total_return_pct=-6.6667,
            max_drawdown_pct=10.0,
            days_below_50pct=0,
            accepted_count=3,
            rejected_count=0,
        )
        verdict = build_adoption_verdict(live_simulation_layer=live, research_layer=research)
        self.assertFalse(verdict["adoptable"])
        self.assertEqual(verdict["primary_metric"], "final_equity")
        self.assertTrue(verdict["research_pf_not_adoption_basis"])

    def test_compute_days_below_50pct(self) -> None:
        rows = [
            {"end_equity": 1_600_000.0},
            {"end_equity": 700_000.0},
            {"end_equity": 1_500_000.0},
        ]
        self.assertEqual(compute_days_below_50pct(rows, starting_equity=1_500_000.0), 1)

    def test_dual_layer_bundle(self) -> None:
        bundle = build_dual_layer_bundle(
            research_layer=build_research_layer([10.0, 20.0]),
            live_simulation_layer=build_live_simulation_layer(
                cap=2,
                final_equity=1_550_000.0,
                total_return_pct=3.3333,
                max_drawdown_pct=2.0,
                days_below_50pct=0,
                accepted_count=2,
                rejected_count=1,
            ),
        )
        self.assertIn("research_layer", bundle)
        self.assertIn("live_simulation_layer", bundle)
        self.assertIn("adoption_verdict", bundle)


if __name__ == "__main__":
    unittest.main()
