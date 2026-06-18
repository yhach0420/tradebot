"""Phase442: No Progress Exit runtime tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[2]
KABU = Path(__file__).resolve().parents[1]
for p in (KABU / "src", REPO):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.structural_exit_policies import (  # noqa: E402
    POLICY_COMBINED_STRUCTURAL_EXIT_V1_TRAILING_MFE_SHADOW,
    combined_exit_signal_on_latest_tick,
    is_official_structural_exit_reason,
)
from small_paper.config import load_pilot_config  # noqa: E402
from small_paper.no_progress_exit import (  # noqa: E402
    NO_PROGRESS_EXIT_REASON,
    PHASE442_POLICY_KEY,
    no_progress_exit_triggered,
    required_mfe_threshold_pct,
)


class TestNoProgressExitPolicy(unittest.TestCase):
    def test_required_mfe_schedule(self) -> None:
        self.assertIsNone(required_mfe_threshold_pct(899.0))
        self.assertEqual(required_mfe_threshold_pct(900.0), 0.6)
        self.assertEqual(required_mfe_threshold_pct(1200.0), 0.65)
        self.assertEqual(required_mfe_threshold_pct(3600.0), 0.8)

    def test_trigger_at_900s(self) -> None:
        self.assertTrue(no_progress_exit_triggered(900.0, 0.5, 0.1))
        self.assertFalse(no_progress_exit_triggered(900.0, 0.9, 0.1))
        self.assertFalse(no_progress_exit_triggered(900.0, 0.5, 0.5))

    def test_exit_reason_official(self) -> None:
        self.assertTrue(is_official_structural_exit_reason(NO_PROGRESS_EXIT_REASON))


class TestNoProgressRuntimeExit(unittest.TestCase):
    def test_fires_before_trailing_mfe(self) -> None:
        entry_ts = 1_000_000.0
        ticks = [
            {
                "ts_epoch": entry_ts + 900.0,
                "price": 100.0,
                "pnl_pct": 0.1,
                "quality": 0.7,
                "momentum": 0.5,
                "favorable": 0.5,
                "pure_price_momentum": 0.0,
            }
        ]
        cfg_off = SimpleNamespace(
            structural_exit_policy=POLICY_COMBINED_STRUCTURAL_EXIT_V1_TRAILING_MFE_SHADOW,
            hard_stop_pct=1.2,
            no_progress_exit_enabled=False,
        )
        cfg_on = SimpleNamespace(
            structural_exit_policy=POLICY_COMBINED_STRUCTURAL_EXIT_V1_TRAILING_MFE_SHADOW,
            hard_stop_pct=1.2,
            no_progress_exit_enabled=True,
        )
        self.assertIsNone(
            combined_exit_signal_on_latest_tick(
                ticks,
                100.0,
                cfg_off,
                entry_ts_epoch=entry_ts,
            )
        )
        sig = combined_exit_signal_on_latest_tick(
            ticks,
            100.0,
            cfg_on,
            entry_ts_epoch=entry_ts,
        )
        self.assertIsNotNone(sig)
        assert sig is not None
        pnl, reason, _px = sig
        self.assertEqual(reason, NO_PROGRESS_EXIT_REASON)
        self.assertAlmostEqual(pnl, 0.1)

    def test_yaml_flag_loaded(self) -> None:
        cfg_path = (
            KABU
            / "configs"
            / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
        )
        cfg = load_pilot_config(cfg_path)
        self.assertTrue(cfg.no_progress_exit_enabled)
        fields = cfg.policy_summary_fields()
        self.assertTrue(fields.get("no_progress_exit_enabled"))
        self.assertEqual(PHASE442_POLICY_KEY, "linmfe_t900_i0p6_s0p05_c0p8_p0p3")


if __name__ == "__main__":
    unittest.main()
