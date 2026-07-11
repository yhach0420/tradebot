"""Phase670 — Flat weak + range forward shadow tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

import pytest

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
for p in (NATIVE / "src", REPO):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase670_flat_weak_range_forward_shadow import (  # noqa: E402
    PHASE670_VERDICT,
    run_forward_shadow_audit,
)
from small_paper.config import load_pilot_config  # noqa: E402
from small_paper.flat_weak_range_forward_shadow import (  # noqa: E402
    REASON_BOTH,
    REASON_FLAT_RANGE_BREAKOUT,
    REASON_FLAT_WEAK_REFINED,
    compute_flat_weak_range_shadow_fields,
    evaluate_flat_weak_range_shadow,
    flat_weak_range_shadow_enabled,
    would_block_flat_weak_range_shadow,
)

CFG_PATH = (
    NATIVE
    / "configs"
    / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
)


def _cfg(**overrides: object) -> SimpleNamespace:
    base = {"flat_weak_range_shadow_enabled": True}
    base.update(overrides)
    return SimpleNamespace(**base)


class TestFlatWeakRangeShadowLogic(unittest.TestCase):
    def test_disabled_returns_no_candidate(self) -> None:
        fields = compute_flat_weak_range_shadow_fields(
            _cfg(flat_weak_range_shadow_enabled=False),
            {"pretrend_shape": "E", "breakout_class": "A"},
        )
        self.assertFalse(fields["flat_weak_range_shadow_candidate"])

    def test_flat_range_breakout_blocks(self) -> None:
        trade = {"pretrend_shape": "E", "breakout_class": "A"}
        blocked, reason = evaluate_flat_weak_range_shadow(trade)
        self.assertTrue(blocked)
        self.assertEqual(reason, REASON_FLAT_RANGE_BREAKOUT)

    def test_flat_weak_refined_blocks(self) -> None:
        trade = {
            "pretrend_shape": "E",
            "breakout_class": "F",
            "vwap_dev_pct": -0.2,
            "r60_sec": -0.1,
            "board_improvement": False,
            "recent_low_break": True,
        }
        blocked, reason = evaluate_flat_weak_range_shadow(trade)
        self.assertTrue(blocked)
        self.assertEqual(reason, REASON_FLAT_WEAK_REFINED)

    def test_both_reason(self) -> None:
        trade = {
            "pretrend_shape": "E",
            "breakout_class": "A",
            "vwap_dev_pct": -0.2,
            "r60_sec": -0.1,
            "board_improvement": False,
            "recent_low_break": True,
        }
        blocked, reason = evaluate_flat_weak_range_shadow(trade)
        self.assertTrue(blocked)
        self.assertEqual(reason, REASON_BOTH)

    def test_non_flat_not_blocked(self) -> None:
        trade = {"pretrend_shape": "A", "breakout_class": "NA"}
        self.assertFalse(would_block_flat_weak_range_shadow(trade))


class TestPhase670Yaml(unittest.TestCase):
    def test_shadow_enabled_in_production_yaml(self) -> None:
        cfg = load_pilot_config(CFG_PATH)
        self.assertTrue(cfg.flat_weak_range_shadow_enabled)
        self.assertTrue(cfg.pbv2_flat_band_mainline_enabled)
        self.assertFalse(cfg.pbv2_rise5_shadow_enabled)
        self.assertFalse(cfg.vwap_shadow_reject_enabled)

    def test_enabled_helper(self) -> None:
        self.assertTrue(flat_weak_range_shadow_enabled(_cfg()))
        self.assertFalse(flat_weak_range_shadow_enabled(_cfg(flat_weak_range_shadow_enabled=False)))


def test_phase670_audit_on_canonical_dataset():
    root = Path(__file__).resolve().parents[1]
    if not (root / "results" / "small_paper").is_dir():
        pytest.skip("small_paper results missing")
    report = run_forward_shadow_audit(skip_slow=True)
    assert report["verdict"] == PHASE670_VERDICT
    assert report["post_flat_band_entry_count"] > 0
    assert report["portfolio"]["blocked_count"] >= 0


if __name__ == "__main__":
    unittest.main()
