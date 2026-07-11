"""Phase683 — Shadow feature namespace fix tests."""

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

from small_paper.microsequence_recovery_fail_forward_shadow import (  # noqa: E402
    compute_microsequence_recovery_fail_shadow_fields,
    evaluate_microsequence_recovery_fail,
)
from small_paper.readiness_forward_shadow import (  # noqa: E402
    compute_readiness_shadow_fields,
    evaluate_readiness_economics,
)
from small_paper.shadow_ihc_portfolio import compute_ihc_shadow_fields  # noqa: E402


def _cfg(**overrides: object) -> SimpleNamespace:
    base = {
        "readiness_precision_shadow_enabled": True,
        "readiness_precision_shadow_expectancy_max": 2.5,
        "readiness_precision_shadow_require_live_incomplete": True,
        "readiness_economics_shadow_enabled": True,
        "readiness_economics_shadow_bounce_min": 0.45,
        "readiness_economics_shadow_require_live_incomplete": True,
        "microsequence_recovery_fail_shadow_enabled": True,
        "microsequence_recovery_fail_bounce_min": 0.2182,
        "microsequence_recovery_fail_fall_from_high_max": -0.1735,
        "microsequence_recovery_fail_slope_5min_max": 0.1152,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class TestNamespaceIsolation(unittest.TestCase):
    def test_h_uses_accept_bounce_not_ring(self) -> None:
        trade = {
            "live_feature_complete": False,
            "bounce_from_recent_low": 0.55,
            "readiness_bounce_from_recent_low_accept": 0.55,
            "readiness_bounce_from_recent_low": 0.05,
        }
        self.assertTrue(evaluate_readiness_economics(_cfg(), trade))

    def test_h_blocks_without_ring_bounce(self) -> None:
        trade = {
            "live_feature_complete": False,
            "bounce_from_recent_low": 0.5,
            "readiness_bounce_from_recent_low_accept": 0.5,
            "readiness_bounce_from_recent_low": None,
        }
        self.assertTrue(evaluate_readiness_economics(_cfg(), trade))

    def test_c_uses_microseq_fields_only(self) -> None:
        trade = {
            "microsequence_pre_entry_ok": True,
            "microseq_bounce_from_recent_low": 0.3,
            "microseq_fall_from_recent_high": -0.2,
            "microseq_slope_5min": 0.1,
            "bounce_from_recent_low": 0.01,
            "fall_from_recent_high": 0.0,
            "slope_5min": 999.0,
        }
        self.assertTrue(evaluate_microsequence_recovery_fail(_cfg(), trade))

    def test_c_ignores_generic_bounce_when_microseq_missing(self) -> None:
        trade = {
            "microsequence_pre_entry_ok": True,
            "bounce_from_recent_low": 0.3,
            "fall_from_recent_high": -0.2,
            "slope_5min": 0.1,
        }
        self.assertFalse(evaluate_microsequence_recovery_fail(_cfg(), trade))

    def test_readiness_fields_set_accept_namespace(self) -> None:
        fields = compute_readiness_shadow_fields(
            _cfg(),
            {"live_feature_complete": False, "bounce_from_recent_low": 0.6, "entry_expectancy_score_v2": 3.0},
            price_ring=[(0.0, 100.0), (1.0, 101.0)],
            entry_ts=1.0,
        )
        self.assertEqual(fields["readiness_bounce_from_recent_low_accept"], 0.6)

    def test_microseq_compute_does_not_leak_generic_fields(self) -> None:
        fields = compute_microsequence_recovery_fail_shadow_fields(
            _cfg(),
            {"bounce_from_recent_low": 0.99},
            price_ring=[(0.0, 100.0), (60.0, 101.0), (120.0, 101.5)],
            entry_ts=120.0,
        )
        self.assertIn("microseq_bounce_from_recent_low", fields)
        self.assertNotIn("bounce_from_recent_low", fields)

    def test_ihc_union_sources(self) -> None:
        fields = compute_ihc_shadow_fields(i_block=True, h_block=True, c_block=False)
        self.assertEqual(fields["ihc_h_feature_source"], "readiness_bounce_from_recent_low_accept")
        self.assertEqual(fields["ihc_c_feature_source"], "microseq_ring")
        self.assertIn("H:accept_bounce", fields["ihc_union_feature_sources"])


def test_phase683_audit_smoke():
    root = Path(__file__).resolve().parents[1]
    if not (root / "results" / "small_paper" / "20260709").is_dir():
        pytest.skip("7/9 paper missing")
    from research.phase683_shadow_feature_namespace import run_audit

    report = run_audit()
    assert report["verdict"] == "SHADOW_NAMESPACE_FIXED_AND_PAPER_STARTED"
    out = root / "results" / "reports" / "phase683_shadow_feature_namespace"
    for name in (
        "phase683_report.json",
        "phase683_h_reconciliation.csv",
        "phase683_ihc_union_recomputed.csv",
        "phase683_decision.md",
    ):
        assert (out / name).is_file(), name


if __name__ == "__main__":
    unittest.main()
