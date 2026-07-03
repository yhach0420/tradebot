"""Phase616 CoreRuntimeMode tests."""

from __future__ import annotations

import json
import os
import unittest
from unittest import mock

from small_paper.config import SmallPaperPilotConfig
from small_paper.core_runtime_mode import (
    CoreRuntimeMode,
    apply_core_runtime_mode,
    audit_enabled_for_mode,
    extension_bus_enabled,
    finalize_core_runtime_config,
)
from small_paper.extension_bus import ExtensionBus
from small_paper.pre625_runtime_structure_mode import (
    apply_pre625_runtime_structure_mode,
    finalize_runtime_structure_config,
)


class TestCoreRuntimeMode(unittest.TestCase):
    def test_core_only_disables_extensions(self) -> None:
        cfg = apply_core_runtime_mode(SmallPaperPilotConfig(), CoreRuntimeMode.CORE_ONLY)
        self.assertEqual(cfg.core_runtime_mode, "CORE_ONLY")
        self.assertFalse(cfg.live_order_adapter_enabled)
        self.assertFalse(cfg.vol_liq_startup_cache_enabled)
        self.assertFalse(extension_bus_enabled(CoreRuntimeMode.CORE_ONLY))

    def test_core_plus_audit_has_bus_and_audit(self) -> None:
        cfg = apply_core_runtime_mode(SmallPaperPilotConfig(), CoreRuntimeMode.CORE_PLUS_AUDIT)
        self.assertTrue(extension_bus_enabled(CoreRuntimeMode.CORE_PLUS_AUDIT))
        self.assertTrue(audit_enabled_for_mode(CoreRuntimeMode.CORE_PLUS_AUDIT))
        self.assertFalse(cfg.live_order_adapter_enabled)

    def test_full_extension_keeps_defaults(self) -> None:
        base = SmallPaperPilotConfig()
        cfg = apply_core_runtime_mode(base, CoreRuntimeMode.FULL_EXTENSION)
        self.assertTrue(cfg.live_order_adapter_enabled)

    def test_pre625_alias(self) -> None:
        cfg = finalize_runtime_structure_config(SmallPaperPilotConfig(), cli_flag=True)
        self.assertEqual(cfg.core_runtime_mode, "CORE_ONLY")

    def test_env_core_runtime_mode(self) -> None:
        cfg = SmallPaperPilotConfig()
        with mock.patch.dict(os.environ, {"CORE_RUNTIME_MODE": "CORE_ONLY"}, clear=False):
            out = finalize_core_runtime_config(cfg)
        self.assertEqual(out.core_runtime_mode, "CORE_ONLY")

    def test_extension_bus_none_for_core_only(self) -> None:
        cfg = apply_core_runtime_mode(SmallPaperPilotConfig(), CoreRuntimeMode.CORE_ONLY)
        bus = ExtensionBus.maybe_create(
            mode=CoreRuntimeMode.CORE_ONLY,
            config=cfg,
            state=object(),
            writer=object(),
        )
        self.assertIsNone(bus)


if __name__ == "__main__":
    unittest.main()
