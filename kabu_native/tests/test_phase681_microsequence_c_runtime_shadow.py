"""Phase681 — Microsequence C runtime forward shadow tests."""

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

from small_paper.config import load_pilot_config  # noqa: E402
from small_paper.microsequence_pre_entry import (  # noqa: E402
    bounce_from_recent_low_ring,
    compute_microsequence_pre_entry_features,
    fall_from_recent_high_ring,
)
from small_paper.microsequence_recovery_fail_forward_shadow import (  # noqa: E402
    evaluate_microsequence_recovery_fail,
    microsequence_recovery_fail_shadow_enabled,
)
from small_paper.shadow_ihc_portfolio import compute_ihc_shadow_fields  # noqa: E402

CFG_PATH = (
    NATIVE
    / "configs"
    / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
)


def _cfg(**overrides: object) -> SimpleNamespace:
    base = {
        "microsequence_recovery_fail_shadow_enabled": True,
        "microsequence_recovery_fail_bounce_min": 0.2182,
        "microsequence_recovery_fail_fall_from_high_max": -0.1735,
        "microsequence_recovery_fail_slope_5min_max": 0.1152,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class TestMicrosequenceCShadow(unittest.TestCase):
    def test_pre_entry_bounce_fall(self) -> None:
        entry_ts = 200.0
        ring = [(80.0, 100.0), (140.0, 102.0), (199.0, 101.0), (200.0, 101.5)]
        bounce = bounce_from_recent_low_ring(ring, entry_ts=entry_ts, entry_px=101.5)
        fall = fall_from_recent_high_ring(ring, entry_ts=entry_ts, entry_px=101.5)
        self.assertIsNotNone(bounce)
        self.assertIsNotNone(fall)
        self.assertLessEqual(float(fall or 0), 0)

    def test_rule_c_blocks(self) -> None:
        trade = {
            "microsequence_pre_entry_ok": True,
            "microseq_bounce_from_recent_low": 0.3,
            "microseq_fall_from_recent_high": -0.2,
            "microseq_slope_5min": 0.1,
        }
        self.assertTrue(evaluate_microsequence_recovery_fail(_cfg(), trade))

    def test_ihc_union(self) -> None:
        fields = compute_ihc_shadow_fields(i_block=True, h_block=False, c_block=True)
        self.assertTrue(fields["shadow_union_ihc_block"])
        self.assertEqual(fields["shadow_overlap_type"], "I+C")


def test_phase681_audit_smoke():
    root = Path(__file__).resolve().parents[1]
    if not (root / "results" / "small_paper" / "20260709").is_dir():
        pytest.skip("7/9 paper missing")
    from research.phase681_microsequence_c_runtime_shadow import run_audit

    report = run_audit()
    assert report["verdict"] in {
        "C_SHADOW_READY",
        "C_NEEDS_REFINEMENT",
        "IHC_SHADOW_PORTFOLIO_READY",
        "HOLD",
        "REJECT",
    }
    out = root / "results" / "reports" / "phase681_microsequence_c_runtime_shadow"
    for name in (
        "phase681_report.json",
        "phase681_shadow_trades.csv",
        "phase681_daily_forward_summary.csv",
        "phase681_ihc_overlap.csv",
        "phase681_c_blocked_winner_quality.csv",
        "phase681_decision.md",
    ):
        assert (out / name).is_file(), name


class TestPhase681Yaml(unittest.TestCase):
    def test_c_enabled_in_yaml(self) -> None:
        cfg = load_pilot_config(CFG_PATH)
        self.assertTrue(cfg.microsequence_recovery_fail_shadow_enabled)
        self.assertTrue(microsequence_recovery_fail_shadow_enabled(_cfg()))


if __name__ == "__main__":
    unittest.main()
