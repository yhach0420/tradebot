"""Phase679 — Readiness forward shadow runtime tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
for p in (NATIVE / "src", REPO):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from small_paper.config import load_pilot_config  # noqa: E402
from small_paper.readiness_forward_shadow import (  # noqa: E402
    ReadinessForwardShadowCounters,
    compute_readiness_shadow_fields,
    enrich_exit_readiness_shadow_fields,
    evaluate_readiness_economics,
    evaluate_readiness_precision,
    readiness_shadow_any_enabled,
)

CFG_PATH = (
    NATIVE
    / "configs"
    / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
)


def _cfg(**overrides: object) -> SimpleNamespace:
    base = {
        "readiness_precision_shadow_enabled": True,
        "readiness_precision_shadow_expectancy_max": 2.5,
        "readiness_precision_shadow_require_live_incomplete": True,
        "readiness_economics_shadow_enabled": True,
        "readiness_economics_shadow_bounce_min": 0.45,
        "readiness_economics_shadow_require_live_incomplete": True,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class TestReadinessShadowLogic(unittest.TestCase):
    def test_disabled_returns_no_block(self) -> None:
        fields = compute_readiness_shadow_fields(
            _cfg(readiness_precision_shadow_enabled=False, readiness_economics_shadow_enabled=False),
            {"live_feature_complete": False, "entry_expectancy_score_v2": 1.0},
        )
        self.assertFalse(fields["readiness_precision_shadow_block"])
        self.assertFalse(fields["readiness_economics_shadow_block"])

    def test_i_precision_blocks_incomplete_low_expectancy(self) -> None:
        trade = {"live_feature_complete": False, "entry_expectancy_score_v2": 2.0}
        self.assertTrue(evaluate_readiness_precision(_cfg(), trade))

    def test_i_precision_skips_complete(self) -> None:
        trade = {"live_feature_complete": True, "entry_expectancy_score_v2": 1.0}
        self.assertFalse(evaluate_readiness_precision(_cfg(), trade))

    def test_h_economics_blocks_incomplete_high_bounce(self) -> None:
        trade = {"live_feature_complete": False, "bounce_from_recent_low": 0.5}
        self.assertTrue(evaluate_readiness_economics(_cfg(), trade))

    def test_union_and_overlap(self) -> None:
        fields = compute_readiness_shadow_fields(
            _cfg(),
            {
                "live_feature_complete": False,
                "entry_expectancy_score_v2": 2.0,
                "bounce_from_recent_low": 0.5,
            },
        )
        self.assertTrue(fields["readiness_precision_shadow_block"])
        self.assertTrue(fields["readiness_economics_shadow_block"])
        self.assertTrue(fields["readiness_shadow_union_block"])
        self.assertTrue(fields["readiness_shadow_overlap_block"])

    def test_exit_enrich_no_block_keeps_pnl(self) -> None:
        exit_fields = enrich_exit_readiness_shadow_fields(
            {"readiness_shadow_union_block": False},
            entry_price=100.0,
            exit_price=101.0,
            exit_reason="take_profit",
            hold_sec=60.0,
        )
        self.assertGreater(exit_fields["actual_pnl_yen_100"], 0)
        self.assertEqual(exit_fields["shadow_pnl_yen_100"], exit_fields["actual_pnl_yen_100"])

    def test_counters_summary(self) -> None:
        counters = ReadinessForwardShadowCounters(precision_enabled=True, economics_enabled=True)
        counters.record_accept({"readiness_precision_shadow_candidate": True})
        counters.record_exit(
            {
                "readiness_precision_shadow_candidate": True,
                "readiness_precision_shadow_block": True,
                "readiness_economics_shadow_block": False,
                "readiness_shadow_union_block": True,
                "readiness_shadow_overlap_block": False,
                "actual_pnl_yen_100": -500.0,
                "shadow_pnl_yen_100": 0.0,
                "is_early_stop_300s": True,
                "is_stop_hit": True,
            }
        )
        summary = counters.summary_fields()
        self.assertEqual(summary["readiness_precision_block_count"], 1)
        self.assertEqual(summary["readiness_union_block_count"], 1)


class TestPhase679Yaml(unittest.TestCase):
    def test_readiness_shadow_enabled_in_production_yaml(self) -> None:
        cfg = load_pilot_config(CFG_PATH)
        self.assertTrue(cfg.readiness_precision_shadow_enabled)
        self.assertTrue(cfg.readiness_economics_shadow_enabled)
        self.assertEqual(cfg.readiness_precision_shadow_expectancy_max, 2.5)
        self.assertEqual(cfg.readiness_economics_shadow_bounce_min, 0.45)

    def test_any_enabled_helper(self) -> None:
        self.assertTrue(readiness_shadow_any_enabled(_cfg()))
        self.assertFalse(
            readiness_shadow_any_enabled(
                _cfg(readiness_precision_shadow_enabled=False, readiness_economics_shadow_enabled=False)
            )
        )


if __name__ == "__main__":
    unittest.main()
