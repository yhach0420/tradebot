"""Tests for Phase612A pre625 runtime structure mode."""

from __future__ import annotations

import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
import sys

for p in (ROOT / "src", ROOT.parent):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from small_paper.config import SmallPaperPilotConfig, load_pilot_config
from small_paper.pre625_runtime_structure_mode import (
    PRE625_RUNTIME_STRUCTURE_OFF,
    apply_pre625_runtime_structure_mode,
    env_pre625_runtime_structure_mode_enabled,
    finalize_runtime_structure_config,
    pre625_runtime_structure_session_fields,
)


class Pre625RuntimeStructureModeTests(unittest.TestCase):
    def test_apply_forces_off(self) -> None:
        cfg = SmallPaperPilotConfig(
            live_order_adapter_enabled=True,
            live_order_notifier_enabled=True,
            live_capital_check_enabled=True,
            entry_freshness_board_fallback_enabled=True,
            vol_liq_startup_cache_enabled=True,
            live_order_jsonl_enabled=True,
        )
        out = apply_pre625_runtime_structure_mode(cfg)
        self.assertTrue(out.pre625_runtime_structure_mode)
        for key, val in PRE625_RUNTIME_STRUCTURE_OFF.items():
            self.assertEqual(getattr(out, key), val, key)

    def test_finalize_cli_flag(self) -> None:
        cfg = SmallPaperPilotConfig()
        out = finalize_runtime_structure_config(cfg, cli_flag=True)
        self.assertTrue(out.pre625_runtime_structure_mode)
        self.assertFalse(out.live_order_adapter_enabled)

    def test_finalize_env_flag(self) -> None:
        cfg = SmallPaperPilotConfig()
        with mock.patch.dict(os.environ, {"PRE625_RUNTIME_STRUCTURE_MODE": "true"}):
            self.assertTrue(env_pre625_runtime_structure_mode_enabled())
            out = finalize_runtime_structure_config(cfg)
        self.assertTrue(out.pre625_runtime_structure_mode)

    def test_session_fields(self) -> None:
        cfg = apply_pre625_runtime_structure_mode(SmallPaperPilotConfig())
        fields = pre625_runtime_structure_session_fields(cfg)
        self.assertTrue(fields["pre625_runtime_structure_mode"])
        self.assertIn("pre625_runtime_structure_forced_off", fields)

    def test_production_yaml_load_unchanged_without_mode(self) -> None:
        yaml_path = ROOT / "configs" / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
        if not yaml_path.is_file():
            self.skipTest("production yaml missing")
        cfg = load_pilot_config(yaml_path)
        self.assertFalse(cfg.pre625_runtime_structure_mode)


if __name__ == "__main__":
    unittest.main()
