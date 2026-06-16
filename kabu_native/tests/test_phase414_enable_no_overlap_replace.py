"""Phase414: production YAML enables no_overlap_replace."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PARENT = REPO.parent
for p in (REPO / "src", PARENT):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from runner.am_pm_daily_runner import TRAILING_MFE_SHADOW_YAML  # noqa: E402
from small_paper.config import load_pilot_config  # noqa: E402


class TestPhase414EnableNoOverlapReplace(unittest.TestCase):
    def test_production_yaml_enables_no_overlap_replace(self) -> None:
        cfg_path = REPO / "configs" / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
        self.assertTrue(cfg_path.is_file())
        cfg = load_pilot_config(cfg_path)
        self.assertEqual(cfg.same_symbol_open_policy, "no_overlap_replace")
        self.assertFalse(cfg.order_enabled)
        self.assertTrue(cfg.paper_only)

    def test_daily_runner_uses_production_yaml(self) -> None:
        self.assertEqual(
            TRAILING_MFE_SHADOW_YAML,
            "kabu_native/configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml",
        )
        cfg_path = REPO / "configs" / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
        cfg = load_pilot_config(cfg_path)
        self.assertEqual(cfg.same_symbol_open_policy, "no_overlap_replace")


if __name__ == "__main__":
    unittest.main()
