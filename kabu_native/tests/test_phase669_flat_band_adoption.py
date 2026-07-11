"""Phase669 — Flat-band mainline adoption tests."""

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

from research.exposure_gate import REJECT_FLAT_BAND_MAINLINE  # noqa: E402
from research.phase669_flat_band_adoption import (  # noqa: E402
    EXPECTED_FLAT_BAND_BLOCKS,
    PHASE669_VERDICT,
    parity_shadow_vs_mainline,
    run_adoption_audit,
)
from small_paper.config import load_pilot_config  # noqa: E402
from small_paper.or_overlay_cap import ENTRY_TYPE_OR  # noqa: E402
from small_paper.pbv2_flat_band_entry_guard import (  # noqa: E402
    REJECT_FLAT_BAND_MAINLINE as MAINLINE_REJECT,
    build_pbv2_flat_band_entry_guard_state,
    compute_flat_band_mainline_fields,
    would_block_flat_band_mainline,
)
from small_paper.pbv2_flat_band_guard_shadow import (  # noqa: E402
    APPLY_POOL_PBV2_ONLY,
    compute_pbv2_flat_band_shadow_fields,
)

CFG_PATH = (
    NATIVE
    / "configs"
    / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
)


def _cfg(**overrides: object) -> SimpleNamespace:
    base = {
        "pbv2_flat_band_mainline_enabled": True,
        "pbv2_flat_band_shadow_enabled": False,
        "pbv2_flat_band_shadow_apply_pool": APPLY_POOL_PBV2_ONLY,
        "pbv2_flat_band_shadow_rise5_flat_min_pct": 0.0,
        "pbv2_flat_band_shadow_rise5_flat_max_pct": 0.5,
        "pbv2_flat_band_shadow_rise10_flat_min_pct": -0.5,
        "pbv2_flat_band_shadow_rise10_flat_max_pct": 0.5,
        "pbv2_flat_band_shadow_overheat_rise5_pct": 2.0,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class TestFlatBandMainlineGuard(unittest.TestCase):
    def test_reject_reason_constant(self) -> None:
        self.assertEqual(REJECT_FLAT_BAND_MAINLINE, "flat_band_mainline")
        self.assertEqual(MAINLINE_REJECT, "flat_band_mainline")

    def test_mainline_matches_shadow_logic(self) -> None:
        trade = {"entry_type": "PBV2", "entry_rise_5min_pct": 0.2, "entry_rise_10min_pct": 0.0}
        shadow = compute_pbv2_flat_band_shadow_fields(
            _cfg(pbv2_flat_band_shadow_enabled=True, pbv2_flat_band_mainline_enabled=False),
            trade,
        )
        blocked, _ = would_block_flat_band_mainline(_cfg(), trade)
        self.assertEqual(shadow["pbv2_flat_band_shadow_block"], blocked)

    def test_or_excluded(self) -> None:
        trade = {
            "entry_type": ENTRY_TYPE_OR,
            "entry_rise_5min_pct": 0.2,
            "entry_rise_10min_pct": 0.0,
        }
        blocked, _ = would_block_flat_band_mainline(_cfg(), trade)
        self.assertFalse(blocked)

    def test_entry_guard_state_blocks(self) -> None:
        guard = build_pbv2_flat_band_entry_guard_state(_cfg())
        assert guard is not None
        chk = guard.check({"entry_type": "PBV2", "entry_rise_5min_pct": 2.5, "entry_rise_10min_pct": 1.0})
        self.assertTrue(chk.blocked)
        self.assertEqual(chk.reject_reason, "flat_band_mainline")

    def test_compute_fields_sets_reject_reason(self) -> None:
        trade = {"entry_type": "PBV2", "entry_rise_5min_pct": 0.2, "entry_rise_10min_pct": 0.0}
        fields = compute_flat_band_mainline_fields(_cfg(), trade)
        self.assertTrue(fields["flat_band_mainline_block"])
        self.assertEqual(fields["reject_reason"], "flat_band_mainline")


class TestPhase669ProductionYaml(unittest.TestCase):
    def test_yaml_flags(self) -> None:
        cfg = load_pilot_config(CFG_PATH)
        self.assertTrue(cfg.pbv2_flat_band_mainline_enabled)
        self.assertFalse(cfg.pbv2_flat_band_shadow_enabled)
        self.assertFalse(cfg.pbv2_rise5_shadow_enabled)
        self.assertFalse(cfg.vwap_shadow_reject_enabled)
        self.assertFalse(cfg.exit_shadow_monitor_enabled)
        self.assertFalse(cfg.exit_shadow_monitor_t2_enabled)
        self.assertFalse(cfg.exit_shadow_monitor_t3_enabled)

    def test_exposure_gate_wires_mainline(self) -> None:
        cfg = load_pilot_config(CFG_PATH)
        gate = cfg.make_exposure_gate()
        self.assertIsNotNone(gate.pbv2_flat_band_entry_guard)


def test_phase669_audit_on_canonical_dataset():
    root = Path(__file__).resolve().parents[1]
    if not (root / "results" / "small_paper").is_dir():
        pytest.skip("small_paper results missing")
    report = run_adoption_audit(skip_slow=True)
    assert report["verdict"] == PHASE669_VERDICT
    assert report["shadow_mainline_parity_ok"] is True
    assert report["flat_band_block_count_ok"] is True
    assert report["replay_validation"]["counterfactual"]["blocked_count"] == EXPECTED_FLAT_BAND_BLOCKS
    assert report["phase668_counterfactual_match"] is True


def test_parity_on_synthetic_trades():
    trades = [
        {"entry_pool": "PBV2", "entry_type": "PBV2", "entry_rise_5min_pct": 0.2, "entry_rise_10min_pct": 0.0},
        {"entry_pool": "PBV2", "entry_type": "PBV2", "entry_rise_5min_pct": 2.5, "entry_rise_10min_pct": 1.0},
        {"entry_pool": "OR", "entry_type": ENTRY_TYPE_OR, "entry_rise_5min_pct": 0.2, "entry_rise_10min_pct": 0.0},
    ]
    out = parity_shadow_vs_mainline(trades)
    assert out["mismatch_count"] == 0


if __name__ == "__main__":
    unittest.main()
