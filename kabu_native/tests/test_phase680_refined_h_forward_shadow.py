"""Phase680 — Refined H forward shadow tests."""

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
from small_paper.mfe_pre_entry import compute_mfe_pre_entry_pct  # noqa: E402
from small_paper.readiness_forward_shadow import (  # noqa: E402
    evaluate_readiness_refined_h,
    readiness_refined_h_shadow_enabled,
)

CFG_PATH = (
    NATIVE
    / "configs"
    / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
)


def _cfg(**overrides: object) -> SimpleNamespace:
    base = {
        "readiness_refined_h_shadow_enabled": True,
        "readiness_refined_h_bounce_min": 0.45,
        "readiness_refined_h_pre_entry_mfe_max_pct": 1.0,
        "readiness_refined_h_require_live_incomplete": True,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class TestMfePreEntry(unittest.TestCase):
    def test_uses_only_pre_entry_ticks(self) -> None:
        entry_ts = 200.0
        ring = [
            (80.0, 100.0),
            (140.0, 102.0),
            (199.0, 101.0),
            (200.0, 101.0),
            (250.0, 110.0),
        ]
        pct = compute_mfe_pre_entry_pct(ring, entry_ts=entry_ts, entry_px=101.0, window_sec=120.0)
        self.assertIsNotNone(pct)
        self.assertAlmostEqual(float(pct or 0), (102.0 - 101.0) / 101.0 * 100, places=3)

    def test_refined_h_blocks_low_pre_mfe(self) -> None:
        trade = {
            "live_feature_complete": False,
            "bounce_from_recent_low": 0.5,
            "mfe_pre_entry_pct": 0.5,
        }
        self.assertTrue(evaluate_readiness_refined_h(_cfg(), trade))

    def test_refined_h_skips_high_pre_mfe(self) -> None:
        trade = {
            "live_feature_complete": False,
            "bounce_from_recent_low": 0.5,
            "mfe_pre_entry_pct": 2.0,
        }
        self.assertFalse(evaluate_readiness_refined_h(_cfg(), trade))


class TestPhase680Yaml(unittest.TestCase):
    def test_refined_h_enabled_in_yaml(self) -> None:
        cfg = load_pilot_config(CFG_PATH)
        self.assertTrue(cfg.readiness_refined_h_shadow_enabled)
        self.assertEqual(cfg.readiness_refined_h_pre_entry_mfe_max_pct, 1.0)

    def test_enabled_helper(self) -> None:
        self.assertTrue(readiness_refined_h_shadow_enabled(_cfg()))


if __name__ == "__main__":
    unittest.main()
